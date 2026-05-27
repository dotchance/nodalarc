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
from ome.types import GroundVisibilityDecisionMap

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
        # Phase 1 (C-foundation-5): propagate typed reasons onto the event.
        # An ISL transition's reason is fully attributable from the
        # feasibility result + scheduling result without consulting
        # the snapshot.
        visibility_reject_reason = "ok" if visible else result.reject_reason

        # ISL unscheduled_reason: only meaningful when visible AND not
        # scheduled. The ISL engine's contract today writes "capacity"
        # in that case. Anything else on a visible+unscheduled pair is
        # an ISL engine contract violation — fail loud so we discover
        # the bug instead of papering over it.
        unscheduled_reason: str | None = None
        if visible and not scheduled:
            isl_unscheduled = scheduled_links[pair].unscheduled_reason
            if isl_unscheduled == "capacity":
                unscheduled_reason = "isl_terminal_capacity"
            else:
                raise ValueError(
                    f"ISL pair {pair} is visible+unscheduled but "
                    f"scheduled_links[pair].unscheduled_reason="
                    f"{isl_unscheduled!r} — expected 'capacity'. The ISL "
                    "engine must not write physical-rejection values into "
                    "the scheduling-axis field."
                )
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
                visibility_reject_reason=visibility_reject_reason,
                unscheduled_reason=unscheduled_reason,
            )
        )

    return IslEventDiff(events=tuple(events), state=state)


def diff_ground_visibility_events(
    *,
    sim_time: datetime,
    visibility_decisions: GroundVisibilityDecisionMap,
    allocation: GroundAllocationResult,
    previous_state: Mapping[tuple[str, str], tuple[bool, bool, str]],
    terminal_types: Mapping[tuple[str, str], str],
) -> GroundEventDiff:
    """Emit ground VisibilityEvents for changed visibility/allocation state.

    Consumes the typed `GroundVisibilityDecisionMap` (Phase 1.2.b
    replacement for the positional `GroundVisibilityDetails` tuple).
    Named field access — no positional unpacking that can silently
    swap fields when the schema grows.

    Reason propagation (Phase 1, C-foundation-5): every emitted event
    carries both ``visibility_reject_reason`` (from the typed
    decision) and ``unscheduled_reason`` (from the allocation's
    unscheduled-pair set). Consumers of the event stream can attribute
    transitions without correlating against the decision snapshot.
    """
    state = dict(previous_state)
    events: list[VisibilityEvent] = []

    # Index the allocator's unscheduled-pair attributions by pair so
    # we can propagate the reason onto the visibility event.
    unscheduled_by_pair = {u.pair: u.unscheduled_reason for u in allocation.unscheduled_pairs}

    for pair, decision in visibility_decisions.items():
        scheduled = pair in allocation.scheduled_pairs if decision.visible else False
        sched_state = "teardown" if pair in allocation.pending_teardowns else "active"
        new_state = (decision.visible, scheduled, sched_state)

        if new_state == state.get(pair, (False, False, "active")):
            continue

        state[pair] = new_state
        indices = allocation.associations[pair] if scheduled else None
        # unscheduled_reason is set only when the pair is visible AND
        # not scheduled (and the allocator recorded a reason for it).
        # An invisible pair never reached the allocator. A scheduled
        # pair has no rejection reason.
        unscheduled_reason = (
            unscheduled_by_pair.get(pair) if (decision.visible and not scheduled) else None
        )
        events.append(
            VisibilityEvent(
                sim_time=sim_time,
                node_a=pair[0],
                node_b=pair[1],
                visible=decision.visible,
                scheduled=scheduled,
                range_km=decision.range_km,
                latency_ms=compute_latency_ms(decision.range_km),
                elevation_deg=decision.elevation_deg,
                terminal_type=terminal_types[pair],
                link_type="ground",
                gs_terminal_index=indices[0] if indices else None,
                sat_terminal_index=indices[1] if indices else None,
                scheduling_state=sched_state,
                visibility_reject_reason=decision.reject_reason,
                unscheduled_reason=unscheduled_reason,
            )
        )

    return GroundEventDiff(events=tuple(events), state=state)
