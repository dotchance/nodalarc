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

# Mirror of the Scheduler actuation state set (lib/nodalarc/models/scheduler_ops.ActuationState).
ActuationStateName = Literal["clean", "actuation_blocked", "kernel_dirty", "unknown"]

NodeFocus = Literal["gs", "sat", "pair"]


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


class EffectiveEnvelopeFacts(BaseModel):
    """The combined envelope a user cannot infer by eye.

    Under a local-vertical boresight, elevation and field-of-regard collapse to a
    single floor: `effective_min_elevation_deg = max(configured_min_elevation,
    90 - FoR/2)`. `binding_source` names which constraint produced the floor and
    `dead_knobs` lists configured values that are non-binding because another
    constraint dominates (e.g. a 25 deg mask under a FoR-derived 30 deg floor).
    """

    model_config = ConfigDict(frozen=True)

    reference_body: str
    configured_min_elevation_deg: float | None
    effective_min_elevation_deg: float | None
    binding_source: str | None
    dead_knobs: tuple[str, ...]
    max_range_km: float | None
    field_of_regard_deg: float | None
    boresight_mode: str | None
    tracking_rate_deg_s: float | None


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


class ActuationFacts(BaseModel):
    """Realization facts. The client decides in_flight vs faulted using the
    C-D actuation-latency bound; the backend only states the raw facts."""

    model_config = ConfigDict(frozen=True)

    state: ActuationStateName
    ome_desired: bool | None
    kernel_up: bool | None
    diverged: bool | None


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
