# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Composer tests for DecisionExplanationFacts, grounded in the live Denver scenario."""

from __future__ import annotations

from datetime import UTC, datetime

from nodalarc.explain import compose_gs_explanation, scheduled_pairs
from nodalarc.models.link_decisions import (
    GroundLinkDecisionSnapshot,
    GroundPolicyAudit,
    GroundVisibilityDecisionWire,
    UnscheduledPair,
)

GS = "gs-denver"


def _decision(
    sat: str,
    *,
    visible: bool,
    range_km: float,
    elevation_deg: float,
    reject_reason: str,
    rejecting_endpoint: str = "none",
) -> GroundVisibilityDecisionWire:
    return GroundVisibilityDecisionWire(
        pair=(GS, sat),
        tenant_id="default",
        reference_body="earth",
        visible=visible,
        range_km=range_km,
        elevation_deg=elevation_deg,
        azimuth_deg=180.0 if elevation_deg > -90 else None,
        observer_frame="body_local",
        reject_reason=reject_reason,
        rejecting_endpoint=rejecting_endpoint,
        applied_min_elevation_deg=25.0,
        applied_gs_max_range_km=2000.0,
        applied_sat_max_range_km=2000.0,
        applied_gs_field_of_regard_deg=120.0,
        applied_sat_field_of_regard_deg=120.0,
        applied_gs_max_tracking_rate_deg_s=1.5,
        applied_sat_max_tracking_rate_deg_s=1.5,
        applied_gs_boresight_mode="local_vertical",
        applied_sat_boresight_mode="nadir",
        applied_gs_terminal_profile="gs-denver.terminals",
        applied_sat_terminal_profile=f"{sat}.ground_terminals",
    )


def _audit() -> GroundPolicyAudit:
    return GroundPolicyAudit(
        selection_policies={GS: "highest-elevation"},
        selection_policy_params={GS: {}},
        handover_policies={GS: "hysteresis"},
        handover_policy_params={GS: {}},
        ranking_order=("lex_pair",),
        handover_mode="bbm",
        mbb_preemption="off",
        successor_abort_policy="hard_release",
        cross_tenant_displacement="off",
        mbb_overlap_ticks=0,
        mbb_reserve=0,
        bbm_acquire_timeout_ticks=1,
        ignored_capacity_fields=(),
    )


def _snapshot(
    decisions: list[GroundVisibilityDecisionWire],
    unscheduled: list[UnscheduledPair] | None = None,
) -> GroundLinkDecisionSnapshot:
    return GroundLinkDecisionSnapshot(
        sim_time=datetime(2026, 5, 29, 18, 8, 20, tzinfo=UTC),
        snapshot_seq=516,
        epoch_id=0,
        decisions=tuple(decisions),
        unscheduled_pairs=tuple(unscheduled or ()),
        policy_audit=_audit(),
        allocation_events=(),
    )


def _gate(facts, name):
    return next(g for g in facts.ladder if g.gate == name)


def test_connected_pair_passes_every_gate():
    snap = _snapshot(
        [
            _decision(
                "sat-P00S03", visible=True, range_km=960.0, elevation_deg=31.6, reject_reason="ok"
            )
        ]
    )
    facts = compose_gs_explanation(
        gs_id=GS,
        snapshot=snap,
        active_pairs=frozenset({(GS, "sat-P00S03")}),
        actuation_state_by_gs={GS: "clean"},
    )
    assert facts is not None
    assert facts.pair == (GS, "sat-P00S03")
    assert facts.binding_gate is None
    assert facts.actuation.state == "clean"
    assert facts.actuation.kernel_up is True
    assert facts.actuation.diverged is False
    assert _gate(facts, "elevation_mask").state == "pass"
    assert _gate(facts, "actuation_proof").state == "pass"


def test_denver_gap_is_expected_no_link_with_for_derived_floor():
    # The live gap: nothing above the mask; S02 is closest (14 deg), S05 out of range.
    snap = _snapshot(
        [
            _decision(
                "sat-P00S02",
                visible=False,
                range_km=1567.0,
                elevation_deg=14.0,
                reject_reason="elevation_below_min",
            ),
            _decision(
                "sat-P00S05",
                visible=False,
                range_km=2407.0,
                elevation_deg=3.0,
                reject_reason="range_exceeded",
                rejecting_endpoint="both",
            ),
        ]
    )
    facts = compose_gs_explanation(
        gs_id=GS, snapshot=snap, active_pairs=frozenset(), actuation_state_by_gs={}
    )
    assert facts is not None
    # Closest-to-visible rejected candidate leads (highest elevation).
    assert facts.pair == (GS, "sat-P00S02")
    assert facts.binding_gate == "elevation_mask"
    assert facts.binding_reason_code == "elevation_below_min"
    assert facts.best_candidate.viable_withheld is False
    # No actuation health available -> honest unknown, never faked clean.
    assert facts.actuation.state == "unknown"
    # OME did not desire this pair and the kernel does not have it — they agree, no divergence.
    assert facts.actuation.ome_desired is False
    assert facts.actuation.diverged is False
    # The Denver insight: FoR (120 deg, vertical) floors elevation at 30, dominating the 25 mask.
    env = facts.envelope
    assert env.configured_min_elevation_deg == 25.0
    assert env.effective_min_elevation_deg == 30.0
    assert env.binding_source == "field_of_regard"
    assert env.dead_knobs == ("min_elevation_deg",)
    # Ladder respects funnel order: LOS before elevation passes, elevation is the fail.
    assert _gate(facts, "line_of_sight").state == "pass"
    assert _gate(facts, "elevation_mask").state == "fail"
    assert _gate(facts, "elevation_mask").is_binding is True
    assert _gate(facts, "field_of_regard").state == "not_evaluated"


def test_viable_but_withheld_dominates_and_is_flagged():
    visible_unsched = _decision(
        "sat-P00S04", visible=True, range_km=900.0, elevation_deg=40.0, reject_reason="ok"
    )
    snap = _snapshot(
        [visible_unsched],
        unscheduled=[
            UnscheduledPair(
                pair=(GS, "sat-P00S04"),
                tenant_id="default",
                reference_body="earth",
                unscheduled_reason="gs_capacity",
                incumbent_pair=None,
                capacity_constraint="gs-denver.terminals",
            )
        ],
    )
    facts = compose_gs_explanation(
        gs_id=GS, snapshot=snap, active_pairs=frozenset(), actuation_state_by_gs={GS: "clean"}
    )
    assert facts is not None
    assert facts.best_candidate.viable_withheld is True
    assert facts.binding_gate == "capacity"
    assert facts.binding_reason_code == "gs_capacity"
    # Physics gates pass; the binding stop is capacity, in the allocator layer.
    assert _gate(facts, "elevation_mask").state == "pass"
    assert _gate(facts, "capacity").state == "fail"
    assert _gate(facts, "capacity").producer == "ome_allocator"


def test_scheduled_but_not_up_is_divergence_with_actuation_state():
    snap = _snapshot(
        [
            _decision(
                "sat-P00S03", visible=True, range_km=960.0, elevation_deg=31.6, reject_reason="ok"
            )
        ]
    )
    facts = compose_gs_explanation(
        gs_id=GS,
        snapshot=snap,
        active_pairs=frozenset(),  # OME desired it, kernel does NOT have it
        actuation_state_by_gs={GS: "kernel_dirty"},
    )
    assert facts is not None
    assert facts.actuation.ome_desired is True
    assert facts.actuation.kernel_up is False
    assert facts.actuation.diverged is True
    assert facts.binding_gate == "actuation_proof"
    assert facts.actuation.state == "kernel_dirty"
    assert _gate(facts, "actuation_proof").state == "fail"


def test_no_decision_for_gs_returns_none():
    snap = _snapshot(
        [
            _decision(
                "sat-P00S03", visible=True, range_km=960.0, elevation_deg=31.6, reject_reason="ok"
            )
        ]
    )
    assert (
        compose_gs_explanation(
            gs_id="gs-tokyo", snapshot=snap, active_pairs=frozenset(), actuation_state_by_gs={}
        )
        is None
    )


def test_dirty_roster_overrides_snapshot_up():
    # The OME link snapshot shows the pair up, but the Scheduler roster reports the GS
    # kernel-dirty. The card must read faulted, not connected — the snapshot is OME's
    # model, the roster is the actuation truth. Guards the masking bug.
    snap = _snapshot(
        [
            _decision(
                "sat-P00S03", visible=True, range_km=960.0, elevation_deg=31.6, reject_reason="ok"
            )
        ]
    )
    facts = compose_gs_explanation(
        gs_id=GS,
        snapshot=snap,
        active_pairs=frozenset({(GS, "sat-P00S03")}),  # OME snapshot: up
        actuation_state_by_gs={GS: "kernel_dirty"},  # Scheduler roster: dirty
    )
    assert facts is not None
    assert facts.binding_gate == "actuation_proof"
    assert facts.actuation.state == "kernel_dirty"
    assert _gate(facts, "actuation_proof").state == "fail"


def test_divergence_carries_elapsed_and_contract_bounds():
    # A scheduled-but-not-actual pair is diverged; the actuation facts must carry how
    # long (server-computed, skew-free) plus the wall-clock contract bounds, so the
    # client can flip in_flight -> faulted at fault_after_ms without hardcoding it.
    snap = _snapshot(
        [
            _decision(
                "sat-P00S03", visible=True, range_km=960.0, elevation_deg=31.6, reject_reason="ok"
            )
        ]
    )
    pair = tuple(sorted((GS, "sat-P00S03")))
    onset = datetime(2026, 5, 29, 18, 8, 18, tzinfo=UTC)
    now = datetime(2026, 5, 29, 18, 8, 18, 500000, tzinfo=UTC)  # 500 ms after onset
    facts = compose_gs_explanation(
        gs_id=GS,
        snapshot=snap,
        active_pairs=frozenset(),  # OME desires it, kernel does NOT have it -> diverged
        actuation_state_by_gs={GS: "clean"},
        divergence_onset_by_pair={pair: onset},
        expected_latency_ms=250.0,
        fault_after_ms=1200.0,
        now=now,
    )
    assert facts.actuation.diverged is True
    assert facts.actuation.diverged_since == onset
    assert facts.actuation.actuation_elapsed_ms == 500.0
    assert facts.actuation.expected_latency_ms == 250.0
    assert facts.actuation.fault_after_ms == 1200.0


def test_connected_pair_has_no_divergence_timing_but_keeps_bounds():
    snap = _snapshot(
        [
            _decision(
                "sat-P00S03", visible=True, range_km=960.0, elevation_deg=31.6, reject_reason="ok"
            )
        ]
    )
    pair = tuple(sorted((GS, "sat-P00S03")))
    facts = compose_gs_explanation(
        gs_id=GS,
        snapshot=snap,
        active_pairs=frozenset({pair}),  # kernel HAS it -> connected, not diverged
        actuation_state_by_gs={GS: "clean"},
        divergence_onset_by_pair={pair: datetime(2026, 5, 29, tzinfo=UTC)},
        expected_latency_ms=250.0,
        fault_after_ms=1200.0,
        now=datetime(2026, 5, 29, 1, tzinfo=UTC),
    )
    assert facts.actuation.diverged is False
    # No stale onset bleeds into a connected pair.
    assert facts.actuation.diverged_since is None
    assert facts.actuation.actuation_elapsed_ms is None
    # The contract bounds are always available to the client.
    assert facts.actuation.fault_after_ms == 1200.0


def test_scheduled_pairs_is_visible_minus_withheld():
    snap = _snapshot(
        [
            _decision(
                "sat-A", visible=True, range_km=900.0, elevation_deg=40.0, reject_reason="ok"
            ),
            _decision(
                "sat-B", visible=True, range_km=900.0, elevation_deg=40.0, reject_reason="ok"
            ),
            _decision(
                "sat-C",
                visible=False,
                range_km=5000.0,
                elevation_deg=-10.0,
                reject_reason="elevation_below_min",
            ),
        ],
        unscheduled=[
            UnscheduledPair(
                pair=(GS, "sat-B"),
                tenant_id="default",
                reference_body="earth",
                unscheduled_reason="gs_capacity",
                incumbent_pair=None,
                capacity_constraint="gs-denver.terminals",
            )
        ],
    )
    sp = scheduled_pairs(snap)
    assert tuple(sorted((GS, "sat-A"))) in sp  # visible + scheduled
    assert tuple(sorted((GS, "sat-B"))) not in sp  # visible but withheld
    assert tuple(sorted((GS, "sat-C"))) not in sp  # not visible


def test_closest_rejected_ranks_by_funnel_depth_not_raw_elevation():
    # sat-hi: higher elevation but range-bound (stopped at the range gate).
    # sat-lo: lower elevation but elevation-bound (got past range, stopped at elevation).
    # The pair that got further through the funnel is closest to connecting, so sat-lo
    # leads despite sat-hi's higher elevation.
    snap = _snapshot(
        [
            _decision(
                "sat-hi",
                visible=False,
                range_km=2500.0,
                elevation_deg=40.0,
                reject_reason="range_exceeded",
                rejecting_endpoint="both",
            ),
            _decision(
                "sat-lo",
                visible=False,
                range_km=1200.0,
                elevation_deg=20.0,
                reject_reason="elevation_below_min",
            ),
        ]
    )
    facts = compose_gs_explanation(
        gs_id=GS, snapshot=snap, active_pairs=frozenset(), actuation_state_by_gs={}
    )
    assert facts is not None
    assert facts.pair == (GS, "sat-lo")
    assert facts.binding_reason_code == "elevation_below_min"
