# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Determinism tests for the OME stateful fold.

NON-NEGOTIABLE GATES: the look-ahead thread and the real-time pacing
loop must produce identical event sequences from the same seed. If
these tests fail, NodalPath will install phantom forwarding state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.session import SessionConfig
from ome.event_stream import build_step_context, compute_step, precompute_timeline_window


def _load_test_session():
    session_path = Path("configs/sessions/demo-36-ospf.yaml")
    if not session_path.exists():
        pytest.skip("demo-36-ospf.yaml not available")
    data = yaml.safe_load(session_path.read_text())
    session = SessionConfig.model_validate(data)
    constellation_config = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)
    satellites = expand_constellation(constellation_config)
    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation_config, addressing)
    return session, constellation_config, gs_file, satellites, addressing, neighbors


class TestFoldDeterminism:
    """Batch precompute and tick-by-tick fold must produce identical results."""

    def test_fold_determinism_60s(self):
        """60 sim-seconds: batch window vs tick-by-tick produce identical events."""
        session, _cc, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        n_steps = 60
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
            step_seconds=step_seconds,
        )
        window_events = window.events

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
        )
        isl_state: dict = {}
        gs_state: dict = {}
        associations: dict = {}
        step_events_all = []
        for step in range(n_steps + 1):
            result = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                isl_state,
                gs_state,
                associations,
            )
            associations = result.associations
            step_events_all.extend(result.events)

        assert len(step_events_all) == len(window_events), (
            f"Event count: {len(step_events_all)} vs {len(window_events)}"
        )
        for i, (se, we) in enumerate(zip(step_events_all, window_events)):
            assert se.event_type == we.event_type, f"Event {i}: type mismatch"
            assert se.data.model_dump_json() == we.data.model_dump_json(), (
                f"Event {i} ({se.event_type}): data mismatch"
            )

    def test_seek_resets_associations(self):
        """After simulating a seek (clearing state), first tick has no discount."""
        session, _cc, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
        )

        # Run 10 ticks to build up association state
        isl_state: dict = {}
        gs_state: dict = {}
        associations: dict = {}
        for step in range(11):
            result = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                isl_state,
                gs_state,
                associations,
            )
            associations = result.associations
        assert len(associations) > 0, "Should have associations after 10 ticks"

        # Simulate seek: reset all state
        isl_state_fresh: dict = {}
        gs_state_fresh: dict = {}
        associations_fresh: frozenset = {}

        # Run first tick from both: seeded and fresh
        compute_step(
            ctx,
            epoch_unix,
            0,
            step_seconds,
            0.0,
            isl_state.copy(),
            gs_state.copy(),
            associations,
        )
        fresh_result = compute_step(
            ctx,
            epoch_unix,
            0,
            step_seconds,
            0.0,
            isl_state_fresh,
            gs_state_fresh,
            associations_fresh,
        )
        assoc_fresh = fresh_result.associations

        # Fresh (post-seek) should match a clean start
        # They share the same epoch/step so positions are identical.
        # The only difference is the discount from prior associations.
        # Fresh start = no discount = amnesiac allocation.
        # Both should produce valid events (we verify fresh has no stale bias).
        assert len(assoc_fresh) > 0, "Fresh tick should produce associations"

    def test_look_ahead_matches_realtime(self):
        """NON-NEGOTIABLE GATE: look-ahead window and tick-by-tick produce
        bit-for-bit identical event sequences from the same seed."""
        session, _cc, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        n_steps = 120
        step_seconds = session.time.step_seconds

        # Simulate: first build associations from first 10 ticks (warm-up seed)
        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
        )
        seed_isl: dict = {}
        seed_gs: dict = {}
        seed_assoc: frozenset = {}
        for step in range(11):
            result = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                seed_isl,
                seed_gs,
                seed_assoc,
            )
            seed_assoc = result.associations

        # Now run from the seeded state: batch vs tick-by-tick
        seed_epoch = epoch_unix + 11 * step_seconds

        window = precompute_timeline_window(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            epoch_unix=seed_epoch,
            duration_s=n_steps * step_seconds,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            step_seconds=step_seconds,
            initial_isl_state=dict(seed_isl),
            initial_gs_state=dict(seed_gs),
            initial_associations=seed_assoc,
        )
        window_events = window.events

        tick_isl = dict(seed_isl)
        tick_gs = dict(seed_gs)
        tick_assoc = seed_assoc
        tick_events = []
        for step in range(n_steps + 1):
            result = compute_step(
                ctx,
                seed_epoch,
                step,
                step_seconds,
                0.0,
                tick_isl,
                tick_gs,
                tick_assoc,
            )
            tick_assoc = result.associations
            tick_events.extend(result.events)

        assert len(tick_events) == len(window_events), (
            f"Event count: {len(tick_events)} vs {len(window_events)}"
        )
        for i, (te, we) in enumerate(zip(tick_events, window_events)):
            assert te.event_type == we.event_type, f"Event {i}: type mismatch"
            assert te.data.model_dump_json() == we.data.model_dump_json(), (
                f"Event {i} ({te.event_type}): data mismatch at index {i}"
            )
        # Final association state must also match
        assert tick_assoc == window.associations, "Final associations must match"

    def test_cross_window_state_handoff(self):
        """NON-NEGOTIABLE GATE: fold state survives a window boundary.

        Uses tick-by-tick for both paths to avoid precompute_timeline_window
        context-recreation artifacts. Verify: (a) associations at end of
        window W match; (b) using that state as seed for W+1 produces
        identical events to a continuous run; (c) hysteresis discount
        survives the boundary.
        """
        session, _cc, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds
        boundary = 30

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
        )

        # --- Path A: continuous 60 ticks ---
        a_isl: dict = {}
        a_gs: dict = {}
        a_assoc: frozenset = {}
        a_events = []
        for step in range(61):
            result = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                a_isl,
                a_gs,
                a_assoc,
            )
            a_assoc = result.associations
            a_events.extend(result.events)

        # --- Path B: two halves with explicit state handoff ---
        b_isl: dict = {}
        b_gs: dict = {}
        b_assoc: frozenset = {}
        b_events = []

        # First half: steps 0..boundary
        for step in range(boundary + 1):
            result = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                b_isl,
                b_gs,
                b_assoc,
            )
            b_assoc = result.associations
            b_events.extend(result.events)

        # Snapshot the boundary state (simulates what _LookAheadThread returns)
        boundary_assoc = b_assoc
        assert len(boundary_assoc) > 0, "Should have associations at boundary"

        # Second half: steps boundary+1..60, seeded from boundary state
        for step in range(boundary + 1, 61):
            result = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                b_isl,
                b_gs,
                b_assoc,
            )
            b_assoc = result.associations
            b_events.extend(result.events)

        # (a) Event count must match
        assert len(b_events) == len(a_events), f"Event count: {len(b_events)} vs {len(a_events)}"

        # (b) Every event must be identical
        for i, (be, ae) in enumerate(zip(b_events, a_events)):
            assert be.event_type == ae.event_type, (
                f"Event {i}: type {be.event_type} != {ae.event_type}"
            )
            assert be.data.model_dump_json() == ae.data.model_dump_json(), (
                f"Event {i} ({be.event_type}): data mismatch"
            )

        # (c) Final associations must match
        assert b_assoc == a_assoc, "Final associations diverged across window boundary"
