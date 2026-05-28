# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for pure ground handover policy hooks."""

from typing import get_args

from nodalarc.models.ground_policy import HandoverPolicyName, HandoverPolicySpec
from nodalarc.models.ground_station import HysteresisParameters
from ome.ground_handover_policies import HOLD_SCORE_FUNCTIONS, HandoverContext, evaluate_handover
from ome.visibility import GroundVisibility


def _visibility(sat_id: str, elevation: float) -> GroundVisibility:
    return GroundVisibility(
        sat_id=sat_id,
        visible=True,
        elevation_deg=elevation,
        range_km=1000.0,
        remaining_visible_s=None,
        reject_reason="ok",
    )


def _context() -> HandoverContext:
    return HandoverContext(
        step=10,
        gs_id="gs-A",
        incumbent_pair=("gs-A", "sat-old"),
        challenger_pair=("gs-A", "sat-new"),
        incumbent_visibility=_visibility("sat-old", 40.0),
        challenger_visibility=_visibility("sat-new", 45.0),
        min_elevation_deg=25.0,
    )


def test_none_policy_does_not_emit_hysteresis_hold_score():
    decision = evaluate_handover(
        policy=HandoverPolicySpec(name="none", params={}),
        incumbent_score=40.0,
        challenger_score=45.0,
        context=_context(),
    )

    assert decision.action == "displace"
    assert decision.incumbent_hold_score is None


def test_hysteresis_policy_emits_policy_specific_hold_score():
    decision = evaluate_handover(
        policy=HandoverPolicySpec(
            name="hysteresis",
            params=HysteresisParameters(discount_factor=1.25, mask_fade_range_deg=5.0).model_dump(),
        ),
        incumbent_score=40.0,
        challenger_score=45.0,
        context=_context(),
    )

    assert decision.action == "hold"
    assert decision.incumbent_hold_score == 50.0


def test_handover_dispatch_table_matches_policy_literal() -> None:
    assert set(HOLD_SCORE_FUNCTIONS) == set(get_args(HandoverPolicyName))


def test_none_policy_holds_when_challenger_does_not_beat_incumbent() -> None:
    decision = evaluate_handover(
        policy=HandoverPolicySpec(name="none", params={}),
        incumbent_score=45.0,
        challenger_score=45.0,
        context=_context(),
    )

    assert decision.action == "hold"
    assert decision.unscheduled_reason == "incumbent_held"
    assert decision.incumbent_hold_score is None


def test_hysteresis_policy_displaces_above_policy_specific_hold_score() -> None:
    decision = evaluate_handover(
        policy=HandoverPolicySpec(
            name="hysteresis",
            params=HysteresisParameters(discount_factor=1.25, mask_fade_range_deg=5.0).model_dump(),
        ),
        incumbent_score=40.0,
        challenger_score=50.1,
        context=_context(),
    )

    assert decision.action == "displace"
    assert decision.unscheduled_reason is None
    assert decision.incumbent_hold_score == 50.0
