# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME visibility event diff engine.

This module owns state-transition detection only. Physics engines decide what
is feasible, schedulers decide what is allocated, and this engine turns the
before/after state into auditable VisibilityEvents. Keeping it pure makes event
ordering and replay contracts testable without running orbital propagation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from nodalarc.geo import compute_latency_ms
from nodalarc.models.events import VisibilityEvent

from ome.ground_allocator import GroundAllocationResult
from ome.isl_engine import IslFeasibilityResult, ScheduledIsl
from ome.types import GroundVisibilityDetails

IslVisibilityState = dict[tuple[str, str], tuple[bool, bool]]
GroundVisibilityState = dict[tuple[str, str], tuple[bool, bool, str]]


@dataclass(frozen=True)
class IslEventDiff:
    """ISL transition result for one OME tick."""

    events: tuple[VisibilityEvent, ...]
    state: IslVisibilityState


@dataclass(frozen=True)
class GroundEventDiff:
    """Ground-link transition result for one OME tick."""

    events: tuple[VisibilityEvent, ...]
    state: GroundVisibilityState


def diff_isl_visibility_events(
    *,
    sim_time: datetime,
    feasibility: Mapping[tuple[str, str], IslFeasibilityResult],
    scheduled_links: Mapping[tuple[str, str], ScheduledIsl],
    previous_state: Mapping[tuple[str, str], tuple[bool, bool]],
) -> IslEventDiff:
    """Emit ISL VisibilityEvents for changed feasibility/scheduling state."""
    state = dict(previous_state)
    events: list[VisibilityEvent] = []

    for pair, result in feasibility.items():
        visible = result.feasible
        scheduled = scheduled_links[pair].scheduled if visible else False
        new_state = (visible, scheduled)

        if new_state == state.get(pair, (False, False)):
            continue

        state[pair] = new_state
        events.append(
            VisibilityEvent(
                sim_time=sim_time,
                node_a=pair[0],
                node_b=pair[1],
                visible=visible,
                scheduled=scheduled,
                range_km=result.range_km,
                latency_ms=result.orbital_one_way_ms,
                elevation_deg=None,
                terminal_type=result.terminal_type,
                link_type="isl",
            )
        )

    return IslEventDiff(events=tuple(events), state=state)


def diff_ground_visibility_events(
    *,
    sim_time: datetime,
    visibility_details: GroundVisibilityDetails,
    allocation: GroundAllocationResult,
    previous_state: Mapping[tuple[str, str], tuple[bool, bool, str]],
    terminal_types: Mapping[tuple[str, str], str],
) -> GroundEventDiff:
    """Emit ground VisibilityEvents for changed visibility/allocation state."""
    state = dict(previous_state)
    events: list[VisibilityEvent] = []

    for pair, (visible, range_km, elev_deg) in visibility_details.items():
        scheduled = pair in allocation.scheduled_pairs if visible else False
        sched_state = "teardown" if pair in allocation.pending_teardowns else "active"
        new_state = (visible, scheduled, sched_state)

        if new_state == state.get(pair, (False, False, "active")):
            continue

        state[pair] = new_state
        indices = allocation.associations[pair] if scheduled else None
        events.append(
            VisibilityEvent(
                sim_time=sim_time,
                node_a=pair[0],
                node_b=pair[1],
                visible=visible,
                scheduled=scheduled,
                range_km=range_km,
                latency_ms=compute_latency_ms(range_km),
                elevation_deg=elev_deg,
                terminal_type=terminal_types[pair],
                link_type="ground",
                gs_terminal_index=indices[0] if indices else None,
                sat_terminal_index=indices[1] if indices else None,
                scheduling_state=sched_state,
            )
        )

    return GroundEventDiff(events=tuple(events), state=state)
