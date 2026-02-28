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
) -> dict[str, dict[str, Any]]:
    """Build interface_info dict keyed by interface name for a satellite."""
    node_area = area_assignments.get(node_id, "")
    interfaces: dict[str, dict[str, Any]] = {}
    for na in node_neighbors:
        peer_area = area_assignments.get(na.peer_node_id, "")
        interfaces[na.interface] = {
            "peer_node_id": na.peer_node_id,
            "link_type": na.link_type,
            "priority": na.priority,
            "peer_area_id": peer_area,
            "cross_area": node_area != peer_area and node_area != "" and peer_area != "",
            "bandwidth_mbps": bandwidth_mbps,
        }
    return interfaces


def _resolve_terrestrial_prefixes(
    station,
    gs_file: GroundStationFile,
    gs_index: int,
) -> list[dict[str, Any]]:
    """Resolve terrestrial prefixes for a ground station."""
    if station.terrestrial_prefixes:
        return [
            {"prefix": tp.prefix, "metric": tp.metric}
            for tp in station.terrestrial_prefixes
        ]
    tpl = gs_file.default_terrestrial_prefixes
    if tpl is None:
        return []
    ipv4 = tpl.ipv4_template.format(gs_index=gs_index)
    ipv6 = tpl.ipv6_template.format(gs_index=gs_index)
    return [
        {"prefix": ipv4, "metric": tpl.metric},
        {"prefix": ipv6, "metric": tpl.metric},
    ]


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
    # Compute area assignments
    pc, spp = _constellation_dims(constellation)
    gs_names = [s.name for s in ground_stations.stations]
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
        result["loopback_ipv4"] = addressing.sat_ipv4(plane, slot)
        result["loopback_ipv6"] = addressing.sat_ipv6(plane, slot)
        result["interface_info"] = _build_interface_info(
            node_neighbors, area_assignments, node_id, bandwidth,
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
        result["loopback_ipv4"] = addressing.gs_ipv4(gs_index)
        result["loopback_ipv6"] = addressing.gs_ipv6(gs_index)
        result["gnd_interfaces"] = addressing.gnd_interfaces(gnd_count)
        result["isl_interfaces"] = []
        result["isl_count"] = 0
        result["interface_info"] = {}
        result["neighbors"] = {}

        station = next(
            (s for s in ground_stations.stations if s.name == gs_name), None,
        )
        if station:
            result["terrestrial_prefixes"] = _resolve_terrestrial_prefixes(
                station, ground_stations, gs_index,
            )
        else:
            result["terrestrial_prefixes"] = []

    return result
