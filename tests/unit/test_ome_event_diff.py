# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for the OME event-diff engine."""

from __future__ import annotations

import math
from datetime import UTC, datetime

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
        applied_max_range_km=None,
        applied_field_of_regard_deg=None,
        applied_max_tracking_rate_deg_s=None,
        applied_boresight_mode=None,
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
