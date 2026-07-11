# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime ISL neighbor facts and topology aggregation helpers."""

from __future__ import annotations

from typing import NamedTuple


class NeighborAssignment(NamedTuple):
    """Immutable ISL neighbor assignment for one terminal."""

    interface: str  # "isl0", "isl1", etc.
    peer_node_id: str  # "sat-P03S08"
    link_type: str  # "intra_plane_isl", "cross_plane_isl", "ground_uplink", "ground_downlink"
    priority: int  # 0=intra-fwd, 1=intra-aft, 2=cross-right, 3=cross-left
    bandwidth_mbps: float | None = None  # Per-interface bottleneck bandwidth when pre-resolved.


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
