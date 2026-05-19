# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""TO link event models — all frozen (immutable after creation).

Published via NATS JetStream.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class LinkDecisionProvenance(BaseModel):
    """Physics and substrate inputs that explain an applied link decision.

    TODO(trust-gap-closure#7): Extend to full audit-grade provenance.
    Currently captures geometry and substrate values — enough to answer
    "what latency?" but not "why does this link exist?" For commercial
    audit, every LinkUp should also carry: terminal_role_a, terminal_role_b,
    propagator_id, visibility_checks_applied (tuple of check names),
    scheduling policy and score, dispatch_mode ("bbm" | "mbb_phase2" |
    "mbb_phase3"), epoch_id, and snapshot_seq. Every LinkDown should carry
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
