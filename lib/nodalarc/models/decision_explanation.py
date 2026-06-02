# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Structured decision-explanation FACTS for the link-explainability UX.

VS-API composes these facts from committed OME/Scheduler truth and the client
renders them. Deliberate split of responsibility:

  - The backend emits FACTS: the funnel ladder (per-gate state + numbers +
    rejecting endpoint + producer), the resolved binding gate, the effective
    envelope, the best-candidate facts, and the actuation/divergence facts.
  - The client owns MEANING: family, severity, human label/sentence, tone, and
    levers all come from the single client-side reason-taxonomy registry.

The backend therefore carries NO family vocabulary and NO human text — that
keeps the Expected/Faulted color law and the explanation strings single-source
on the client, with nothing to drift across the language boundary.

See specs/plans/link-explainability-ux.md, "Data Contracts".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from nodalarc.models.link_decisions import GroundVisibilityRejectingEndpoint
from nodalarc.models.scheduler_ops import ActuationState

# The canonical decision funnel, in order. A pair stops at exactly one binding gate.
FunnelGate = Literal[
    "line_of_sight",
    "range",
    "elevation_mask",
    "field_of_regard",
    "tracking_rate",
    "selection_policy",
    "capacity",
    "handover_policy",
    "actuation_proof",
]

GateState = Literal["pass", "fail", "not_evaluated", "not_applicable"]

# Which source-of-truth component owns a gate's verdict.
ExplanationProducer = Literal["ome_visibility", "ome_allocator", "scheduler", "node_agent"]

NodeFocus = Literal["gs", "sat", "pair"]
DecisionSampleState = Literal["scheduled", "eligible_unselected", "expected_no_link"]


class LadderGate(BaseModel):
    """One row of the decision funnel for a focal pair.

    `actual`/`threshold` are populated where the value is numeric and available
    in the committed decision; `None` where not applicable or not carried by the
    source decision (e.g. the required slew rate is not on the wire). The client
    renders the reason_code through the registry — this row carries no human text.
    """

    model_config = ConfigDict(frozen=True)

    gate: FunnelGate
    state: GateState
    actual: float | None
    threshold: float | None
    rejecting_endpoint: GroundVisibilityRejectingEndpoint | None
    reason_code: str | None
    producer: ExplanationProducer
    is_binding: bool


class EnvelopeEndpoint(BaseModel):
    """One terminal's raw pointing/range constraints.

    Carried for BOTH endpoints so the "what to change" lever targets the terminal that
    actually binds — the satellite terminal can be the limiting constraint, not always
    the ground station. A satellite's nadir boresight does not collapse to a ground
    elevation floor, so its envelope is shown as the raw pointing/range constraints
    rather than pretending it is one scalar (see the UX plan, Effective Envelope).
    """

    model_config = ConfigDict(frozen=True)

    node_role: Literal["ground", "satellite"]
    terminal_profile: str | None
    boresight_mode: str | None
    field_of_regard_deg: float | None
    max_tracking_rate_deg_s: float | None
    max_range_km: float | None


class EffectiveEnvelopeFacts(BaseModel):
    """The combined envelope a user cannot infer by eye.

    Under a local-vertical GROUND boresight, elevation and field-of-regard collapse to a
    single floor: `effective_min_elevation_deg = max(configured_min_elevation,
    90 - FoR/2)`. `binding_source` names which constraint produced the floor and
    `dead_knobs` lists configured values that are non-binding because another constraint
    dominates (e.g. a 25 deg mask under a FoR-derived 30 deg floor). The collapse is
    ground-specific; `ground`/`satellite` carry each terminal's raw constraints and
    `binding_endpoint` names which terminal the binding gate rejected at, so a hint
    points at the terminal that actually limits the link.
    """

    model_config = ConfigDict(frozen=True)

    reference_body: str
    configured_min_elevation_deg: float | None
    effective_min_elevation_deg: float | None
    binding_source: str | None
    dead_knobs: tuple[str, ...]
    max_range_km: float | None
    ground: EnvelopeEndpoint
    satellite: EnvelopeEndpoint
    binding_endpoint: GroundVisibilityRejectingEndpoint


class CandidateFacts(BaseModel):
    """The pair the card leads with.

    Ordering is by meaning, resolved by the composer: a viable-but-withheld pair
    (`viable_withheld=True` — cleared physics/terminal gates, withheld by
    policy/capacity) dominates a physics near-miss. Only when no viable pair
    exists is the closest-to-visible rejected pair chosen.
    """

    model_config = ConfigDict(frozen=True)

    pair: tuple[str, str]
    binding_gate: FunnelGate | None
    binding_reason_code: str | None
    rejecting_endpoint: GroundVisibilityRejectingEndpoint | None
    range_km: float | None
    elevation_deg: float | None
    viable_withheld: bool


class PendingActuation(BaseModel):
    """Divergence timing for one desired-but-not-kernel-actual pair, fed to the composer.

    The Scheduler OWNS the origin: ``pending_since`` is the wall-clock instant it folded
    the pair into its effective-desired set without a verified kernel entry, recovered by
    VS-API from the retained ``ActualLinkSnapshot`` (so it survives a VS-API restart —
    the bug the old VS-API-observed onset had). ``actuation_elapsed_ms`` is VS-API's
    skew-free age of that divergence (a same-clock Scheduler delta plus a same-clock
    VS-API receive-to-now delta), so the composer never reads a clock and the bound is
    compared against the actuation window it was justified for, not an end-to-end one."""

    model_config = ConfigDict(frozen=True)

    pending_since: datetime
    actuation_elapsed_ms: float


class ActuationFacts(BaseModel):
    """Realization facts. The client decides in_flight vs faulted by comparing
    ``actuation_elapsed_ms`` to ``fault_after_ms`` — the backend states the raw facts
    and carries the contract bounds; it never assigns the family.

    ``diverged_since`` is the wall-clock instant (UTC) the SCHEDULER folded this pair into
    its desired set without kernel proof (recovered from the retained ActualLinkSnapshot,
    not VS-API's observation time); ``actuation_elapsed_ms`` is the skew-free age of that
    divergence at compose time. Both are ``None`` when the pair is not diverged, or when
    the Scheduler's pending clock has not yet reached VS-API. ``expected_latency_ms``/
    ``fault_after_ms`` are the ``simulation.actuation`` contract, wall-clock not sim-time:
    a divergence younger than ``fault_after_ms`` is calm in_flight, at/older is faulted;
    the deadline a UI shows is ``diverged_since + fault_after_ms``."""

    model_config = ConfigDict(frozen=True)

    state: ActuationState
    ome_desired: bool | None
    kernel_up: bool | None
    diverged: bool | None
    diverged_since: datetime | None = None
    actuation_elapsed_ms: float | None = None
    expected_latency_ms: float | None = None
    fault_after_ms: float | None = None


class DecisionExplanationFacts(BaseModel):
    """The structured facts a single explainability surface consumes.

    Carries no family/label/sentence — the client registry adds those. The
    `pair` is the focal pair (connected, else best candidate). Provenance ties
    the explanation to the committed snapshot it was derived from.
    """

    model_config = ConfigDict(frozen=True)

    gs_id: str
    pair: tuple[str, str] | None
    node_focus: NodeFocus
    reference_body: str
    tenant_id: str
    binding_gate: FunnelGate | None
    binding_reason_code: str | None
    rejecting_endpoint: GroundVisibilityRejectingEndpoint | None
    ladder: tuple[LadderGate, ...]
    envelope: EffectiveEnvelopeFacts | None
    best_candidate: CandidateFacts | None
    actuation: ActuationFacts | None
    sim_time: datetime
    snapshot_seq: int
    epoch_id: int


class GsDecisionTimelineSample(BaseModel):
    """One bounded observed decision sample for a ground station.

    This is not historical playback and does not try to reconstruct old full
    snapshots. VS-API samples the committed OME decision surface as it arrives
    and keeps a bounded in-memory window per GS so the UI can answer "what has
    this station been doing recently?" without polling the full GS×sat matrix.
    """

    model_config = ConfigDict(frozen=True)

    gs_id: str
    sim_time: datetime
    snapshot_seq: int
    epoch_id: int
    state: DecisionSampleState
    pair: tuple[str, str] | None
    binding_gate: FunnelGate | None
    reason_code: str | None
    rejecting_endpoint: GroundVisibilityRejectingEndpoint | None
    range_km: float | None
    elevation_deg: float | None


class GsDecisionReasonCount(BaseModel):
    """Aggregated count for a reason/state in the sampled window."""

    model_config = ConfigDict(frozen=True)

    state: DecisionSampleState
    reason_code: str | None
    count: int


class GsDecisionTimelineFacts(BaseModel):
    """Bounded observed window for one GS, plus diagnosis roll-up facts."""

    model_config = ConfigDict(frozen=True)

    gs_id: str
    sample_count: int
    window_started_sim_time: datetime | None
    window_ended_sim_time: datetime | None
    samples: tuple[GsDecisionTimelineSample, ...]
    reason_counts: tuple[GsDecisionReasonCount, ...]
