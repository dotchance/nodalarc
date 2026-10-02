# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Allocation contract tests (§4.4 of hysteresis workbench doc).

Three invariants must hold after every allocation cycle:
1. No ground segment has more active associations than its capacity.
2. No satellite has more active ground links than its ground_terminal_count.
3. Every allocated pair is geometrically feasible at the current sim_time.

Runs against a real fixture constellation with hysteresis active.
"""

from __future__ import annotations

from ome.event_stream import build_step_context, compute_step

from tests.conftest import load_runtime_ome_test_inputs


def _load_test_session():
    session, _resolved, gs_file, satellites, addressing, neighbors, candidates = (
        load_runtime_ome_test_inputs(origin="test.allocation_contract")
    )
    return (
        session,
        gs_file,
        satellites,
        addressing,
        neighbors,
        candidates,
    )


class TestAllocationContractInvariants:
    """Run 120 ticks with hysteresis and verify invariants on every tick."""

    def test_capacity_and_feasibility_invariants_120_ticks(self):
        session, gs_file, sats, addressing, neighbors, ground_candidates = _load_test_session()
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
            body_frames=session.body_frames,
        )

        isl_state: dict = {}
        gs_state: dict = {}
        associations: dict = {}
        observed_associations = 0

        for step in range(121):
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
            observed_associations += len(associations)

            # Invariant 1: No GS exceeds its terminal capacity
            gs_counts: dict[str, int] = {}
            for gs_id, sat_id in associations:
                gs_counts[gs_id] = gs_counts.get(gs_id, 0) + 1

            for gs_id, count in gs_counts.items():
                cap = ctx.gs_terminal_counts[gs_id]
                assert count <= cap, (
                    f"Step {step}: {gs_id} has {count} associations but capacity is {cap}"
                )

            # Invariant 2: No satellite exceeds its ground_terminal_count
            sat_counts: dict[str, int] = {}
            for gs_id, sat_id in associations:
                sat_counts[sat_id] = sat_counts.get(sat_id, 0) + 1

            for sat_id, count in sat_counts.items():
                cap = ctx.sat_ground_terminals[sat_id]
                assert count <= cap, (
                    f"Step {step}: {sat_id} has {count} GS associations "
                    f"but ground_terminal_count is {cap}"
                )

            for pair in associations:
                state = gs_state.get(pair)
                assert state is not None, f"Step {step}: allocated pair {pair} not in gs_state"
                assert state[0], f"Step {step}: allocated pair {pair} is not visible"
                assert state[1], f"Step {step}: allocated pair {pair} is not scheduled"

        assert observed_associations > 0, "fixture exercised no ground allocations"
