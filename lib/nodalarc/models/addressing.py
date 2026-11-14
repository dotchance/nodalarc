# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Runtime ISL neighbor facts and topology aggregation helpers."""

from __future__ import annotations

from typing import Literal, NamedTuple

# OME's plane relation for an ISL neighbor; it never leaves OME.
IslPlaneRelation = Literal["intra_plane_isl", "cross_plane_isl"]


class NeighborAssignment(NamedTuple):
    """Immutable ISL neighbor assignment for one terminal."""

    interface: str  # "isl0", "isl1", etc.
    peer_node_id: str  # "sat-P03S08"
    link_type: IslPlaneRelation
    priority: int  # 0=intra-fwd, 1=intra-aft, 2=cross-right, 3=cross-left
    # The resolver's class of this link: True for a long-delay link, whose contacts OME names
    # and the Scheduler drains at their end.
    long_delay: bool


class AssignedSpaceEnd(NamedTuple):
    """One end of a candidate pair between two space nodes whose terminals OME assigns per
    pass: the node's terminal pool the pair draws on, and what the allocator and the space
    physics need of that end."""

    # The pool's name at the node, and the node's terminal indices in it.
    pool: str
    terminal_indices: tuple[int, ...]
    # The limits of the pool's terminals.
    terminal_type: str
    max_range_km: float
    max_tracking_rate_deg_s: float
    field_of_regard_deg: float
    # The node's tenant and the body it orbits: a pair no node states scheduling for is
    # attributed to its first node's.
    tenant_id: str
    central_body: str


class AssignedPairPools(NamedTuple):
    """The link rule of a candidate pair on assigned terminals and the terminal pool the pair
    draws on at each node, in node-pair order."""

    rule_id: str
    pool_a: str
    pool_b: str


class AssignedSpacePair(NamedTuple):
    """One candidate pair between two space nodes whose terminals OME assigns per pass, in
    node-pair order."""

    pair: tuple[str, str]
    rule_id: str
    link_type: IslPlaneRelation
    end_a: AssignedSpaceEnd
    end_b: AssignedSpaceEnd


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
        result[node_id].sort(key=lambda x: (x.priority, x.interface, x.peer_node_id, x.link_type))
    return result
