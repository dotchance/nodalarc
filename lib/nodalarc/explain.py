# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Compose DecisionExplanationFacts from committed OME/Scheduler truth.

Pure functions over lib models — no VS-API or service imports, so this is
unit-testable in isolation. VS-API wires its session state (latest ground
decision snapshot, active links, per-GS actuation state) into `compose_gs_explanation`.

The composer reports FACTS only (gate states, numbers, divergence). It assigns
no family and writes no human text — the client registry owns meaning. See
lib/nodalarc/models/decision_explanation.py for the responsibility split.
"""

from __future__ import annotations

from collections.abc import Mapping

from nodalarc.models.decision_explanation import (
    ActuationFacts,
    ActuationStateName,
    CandidateFacts,
    DecisionExplanationFacts,
    EffectiveEnvelopeFacts,
    FunnelGate,
    GateState,
    LadderGate,
)
from nodalarc.models.link_decisions import (
    GroundLinkDecisionSnapshot,
    GroundVisibilityDecisionWire,
    UnscheduledPair,
)

Pair = tuple[str, str]

# Funnel order is canonical; index gives before/at/after the binding gate.
FUNNEL_ORDER: tuple[FunnelGate, ...] = (
    "line_of_sight",
    "range",
    "elevation_mask",
    "field_of_regard",
    "tracking_rate",
    "selection_policy",
    "capacity",
    "handover_policy",
    "actuation_proof",
)
_GATE_INDEX = {g: i for i, g in enumerate(FUNNEL_ORDER)}

_REJECT_GATE: dict[str, FunnelGate] = {
    "los_blocked": "line_of_sight",
    "range_exceeded": "range",
    "elevation_below_min": "elevation_mask",
    "field_of_regard": "field_of_regard",
    "tracking_exceeded": "tracking_rate",
}

_UNSCHEDULED_GATE: dict[str, FunnelGate] = {
    "gs_capacity": "capacity",
    "sat_capacity": "capacity",
    "bbm_no_spare": "capacity",
    "hysteresis_hold": "handover_policy",
    "incumbent_held": "handover_policy",
    "mbb_overlap_locked": "handover_policy",
    "replaced_by_successor": "handover_policy",
    "successor_aborted": "handover_policy",
    "failed_successor": "handover_policy",
    "failed_acquire": "handover_policy",
}

_GATE_PRODUCER = {
    "line_of_sight": "ome_visibility",
    "range": "ome_visibility",
    "elevation_mask": "ome_visibility",
    "field_of_regard": "ome_visibility",
    "tracking_rate": "ome_visibility",
    "selection_policy": "ome_allocator",
    "capacity": "ome_allocator",
    "handover_policy": "ome_allocator",
    "actuation_proof": "scheduler",
}


def _ordered_pair(pair: tuple[str, str]) -> Pair:
    a, b = pair
    return (a, b) if a <= b else (b, a)


def _min_opt(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def _gate_numbers(
    gate: FunnelGate, d: GroundVisibilityDecisionWire
) -> tuple[float | None, float | None]:
    """(actual, threshold) for a numeric gate, or (None, None)."""
    if gate == "line_of_sight":
        return (d.elevation_deg, 0.0)
    if gate == "range":
        return (d.range_km, _min_opt(d.applied_gs_max_range_km, d.applied_sat_max_range_km))
    if gate == "elevation_mask":
        return (d.elevation_deg, d.applied_min_elevation_deg)
    if gate == "field_of_regard":
        for_full = _min_opt(d.applied_gs_field_of_regard_deg, d.applied_sat_field_of_regard_deg)
        threshold = for_full / 2.0 if for_full is not None else None
        # Off-boresight angle is recoverable only for a local-vertical ground boresight.
        actual = 90.0 - d.elevation_deg if d.applied_gs_boresight_mode == "local_vertical" else None
        return (actual, threshold)
    if gate == "tracking_rate":
        # Required slew rate is not carried on the decision wire; only the limit is.
        return (
            None,
            _min_opt(d.applied_gs_max_tracking_rate_deg_s, d.applied_sat_max_tracking_rate_deg_s),
        )
    return (None, None)


def _gate_applicable(gate: FunnelGate, d: GroundVisibilityDecisionWire) -> bool:
    if gate == "range":
        return d.applied_gs_max_range_km is not None or d.applied_sat_max_range_km is not None
    if gate == "field_of_regard":
        return (
            d.applied_gs_field_of_regard_deg is not None
            or d.applied_sat_field_of_regard_deg is not None
        )
    if gate == "tracking_rate":
        return (
            d.applied_gs_max_tracking_rate_deg_s is not None
            or d.applied_sat_max_tracking_rate_deg_s is not None
        )
    return True


def _build_ladder(
    d: GroundVisibilityDecisionWire,
    *,
    binding_gate: FunnelGate | None,
    binding_reason: str | None,
    actuation_pass: bool | None,
) -> tuple[LadderGate, ...]:
    """Walk the funnel: before binding = pass, binding = fail, after = not_evaluated.

    A connected pair (binding_gate None, actuation_pass True) passes every gate.
    """
    rows: list[LadderGate] = []
    binding_idx = _GATE_INDEX[binding_gate] if binding_gate is not None else len(FUNNEL_ORDER)
    for gate in FUNNEL_ORDER:
        idx = _GATE_INDEX[gate]
        actual, threshold = _gate_numbers(gate, d)
        producer = _GATE_PRODUCER[gate]
        is_binding = gate == binding_gate
        rejecting = d.rejecting_endpoint if is_binding and producer == "ome_visibility" else None
        reason = binding_reason if is_binding else None

        state: GateState
        if not _gate_applicable(gate, d):
            state = "not_applicable"
        elif is_binding:
            state = "fail"
        elif idx < binding_idx:
            state = "pass"
        elif gate == "actuation_proof" and binding_gate is None and actuation_pass is not None:
            state = "pass" if actuation_pass else "fail"
        else:
            state = "not_evaluated"

        rows.append(
            LadderGate(
                gate=gate,
                state=state,
                actual=actual,
                threshold=threshold,
                rejecting_endpoint=rejecting,
                reason_code=reason,
                producer=producer,  # type: ignore[arg-type]
                is_binding=is_binding,
            )
        )
    return tuple(rows)


def _effective_envelope(d: GroundVisibilityDecisionWire) -> EffectiveEnvelopeFacts:
    configured = d.applied_min_elevation_deg
    gs_for = d.applied_gs_field_of_regard_deg
    for_floor: float | None = None
    if d.applied_gs_boresight_mode == "local_vertical" and gs_for is not None:
        for_floor = 90.0 - gs_for / 2.0

    if for_floor is not None and for_floor > configured:
        effective = for_floor
        binding_source = "field_of_regard"
        dead_knobs: tuple[str, ...] = ("min_elevation_deg",)
    else:
        effective = configured
        binding_source = "min_elevation_mask"
        dead_knobs = ()

    return EffectiveEnvelopeFacts(
        reference_body=d.reference_body,
        configured_min_elevation_deg=configured,
        effective_min_elevation_deg=effective,
        binding_source=binding_source,
        dead_knobs=dead_knobs,
        max_range_km=_min_opt(d.applied_gs_max_range_km, d.applied_sat_max_range_km),
        field_of_regard_deg=gs_for,
        boresight_mode=d.applied_gs_boresight_mode,
        tracking_rate_deg_s=d.applied_gs_max_tracking_rate_deg_s,
    )


def compose_gs_explanation(
    *,
    gs_id: str,
    snapshot: GroundLinkDecisionSnapshot,
    active_pairs: frozenset[Pair],
    actuation_state_by_gs: Mapping[str, ActuationStateName],
) -> DecisionExplanationFacts | None:
    """Compose the explanation facts for one ground station.

    Returns None if the snapshot covers no decision for this GS (the caller
    should 404). Focal-pair precedence: connected > scheduled-but-not-up >
    visible-but-withheld > closest-to-visible rejected.
    """
    decisions = [d for d in snapshot.decisions if gs_id in d.pair]
    if not decisions:
        return None

    unscheduled: dict[Pair, UnscheduledPair] = {
        _ordered_pair(u.pair): u for u in snapshot.unscheduled_pairs if gs_id in u.pair
    }
    visible = [d for d in decisions if d.visible]
    scheduled = [d for d in visible if _ordered_pair(d.pair) not in unscheduled]
    withheld = [d for d in visible if _ordered_pair(d.pair) in unscheduled]

    actuation_state: ActuationStateName = actuation_state_by_gs.get(gs_id, "unknown")
    reference_body = decisions[0].reference_body
    tenant_id = decisions[0].tenant_id

    focal: GroundVisibilityDecisionWire
    binding_gate: FunnelGate | None
    binding_reason: str | None
    viable_withheld: bool
    ome_desired: bool | None
    kernel_up: bool | None
    actuation_pass: bool | None

    if scheduled:
        # OME wants this pair up. Connected if proven up; diverged otherwise.
        focal = scheduled[0]
        kernel_up = _ordered_pair(focal.pair) in active_pairs
        ome_desired = True
        viable_withheld = False
        if kernel_up:
            binding_gate = None
            binding_reason = None
            actuation_pass = True
        else:
            binding_gate = "actuation_proof"
            binding_reason = actuation_state
            actuation_pass = False
    elif withheld:
        # Connectivity was available but policy/capacity withheld it (the message to surface).
        focal = withheld[0]
        u = unscheduled[_ordered_pair(focal.pair)]
        binding_gate = _UNSCHEDULED_GATE.get(u.unscheduled_reason, "handover_policy")
        binding_reason = u.unscheduled_reason
        viable_withheld = True
        ome_desired = False
        kernel_up = _ordered_pair(focal.pair) in active_pairs
        actuation_pass = None
    else:
        # Nothing viable. Lead with the rejected pair closest to coming into view.
        focal = max(decisions, key=lambda d: d.elevation_deg)
        binding_gate = _REJECT_GATE.get(focal.reject_reason)
        binding_reason = focal.reject_reason
        viable_withheld = False
        ome_desired = False
        kernel_up = False
        actuation_pass = None

    ladder = _build_ladder(
        focal,
        binding_gate=binding_gate,
        binding_reason=binding_reason,
        actuation_pass=actuation_pass,
    )

    diverged: bool | None = (ome_desired and not kernel_up) if ome_desired is not None else None

    return DecisionExplanationFacts(
        gs_id=gs_id,
        pair=_ordered_pair(focal.pair),
        node_focus="gs",
        reference_body=reference_body,
        tenant_id=tenant_id,
        binding_gate=binding_gate,
        binding_reason_code=binding_reason,
        rejecting_endpoint=focal.rejecting_endpoint
        if binding_gate in _REJECT_GATE.values()
        else None,
        ladder=ladder,
        envelope=_effective_envelope(focal),
        best_candidate=CandidateFacts(
            pair=_ordered_pair(focal.pair),
            binding_gate=binding_gate,
            binding_reason_code=binding_reason,
            rejecting_endpoint=focal.rejecting_endpoint,
            range_km=focal.range_km,
            elevation_deg=focal.elevation_deg,
            viable_withheld=viable_withheld,
        ),
        actuation=ActuationFacts(
            state=actuation_state,
            ome_desired=ome_desired,
            kernel_up=kernel_up,
            diverged=diverged,
        ),
        sim_time=snapshot.sim_time,
        snapshot_seq=snapshot.snapshot_seq,
        epoch_id=snapshot.epoch_id,
    )
