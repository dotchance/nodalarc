"""Addressing, area assignment, and ISL neighbor assignment.

Single source of truth for all node identity derivation, IP addressing,
routing area computation, and structural ISL neighbor assignment.
"""

from __future__ import annotations

from typing import NamedTuple

from nodalarc.models.constellation import (
    ConstellationConfig,
    ExplicitConstellation,
    ParametricConstellation,
)
from nodalarc.models.session import AreaAssignmentConfig, AddressingConfig


class AddressingScheme:
    """Derives all node identifiers and IPs from plane/slot/station indices.

    Constructed once per session from the AddressingConfig in the session YAML.
    """

    def __init__(self, config: AddressingConfig | None = None) -> None:
        cfg = config or AddressingConfig()
        self._sat_id_tpl = cfg.sat_id_template
        self._gs_id_tpl = cfg.gs_id_template
        self._ipv4_sat_tpl = cfg.ipv4_sat_template
        self._ipv4_gs_tpl = cfg.ipv4_gs_template
        self._ipv6_sat_tpl = cfg.ipv6_sat_template
        self._ipv6_gs_tpl = cfg.ipv6_gs_template

    # -- Node IDs --

    def sat_id(self, plane: int, slot: int) -> str:
        return self._sat_id_tpl.format(plane=plane, slot=slot)

    def gs_id(self, name: str) -> str:
        return self._gs_id_tpl.format(name=name)

    # -- IP addresses --

    def sat_ipv4(self, plane: int, slot: int) -> str:
        return self._ipv4_sat_tpl.format(plane=plane, slot=slot)

    def sat_ipv6(self, plane: int, slot: int) -> str:
        return self._ipv6_sat_tpl.format(plane=plane, slot=slot)

    def gs_ipv4(self, gs_index: int) -> str:
        return self._ipv4_gs_tpl.format(gs_index=gs_index)

    def gs_ipv6(self, gs_index: int) -> str:
        return self._ipv6_gs_tpl.format(gs_index=gs_index)

    # -- Interface names --

    @staticmethod
    def isl_interfaces(count: int) -> list[str]:
        return [f"isl{i}" for i in range(count)]

    @staticmethod
    def gnd_interfaces(count: int) -> list[str]:
        return [f"gnd{i}" for i in range(count)]


# ---------------------------------------------------------------------------
# Area assignment
# ---------------------------------------------------------------------------

class AreaAssignment(NamedTuple):
    """Area assignment for a single node."""
    node_id: str
    area_id: str


def compute_area_assignments(
    config: AreaAssignmentConfig,
    plane_count: int,
    sats_per_plane: int,
    addressing: AddressingScheme,
    gs_names: list[str] | None = None,
) -> dict[str, str]:
    """Compute area_id for every satellite and ground station.

    Returns a dict mapping node_id -> area_id.

    Strategies:
    - flat: all nodes share one area_id ("49.0001")
    - per-plane: each plane gets its own area_id
    - stripe: groups of `planes_per_stripe` planes share an area_id
    - explicit: user-provided mapping from plane indices to area_ids
    """
    result: dict[str, str] = {}
    strategy = config.strategy

    if strategy == "flat":
        area_id = "49.0001"
        for p in range(plane_count):
            for s in range(sats_per_plane):
                result[addressing.sat_id(p, s)] = area_id

    elif strategy == "per-plane":
        for p in range(plane_count):
            area_id = f"49.{p + 1:04d}"
            for s in range(sats_per_plane):
                result[addressing.sat_id(p, s)] = area_id

    elif strategy == "stripe":
        pps = config.planes_per_stripe
        assert pps is not None and pps > 0
        for p in range(plane_count):
            stripe_index = p // pps
            area_id = f"49.{stripe_index + 1:04d}"
            for s in range(sats_per_plane):
                result[addressing.sat_id(p, s)] = area_id

    elif strategy == "explicit":
        assert config.assignments is not None
        # Build plane -> area_id lookup
        plane_to_area: dict[int, str] = {}
        for mapping in config.assignments:
            if mapping.planes is not None:
                for p in mapping.planes:
                    plane_to_area[p] = mapping.area_id
        for p in range(plane_count):
            area_id = plane_to_area.get(p, "49.0001")  # default fallback
            for s in range(sats_per_plane):
                result[addressing.sat_id(p, s)] = area_id

    # Ground stations always get gs_area_id (or default "49.0000")
    gs_area = config.gs_area_id or "49.0000"
    if gs_names:
        for name in gs_names:
            result[addressing.gs_id(name)] = gs_area

    return result


# ---------------------------------------------------------------------------
# ISL neighbor assignment (structural — plane/slot modular arithmetic)
# ---------------------------------------------------------------------------

class NeighborAssignment(NamedTuple):
    """Immutable ISL neighbor assignment for one terminal."""
    interface: str       # "isl0", "isl1", etc.
    peer_node_id: str    # "sat-P03S08"
    link_type: str       # "intra" or "cross"
    priority: int        # 0=intra-fwd, 1=intra-aft, 2=cross-right, 3=cross-left


def _get_constellation_params(
    constellation: ConstellationConfig,
) -> tuple[int, int, int, bool]:
    """Extract (plane_count, sats_per_plane, isl_terminal_count, wraps_cross_plane).

    wraps_cross_plane is True for walker-star (RAAN spread >= 360°) and
    False for walker-delta (RAAN spread < 360°).
    """
    if isinstance(constellation, ParametricConstellation):
        plane_count = constellation.planes.count
        sats_per_plane = constellation.planes.sats_per_plane
        isl_count = sum(t.count for t in constellation.default_terminals.isl)
        raan_spread = constellation.planes.raan_spacing_deg * plane_count
        wraps = raan_spread >= 360.0
        return plane_count, sats_per_plane, isl_count, wraps

    if isinstance(constellation, ExplicitConstellation):
        # Derive plane/slot counts from satellites
        planes: dict[int, list[int]] = {}
        for sat in constellation.satellites:
            planes.setdefault(sat.plane, []).append(sat.slot)
        plane_count = len(planes)
        sats_per_plane = max(len(slots) for slots in planes.values())
        isl_count = sum(t.count for t in constellation.default_terminals.isl)
        # Explicit mode: no auto cross-plane wrap
        return plane_count, sats_per_plane, isl_count, False

    raise NotImplementedError(f"ISL neighbor assignment not supported for {type(constellation)}")


def assign_isl_neighbors(
    constellation: ConstellationConfig,
    addressing: AddressingScheme,
) -> frozenset[tuple[str, NeighborAssignment]]:
    """Compute ISL neighbor assignments for all satellites.

    Returns a frozenset of (node_id, NeighborAssignment) tuples.
    Frozen — computed once at session startup, immutable.

    Section 13.4 priority: intra-fwd(0) > intra-aft(1) > cross-right(2) > cross-left(3).
    If terminal_count < 4, lower-priority links are not assigned.

    Cross-plane wrap behavior depends on constellation pattern:
    - Walker-star (RAAN spread >= 360°): last plane wraps to first plane
    - Walker-delta (RAAN spread < 360°): no wrap between first/last planes
    """
    plane_count, sats_per_plane, isl_count, wraps = _get_constellation_params(constellation)

    # Build override lookup: node_id -> {terminal_name: peer}
    overrides: dict[str, dict[str, str]] = {}
    isl_overrides = None
    if isinstance(constellation, (ParametricConstellation, ExplicitConstellation)):
        isl_overrides = constellation.isl_overrides
    if isl_overrides:
        for ovr in isl_overrides:
            ovr_map: dict[str, str] = {}
            for link in ovr.links:
                ovr_map[link.terminal] = link.peer
            overrides[ovr.node] = ovr_map

    assignments: list[tuple[str, NeighborAssignment]] = []

    for p in range(plane_count):
        for s in range(sats_per_plane):
            node_id = addressing.sat_id(p, s)

            # Check for override
            if node_id in overrides:
                ovr_map = overrides[node_id]
                for idx, (terminal, peer) in enumerate(ovr_map.items()):
                    # Infer link_type from peer's plane
                    link_type = "override"
                    assignments.append((node_id, NeighborAssignment(
                        interface=terminal,
                        peer_node_id=peer,
                        link_type=link_type,
                        priority=idx,
                    )))
                continue

            # Standard structural assignment
            candidates: list[NeighborAssignment] = []

            # Priority 0: intra-plane forward (next slot in same plane)
            fwd_slot = (s + 1) % sats_per_plane
            candidates.append(NeighborAssignment(
                interface="",  # filled below
                peer_node_id=addressing.sat_id(p, fwd_slot),
                link_type="intra",
                priority=0,
            ))

            # Priority 1: intra-plane aft (previous slot in same plane)
            aft_slot = (s - 1) % sats_per_plane
            candidates.append(NeighborAssignment(
                interface="",
                peer_node_id=addressing.sat_id(p, aft_slot),
                link_type="intra",
                priority=1,
            ))

            # Priority 2: cross-plane right (same slot, next plane)
            right_plane = p + 1
            if right_plane < plane_count:
                candidates.append(NeighborAssignment(
                    interface="",
                    peer_node_id=addressing.sat_id(right_plane, s),
                    link_type="cross",
                    priority=2,
                ))
            elif wraps:
                # Walker-star: wrap to plane 0
                candidates.append(NeighborAssignment(
                    interface="",
                    peer_node_id=addressing.sat_id(0, s),
                    link_type="cross",
                    priority=2,
                ))

            # Priority 3: cross-plane left (same slot, previous plane)
            left_plane = p - 1
            if left_plane >= 0:
                candidates.append(NeighborAssignment(
                    interface="",
                    peer_node_id=addressing.sat_id(left_plane, s),
                    link_type="cross",
                    priority=3,
                ))
            elif wraps:
                # Walker-star: wrap to last plane
                candidates.append(NeighborAssignment(
                    interface="",
                    peer_node_id=addressing.sat_id(plane_count - 1, s),
                    link_type="cross",
                    priority=3,
                ))

            # Trim to available terminal count
            assigned = candidates[:isl_count]

            # Assign interface names
            for idx, na in enumerate(assigned):
                assignments.append((node_id, NeighborAssignment(
                    interface=f"isl{idx}",
                    peer_node_id=na.peer_node_id,
                    link_type=na.link_type,
                    priority=na.priority,
                )))

    return frozenset(assignments)


def neighbors_by_node(
    assignments: frozenset[tuple[str, NeighborAssignment]],
) -> dict[str, list[NeighborAssignment]]:
    """Convert frozenset assignments to a dict keyed by node_id.

    Convenience function for consumers that need per-node lookups.
    """
    result: dict[str, list[NeighborAssignment]] = {}
    for node_id, na in assignments:
        result.setdefault(node_id, []).append(na)
    # Sort each node's assignments by priority
    for node_id in result:
        result[node_id].sort(key=lambda x: x.priority)
    return result
