"""VS-API state models — all frozen (immutable after creation).

StateSnapshot is the complete payload sent over WebSocket at ~1Hz.
"""

from datetime import datetime

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
    routing_area: str | None
    neighbor_count: int
    isl_count: int
    gnd_count: int
    prefix: str | None = None  # Ground station advertised prefix


class LinkState(BaseModel):
    """State of a single link between two nodes."""

    model_config = ConfigDict(frozen=True)

    node_a: str
    node_b: str
    state: str  # "active" or "inactive" (Phase 1)
    link_type: str | None  # intra_plane_isl, cross_plane_isl, ground_uplink, ground_downlink
    link_reason: str | None
    latency_ms: float
    bandwidth_mbps: float
    range_km: float
    traffic_load_pct: float | None  # None = no probe data (distinct from 0)


class TracedPath(BaseModel):
    """Forwarding path trace for a traffic flow."""

    model_config = ConfigDict(frozen=True)

    flow_id: str
    src_node: str
    dst_node: str
    hops: list[str]


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


class StateSnapshot(BaseModel):
    """Complete constellation state sent via WebSocket at ~1Hz.

    Full snapshots only — no deltas. Drop intermediate frames
    if behind, never queue.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    schema_version: int  # Always 1 for Phase 1
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
