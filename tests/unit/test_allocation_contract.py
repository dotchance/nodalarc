# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Allocation contract tests (§4.4 of hysteresis workbench doc).

Three invariants must hold after every allocation cycle:
1. No ground segment has more active associations than its capacity.
2. No satellite has more active ground links than its ground_terminal_count.
3. Every allocated pair is geometrically feasible at the current sim_time.

Runs against a real fixture constellation with hysteresis active.
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
from ome.event_stream import build_step_context, compute_step


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
    return session, gs_file, satellites, addressing, neighbors


class TestAllocationContractInvariants:
    """Run 120 ticks with hysteresis and verify invariants on every tick."""

    def test_capacity_invariants_120_ticks(self):
        session, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
        )

        isl_state: dict = {}
        gs_state: dict = {}
        associations: dict = {}

        for step in range(121):
            _events, _positions, associations = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                isl_state,
                gs_state,
                associations,
            )

            # Invariant 1: No GS exceeds its terminal capacity
            gs_counts: dict[str, int] = {}
            for gs_id, sat_id in associations:
                gs_counts[gs_id] = gs_counts.get(gs_id, 0) + 1

            for gs_id, count in gs_counts.items():
                cap = ctx.gs_terminal_counts.get(gs_id, 1)
                assert count <= cap, (
                    f"Step {step}: {gs_id} has {count} associations but capacity is {cap}"
                )

            # Invariant 2: No satellite exceeds its ground_terminal_count
            sat_counts: dict[str, int] = {}
            for gs_id, sat_id in associations:
                sat_counts[sat_id] = sat_counts.get(sat_id, 0) + 1

            for sat_id, count in sat_counts.items():
                cap = ctx.sat_ground_terminals.get(sat_id, 1)
                assert count <= cap, (
                    f"Step {step}: {sat_id} has {count} GS associations "
                    f"but ground_terminal_count is {cap}"
                )

    def test_associations_are_feasible(self):
        """Every allocated pair must be geometrically visible at that tick."""
        session, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
        )

        isl_state: dict = {}
        gs_state: dict = {}
        associations: dict = {}

        for step in range(61):
            _events, _positions, associations = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                isl_state,
                gs_state,
                associations,
            )

            # Invariant 3: allocated pairs must be in gs_state with visible=True
            for pair in associations:
                state = gs_state.get(pair)
                assert state is not None, f"Step {step}: allocated pair {pair} not in gs_state"
                visible, scheduled = state
                assert visible, f"Step {step}: allocated pair {pair} is not visible"
                assert scheduled, f"Step {step}: allocated pair {pair} is not scheduled"

    def test_hysteresis_reduces_flapping(self):
        """With hysteresis active, there should be fewer handover events
        than without (amnesiac). This is a statistical check, not absolute."""
        session, gs_file, sats, addressing, neighbors = _load_test_session()
        epoch_unix = 1704067200.0
        step_seconds = session.time.step_seconds
        n_steps = 120

        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
        )

        # Run with hysteresis (stateful fold)
        isl_h: dict = {}
        gs_h: dict = {}
        assoc_h: frozenset = {}
        hyst_transitions = 0
        for step in range(n_steps + 1):
            _e, _p, new_assoc_h = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                isl_h,
                gs_h,
                assoc_h,
            )
            if step > 0:
                hyst_transitions += len(
                    set(new_assoc_h.keys()).symmetric_difference(set(assoc_h.keys()))
                )
            assoc_h = new_assoc_h

        # Run without hysteresis (amnesiac — pass empty frozenset every tick)
        isl_a: dict = {}
        gs_a: dict = {}
        amnesiac_transitions = 0
        prev_assoc: frozenset = {}
        for step in range(n_steps + 1):
            _e, _p, new_assoc_a = compute_step(
                ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                isl_a,
                gs_a,
                {},
            )
            if step > 0:
                amnesiac_transitions += len(
                    set(new_assoc_a.keys()).symmetric_difference(set(prev_assoc.keys()))
                )
            prev_assoc = new_assoc_a

        # Hysteresis should produce <= transitions than amnesiac.
        # In a short window with slow-moving sats this might be equal.
        assert hyst_transitions <= amnesiac_transitions, (
            f"Hysteresis produced MORE transitions ({hyst_transitions}) "
            f"than amnesiac ({amnesiac_transitions})"
        )
