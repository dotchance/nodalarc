# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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
from nodalarc.models.session import AddressingConfig, AreaAssignmentConfig


class AddressingScheme:
    """Derives all node identifiers and IPs from plane/slot/station indices.

    Constructed once per session from the AddressingConfig in the session YAML.
    Optionally initialized with satellite and GS lists to populate the
    node type registry — required for any caller that needs node_type(),
    is_ground_segment(), or is_satellite().
    """

    def __init__(
        self,
        config: AddressingConfig | None = None,
        satellites: list | None = None,
        gs_file: object | None = None,
    ) -> None:
        cfg = config or AddressingConfig()
        self._sat_id_tpl = cfg.sat_id_template
        self._gs_id_tpl = cfg.gs_id_template
        self._ipv4_sat_tpl = cfg.ipv4_sat_template
        self._ipv4_gs_tpl = cfg.ipv4_gs_template
        self._ipv6_sat_tpl = cfg.ipv6_sat_template
        self._ipv6_gs_tpl = cfg.ipv6_gs_template
        self._node_types: dict[str, str] = {}
        self._sat_node_ids_by_location: dict[tuple[int, int], str] = {}
        self._gs_node_ids_by_source_name: dict[str, str] = {}
        self._ambiguous_gs_source_names: set[str] = set()

        if satellites:
            for sat in satellites:
                nid = getattr(sat, "node_id", None) or self._sat_id_tpl.format(
                    plane=sat.plane,
                    slot=sat.slot,
                )
                self._sat_node_ids_by_location[(sat.plane, sat.slot)] = nid
                self._node_types[nid] = "satellite"
        if gs_file and hasattr(gs_file, "stations"):
            for station in gs_file.stations:
                nid = self.gs_id(station.name)
                self._node_types[nid] = "ground_station"
                source_name = getattr(station, "source_name", None)
                if source_name:
                    key = str(source_name)
                    existing = self._gs_node_ids_by_source_name.get(key)
                    if existing is not None and existing != nid:
                        self._ambiguous_gs_source_names.add(key)
                        self._gs_node_ids_by_source_name.pop(key, None)
                    elif key not in self._ambiguous_gs_source_names:
                        self._gs_node_ids_by_source_name[key] = nid

    def node_type(self, node_id: str) -> str:
        if node_id not in self._node_types:
            raise KeyError(
                f"node_id {node_id!r} not in type registry. "
                "AddressingScheme must be initialized with "
                "satellites and gs_file to use type queries."
            )
        return self._node_types[node_id]

    def is_ground_segment(self, node_id: str) -> bool:
        return self.node_type(node_id) in ("ground_station", "user_terminal")

    def is_satellite(self, node_id: str) -> bool:
        return self.node_type(node_id) == "satellite"

    @property
    def has_type_registry(self) -> bool:
        return len(self._node_types) > 0

    # -- Node IDs --

    def sat_id(self, plane: int, slot: int) -> str:
        if (plane, slot) in self._sat_node_ids_by_location:
            return self._sat_node_ids_by_location[(plane, slot)]
        return self._sat_id_tpl.format(plane=plane, slot=slot)

    def gs_id(self, name: str) -> str:
        if name in self._ambiguous_gs_source_names:
            raise KeyError(
                f"ground station source name {name!r} is ambiguous across segments; "
                "use the resolved runtime node_id instead"
            )
        if name in self._gs_node_ids_by_source_name:
            return self._gs_node_ids_by_source_name[name]
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
    def term_interfaces(count: int) -> list[str]:
        return [f"term{i}" for i in range(count)]

    @staticmethod
    def gnd_interfaces(count: int) -> list[str]:
        return [f"gnd{i}" for i in range(count)]

    def ground_link_interfaces(
        self,
        pair: tuple[str, str],
        gs_terminal_index: int = 0,
        sat_terminal_index: int = 0,
    ) -> tuple[str, str]:
        """Return (iface_for_pair[0], iface_for_pair[1]) for a ground link.

        Uses the type registry to determine which node is the ground
        segment (gets termN) and which is the satellite (gets gndN).
        """
        if self.is_ground_segment(pair[0]):
            return (f"term{gs_terminal_index}", f"gnd{sat_terminal_index}")
        return (f"gnd{sat_terminal_index}", f"term{gs_terminal_index}")


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
    protocol: str = "isis",
) -> dict[str, str]:
    """Compute area_id for every satellite and ground station.

    Returns a dict mapping node_id -> area_id.

    IS-IS areas use NET prefix format: 49.0001, 49.0002, etc.
    OSPF areas use dotted IP format: 0.0.0.0 (backbone), 0.0.0.1, etc.
    OSPF areas must be contiguous and all non-backbone areas must
    touch area 0.0.0.0 via ABRs.

    Strategies:
    - flat: all nodes share one area
    - per-plane: each plane gets its own area
    - stripe: groups of `planes_per_stripe` planes share an area
    - explicit: user-provided mapping from plane indices to area_ids
    """
    result: dict[str, str] = {}
    strategy = config.strategy
    is_ospf = protocol == "ospf"

    # Explicit per-ground-station area overrides (applied below). "all" maps every
    # station; a name list (or bare name) maps those stations. These are honored,
    # never silently dropped.
    gs_name_to_area: dict[str, str] = {}
    explicit_all_gs_area: str | None = None

    if strategy == "flat":
        default = "0.0.0.0" if is_ospf else "49.0001"
        area_id = config.gs_area_id or default
        for p in range(plane_count):
            for s in range(sats_per_plane):
                result[addressing.sat_id(p, s)] = area_id

    elif strategy == "per-plane":
        for p in range(plane_count):
            area_id = f"0.0.0.{p + 1}" if is_ospf else f"49.{p + 1:04d}"
            for s in range(sats_per_plane):
                result[addressing.sat_id(p, s)] = area_id

    elif strategy == "stripe":
        pps = config.planes_per_stripe
        assert pps is not None and pps > 0
        for p in range(plane_count):
            stripe_index = p // pps
            area_id = f"0.0.0.{stripe_index + 1}" if is_ospf else f"49.{stripe_index + 1:04d}"
            for s in range(sats_per_plane):
                result[addressing.sat_id(p, s)] = area_id

    elif strategy == "explicit":
        assert config.assignments is not None
        plane_to_area: dict[int, str] = {}
        for mapping in config.assignments:
            if mapping.planes is not None:
                for p in mapping.planes:
                    plane_to_area[p] = mapping.area_id
            if mapping.ground_stations is not None:
                if mapping.ground_stations == "all":
                    explicit_all_gs_area = mapping.area_id
                else:
                    for name in mapping.ground_stations:
                        gs_name_to_area[name] = mapping.area_id
        # A mapping that targets a non-existent plane or station does nothing —
        # fail loud rather than silently drop it.
        out_of_range = sorted(p for p in plane_to_area if not 0 <= p < plane_count)
        if out_of_range:
            raise ValueError(
                f"explicit area assignment maps plane(s) outside [0, {plane_count}): {out_of_range}"
            )
        explicit_gs_intent = explicit_all_gs_area is not None or bool(gs_name_to_area)
        if explicit_gs_intent and gs_names is None:
            raise ValueError(
                "explicit ground-station area assignment requires the known gs_names universe"
            )
        if gs_names is not None:
            known = set(gs_names)
            unknown = sorted(n for n in gs_name_to_area if n not in known)
            if unknown:
                raise ValueError(
                    f"explicit area assignment maps unknown ground station(s): {unknown}"
                )
        fallback = "0.0.0.0" if is_ospf else "49.0001"
        for p in range(plane_count):
            area_id = plane_to_area.get(p, fallback)
            for s in range(sats_per_plane):
                result[addressing.sat_id(p, s)] = area_id

    else:
        raise ValueError(f"unknown area-assignment strategy: {strategy!r}")

    # Ground stations: explicit per-GS/all overrides win; else gs_area_id; else
    # the protocol default (OSPF backbone / IS-IS 49.0000).
    gs_default = (config.gs_area_id or "0.0.0.0") if is_ospf else (config.gs_area_id or "49.0000")
    if gs_names:
        for name in gs_names:
            area = gs_name_to_area.get(name, explicit_all_gs_area or gs_default)
            result[addressing.gs_id(name)] = area

    return result


# ---------------------------------------------------------------------------
# ISL neighbor assignment (structural — plane/slot modular arithmetic)
# ---------------------------------------------------------------------------


class NeighborAssignment(NamedTuple):
    """Immutable ISL neighbor assignment for one terminal."""

    interface: str  # "isl0", "isl1", etc.
    peer_node_id: str  # "sat-P03S08"
    link_type: str  # "intra_plane_isl", "cross_plane_isl", "ground_uplink", "ground_downlink"
    priority: int  # 0=intra-fwd, 1=intra-aft, 2=cross-right, 3=cross-left
    bandwidth_mbps: float | None = None  # Per-interface bottleneck bandwidth when pre-resolved.


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
    if isinstance(constellation, ParametricConstellation | ExplicitConstellation):
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
                    assignments.append(
                        (
                            node_id,
                            NeighborAssignment(
                                interface=terminal,
                                peer_node_id=peer,
                                link_type=link_type,
                                priority=idx,
                            ),
                        )
                    )
                continue

            # Standard structural assignment
            candidates: list[NeighborAssignment] = []

            # Priority 0: intra-plane forward (next slot in same plane)
            fwd_slot = (s + 1) % sats_per_plane
            candidates.append(
                NeighborAssignment(
                    interface="",  # filled below
                    peer_node_id=addressing.sat_id(p, fwd_slot),
                    link_type="intra_plane_isl",
                    priority=0,
                )
            )

            # Priority 1: intra-plane aft (previous slot in same plane)
            aft_slot = (s - 1) % sats_per_plane
            candidates.append(
                NeighborAssignment(
                    interface="",
                    peer_node_id=addressing.sat_id(p, aft_slot),
                    link_type="intra_plane_isl",
                    priority=1,
                )
            )

            # Priority 2: cross-plane right (same slot, next plane)
            right_plane = p + 1
            if right_plane < plane_count:
                candidates.append(
                    NeighborAssignment(
                        interface="",
                        peer_node_id=addressing.sat_id(right_plane, s),
                        link_type="cross_plane_isl",
                        priority=2,
                    )
                )
            elif wraps:
                # Walker-star: wrap to plane 0
                candidates.append(
                    NeighborAssignment(
                        interface="",
                        peer_node_id=addressing.sat_id(0, s),
                        link_type="cross_plane_isl",
                        priority=2,
                    )
                )

            # Priority 3: cross-plane left (same slot, previous plane)
            left_plane = p - 1
            if left_plane >= 0:
                candidates.append(
                    NeighborAssignment(
                        interface="",
                        peer_node_id=addressing.sat_id(left_plane, s),
                        link_type="cross_plane_isl",
                        priority=3,
                    )
                )
            elif wraps:
                # Walker-star: wrap to last plane
                candidates.append(
                    NeighborAssignment(
                        interface="",
                        peer_node_id=addressing.sat_id(plane_count - 1, s),
                        link_type="cross_plane_isl",
                        priority=3,
                    )
                )

            # Deduplicate: if fwd and aft resolve to the same peer
            # (e.g. 2 sats per plane), keep only the higher-priority one
            seen_peers: set[str] = set()
            deduped: list[NeighborAssignment] = []
            for c in candidates:
                if c.peer_node_id not in seen_peers:
                    seen_peers.add(c.peer_node_id)
                    deduped.append(c)

            # Trim to available terminal count
            assigned = deduped[:isl_count]

            # Assign interface names
            for idx, na in enumerate(assigned):
                assignments.append(
                    (
                        node_id,
                        NeighborAssignment(
                            interface=f"isl{idx}",
                            peer_node_id=na.peer_node_id,
                            link_type=na.link_type,
                            priority=na.priority,
                        ),
                    )
                )

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
    # Sort each node's assignments by a total key. The source collection is a
    # frozenset, so priority alone leaves equal-priority entries hash-seed
    # dependent.
    for node_id in result:
        result[node_id].sort(
            key=lambda x: (
                x.priority,
                x.interface,
                x.peer_node_id,
                x.link_type,
                -1.0 if x.bandwidth_mbps is None else x.bandwidth_mbps,
            )
        )
    return result


def unique_isl_pairs(
    assignments: frozenset[tuple[str, NeighborAssignment]],
) -> set[tuple[str, str]]:
    """Return deduplicated set of ISL pairs as (node_a, node_b) tuples.

    Each ISL appears twice in the assignment set (A→B and B→A).
    This returns sorted tuples so each pair appears exactly once.
    """
    pairs: set[tuple[str, str]] = set()
    for node_id, na in assignments:
        pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
        pairs.add(pair)
    return pairs


def topology_summary(
    assignments: frozenset[tuple[str, NeighborAssignment]],
) -> dict[str, int | bool]:
    """Compute structural topology properties from neighbor assignments.

    Returns dict with:
        intra_per_sat: typical intra-plane links per satellite
        cross_per_sat: typical cross-plane links per satellite
        max_cross_per_sat: max cross-plane links any satellite has
        has_cross_plane: whether any cross-plane links exist
        total_unique_pairs: unique ISL pair count
    """
    by_node = neighbors_by_node(assignments)
    intra_counts: list[int] = []
    cross_counts: list[int] = []
    for _nid, node_assignments in by_node.items():
        intra = sum(1 for a in node_assignments if a.link_type == "intra_plane_isl")
        cross = sum(1 for a in node_assignments if a.link_type == "cross_plane_isl")
        intra_counts.append(intra)
        cross_counts.append(cross)

    max_cross = max(cross_counts) if cross_counts else 0
    return {
        "intra_per_sat": max(set(intra_counts), key=intra_counts.count) if intra_counts else 0,
        "cross_per_sat": max(set(cross_counts), key=cross_counts.count) if cross_counts else 0,
        "max_cross_per_sat": max_cross,
        "has_cross_plane": max_cross > 0,
        "total_unique_pairs": len(unique_isl_pairs(assignments)),
    }
