"""Template variable builder — single public API per Section 13.25.

Thin orchestrator that delegates to AddressingScheme and addressing
helpers. Produces the complete Jinja2 template variable namespace
for any satellite or ground station node.
"""

from __future__ import annotations

from typing import Any

from nodalarc.models.addressing import (
    AddressingScheme,
    NeighborAssignment,
    assign_isl_neighbors,
    compute_area_assignments,
    neighbors_by_node,
)
from nodalarc.models.constellation import (
    ConstellationConfig,
    ExplicitConstellation,
    ParametricConstellation,
    TLEConstellation,
)
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.session import SessionConfig


def _constellation_dims(
    constellation: ConstellationConfig,
) -> tuple[int, int]:
    """Extract (plane_count, sats_per_plane) from constellation config."""
    if isinstance(constellation, ParametricConstellation):
        return constellation.planes.count, constellation.planes.sats_per_plane
    if isinstance(constellation, ExplicitConstellation):
        planes: dict[int, list[int]] = {}
        for sat in constellation.satellites:
            planes.setdefault(sat.plane, []).append(sat.slot)
        return len(planes), max(len(slots) for slots in planes.values())
    raise NotImplementedError(f"Unsupported constellation type: {type(constellation)}")


def _isl_bandwidth(constellation: ConstellationConfig) -> float:
    """Extract default ISL bandwidth_mbps from constellation terminals."""
    if isinstance(constellation, (ParametricConstellation, ExplicitConstellation)):
        for term in constellation.default_terminals.isl:
            return term.bandwidth_mbps
    return 1000.0


def _ground_terminal_count(constellation: ConstellationConfig) -> int:
    """Extract total ground terminal count from constellation."""
    if isinstance(constellation, (ParametricConstellation, ExplicitConstellation)):
        return sum(t.count for t in constellation.default_terminals.ground)
    return 1


def _build_interface_info(
    node_neighbors: list[NeighborAssignment],
    area_assignments: dict[str, str],
    node_id: str,
    bandwidth_mbps: float,
    loopback_map: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build interface_info dict keyed by interface name for a satellite."""
    node_area = area_assignments.get(node_id, "")
    lb = loopback_map or {}
    interfaces: dict[str, dict[str, Any]] = {}
    for na in node_neighbors:
        peer_area = area_assignments.get(na.peer_node_id, "")
        info: dict[str, Any] = {
            "peer_node_id": na.peer_node_id,
            "link_type": na.link_type,
            "priority": na.priority,
            "peer_area_id": peer_area,
            "cross_area": node_area != peer_area and node_area != "" and peer_area != "",
            "bandwidth_mbps": bandwidth_mbps,
        }
        if na.peer_node_id in lb:
            info["peer_loopback_ipv4"] = lb[na.peer_node_id]
        interfaces[na.interface] = info
    return interfaces


def _host_address_from_prefix(prefix: str) -> str:
    """Derive a host address (.1 / ::1) from a CIDR network prefix.

    '172.16.0.0/24' -> '172.16.0.1/24'
    'fd10::0:0/112' -> 'fd10::0:1/112'
    """
    import ipaddress

    net = ipaddress.ip_network(prefix, strict=False)
    # First usable host = network_address + 1
    host = net.network_address + 1
    return f"{host}/{net.prefixlen}"


def _resolve_terrestrial_prefixes(
    station,
    gs_file: GroundStationFile,
    gs_index: int,
) -> list[dict[str, Any]]:
    """Resolve terrestrial prefixes for a ground station.

    Each prefix includes both the network prefix (for routing announcements)
    and the host_address (for interface configuration).
    """
    prefixes: list[tuple[str, int]] = []
    if station.terrestrial_prefixes:
        prefixes = [(tp.prefix, tp.metric) for tp in station.terrestrial_prefixes]
    else:
        tpl = gs_file.default_terrestrial_prefixes
        if tpl is None:
            return []
        ipv4 = tpl.ipv4_template.format(gs_index=gs_index)
        ipv6 = tpl.ipv6_template.format(gs_index=gs_index)
        prefixes = [(ipv4, tpl.metric), (ipv6, tpl.metric)]

    return [
        {
            "prefix": pfx,
            "host_address": _host_address_from_prefix(pfx),
            "metric": metric,
        }
        for pfx, metric in prefixes
    ]


def _build_loopback_map(
    constellation: ConstellationConfig,
    ground_stations: GroundStationFile,
    addressing: AddressingScheme,
) -> dict[str, str]:
    """Build node_id -> loopback_ipv4 mapping for all nodes.

    Used by static routing templates that need peer loopback IPs for
    explicit next-hop routes (no IGP to discover them).
    """
    lb: dict[str, str] = {}
    if isinstance(constellation, ParametricConstellation):
        for p in range(constellation.planes.count):
            for s in range(constellation.planes.sats_per_plane):
                nid = addressing.sat_id(p, s)
                lb[nid] = addressing.sat_ipv4(p, s)
    elif isinstance(constellation, ExplicitConstellation):
        for sat in constellation.satellites:
            nid = addressing.sat_id(sat.plane, sat.slot)
            lb[nid] = addressing.sat_ipv4(sat.plane, sat.slot)
    for idx, station in enumerate(ground_stations.stations):
        nid = addressing.gs_id(station.name)
        lb[nid] = addressing.gs_ipv4(idx)
    return lb


def build_template_vars(
    session: SessionConfig,
    constellation: ConstellationConfig,
    ground_stations: GroundStationFile,
    addressing: AddressingScheme,
    node_type: str,
    plane: int | None = None,
    slot: int | None = None,
    gs_name: str | None = None,
    gs_index: int | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build complete Jinja2 template variable namespace for a node.

    This is the single public API (Section 13.25). Computes area
    assignments, ISL neighbors, and all derived variables internally.

    Args:
        session: The session configuration
        constellation: The constellation configuration (discriminated union)
        ground_stations: The ground station file
        addressing: The AddressingScheme instance for this session
        node_type: "satellite" or "ground_station"
        plane: Orbital plane index (required for satellite nodes)
        slot: Slot index within plane (required for satellite nodes)
        gs_name: Ground station name (required for ground_station nodes)
        gs_index: Ground station index (required for ground_station nodes)
        config_overrides: Merged stack template_variables + session overrides

    Returns:
        Complete dict of template variables for Jinja2 rendering.
    """
    # Compute area assignments (empty if not configured)
    pc, spp = _constellation_dims(constellation)
    gs_names = [s.name for s in ground_stations.stations]
    area_assignments: dict[str, str] = {}
    if session.routing.area_assignment is not None:
        area_assignments = compute_area_assignments(
            session.routing.area_assignment,
            plane_count=pc,
            sats_per_plane=spp,
            addressing=addressing,
            gs_names=gs_names,
        )

    # Compute ISL neighbors
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    # Build loopback map for peer IP resolution (used by static routes)
    loopback_map = _build_loopback_map(
        constellation, ground_stations, addressing,
    )

    # Derive node_id
    if node_type == "satellite":
        assert plane is not None and slot is not None
        node_id = addressing.sat_id(plane, slot)
    elif node_type == "ground_station":
        assert gs_name is not None and gs_index is not None
        node_id = addressing.gs_id(gs_name)
    else:
        raise ValueError(f"Unknown node_type: {node_type}")

    # Start with config_overrides as base (node vars override them)
    result: dict[str, Any] = {}
    if config_overrides:
        result.update(config_overrides)

    # Core variables (always override config_overrides)
    result.update({
        "node_id": node_id,
        "hostname": node_id,
        "node_type": node_type,
        "area_id": area_assignments.get(node_id, ""),
        "mgmt_interface": "eth0",
        "compression_factor": session.time.compression,
    })

    bandwidth = _isl_bandwidth(constellation)
    gnd_count = _ground_terminal_count(constellation)

    if node_type == "satellite":
        node_neighbors = by_node.get(node_id, [])
        result["plane"] = plane
        result["slot"] = slot
        result["ipv4_loopback"] = addressing.sat_ipv4(plane, slot)
        result["ipv6_loopback"] = addressing.sat_ipv6(plane, slot)
        result["interface_info"] = _build_interface_info(
            node_neighbors, area_assignments, node_id, bandwidth,
            loopback_map=loopback_map,
        )
        result["isl_count"] = len(node_neighbors)
        result["isl_interfaces"] = addressing.isl_interfaces(len(node_neighbors))
        result["gnd_interfaces"] = addressing.gnd_interfaces(gnd_count)
        result["neighbors"] = {
            na.interface: na.peer_node_id for na in node_neighbors
        }

    elif node_type == "ground_station":
        result["gs_name"] = gs_name
        result["gs_index"] = gs_index
        result["ipv4_loopback"] = addressing.gs_ipv4(gs_index)
        result["ipv6_loopback"] = addressing.gs_ipv6(gs_index)
        result["gnd_interfaces"] = addressing.gnd_interfaces(gnd_count)
        result["isl_interfaces"] = []
        result["isl_count"] = 0
        result["interface_info"] = {}
        result["neighbors"] = {}

        station = next(
            (s for s in ground_stations.stations if s.name == gs_name), None,
        )
        if station:
            terrestrial_prefixes = _resolve_terrestrial_prefixes(
                station, ground_stations, gs_index,
            )
            result["terrestrial_prefixes"] = terrestrial_prefixes
            if terrestrial_prefixes:
                result["terr0_metric"] = max(
                    tp["metric"] for tp in terrestrial_prefixes
                )
        else:
            result["terrestrial_prefixes"] = []

    return result
