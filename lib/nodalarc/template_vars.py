# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Template variable builder — single public API per Section 13.25.

Thin orchestrator that delegates to AddressingScheme and addressing
helpers. Produces the complete Jinja2 template variable namespace
for any satellite or ground station node.
"""

from __future__ import annotations

from typing import Any

from nodalarc.constellation_loader import isl_link_bandwidth_mbps
from nodalarc.ground_terminals import station_ground_terminal_capacity
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


def _ground_terminal_count(constellation: ConstellationConfig) -> int:
    """Extract total ground terminal count from constellation."""
    if isinstance(constellation, ParametricConstellation | ExplicitConstellation):
        return sum(t.count for t in constellation.default_terminals.ground)
    return 1


def _build_interface_info(
    node_neighbors: list[NeighborAssignment],
    area_assignments: dict[str, str],
    node_id: str,
    bandwidth_by_interface: dict[tuple[str, str], float],
    loopback_map: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build interface_info dict keyed by interface name for a satellite."""
    node_area = area_assignments.get(node_id, "")
    lb = loopback_map or {}
    interfaces: dict[str, dict[str, Any]] = {}
    for na in node_neighbors:
        peer_area = area_assignments.get(na.peer_node_id, "")
        static_only = na.link_type.startswith("static_ip:")
        bandwidth_mbps = na.bandwidth_mbps
        if bandwidth_mbps is None:
            key = (na.interface, na.peer_node_id)
            try:
                bandwidth_mbps = bandwidth_by_interface[key]
            except KeyError as exc:
                raise ValueError(
                    f"missing resolved ISL bandwidth for {node_id}:{na.interface} "
                    f"to {na.peer_node_id}"
                ) from exc
        info: dict[str, Any] = {
            "peer_node_id": na.peer_node_id,
            "link_type": na.link_type,
            "priority": na.priority,
            "peer_area_id": peer_area,
            "cross_area": node_area != peer_area and node_area != "" and peer_area != "",
            "bandwidth_mbps": float(bandwidth_mbps),
            "static_only": static_only,
        }
        if na.peer_node_id in lb:
            info["peer_loopback_ipv4"] = lb[na.peer_node_id]
        interfaces[na.interface] = info
    return interfaces


def _satellite_plane_slot_map(
    constellation: ConstellationConfig,
    addressing: AddressingScheme,
) -> dict[str, tuple[int, int]]:
    """Return runtime node_id -> runtime plane/slot for template projection."""
    positions: dict[str, tuple[int, int]] = {}
    if isinstance(constellation, ParametricConstellation):
        for plane in range(constellation.planes.count):
            for slot in range(constellation.planes.sats_per_plane):
                positions[addressing.sat_id(plane, slot)] = (plane, slot)
        return positions
    if isinstance(constellation, ExplicitConstellation):
        for sat in constellation.satellites:
            positions[addressing.sat_id(sat.plane, sat.slot)] = (sat.plane, sat.slot)
        return positions
    raise ValueError(f"ISL bandwidth projection is not supported for {type(constellation)}")


def _peer_interface(
    assignments: frozenset[tuple[str, NeighborAssignment]],
    node_id: str,
    assignment: NeighborAssignment,
) -> str:
    matches = [
        peer_assignment.interface
        for peer_node_id, peer_assignment in assignments
        if peer_node_id == assignment.peer_node_id and peer_assignment.peer_node_id == node_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"ISL assignment {node_id}:{assignment.interface}->{assignment.peer_node_id} "
            f"has {len(matches)} reciprocal peer interface(s); expected exactly one"
        )
    return matches[0]


def _resolve_isl_bandwidths(
    constellation: ConstellationConfig,
    addressing: AddressingScheme,
    assignments: frozenset[tuple[str, NeighborAssignment]],
) -> dict[str, dict[tuple[str, str], float]]:
    """Resolve per-node ISL interface bandwidths from terminal inventory.

    The bottleneck bandwidth is a property of the two concrete terminal slots on
    the link. This function is deliberately fail-loud: if the neighbor graph is
    not reciprocal or an interface cannot be mapped back to a terminal block,
    the session cannot render trustworthy FRR metrics.
    """
    positions = _satellite_plane_slot_map(constellation, addressing)
    resolved: dict[str, dict[tuple[str, str], float]] = {}
    for node_id, assignment in assignments:
        if assignment.bandwidth_mbps is not None:
            resolved.setdefault(node_id, {})[(assignment.interface, assignment.peer_node_id)] = (
                float(assignment.bandwidth_mbps)
            )
            continue
        if node_id not in positions or assignment.peer_node_id not in positions:
            if assignment.link_type.startswith("static_ip:"):
                # Static inter-body links still use satellite ISL terminals, so
                # missing positions mean the runtime projection is malformed.
                pass
            raise ValueError(
                f"cannot resolve ISL bandwidth for {node_id}->{assignment.peer_node_id}; "
                "both endpoints must be satellites in the runtime constellation"
            )
        node_plane, node_slot = positions[node_id]
        peer_plane, peer_slot = positions[assignment.peer_node_id]
        peer_iface = _peer_interface(assignments, node_id, assignment)
        bw = isl_link_bandwidth_mbps(
            constellation,
            node_plane,
            node_slot,
            assignment.interface,
            peer_plane,
            peer_slot,
            peer_iface,
        )
        resolved.setdefault(node_id, {})[(assignment.interface, assignment.peer_node_id)] = bw
    return resolved


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
) -> tuple[list[dict[str, Any]], bool, int]:
    """Resolve terrestrial prefixes for a ground station.

    Each prefix includes both the network prefix (for routing announcements)
    and the host_address (for interface configuration).

    Default route prefixes (0.0.0.0/0, ::/0) are NOT included in the prefix
    list — they are not interface addresses. Instead, they set the
    default_route flag and metric for IGP default-information originate.

    Returns:
        (prefix_list, has_default_route, default_route_metric)
    """
    import ipaddress

    raw_prefixes: list[tuple[str, int]] = []
    if station.terrestrial_prefixes:
        raw_prefixes = [(tp.prefix, tp.metric) for tp in station.terrestrial_prefixes]
    else:
        tpl = gs_file.default_terrestrial_prefixes
        if tpl is None:
            return [], False, 0
        ipv4 = tpl.ipv4_template.format(gs_index=gs_index)
        ipv6 = tpl.ipv6_template.format(gs_index=gs_index)
        raw_prefixes = [(ipv4, tpl.metric), (ipv6, tpl.metric)]
        # Template-level default route (applies to all GS using this template)
        if tpl.default_route:
            raw_prefixes.append(("0.0.0.0/0", tpl.default_route_metric))

    result = []
    has_default_route = False
    default_route_metric = 100
    for pfx, metric in raw_prefixes:
        net = ipaddress.ip_network(pfx, strict=False)
        if net.prefixlen == 0:
            has_default_route = True
            default_route_metric = metric
            continue
        result.append(
            {
                "prefix": pfx,
                "host_address": _host_address_from_prefix(pfx),
                "metric": metric,
            }
        )
    return result, has_default_route, default_route_metric


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
    sat_node_id: str | None = None,
    sat_ground_terminal_count: int | None = None,
    gs_name: str | None = None,
    gs_index: int | None = None,
    config_overrides: dict[str, Any] | None = None,
    neighbors: frozenset[tuple[str, NeighborAssignment]] | None = None,
    node_sid_index: int | None = None,
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
            protocol=session.routing.protocol or "isis",
        )

    # Compute ISL neighbors unless the resolver provided the declared graph.
    neighbor_assignments = (
        neighbors if neighbors is not None else assign_isl_neighbors(constellation, addressing)
    )
    by_node = neighbors_by_node(neighbor_assignments)
    isl_bandwidths = _resolve_isl_bandwidths(
        constellation,
        addressing,
        neighbor_assignments,
    )

    # Build loopback map for peer IP resolution (used by static routes)
    loopback_map = _build_loopback_map(
        constellation,
        ground_stations,
        addressing,
    )

    # Derive node_id
    if node_type == "satellite":
        assert plane is not None and slot is not None
        node_id = sat_node_id or addressing.sat_id(plane, slot)
    elif node_type == "ground_station":
        assert gs_name is not None and gs_index is not None
        node_id = addressing.gs_id(gs_name)
    else:
        raise ValueError(f"Unknown node_type: {node_type}")

    # Start with config_overrides as base (node vars override them)
    result: dict[str, Any] = {}
    if config_overrides:
        result.update(config_overrides)

    # Routing config — first-class fields from RoutingConfig
    routing = session.routing
    if routing:
        result.update(
            {
                "bfd_enabled": routing.bfd,
                "bfd_detect_multiplier": routing.bfd_detect_multiplier,
                "bfd_rx_interval": routing.bfd_rx_interval,
                "bfd_tx_interval": routing.bfd_tx_interval,
                "isis_hello_interval": routing.isis_hello_interval,
                "isis_hello_multiplier": routing.isis_hello_multiplier,
                "spf_init_delay": routing.spf_init_delay,
                "spf_short_delay": routing.spf_short_delay,
                "spf_long_delay": routing.spf_long_delay,
                "spf_holddown": routing.spf_holddown,
                "spf_time_to_learn": routing.spf_time_to_learn,
                "ospf_hello_interval": routing.ospf_hello_interval,
                "ospf_dead_interval": routing.ospf_dead_interval,
                "ospf_spf_delay": routing.ospf_spf_delay,
                "ospf_spf_initial_hold": routing.ospf_spf_initial_hold,
                "ospf_spf_max_hold": routing.ospf_spf_max_hold,
            }
        )
    if result.get("sr_enabled"):
        if node_sid_index is None:
            raise ValueError(
                f"segment routing is enabled but no resolved SID index was provided for {node_id}"
            )
        result["node_sid_index"] = node_sid_index

    # Core variables (always override config_overrides)
    result.update(
        {
            "node_id": node_id,
            "hostname": node_id,
            "node_type": node_type,
            "area_id": area_assignments.get(node_id, ""),
            "mgmt_interface": "eth0",
            "compression_factor": session.time.compression,
        }
    )

    gnd_count = (
        sat_ground_terminal_count
        if sat_ground_terminal_count is not None
        else _ground_terminal_count(constellation)
    )

    if node_type == "satellite":
        node_neighbors = by_node.get(node_id, [])
        result["plane"] = plane
        result["slot"] = slot
        result["ipv4_loopback"] = addressing.sat_ipv4(plane, slot)
        result["ipv6_loopback"] = addressing.sat_ipv6(plane, slot)
        result["interface_info"] = _build_interface_info(
            node_neighbors,
            area_assignments,
            node_id,
            isl_bandwidths.get(node_id, {}),
            loopback_map=loopback_map,
        )
        result["isl_count"] = len(node_neighbors)
        result["isl_interfaces"] = addressing.isl_interfaces(len(node_neighbors))
        result["gnd_interfaces"] = addressing.gnd_interfaces(gnd_count)
        result["neighbors"] = {na.interface: na.peer_node_id for na in node_neighbors}

    elif node_type == "ground_station":
        result["gs_name"] = gs_name
        result["gs_index"] = gs_index
        result["ipv4_loopback"] = addressing.gs_ipv4(gs_index)
        result["ipv6_loopback"] = addressing.gs_ipv6(gs_index)
        gs_station = next((s for s in ground_stations.stations if s.name == gs_name), None)
        if gs_station is None:
            raise ValueError(f"Ground station {gs_name!r} not found in ground station file")
        gs_terminal_count = station_ground_terminal_capacity(ground_stations, gs_station)
        result["gnd_interfaces"] = addressing.term_interfaces(gs_terminal_count)
        result["isl_interfaces"] = []
        result["isl_count"] = 0
        result["interface_info"] = {}
        result["neighbors"] = {}

        station = next(
            (s for s in ground_stations.stations if s.name == gs_name),
            None,
        )
        if station:
            terrestrial_prefixes, has_default, default_metric = _resolve_terrestrial_prefixes(
                station,
                ground_stations,
                gs_index,
            )
            result["terrestrial_prefixes"] = terrestrial_prefixes
            if terrestrial_prefixes:
                result["terr0_metric"] = max(tp["metric"] for tp in terrestrial_prefixes)
            result["terr0_default_route"] = has_default
            result["terr0_default_metric"] = default_metric
        else:
            result["terrestrial_prefixes"] = []
            result["terr0_default_route"] = False
            result["terr0_default_metric"] = 0

    return result
