# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""VS-API state models — all frozen (immutable after creation).

StateSnapshot is the complete payload sent over WebSocket at ~1Hz.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class NodeState(BaseModel):
    """State of a single node in the constellation."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    node_type: str  # "satellite" or "ground_station"
    lat_deg: float
    lon_deg: float
    alt_km: float
    vel_x_km_s: float | None  # None for ground stations
    vel_y_km_s: float | None
    vel_z_km_s: float | None
    plane: int | None  # None for ground stations
    slot: int | None
    routing_area: str | None = None
    neighbor_count: int = 0
    isl_count: int = 0
    gnd_count: int = 0
    prefix: str | None = None  # Ground station advertised prefix
    min_elevation_deg: float | None = None  # Ground stations only
    beam_falloff_exponent: float | None = None  # Satellites only, from satellite type


class LinkState(BaseModel):
    """State of a single link between two nodes."""

    model_config = ConfigDict(frozen=True)

    node_a: str
    node_b: str
    state: str  # "active" or "inactive"
    link_type: str | None  # intra_plane_isl, cross_plane_isl, ground_uplink, ground_downlink
    link_reason: str | None
    latency_ms: float
    bandwidth_mbps: float
    range_km: float
    traffic_load_pct: float | None  # None = no probe data (distinct from 0)
    interface_a: str = ""
    interface_b: str = ""


class LinkDecisionTrace(BaseModel):
    """Why an active link exists and which authority produced its values."""

    model_config = ConfigDict(frozen=True)

    node_a: str
    node_b: str
    link_type: str
    state: str
    interface_a: str
    interface_b: str
    reason: str | None = None
    geometry_authority: Literal["ome"]
    authority_source: str
    authority_sim_time: datetime
    authority_sequence: int | None = None
    authority_age_ms: float
    range_km: float
    orbital_one_way_ms: float
    substrate_rtt_ms: float | None
    substrate_one_way_ms: float | None
    netem_one_way_ms: float | None
    rtt_to_one_way_policy: str | None


class TracedPath(BaseModel):
    """Forwarding path trace for a traffic flow."""

    model_config = ConfigDict(frozen=True)

    flow_id: str
    src_node: str
    dst_node: str
    hops: list[str]
    reverse_hops: list[str] = []
    hop_rtts: list[float | None] = []
    reverse_hop_rtts: list[float | None] = []
    rtt_ms: float = 0.0
    reverse_rtt_ms: float = 0.0
    asymmetry_detected: bool = False
    method: str = "tracepath"
    path_valid_until: str | None = None
    path_valid_seconds: float | None = None
    traced_at: str | None = None


class NetworkHealth(BaseModel):
    """Overall network health status."""

    model_config = ConfigDict(frozen=True)

    status: str  # "converged", "converging", or "degraded"
    converging_since_ms: int | None
    unreachable_flows: int
    last_convergence_ms: float | None


class ActiveFlow(BaseModel):
    """Active traffic flow configuration."""

    model_config = ConfigDict(frozen=True)

    flow_id: str
    src_node: str
    dst_node: str
    protocol: str  # "udp" or "tcp"
    probe_type: str  # "continuous" or "burst"


class RecentEvent(BaseModel):
    """Recent event for the VF event log."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    node_id: str
    event_type: str
    summary: str


class AlmanacState(BaseModel):
    """NodalPath almanac push tracking state."""

    model_config = ConfigDict(frozen=True)

    last_topology_state_id: str | None = None
    last_push_sim_time: str | None = None
    last_push_wall_time: float | None = None
    nodes_succeeded: int = 0
    nodes_failed: int = 0
    deviation_count: int = 0
    recomputation_count: int = 0
    nodalpath_active: bool = False


class StateSnapshot(BaseModel):
    """Complete constellation state sent via WebSocket at ~1Hz.

    Full snapshots only — no deltas. Drop intermediate frames
    if behind, never queue.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    schema_version: int  # Always 1
    session_id: str
    nodes: list[NodeState]
    links: list[LinkState]
    traced_paths: list[TracedPath]
    active_flows: list[ActiveFlow]
    recent_events: list[RecentEvent]
    network_health: NetworkHealth
    routing_stack: str | None = None
    constellation_name: str | None = None
    session_status: str | None = None  # "ready", "switching", "error", "idle"
    session_status_detail: str | None = None
    playback_paused: bool = False
    playback_speed: float = 1.0
    stale: bool = False
