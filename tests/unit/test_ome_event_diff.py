# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for the OME event-diff engine."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from nodalarc.geo import compute_latency_ms
from ome.event_diff import diff_ground_visibility_events, diff_isl_visibility_events
from ome.ground_allocator import GroundAllocationResult
from ome.isl_engine import IslFeasibilityResult, ScheduledIsl
from ome.types import GroundVisibilityDecision, MbbTeardown


def _decision(
    pair: tuple[str, str],
    *,
    visible: bool,
    range_km: float,
    elevation_deg: float,
    reject_reason: str = "ok",
) -> GroundVisibilityDecision:
    """Build a typed visibility decision for test inputs.

    Phase 1.2.b replaced the positional tuple `(visible, range_km,
    elevation_deg)` with `GroundVisibilityDecision`. Tests construct
    the typed form explicitly — no positional shortcuts.
    """
    return GroundVisibilityDecision(
        pair=pair,
        tenant_id="default",
        reference_body="earth",
        visible=visible,
        range_km=range_km,
        elevation_deg=elevation_deg,
        azimuth_deg=None,
        observer_frame="body_local",
        reject_reason=reject_reason,  # type: ignore[arg-type]
        applied_min_elevation_deg=25.0,
        rejecting_endpoint="none",
        applied_gs_max_range_km=None,
        applied_sat_max_range_km=None,
        applied_gs_field_of_regard_deg=None,
        applied_sat_field_of_regard_deg=None,
        applied_gs_max_tracking_rate_deg_s=None,
        applied_sat_max_tracking_rate_deg_s=None,
        applied_gs_boresight_mode=None,
        applied_sat_boresight_mode=None,
        applied_gs_terminal_profile=None,
        applied_sat_terminal_profile=None,
    )


SIM = datetime(2026, 1, 1, tzinfo=UTC)


def _isl_result(pair: tuple[str, str], *, feasible: bool = True) -> IslFeasibilityResult:
    return IslFeasibilityResult(
        pair=pair,
        link_type="intra_plane_isl",
        feasible=feasible,
        range_km=1234.5,
        orbital_one_way_ms=compute_latency_ms(1234.5),
        reject_reason="ok" if feasible else "range_exceeded",
        terminal_type="optical",
        terminal_role_a="intra-plane",
        terminal_role_b="intra-plane",
        interface_a="isl0",
        interface_b="isl1",
        applied_max_range_km=5016.0,
        applied_max_tracking_rate_deg_s=None,
        applied_field_of_regard_deg=60.0,
    )


def _scheduled(pair: tuple[str, str], *, scheduled: bool = True) -> ScheduledIsl:
    return ScheduledIsl(
        pair=pair,
        terminal_role_a="intra-plane",
        terminal_role_b="intra-plane",
        range_km=1234.5,
        orbital_one_way_ms=compute_latency_ms(1234.5),
        scheduled=scheduled,
        unscheduled_reason=None if scheduled else "capacity",
    )


def test_isl_event_diff_emits_only_state_changes_and_preserves_authority_values():
    pair = ("sat-a", "sat-b")
    result = _isl_result(pair)
    diff = diff_isl_visibility_events(
        sim_time=SIM,
        feasibility={pair: result},
        scheduled_links={pair: _scheduled(pair)},
        previous_state={},
    )

    assert diff.state[pair] == (True, True)
    assert len(diff.events) == 1
    event = diff.events[0]
    assert event.link_type == "isl"
    assert event.range_km == result.range_km
    assert event.latency_ms == result.orbital_one_way_ms

    unchanged = diff_isl_visibility_events(
        sim_time=SIM,
        feasibility={pair: result},
        scheduled_links={pair: _scheduled(pair)},
        previous_state=diff.state,
    )
    assert unchanged.events == ()
    assert unchanged.state == diff.state


def test_ground_event_diff_sets_terminal_indices_and_one_way_latency():
    pair = ("gs-den", "sat-a")
    allocation = GroundAllocationResult(
        associations={pair: (1, 0)},
        pending_teardowns={},
        scheduled_pairs=frozenset({pair}),
        unscheduled_pairs=(),
    )

    diff = diff_ground_visibility_events(
        sim_time=SIM,
        visibility_decisions={
            pair: _decision(pair, visible=True, range_km=2000.0, elevation_deg=37.5)
        },
        allocation=allocation,
        terminal_types={pair: "rf"},
        previous_state={},
    )

    assert diff.state[pair] == (True, True, "active")
    assert len(diff.events) == 1
    event = diff.events[0]
    assert event.link_type == "ground"
    assert event.gs_terminal_index == 1
    assert event.sat_terminal_index == 0
    assert math.isclose(event.latency_ms or 0.0, compute_latency_ms(2000.0))


def test_ground_event_diff_marks_mbb_teardown_state():
    pair = ("gs-den", "sat-old")
    successor = ("gs-den", "sat-new")
    allocation = GroundAllocationResult(
        associations={pair: (0, 0)},
        pending_teardowns={pair: MbbTeardown(10, successor)},
        scheduled_pairs=frozenset({pair, successor}),
        unscheduled_pairs=(),
    )

    diff = diff_ground_visibility_events(
        sim_time=SIM,
        visibility_decisions={
            pair: _decision(pair, visible=True, range_km=1900.0, elevation_deg=25.0)
        },
        allocation=allocation,
        terminal_types={pair: "rf", successor: "rf"},
        previous_state={},
    )

    assert diff.state[pair] == (True, True, "teardown")
    assert diff.events[0].scheduling_state == "teardown"


# ---------------------------------------------------------------------------
# Reason propagation (Phase 1, C-foundation-5):
# event_diff must surface BOTH visibility_reject_reason (from the
# typed decision) and unscheduled_reason (from the allocator's
# unscheduled_pairs) onto VisibilityEvent so consumers can explain
# transitions from the event stream alone.
# ---------------------------------------------------------------------------


from nodalarc.models.link_decisions import UnscheduledPair as _UnscheduledPair


def test_ground_event_diff_propagates_visibility_reject_reason_for_invisible_pair():
    pair = ("gs-den", "sat-a")
    allocation = GroundAllocationResult(
        associations={},
        pending_teardowns={},
        scheduled_pairs=frozenset(),
        unscheduled_pairs=(),
    )

    diff = diff_ground_visibility_events(
        sim_time=SIM,
        visibility_decisions={
            pair: _decision(
                pair,
                visible=False,
                range_km=3000.0,
                elevation_deg=15.0,
                reject_reason="elevation_below_min",
            )
        },
        allocation=allocation,
        terminal_types={pair: "rf"},
        previous_state={pair: (True, True, "active")},
    )

    assert len(diff.events) == 1
    event = diff.events[0]
    assert event.visible is False
    assert event.visibility_reject_reason == "elevation_below_min"
    assert event.unscheduled_reason is None  # invisible → never reached allocator


def test_ground_event_diff_propagates_unscheduled_reason_for_visible_but_unallocated_pair():
    """The allocator decided the pair is visible-but-unscheduled with
    a typed reason; that reason must surface on the emitted event."""
    pair = ("gs-den", "sat-a")
    incumbent = ("gs-den", "sat-b")
    allocation = GroundAllocationResult(
        associations={incumbent: (0, 0)},
        pending_teardowns={},
        scheduled_pairs=frozenset({incumbent}),
        unscheduled_pairs=(
            _UnscheduledPair(
                pair=pair,
                tenant_id="default",
                reference_body="earth",
                unscheduled_reason="bbm_no_spare",
                incumbent_pair=incumbent,
                capacity_constraint=None,
            ),
        ),
    )

    diff = diff_ground_visibility_events(
        sim_time=SIM,
        visibility_decisions={
            pair: _decision(pair, visible=True, range_km=900.0, elevation_deg=80.0),
        },
        allocation=allocation,
        terminal_types={pair: "rf"},
        previous_state={pair: (True, True, "active")},
    )

    # Exactly one event for the pair we focused on.
    events_for_pair = [e for e in diff.events if (e.node_a, e.node_b) == pair]
    assert len(events_for_pair) == 1
    event = events_for_pair[0]
    assert event.visible is True
    assert event.scheduled is False
    assert event.visibility_reject_reason == "ok"
    assert event.unscheduled_reason == "bbm_no_spare"


def test_ground_event_diff_fails_loud_when_allocator_omits_attribution_for_visible_unscheduled():
    """If the allocator hands diff_ground_visibility_events a visible-but-
    unscheduled pair without a matching UnscheduledPair attribution, the
    producer is wrong. The diff engine must refuse to emit an event with
    no scheduling reason rather than papering over the gap with None."""
    pair = ("gs-den", "sat-a")
    allocation = GroundAllocationResult(
        associations={},
        pending_teardowns={},
        scheduled_pairs=frozenset(),
        unscheduled_pairs=(),  # allocator omitted attribution
    )

    with pytest.raises(ValueError, match="did not attribute an unscheduled_reason"):
        diff_ground_visibility_events(
            sim_time=SIM,
            visibility_decisions={
                pair: _decision(pair, visible=True, range_km=900.0, elevation_deg=80.0),
            },
            allocation=allocation,
            terminal_types={pair: "rf"},
            previous_state={},
        )


def test_ground_event_diff_clears_unscheduled_reason_for_scheduled_pair():
    """A scheduled pair has no unscheduled reason — even if the
    allocator's unscheduled_pairs happened to mention something for
    another pair on the same GS."""
    pair = ("gs-den", "sat-a")
    allocation = GroundAllocationResult(
        associations={pair: (0, 0)},
        pending_teardowns={},
        scheduled_pairs=frozenset({pair}),
        unscheduled_pairs=(),
    )

    diff = diff_ground_visibility_events(
        sim_time=SIM,
        visibility_decisions={
            pair: _decision(pair, visible=True, range_km=900.0, elevation_deg=80.0)
        },
        allocation=allocation,
        terminal_types={pair: "rf"},
        previous_state={},
    )

    assert len(diff.events) == 1
    event = diff.events[0]
    assert event.scheduled is True
    assert event.visibility_reject_reason == "ok"
    assert event.unscheduled_reason is None
