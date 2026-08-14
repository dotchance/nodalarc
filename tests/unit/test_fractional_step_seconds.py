"""Fractional step_seconds executes as declared.

The grammar accepts any positive finite step_seconds. OME once truncated
it with int() at both entry points: 1.5 silently ran as 1, and 0.5 became
0 and crashed live setup with ZeroDivisionError. Execution is float
end-to-end now; these tests drive the production compute loop, the batch
window, and the authority-age bound at fractional steps, and pin
half-step arithmetic bit-identical against the equivalent whole step so
the epoch-anchoring exactness guarantees survive fractional granularity.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nodalarc.models.session import resolve_session_epoch
from ome.event_stream import (
    build_step_context,
    compute_step,
    precompute_timeline_window_from_context,
)
from ome.main import (
    _authority_snapshot_interval_s,
    _effective_ground_scheduling_for_runtime,
    _load_session_config,
)

SESSION = Path(__file__).resolve().parents[2] / "catalog/nodalarc/sessions/earth-leo-simple.yaml"


@pytest.fixture(scope="module")
def leo_context():
    cfg = _load_session_config(str(SESSION), run_id="run-fractional-0001")
    ctx = build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        propagator_id=cfg.propagator_id,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_scheduling=_effective_ground_scheduling_for_runtime(cfg.ground_scheduling),
        ground_link_model=cfg.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=cfg.ground_candidate_satellites_by_gs,
        node_metadata=cfg.node_metadata,
        body_frames=cfg.body_frames,
        body_ephemeris=cfg.body_ephemeris,
        active_bodies=cfg.active_bodies,
    )
    return ctx, resolve_session_epoch(cfg.resolved.time)


def _states(ctx, epoch_unix, step, step_seconds):
    result = compute_step(ctx, epoch_unix, step, step_seconds, 0.0, {}, {})
    return result.propagated_states


def test_authority_age_bound_accepts_fractional_steps():
    assert (
        _authority_snapshot_interval_s(
            platform_snapshot_interval_s=5.0,
            max_latency_age_ticks=10,
            step_seconds=0.5,
        )
        == 5.0
    )
    assert (
        _authority_snapshot_interval_s(
            platform_snapshot_interval_s=5.0,
            max_latency_age_ticks=2,
            step_seconds=1.5,
        )
        == 3.0
    )


def test_fractional_step_advances_sim_time_fractionally(leo_context):
    ctx, epoch_unix = leo_context
    states = _states(ctx, epoch_unix, 1, 1.5)
    assert states
    for state in states.values():
        assert state.sim_time_unix == epoch_unix + 1.5


def test_sub_second_steps_run_the_full_compute_loop(leo_context):
    """step_seconds 0.5 once became int() 0 and crashed live setup."""
    ctx, epoch_unix = leo_context
    isl_state: dict = {}
    gs_state: dict = {}
    last = None
    for step in range(4):
        last = compute_step(ctx, epoch_unix, step, 0.5, 0.0, isl_state, gs_state)
    assert last is not None
    for state in last.propagated_states.values():
        assert state.sim_time_unix == epoch_unix + 1.5


def test_two_half_steps_are_bit_identical_to_one_whole_step(leo_context):
    """dt reaches propagation as step * step_seconds; 2 * 0.5 and 1 * 1.0
    are the same float, so anchored canonical-phase arithmetic must produce
    bit-identical state at the shared instant."""
    ctx, epoch_unix = leo_context
    half = _states(ctx, epoch_unix, 2, 0.5)
    whole = _states(ctx, epoch_unix, 1, 1.0)
    assert set(half) == set(whole)
    for node_id, state in whole.items():
        other = half[node_id]
        assert other.sim_time_unix == state.sim_time_unix
        assert other.position_ecef_km == state.position_ecef_km
        assert other.velocity_ecef_km_s == state.velocity_ecef_km_s
        assert other.position_common_km == state.position_common_km


def test_fractional_longest_remaining_pass_lookahead_runs():
    """The dwell lookahead propagates future ticks of step_seconds each;
    the shipped inmarsat session runs longest_remaining_pass, so driving
    it at a fractional step proves pass scoring works off-integer. The
    policy contract requires a non-None remaining_visible_s on every
    visible decision or scoring fails loudly."""
    session = SESSION.parent / "earth-geo-inmarsat.yaml"
    cfg = _load_session_config(str(session), run_id="run-fractional-0002")
    ctx = build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        propagator_id=cfg.propagator_id,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_scheduling=_effective_ground_scheduling_for_runtime(cfg.ground_scheduling),
        ground_link_model=cfg.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=cfg.ground_candidate_satellites_by_gs,
        node_metadata=cfg.node_metadata,
        body_frames=cfg.body_frames,
        body_ephemeris=cfg.body_ephemeris,
        active_bodies=cfg.active_bodies,
    )
    epoch_unix = resolve_session_epoch(cfg.resolved.time)

    isl_state: dict = {}
    gs_state: dict = {}
    dwell_state: dict = {}
    last = None
    for step in range(3):
        last = compute_step(
            ctx, epoch_unix, step, 0.5, 0.0, isl_state, gs_state, dwell_state=dwell_state
        )
    assert last is not None
    visible = [decision for decision in last.ground_decisions.values() if decision.visible]
    assert visible, "inmarsat gateways must see the GEO ring"
    # The pass-frontier memo is only ever populated by the
    # longest-remaining-pass lookahead walking future ticks; entries here
    # mean the dwell walk propagated fractional steps.
    assert dwell_state, "longest-remaining-pass lookahead never ran"
    scheduled = [pair for pair, (vis, sched, _t) in gs_state.items() if vis and sched]
    assert scheduled, "pass scoring produced no scheduled ground links"


def test_batch_window_precomputes_fractional_steps(leo_context):
    ctx, epoch_unix = leo_context
    window = precompute_timeline_window_from_context(
        ctx,
        epoch_unix,
        duration_s=3.0,
        step_seconds=1.5,
    )
    ticks = [event for event in window.events if event.event_type == "ClockTick"]
    assert len(ticks) == 3  # steps 0, 1.5, 3.0
    assert [event.data.sim_time.timestamp() - epoch_unix for event in ticks] == [0.0, 1.5, 3.0]
