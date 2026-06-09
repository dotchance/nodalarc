# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""VS-API state models — all frozen (immutable after creation).

StateSnapshot is the complete payload sent over WebSocket at ~1Hz.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nodalarc.body_frames import SupportedSurfaceBody
from nodalarc.models.scheduler_ops import ActuationState


class NodeAddress(BaseModel):
    """Configured network identity/address associated with one node."""

    model_config = ConfigDict(frozen=True)

    purpose: Literal["router_loopback", "site_interface", "site_prefix"]
    family: Literal["ipv4", "ipv6"]
    address: str
    interface: str | None = None
    metric: int | None = None


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
    addresses: tuple[NodeAddress, ...] = ()
    min_elevation_deg: float | None = None  # Ground stations only
    beam_falloff_exponent: float | None = None  # Satellites only, from satellite type
    # Which celestial body this node is anchored to. Required so render and API
    # consumers never infer Earth from an omitted field.
    reference_body: SupportedSurfaceBody
    frame_id: str
    tenant_id: str = "default"
    segment_id: str | None = None
    local_node_id: str | None = None
    namespace: str | None = None
    tags: tuple[str, ...] = ()


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
    link_rule_id: str | None = None
    topology_mode: str | None = None
    endpoint_segments: tuple[str, str] | None = None
    scheduling_state: str = "active"
    teardown_remaining_ticks: int | None = None
    successor_pair: tuple[str, str] | None = None


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
    link_rule_id: str | None = None
    topology_mode: str | None = None
    endpoint_segments: tuple[str, str] | None = None


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


class ActuationNotice(BaseModel):
    """User-visible Scheduler actuation problem for one ground station."""

    model_config = ConfigDict(frozen=True)

    gs_id: str
    actuation_state: ActuationState
    reason_code: str
    message: str
    since: str | None = None
    blocking_new_ground_link_up: bool
    affected_pairs: list[list[str]] = Field(default_factory=list)
    desired_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    actual_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    ome_visible_scheduled_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    recovery_status: dict = Field(default_factory=dict)
    last_event: dict = Field(default_factory=dict)


class ActuationHealthGroundStation(BaseModel):
    """Latest actuation state for one GS on one Scheduler instance."""

    model_config = ConfigDict(frozen=True)

    gs_id: str
    actuation_state: ActuationState
    since: str | None = None
    reason_code: str | None = None
    blocking_new_ground_link_up: bool
    recovery_status: dict = Field(default_factory=dict)
    last_event: dict = Field(default_factory=dict)


class ActuationHealthInstance(BaseModel):
    """Aggregated actuation health for one Scheduler instance."""

    model_config = ConfigDict(frozen=True)

    scheduler_instance_id: str
    hostname: str
    status: str
    ground_stations: list[ActuationHealthGroundStation] = Field(default_factory=list)


class ActuationHealth(BaseModel):
    """Session-level Scheduler actuation health."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    wiring_generation: str
    scheduler_instances: list[ActuationHealthInstance] = Field(default_factory=list)


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
    # Scheduler-verified kernel-actual pairs (ordered [a, b]) recovered from the retained
    # ActualLinkSnapshot — the kernel-PROVEN link set, distinct from `links` (OME's
    # admin/carrier model). The globe renders a proven link as a solid beam and an
    # OME-desired-but-not-proven link dimmed, so a beam never reads connected while the
    # decision card says in_flight/faulted. Empty until the first ActualLinkSnapshot lands
    # (honest: nothing proven), never a masked connected.
    kernel_actual_pairs: list[list[str]] = Field(default_factory=list)
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
    actuation_notices: list[ActuationNotice] = Field(default_factory=list)
    ome_lifecycle_notices: list[dict[str, Any]] = Field(default_factory=list)
    actuation_health: ActuationHealth | None = None
