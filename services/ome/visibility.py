# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Visibility result types and ISL terminal scheduling.

Per-direction geometry and terminal constraints live in ``ome.direction_checks``; this module
holds the ground visibility record the allocator ranks and the priority-based ISL terminal
scheduling with its symmetric constraint.
"""

from __future__ import annotations

from typing import NamedTuple

from nodalarc.models.link_decisions import (
    GroundVisibilityRejectingEndpoint,
    GroundVisibilityRejectReason,
)


class GroundVisibility(NamedTuple):
    """Ground station to satellite visibility details.

    ``reject_reason`` carries the specific reason a non-visible pair
    failed (``los_blocked``, ``elevation_below_min``, etc.). For a
    visible pair it is ``"ok"``. ``rejecting_endpoint`` identifies the
    endpoint whose terminal-bound constraint rejected the pair. It is
    ``"none"`` for visible pairs and for non-terminal rejections such
    as LOS and elevation mask failures.

    ``remaining_visible_s`` is populated only for policies that
    explicitly require future dwell prediction. ``None`` here means
    "not applicable for this policy"; for ``longest-remaining-pass``
    the producer must populate a non-None value or downstream
    scoring fails loudly.
    """

    sat_id: str
    visible: bool
    elevation_deg: float
    range_km: float
    remaining_visible_s: float | None
    reject_reason: GroundVisibilityRejectReason
    rejecting_endpoint: GroundVisibilityRejectingEndpoint = "none"
    azimuth_deg: float | None = None
    sat_off_nadir_deg: float | None = None


class ScheduledLink(NamedTuple):
    """A link that has been scheduled (terminal allocated)."""

    node_a: str
    node_b: str
    scheduled: bool  # True if terminal allocated, False if visible but unscheduled
    range_km: float


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def schedule_isl_terminals(
    node_id: str,
    feasible_isls: list[tuple[str, int, float]],
    terminal_count: int,
) -> list[ScheduledLink]:
    """Schedule ISL terminals based on priority.

    Args:
        node_id: The satellite doing the scheduling
        feasible_isls: List of (peer_id, priority, range_km) tuples
        terminal_count: Number of ISL terminals available

    Returns:
        List of ScheduledLink (scheduled=True for top-priority links)
    """
    # Sort by priority (lower = higher priority)
    sorted_isls = sorted(feasible_isls, key=lambda x: x[1])

    results: list[ScheduledLink] = []
    for i, (peer_id, _priority, range_km) in enumerate(sorted_isls):
        scheduled = i < terminal_count
        results.append(
            ScheduledLink(
                node_a=node_id,
                node_b=peer_id,
                scheduled=scheduled,
                range_km=range_km,
            )
        )

    return results


def enforce_symmetric_scheduling(
    all_schedules: dict[str, list[ScheduledLink]],
) -> dict[str, list[ScheduledLink]]:
    """Enforce symmetric constraint: if A schedules link to B, B must also schedule link to A.

    If B doesn't have capacity, both sides become unscheduled.
    Modifies and returns the schedule dict.
    """
    # Build lookup: (node_a, node_b) -> is_scheduled
    scheduled_pairs: set[tuple[str, str]] = set()
    for _node_id, links in all_schedules.items():
        for link in links:
            if link.scheduled:
                scheduled_pairs.add((link.node_a, link.node_b))

    # Find asymmetric pairs
    to_unschedule: set[tuple[str, str]] = set()
    for a, b in scheduled_pairs:
        if (b, a) not in scheduled_pairs:
            to_unschedule.add((a, b))

    if not to_unschedule:
        return all_schedules

    # Unschedule asymmetric links
    result: dict[str, list[ScheduledLink]] = {}
    for node_id, links in all_schedules.items():
        new_links: list[ScheduledLink] = []
        for link in links:
            if (link.node_a, link.node_b) in to_unschedule:
                new_links.append(
                    ScheduledLink(
                        link.node_a,
                        link.node_b,
                        False,
                        link.range_km,
                    )
                )
            else:
                new_links.append(link)
        result[node_id] = new_links

    return result
