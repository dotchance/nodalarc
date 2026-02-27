"""Template variable builder — single public API per Section 13.25.

Thin orchestrator (~80 lines) that delegates to AddressingScheme
and addressing helpers. Produces the complete Jinja2 template
variable namespace for any satellite or ground station node.
"""

from __future__ import annotations

from typing import Any

from nodalarc.models.addressing import (
    AddressingScheme,
    NeighborAssignment,
    neighbors_by_node,
)
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    TerrestrialPrefixTemplate,
)


def _build_interface_info(
    node_neighbors: list[NeighborAssignment],
    area_assignments: dict[str, str],
    node_id: str,
) -> list[dict[str, Any]]:
    """Build interface_info list for a satellite's ISL terminals."""
    node_area = area_assignments.get(node_id, "")
    interfaces = []
    for na in node_neighbors:
        peer_area = area_assignments.get(na.peer_node_id, "")
        interfaces.append({
            "interface": na.interface,
            "peer_node_id": na.peer_node_id,
            "link_type": na.link_type,
            "priority": na.priority,
            "peer_area_id": peer_area,
            "cross_area": node_area != peer_area and node_area != "" and peer_area != "",
        })
    return interfaces


def _resolve_terrestrial_prefixes(
    station: GroundStationConfig,
    gs_file: GroundStationFile,
    gs_index: int,
) -> list[dict[str, Any]]:
    """Resolve terrestrial prefixes for a ground station.

    Uses per-station override if present, otherwise expands the
    default template from the GroundStationFile.
    """
    if station.terrestrial_prefixes:
        return [
            {"prefix": tp.prefix, "metric": tp.metric}
            for tp in station.terrestrial_prefixes
        ]

    tpl = gs_file.default_terrestrial_prefixes
    if tpl is None:
        return []

    prefixes = []
    ipv4 = tpl.ipv4_template.format(gs_index=gs_index)
    ipv6 = tpl.ipv6_template.format(gs_index=gs_index)
    prefixes.append({"prefix": ipv4, "metric": tpl.metric})
    prefixes.append({"prefix": ipv6, "metric": tpl.metric})
    return prefixes


def build_template_vars(
    node_id: str,
    node_type: str,
    addressing: AddressingScheme,
    area_assignments: dict[str, str],
    neighbors: frozenset[tuple[str, NeighborAssignment]],
    gs_file: GroundStationFile | None = None,
    gs_index: int | None = None,
    plane: int | None = None,
    slot: int | None = None,
    gs_name: str | None = None,
    stack_vars: dict[str, Any] | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build complete Jinja2 template variable namespace for a node.

    This is the single public API (Section 13.25). Delegates all heavy
    lifting to existing functions.

    Args:
        node_id: The node identifier (e.g. "sat-P03S07" or "gs-hawthorne")
        node_type: "satellite" or "ground_station"
        addressing: The AddressingScheme instance for this session
        area_assignments: Pre-computed dict from compute_area_assignments()
        neighbors: Pre-computed frozen ISL neighbor assignments
        gs_file: GroundStationFile (required for ground_station nodes)
        gs_index: Ground station index (required for ground_station nodes)
        plane: Orbital plane index (required for satellite nodes)
        slot: Slot index within plane (required for satellite nodes)
        gs_name: Ground station name (required for ground_station nodes)
        stack_vars: Variables from the routing stack config
        config_overrides: Session-level config overrides

    Returns:
        Complete dict of template variables for Jinja2 rendering.
    """
    vars: dict[str, Any] = {
        "node_id": node_id,
        "node_type": node_type,
        "area_id": area_assignments.get(node_id, ""),
    }

    if node_type == "satellite":
        assert plane is not None and slot is not None
        vars["plane"] = plane
        vars["slot"] = slot
        vars["loopback_ipv4"] = addressing.sat_ipv4(plane, slot)
        vars["loopback_ipv6"] = addressing.sat_ipv6(plane, slot)

        # ISL interface info from pre-computed neighbors
        by_node = neighbors_by_node(neighbors)
        node_neighbors = by_node.get(node_id, [])
        vars["interface_info"] = _build_interface_info(
            node_neighbors, area_assignments, node_id,
        )
        vars["isl_count"] = len(node_neighbors)

    elif node_type == "ground_station":
        assert gs_name is not None and gs_index is not None
        assert gs_file is not None
        vars["gs_name"] = gs_name
        vars["gs_index"] = gs_index
        vars["loopback_ipv4"] = addressing.gs_ipv4(gs_index)
        vars["loopback_ipv6"] = addressing.gs_ipv6(gs_index)

        # Terrestrial prefix resolution
        station = next(
            (s for s in gs_file.stations if s.name == gs_name), None
        )
        if station:
            vars["terrestrial_prefixes"] = _resolve_terrestrial_prefixes(
                station, gs_file, gs_index,
            )
        else:
            vars["terrestrial_prefixes"] = []

    # Merge stack variables and config overrides
    if stack_vars:
        vars = {**stack_vars, **vars}
    if config_overrides:
        vars = {**vars, **config_overrides}

    return vars
