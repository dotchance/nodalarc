# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""TO link event models — all frozen (immutable after creation).

Published via NATS JetStream.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The `reason` of the LinkActive rows a recorded history writes, never sent on the wire: the links
# the kernel had up when recording began, or when it resumed after VS-API restarted.
HistoryLinkReason = Literal["recording_start", "recording_resumed"]


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
    # OME's one-way light time each direction (node_a to node_b, node_b to node_a), the
    # substrate's measured one-way share, and the delay each direction's egress commands.
    orbital_a_to_b_ms: float
    orbital_b_to_a_ms: float
    substrate_rtt_ms: float
    substrate_one_way_ms: float
    netem_a_to_b_ms: float
    netem_b_to_a_ms: float
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
    latency_a_to_b_ms: float
    latency_b_to_a_ms: float
    range_km: float
    reason: str  # vis_gained, gs_above_horizon, scenario_inject_up, scenario_reconciliation
    link_type: Literal["isl", "ground"]
    provenance: LinkDecisionProvenance | None = None
    # The contact that opened, on a fixed long-delay link (``link_state.contact_id``).
    contact_id: str | None = None


# Why a link end's contact ended. A local link closes when its contact ends ("end_of_contact").
# A long-delay end closes when the last packet sent before its peer stopped admitting has had the
# light time of the direction it receives ("drain_deadline"); a seek or a scenario command closes
# it before then. A long-delay end whose next contact opens while it still drains keeps its
# queue for that contact ("reopened"): nothing is discarded, and what the end counted until the
# new contact was prepared is the ended contact's. A long-delay end whose terminal OME points at
# another peer before its last packet arrives closes then ("retargeted"): what it still held can
# no longer be received.
type LinkEndCloseCause = Literal[
    "end_of_contact", "drain_deadline", "seek", "scenario", "reopened", "retargeted"
]


class LinkEndLoss(BaseModel):
    """What one link end lost during its contact, from the Node Agent's counts at each stage.

    ``discarded_at_close`` is what the end's delay queue still held when it closed. With
    ``discarded_exact`` it is a count: admission had stopped at both ends, so nothing entered the
    queue between the read and the reset. Without it, it is a sample read just before the close
    of a local link, which keeps admitting until then. ``queue_dropped`` is what the end's delay
    queue dropped at its limit, and ``transmit_dropped`` what its transmit queue dropped at its
    limit (both substrate resource loss). On a long-delay end, ``refused_before_open`` is what
    its stopped admission refused while the contact was being prepared, ``untransmitted_at_stop``
    what still waited to be transmitted when the contact ended (purged at the stop, never sent),
    and ``admission_taken`` what its stopped admission refused after the stop; all three are 0 on
    a local link."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    interface: str
    cause: LinkEndCloseCause
    refused_before_open: int = Field(ge=0)
    untransmitted_at_stop: int = Field(ge=0)
    admission_taken: int = Field(ge=0)
    transmit_dropped: int = Field(ge=0)
    queue_dropped: int = Field(ge=0)
    discarded_at_close: int = Field(ge=0)
    discarded_exact: bool


class LinkDown(BaseModel):
    """Link went down between two nodes: every end of its contact closed."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    node_a: str
    node_b: str
    interface_a: str
    interface_b: str
    reason: str  # vis_lost, tracking_exceeded, terminal_exhausted, gs_below_horizon, scenario_inject_down, scenario_reconciliation, satellite_loss, seek
    link_type: Literal["isl", "ground"]
    # The contact that closed, on a fixed long-delay link (``link_state.contact_id``).
    contact_id: str | None = None
    # One entry per closed end that reported its queues.
    losses: tuple[LinkEndLoss, ...] = ()


class LatencyUpdate(BaseModel):
    """Latency changed on an active link (range-dependent)."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    node_a: str
    node_b: str
    latency_a_to_b_ms: float
    latency_b_to_a_ms: float
    range_km: float
    provenance: LinkDecisionProvenance | None = None
