# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""MI measurement event models — all frozen (immutable after creation).

Published on ZeroMQ port 5562 (MI_EVENTS_PORT).
Convergence gate uses port 5563 (MI_CONVERGENCE_GATE_PORT) REQ/REP.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from nodalarc.models.link_events import LinkDown, LinkUp


class ConvergenceRequest(BaseModel):
    """TO → MI: request convergence measurement after a link event."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    link_event: LinkUp | LinkDown


class ConvergenceResult(BaseModel):
    """MI → TO: convergence measurement result."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    converged: bool
    duration_ms: float
    packets_lost: int
    packets_sent: int
    sim_time_start: datetime
    sim_time_end: datetime
    wall_time_start: datetime
    wall_time_end: datetime
    triggering_link_event_id: int | None = None


class TraceRequest(BaseModel):
    """VS-API → MI: request a forwarding path trace between two nodes."""

    model_config = ConfigDict(frozen=True)

    src_node: str
    dst_node: str


class TraceResponse(BaseModel):
    """MI → VS-API: forwarding path trace result."""

    model_config = ConfigDict(frozen=True)

    src_node: str
    dst_node: str
    hops: list[str]
    success: bool
    error: str | None = None


class ProbeResult(BaseModel):
    """Result of a single probe measurement burst."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    flow_id: str
    src_node: str
    dst_node: str
    packets_sent: int
    packets_received: int
    latency_min_ms: float
    latency_max_ms: float
    latency_avg_ms: float
    jitter_ms: float


class AdapterEvent(BaseModel):
    """Protocol adapter event (FRR IS-IS, OSPF, etc.).

    event_data is the one permitted Any use — contains
    adapter-specific data with source recorded for debugging.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    node_id: str
    event_type: str  # adjacency_up, adjacency_down, spf_start, spf_end, lsp_flood, etc.
    event_data: dict[str, Any]
