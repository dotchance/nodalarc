# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""TO link event models — all frozen (immutable after creation).

Published via NATS JetStream.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# The authoritative vocabulary of LinkUp/LinkDown `reason` codes (the link-lifecycle vocabulary,
# distinct from the ground-decision funnel reasons in link_decisions.py). Kept as a constant — not
# a Literal on the wire field — so a live Scheduler emitting a not-yet-listed code degrades to the
# raw code in the UI rather than crashing validation; the frontend mirrors this set in
# explain/linkEvents.ts (LINK_EVENT_REASONS) and the cross-language contract test asserts they match
# so the two vocabularies cannot silently drift.
LINK_EVENT_REASONS: frozenset[str] = frozenset(
    {
        # up
        "vis_gained",
        "gs_above_horizon",
        "scenario_inject_up",
        "scenario_reconciliation",
        # down
        "vis_lost",
        "gs_below_horizon",
        "tracking_exceeded",
        "terminal_exhausted",
        "scenario_inject_down",
        "satellite_loss",
    }
)


class LinkDecisionProvenance(BaseModel):
    """Physics and substrate inputs that explain an applied link decision.

    TODO(trust-gap-closure#7): Extend to full audit-grade provenance.
    Currently captures geometry and substrate values — enough to answer
    "what latency?" but not "why does this link exist?" For commercial
    audit, every LinkUp should also carry: terminal_role_a, terminal_role_b,
    propagator_id, visibility_checks_applied (tuple of check names),
    scheduling policy and score, dispatch stage, epoch_id, and snapshot_seq.
    Every LinkDown should carry
    rejection_reason. This makes each event self-contained — an auditor
    can reconstruct the decision from the event alone.
    """

    model_config = ConfigDict(frozen=True)

    geometry_authority: Literal["ome"] = "ome"
    authority_source: str
    authority_sim_time: datetime
    authority_sequence: int | None
    authority_age_ms: float
    range_km: float
    orbital_one_way_ms: float
    substrate_rtt_ms: float
    substrate_one_way_ms: float
    netem_one_way_ms: float
    rtt_to_one_way_policy: str


class LinkUp(BaseModel):
    """Link came up between two nodes."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    node_a: str
    node_b: str
    interface_a: str
    interface_b: str
    latency_ms: float
    bandwidth_mbps: float
    range_km: float
    reason: str  # vis_gained, gs_above_horizon, scenario_inject_up, scenario_reconciliation
    link_type: Literal["isl", "ground"]
    provenance: LinkDecisionProvenance | None = None


class LinkDown(BaseModel):
    """Link went down between two nodes."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    node_a: str
    node_b: str
    interface_a: str
    interface_b: str
    reason: str  # vis_lost, tracking_exceeded, terminal_exhausted, gs_below_horizon, scenario_inject_down, scenario_reconciliation, satellite_loss
    link_type: Literal["isl", "ground"]


class LatencyUpdate(BaseModel):
    """Latency changed on an active link (range-dependent)."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    node_a: str
    node_b: str
    latency_ms: float
    range_km: float
    provenance: LinkDecisionProvenance | None = None
