# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Canonical path model for the NodalArc platform.

Used by NodalPath (derived paths from MPLS forwarding tables),
measurement infrastructure (probed paths from live packet injection),
VS-API (unified path endpoint), and VF (path overlay rendering).

All path producers emit PathResult. All path consumers accept PathResult.
The method field tells the consumer how the path was computed.
"""

from __future__ import annotations

from pydantic import BaseModel


class PathHop(BaseModel, frozen=True):
    """A single hop in a computed or probed path."""

    node_id: str  # e.g., "sat-P02S05" or "gs-ashburn"
    node_type: str  # "satellite" or "ground_station"
    sid: int | None = None  # node's SR SID (used by label computation)
    in_label: int | None = None  # MPLS label arriving at this node (None at ingress)
    out_label: int | None = None  # MPLS label leaving this node (None at egress/pop)
    action: str | None = None  # "push", "swap", "pop", or None at src/dst
    in_interface: str | None = None  # interface the packet arrives on (None at ingress)
    out_interface: str | None = None  # interface used to reach next hop
    latency_to_next_ms: float | None = None  # one-way propagation delay to next hop (derived paths)
    rtt_ms: float | None = None  # round-trip time from src to this hop (traceroute)
    responding_ip: str | None = None  # IP from traceroute output (debugging aid)


class PathResult(BaseModel, frozen=True):
    """A complete path from source to destination.

    Produced by NodalPath (derived) or measurement infrastructure (probed).
    Consumed by VS-API, VF, and NodalPath console.
    """

    src: str  # source node_id (ground station)
    dst: str  # destination node_id (ground station)
    hops: list[PathHop]  # ordered list from src to dst inclusive
    total_latency_ms: float  # sum of latency_to_next_ms across all hops
    method: str  # "derived" | "probed"
    sim_time: str  # ISO 8601 — when this path is valid
    topology_state_id: str  # links to AlmanacEntry
    reachable: bool  # False if no path exists
    unreachable_reason: str | None = None  # e.g., "no ingress rule for dst_prefix"
    pipe_mode: bool = False  # True when MPLS pipe mode hides intermediate hops
    raw_output: str | None = None  # traceroute stdout for debugging


class TracepathHop(BaseModel, frozen=True):
    """A single hop from tracepath output."""

    hop_num: int
    ip: str | None = None
    rtt_ms: float | None = None
    asymm: int | None = None
    pmtu: int | None = None
    reached: bool = False


class TracepathResult(BaseModel, frozen=True):
    """Parsed result from tracepath -n -b output."""

    hops: list[TracepathHop]
    pmtu: int | None = None
    forward_hops: int | None = None
    return_hops: int | None = None
    raw_output: str = ""


class LiveTraceLink(BaseModel, frozen=True):
    """A single link in a live trace with kernel netem delay."""

    from_node: str
    to_node: str
    interface: str
    netem_delay_ms: float | None = None
    link_type: str | None = None


class LiveTraceDirection(BaseModel, frozen=True):
    """One direction of a bidirectional live trace."""

    hops: list[PathHop]
    links: list[LiveTraceLink]
    rtt_ms: float
    asymmetry_detected: bool
    pmtu: int | None = None
    raw_output: str | None = None


class LiveTraceResult(BaseModel, frozen=True):
    """Complete bidirectional live trace result."""

    src: str
    dst: str
    forward: LiveTraceDirection
    reverse: LiveTraceDirection
    traced_at: str
    sim_time: str
    topology_state_id: str
    path_valid_until: str | None = None
    path_valid_seconds: float | None = None
    method: str
    trace_mode: str


class PathQuery(BaseModel):
    """A request to compute or retrieve a path."""

    src: str  # source node_id
    dst: str  # destination node_id
    sim_time: str | None = None  # None = current live state
