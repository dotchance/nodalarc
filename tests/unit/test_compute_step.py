# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for compute_step() — verifies StepResult output matches batch windows."""

from __future__ import annotations

import pytest
from nodalarc.models.ground_policy import SelectionPolicySpec
from nodalarc.models.session import GroundSchedulingConfig
from ome.event_stream import (
    build_step_context,
    compute_step,
    precompute_timeline_window,
    precompute_timeline_window_from_context,
)

from tests.conftest import load_runtime_ome_test_inputs


def _load_test_session():
    """Load a small test constellation for step comparison."""
    session, resolved, gs_file, satellites, addressing, neighbors, candidates = (
        load_runtime_ome_test_inputs(origin="test.compute_step")
    )
    return (
        session,
        resolved,
        gs_file,
        satellites,
        addressing,
        neighbors,
        candidates,
    )


class TestComputeStepMatchesWindow:
    """compute_step() called N times must produce identical events to
    precompute_timeline_window(duration_s=N*step_seconds)."""

    def test_first_10_steps_match_window_prefix(self):
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        epoch_unix = 1704067200.0  # Fixed epoch for determinism
        n_steps = 10
        step_seconds = session.time.step_seconds

        # Batch: compute a window of n_steps
        window = precompute_timeline_window(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            epoch_unix=epoch_unix,
            duration_s=n_steps * step_seconds,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            step_seconds=step_seconds,
            ground_link_model=session.ground_link_model,
        )
        window_events = window.events

        # Per-step: compute the same steps one at a time
        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            ground_link_model=session.ground_link_model,
        )
        isl_state: dict = {}
        gs_state: dict = {}
        step_events_all = []
        for step in range(n_steps + 1):
            result = compute_step(ctx, epoch_unix, step, step_seconds, 0.0, isl_state, gs_state)
            step_events_all.extend(result.events)

        # Must produce identical event count
        assert len(step_events_all) == len(window_events), (
            f"Event count mismatch: {len(step_events_all)} vs {len(window_events)}"
        )

        # Each event must have identical type, timestamp_s, and data
        for i, (se, we) in enumerate(zip(step_events_all, window_events)):
            assert se.event_type == we.event_type, (
                f"Event {i}: type {se.event_type} != {we.event_type}"
            )
            assert se.timestamp_s == we.timestamp_s, (
                f"Event {i}: ts {se.timestamp_s} != {we.timestamp_s}"
            )
            # Compare serialized data (handles datetime equality)
            assert se.data.model_dump_json() == we.data.model_dump_json(), (
                f"Event {i} ({se.event_type}): data mismatch"
            )

    def test_isl_state_continuity(self):
        """isl_state after N per-step calls matches window's returned isl_state."""
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        epoch_unix = 1704067200.0
        n_steps = 10
        step_seconds = session.time.step_seconds

        window = precompute_timeline_window(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            epoch_unix=epoch_unix,
            duration_s=n_steps * step_seconds,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            step_seconds=step_seconds,
            ground_link_model=session.ground_link_model,
        )

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            ground_link_model=session.ground_link_model,
        )
        isl_state: dict = {}
        gs_state: dict = {}
        for step in range(n_steps + 1):
            compute_step(ctx, epoch_unix, step, step_seconds, 0.0, isl_state, gs_state)

        assert isl_state == window.isl_state
        assert gs_state == window.gs_state

    def test_context_precompute_uses_exact_context_configuration(self):
        """Context precompute uses the already-normalized StepContext."""
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            ground_scheduling=GroundSchedulingConfig(
                selection_policy=SelectionPolicySpec(name="lowest-elevation"),
                handover_mode="bbm",
                mbb_overlap_ticks=7,
                mbb_reserve=0,
            ),
            propagator_id="j2-mean-elements",
            ground_candidate_satellites_by_gs=ground_candidates,
            ground_link_model=session.ground_link_model,
        )

        window = precompute_timeline_window_from_context(
            ctx,
            epoch_unix=epoch_unix,
            duration_s=2 * step_seconds,
            step_seconds=step_seconds,
            predictive=True,
        )

        assert window.predictive is True
        assert set(ctx.gs_handover_modes.values()) == {"bbm"}
        assert set(ctx.gs_mbb_overlap_ticks.values()) == {0}
        assert set(ctx.gs_mbb_reserve.values()) == {0}
        assert ctx.propagator_id == "j2-mean-elements"
        assert len(window.events) > 0

    def test_selection_score_ranking_rejects_incompatible_policy_scales(self):
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        if len(gs_file.stations) < 2:
            pytest.skip("test requires at least two ground stations")

        gs_file = gs_file.model_copy(deep=True)
        gs_file.stations[0].selection_policy = SelectionPolicySpec(
            name="longest-remaining-pass",
            params={"lookahead_horizon_ticks": 10},
        )
        gs_file.stations[1].selection_policy = SelectionPolicySpec(name="highest-elevation")

        with pytest.raises(ValueError, match="incompatible score scales"):
            build_step_context(
                satellites=sats,
                addressing=addressing,
                gs_file=gs_file,
                neighbors=neighbors,
                ground_scheduling=GroundSchedulingConfig(
                    ranking_order=["selection_score", "lex_pair"],
                ),
                propagator_id=session.orbit.propagator,
                ground_candidate_satellites_by_gs=ground_candidates,
                ground_link_model=session.ground_link_model,
            )

    def test_visibility_transitions_only_on_state_change(self):
        """VisibilityEvents are emitted only when state changes, not every step."""
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            ground_link_model=session.ground_link_model,
        )
        isl_state: dict = {}
        gs_state: dict = {}

        # Step 0 may emit initial visibility events
        result_0 = compute_step(ctx, epoch_unix, 0, step_seconds, 0.0, isl_state, gs_state)
        events_0 = result_0.events
        vis_count_0 = sum(1 for e in events_0 if e.event_type == "VisibilityEvent")

        # Step 1 should emit fewer or zero VisibilityEvents (state hasn't changed in 1 second)
        result_1 = compute_step(ctx, epoch_unix, 1, step_seconds, 0.0, isl_state, gs_state)
        events_1 = result_1.events
        vis_count_1 = sum(1 for e in events_1 if e.event_type == "VisibilityEvent")

        # Every step emits exactly 1 ClockTick (Snapshot removed in PRD v0.71)
        non_vis_0 = [e for e in events_0 if e.event_type != "VisibilityEvent"]
        assert len(non_vis_0) == 1  # ClockTick only
        non_vis_1 = [e for e in events_1 if e.event_type != "VisibilityEvent"]
        assert len(non_vis_1) == 1

        # Step 1 should have fewer visibility events than step 0 (or equal if nothing changed)
        assert vis_count_1 <= vis_count_0

    def test_positions_returned_alongside_events(self):
        """compute_step() returns positions dict alongside events."""
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            ground_link_model=session.ground_link_model,
        )
        isl_state: dict = {}
        gs_state: dict = {}

        result = compute_step(ctx, epoch_unix, 0, step_seconds, 0.0, isl_state, gs_state)
        positions = result.positions
        assert isinstance(positions, dict)
        assert len(positions) > 0
        # Positions should include both satellites and ground stations
        sat_count = sum(1 for k in positions if k.startswith("space-sat-"))
        gs_count = sum(1 for k in positions if k.startswith("ground-gs-"))
        assert sat_count > 0
        assert gs_count >= 0  # May be 0 if no GS configured

    def test_step_result_snapshot_source_survives_zero_event_delta(self):
        """Snapshot authority is full StepResult state, not emitted-event replay.

        The second compute repeats the same tick with the first compute's
        event-diff baselines already populated. It therefore emits no
        VisibilityEvents. A snapshot source built from emitted events would be
        empty; the committed StepResult source must still contain the current
        visible pairs and allocation state.
        """
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            ground_link_model=session.ground_link_model,
        )
        isl_state: dict = {}
        gs_state: dict = {}

        first = compute_step(ctx, epoch_unix, 0, step_seconds, 0.0, isl_state, gs_state)
        previously_visible = {
            pair
            for pair, (visible, _scheduled) in first.link_snapshot_source.isl_state.items()
            if visible
        } | {
            pair
            for pair, (
                visible,
                _scheduled,
                _state,
            ) in first.link_snapshot_source.ground_state.items()
            if visible
        }
        assert previously_visible

        stable = compute_step(ctx, epoch_unix, 0, step_seconds, 0.0, isl_state, gs_state)
        emitted_visibility = [
            event for event in stable.events if event.event_type == "VisibilityEvent"
        ]
        assert emitted_visibility == []

        source = stable.link_snapshot_source
        stable_visible = {
            pair for pair, (visible, _scheduled) in source.isl_state.items() if visible
        } | {pair for pair, (visible, _scheduled, _state) in source.ground_state.items() if visible}
        assert previously_visible <= stable_visible
        assert source.propagated_states == stable.propagated_states
        assert source.associations == stable.associations
        assert source.pending_teardowns == stable.pending_teardowns

    def test_link_snapshot_source_is_forwarding_plane_not_visibility_cross_product(self):
        """LinkStateSnapshot source excludes invisible GS x sat decisions.

        GroundLinkDecisionSnapshot carries the full physical visibility audit.
        LinkStateSnapshot is forwarding-plane authority, so its source is
        limited to visible scheduled pairs, visible unscheduled candidates, and
        pending teardowns. This keeps snapshot size proportional to actual link
        candidates rather than the full ground-station by satellite product.
        """
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            ground_link_model=session.ground_link_model,
        )

        result = compute_step(ctx, epoch_unix, 0, step_seconds, 0.0, {}, {})
        source = result.link_snapshot_source

        expected_ground_pairs = (
            set(result.ground_allocation.scheduled_pairs)
            | set(result.ground_allocation.pending_teardowns)
            | {pair.pair for pair in result.ground_allocation.unscheduled_pairs}
        )
        expected_isl_pairs = {
            pair for pair, feasibility in result.isl_feasibility.items() if feasibility.feasible
        }

        assert set(source.ground_state) == expected_ground_pairs
        assert set(source.isl_state) == expected_isl_pairs
        assert set(source.ground_state) < set(result.ground_decisions)
        assert all(result.ground_decisions[pair].visible for pair in source.ground_state)
        assert len(source.ground_state) < len(result.ground_decisions)

    def test_ground_reason_completeness_audit_for_every_considered_pair(self):
        """Every considered ground pair has exactly one truth axis.

        Invisible pairs are physical decisions and need a visibility reject
        reason. Visible unscheduled pairs are scheduling decisions and need an
        unscheduled reason. Scheduled/teardown pairs must be represented in the
        allocation state, not hidden in a missing-reason gap.
        """
        session, cc, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=ground_candidates,
            ground_link_model=session.ground_link_model,
        )
        result = compute_step(ctx, epoch_unix, 0, step_seconds, 0.0, {}, {})
        allocation = result.ground_allocation
        unscheduled = {item.pair: item for item in allocation.unscheduled_pairs}

        assert result.ground_decisions
        for pair, decision in result.ground_decisions.items():
            if not decision.visible:
                assert decision.reject_reason != "ok", pair
                assert pair not in allocation.scheduled_pairs
                assert pair not in unscheduled
                continue

            assert decision.reject_reason == "ok", pair
            scheduled = pair in allocation.scheduled_pairs
            unscheduled_item = unscheduled.get(pair)
            if scheduled:
                assert pair in allocation.associations, pair
                assert unscheduled_item is None, pair
            else:
                assert unscheduled_item is not None, pair
                assert unscheduled_item.unscheduled_reason, pair
