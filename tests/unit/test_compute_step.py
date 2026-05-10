# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for compute_step() — verifies StepResult output matches batch windows."""

from __future__ import annotations

from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.session import SessionConfig
from ome.event_stream import (
    build_step_context,
    compute_step,
    precompute_timeline_window,
    precompute_timeline_window_from_context,
)


def _load_test_session():
    """Load a small test constellation for step comparison."""
    from pathlib import Path

    import yaml

    session_path = Path("configs/sessions/demo-36-ospf.yaml")
    if not session_path.exists():
        import pytest

        pytest.skip("demo-36-ospf.yaml not available")

    data = yaml.safe_load(session_path.read_text())
    session = SessionConfig.model_validate(data)
    constellation_config = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)
    satellites = expand_constellation(constellation_config)
    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation_config, addressing)
    return session, constellation_config, gs_file, satellites, addressing, neighbors


class TestComputeStepMatchesWindow:
    """compute_step() called N times must produce identical events to
    precompute_timeline_window(duration_s=N*step_seconds)."""

    def test_first_10_steps_match_window_prefix(self):
        session, cc, gs_file, sats, addressing, neighbors = _load_test_session()
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
            step_seconds=step_seconds,
        )
        window_events = window.events

        # Per-step: compute the same steps one at a time
        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
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
        session, cc, gs_file, sats, addressing, neighbors = _load_test_session()
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
            step_seconds=step_seconds,
        )

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
        )
        isl_state: dict = {}
        gs_state: dict = {}
        for step in range(n_steps + 1):
            compute_step(ctx, epoch_unix, step, step_seconds, 0.0, isl_state, gs_state)

        assert isl_state == window.isl_state
        assert gs_state == window.gs_state

    def test_context_precompute_uses_exact_context_configuration(self):
        """Context-based precompute shares live OME scheduling parameters."""
        session, cc, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            mbb_overlap_ticks=7,
            mbb_reserve=2,
            default_ground_policy="lowest-elevation",
            propagator_id="j2-mean-elements",
        )

        window = precompute_timeline_window_from_context(
            ctx,
            epoch_unix=epoch_unix,
            duration_s=2 * step_seconds,
            step_seconds=step_seconds,
            predictive=True,
        )

        assert window.predictive is True
        assert ctx.mbb_overlap_ticks == 7
        assert ctx.mbb_reserve == 2
        assert ctx.propagator_id == "j2-mean-elements"
        assert len(window.events) > 0

    def test_visibility_transitions_only_on_state_change(self):
        """VisibilityEvents are emitted only when state changes, not every step."""
        session, cc, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
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
        session, cc, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
        )
        isl_state: dict = {}
        gs_state: dict = {}

        result = compute_step(ctx, epoch_unix, 0, step_seconds, 0.0, isl_state, gs_state)
        positions = result.positions
        assert isinstance(positions, dict)
        assert len(positions) > 0
        # Positions should include both satellites and ground stations
        sat_count = sum(1 for k in positions if k.startswith("sat-"))
        gs_count = sum(1 for k in positions if k.startswith("gs-"))
        assert sat_count > 0
        assert gs_count >= 0  # May be 0 if no GS configured
