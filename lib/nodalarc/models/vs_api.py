# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""VS-API state models — all frozen (immutable after creation).

StateSnapshot is the complete payload sent over WebSocket at ~1Hz. StateFeedFrame is the
union of every message the state socket sends, discriminated on ``msg_type``.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.body_frames import SupportedSurfaceBody
from nodalarc.model_validation import AddressFamily
from nodalarc.models.events import OpsEvent, ServiceSource, SessionEphemeris
from nodalarc.models.link_rules import TopologyMode
from nodalarc.models.link_state import SchedulingState
from nodalarc.models.resolved_session import NodeKind, NodeRole
from nodalarc.models.scheduler_ops import ActuationState, SchedulerOpsCode
from nodalarc.models.segment_session import LINK_STATE_PROTOCOLS, RoutingProtocol


class NodeAddress(BaseModel):
    """Configured network identity/address associated with one node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    purpose: Literal["router_loopback", "site_interface", "site_prefix"]
    family: AddressFamily
    address: str
    interface: str | None = None
    metric: int | None = None


class NodeInstanceInterface(BaseModel):
    """One interface a node runs a routing instance on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    # The interface's OSPF area. IS-IS and static interfaces carry no area.
    area_id: str | None


class NodeRoutingInstance(BaseModel):
    """A node's participation in one routing instance (a ``routing.domains`` entry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain_id: str
    protocol: RoutingProtocol
    # IS-IS: the router's area addresses. OSPF: every area it has an
    # interface in, the loopback's included. Static: none.
    areas: tuple[str, ...]
    interfaces: tuple[NodeInstanceInterface, ...]
    # An area border router of the instance.
    area_border: bool
    # Redistributes routing-boundary exports into the instance.
    as_boundary: bool

    @model_validator(mode="after")
    def _areas_follow_the_protocol(self) -> NodeRoutingInstance:
        is_ospf = self.protocol == "ospf"
        if any((interface.area_id is not None) != is_ospf for interface in self.interfaces):
            raise ValueError(
                f"{self.protocol} instance {self.domain_id!r} interfaces carry areas exactly "
                "when the protocol is OSPF"
            )
        if self.protocol not in LINK_STATE_PROTOCOLS and (
            self.areas or self.area_border or self.as_boundary
        ):
            raise ValueError(
                f"{self.protocol} instance {self.domain_id!r} has no areas, area border "
                "routers or redistribution"
            )
        return self


class NodeState(BaseModel):
    """State of a single node in the constellation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    node_type: NodeKind
    lat_deg: float
    lon_deg: float
    alt_km: float
    vel_x_km_s: float | None  # None for ground stations
    vel_y_km_s: float | None
    vel_z_km_s: float | None
    plane: int | None  # None for ground stations
    slot: int | None
    # Every routing instance the node participates in, in declared order;
    # empty for a node in none.
    routing_instances: tuple[NodeRoutingInstance, ...]
    role: NodeRole
    isl_count: int = 0
    gnd_count: int = 0
    prefix: str | None = None  # Ground station advertised prefix
    addresses: tuple[NodeAddress, ...] = ()
    min_elevation_deg: float | None = None  # Ground stations only
    beam_falloff_exponent: float | None = None  # Satellites only, from satellite type
    # Which celestial body this node is anchored to. Required so render and API
    # consumers never infer Earth from an omitted field.
    reference_body: SupportedSurfaceBody
    frame_id: SupportedSurfaceBody
    tenant_id: str
    segment_id: str
    local_node_id: str
    namespace: str
    tags: tuple[str, ...] = ()


# Where VS-API's session manager stands; see SessionManager.
SessionStatus = Literal["idle", "switching", "wiring", "ready", "error"]

# The kinds VS-API reports for a link: OME and the Scheduler carry "isl" and
# "ground"; VS-API refines an ISL by its resolved rule (see _derive_link_type).
PublicLinkType = Literal["ground", "isl", "inter_constellation", "inter_body_relay"]


class LinkState(BaseModel):
    """State of a single link between two nodes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_a: str
    node_b: str
    # VS-API lists only active links; a link that goes down leaves the list.
    state: Literal["active"]
    link_type: PublicLinkType
    link_reason: str | None
    latency_ms: float
    # Each end's own terminal rates: node_a's terminal sends at transmit_mbps_a
    # and takes in at receive_mbps_a; node_b's at transmit_mbps_b and
    # receive_mbps_b. A direction carries no more than its sender transmits or
    # its receiver takes in.
    transmit_mbps_a: float
    receive_mbps_a: float
    transmit_mbps_b: float
    receive_mbps_b: float
    range_km: float
    traffic_load_pct: float | None  # None = no probe data (distinct from 0)
    interface_a: str = ""
    interface_b: str = ""
    link_rule_id: str | None = None
    topology_mode: TopologyMode | None = None
    endpoint_segments: tuple[str, str] | None = None
    scheduling_state: SchedulingState = "active"
    teardown_remaining_ticks: int | None = None
    successor_pair: tuple[str, str] | None = None


class LinkDecisionTrace(BaseModel):
    """Why an active link exists and which authority produced its values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_a: str
    node_b: str
    link_type: PublicLinkType
    state: Literal["active"]
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
    topology_mode: TopologyMode | None = None
    endpoint_segments: tuple[str, str] | None = None


TraceState = Literal["running", "reached", "not_reached", "failed"]
# Why a continuous trace stopped on its own: its time limit, or an internal
# error in the trace loop.
TraceStopReason = Literal["time_limit", "internal_error"]


class TracedPath(BaseModel):
    """The live measured path between two nodes: one traceroute per direction.

    Each direction lists what answered at every hop, in hop order, after the
    node the trace starts from: a node id when the resolver assigned the
    answering address to that node, the address itself when no node owns it,
    and ``*`` when nothing answered. ``state`` says how the trace ended. ``rtt_ms`` is the
    destination's round trip and exists only when the destination answered;
    ``error`` exists only when the trace could not run. ``asymmetry_detected``
    exists only when both directions reached their destination and every hop
    between the ends answered from a node's address. ``tracing`` is false once
    the trace loop has stopped; the last result then stays until the trace is
    stopped or restarted. ``stop_reason`` says why a continuous trace stopped
    on its own; a one-shot trace and a running trace have none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # True for the session's continuous trace, false for a one-shot trace.
    continuous_trace: bool
    src_node: str
    dst_node: str
    hops: list[str]
    hop_rtts: list[float | None]
    state: TraceState
    rtt_ms: float | None
    error: str | None
    reverse_hops: list[str]
    reverse_hop_rtts: list[float | None]
    reverse_state: TraceState
    reverse_rtt_ms: float | None
    reverse_error: str | None
    asymmetry_detected: bool | None
    tracing: bool
    traced_at: str
    sim_time: str
    stop_reason: TraceStopReason | None = None

    @model_validator(mode="after")
    def _outcomes_match_their_states(self) -> TracedPath:
        for label, hops, rtts, state, rtt_ms, error in (
            ("forward", self.hops, self.hop_rtts, self.state, self.rtt_ms, self.error),
            (
                "reverse",
                self.reverse_hops,
                self.reverse_hop_rtts,
                self.reverse_state,
                self.reverse_rtt_ms,
                self.reverse_error,
            ),
        ):
            if len(hops) != len(rtts):
                raise ValueError(f"{label} trace has {len(hops)} hops and {len(rtts)} round trips")
            if (rtt_ms is not None) != (state == "reached"):
                raise ValueError(f"{label} trace rtt_ms exists only when it reached")
            if (error is not None) != (state == "failed"):
                raise ValueError(f"{label} trace error exists only when it failed")
        both_reached = self.state == "reached" and self.reverse_state == "reached"
        if self.asymmetry_detected is not None and not both_reached:
            raise ValueError("asymmetry is known only when both directions reached")
        if self.stop_reason is not None and self.tracing:
            raise ValueError("a trace has a stop reason only once it stopped")
        return self


class RecentEvent(BaseModel):
    """Recent event for the VF event log."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sim_time: datetime
    node_id: str
    event_type: Literal["link_up", "link_down", "PATH_CHANGE"]
    summary: str


class ActuationNotice(BaseModel):
    """User-visible Scheduler actuation problem for one ground station."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gs_id: str
    actuation_state: ActuationState
    reason_code: SchedulerOpsCode
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

    model_config = ConfigDict(frozen=True, extra="forbid")

    gs_id: str
    actuation_state: ActuationState
    since: str | None = None
    reason_code: SchedulerOpsCode
    blocking_new_ground_link_up: bool
    recovery_status: dict = Field(default_factory=dict)


class ActuationHealthInstance(BaseModel):
    """Aggregated actuation health for one Scheduler instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scheduler_instance_id: str
    hostname: str
    status: Literal["clean", "dirty", "degraded", "unknown"]
    ground_stations: list[ActuationHealthGroundStation] = Field(default_factory=list)


class ActuationHealth(BaseModel):
    """Session-level Scheduler actuation health."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    wiring_generation: str
    scheduler_instances: list[ActuationHealthInstance] = Field(default_factory=list)


class AlmanacState(BaseModel):
    """NodalPath almanac push tracking state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    last_topology_state_id: str | None = None
    last_push_sim_time: str | None = None
    last_push_wall_time: float | None = None
    nodes_succeeded: int = 0
    nodes_failed: int = 0
    deviation_count: int = 0
    recomputation_count: int = 0
    nodalpath_active: bool = False


class HistoryRecordingState(BaseModel):
    """A recorded session run's history recording: running, or stopped and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["recording", "stopped"]
    error: str | None


class StampedOpsEvent(OpsEvent):
    """An ops event as VS-API serves it, stamped with its arrival seq."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int


class StateSnapshot(BaseModel):
    """Complete constellation state sent via WebSocket at ~1Hz.

    Full snapshots only, except the ops log: a connection's frame carries only the ops
    events that connection has not been sent. Drop intermediate frames if behind, never
    queue.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    msg_type: Literal["state_snapshot"] = "state_snapshot"
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
    recent_events: list[RecentEvent]
    routing_stack: str | None = None
    constellation_name: str | None = None
    session_status: SessionStatus
    session_status_detail: str | None = None
    playback_paused: bool = False
    playback_speed: float = 1.0
    # Rate honesty: playback_speed is the COMMANDED rate (from ClockTick
    # compression_ratio); playback_achieved is what the engine measured
    # itself delivering; pacing_degraded is the engine's own sustained-
    # shortfall judgment. The UI must show the divergence, never imply
    # the commanded rate is being delivered when it is not.
    playback_achieved: float | None = None
    pacing_degraded: bool = False
    stale: bool = False
    actuation_notices: list[ActuationNotice] = Field(default_factory=list)
    actuation_health: ActuationHealth | None = None
    # This run's history recording; None when the run was deployed without one.
    history_recording: HistoryRecordingState | None
    # Operator-visible ops events, seq-ordered: the full tail on a connection's first
    # frame, then only the events newer than that connection's cursor.
    ops_events: list[StampedOpsEvent]
    # Identifies VS-API's seq space; a change means VS-API restarted and the client
    # replaces its scrollback.
    ops_log_token: str
    # The latest debug events and the services sending them; empty while no client
    # streams debug output.
    debug_events: list[OpsEvent]
    debug_sources: list[ServiceSource]


class SessionEphemerisFrame(SessionEphemeris):
    """The session ephemeris as the state socket sends it: on connect and on epoch change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    msg_type: Literal["session_ephemeris"] = "session_ephemeris"


class WiringProgressFrame(BaseModel):
    """A wiring progress line from the Node Agent, relayed as it arrives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    msg_type: Literal["wiring_progress"] = "wiring_progress"
    message: str


class SessionTransitioningFrame(BaseModel):
    """A session switch has started; later frames carry its progress detail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    msg_type: Literal["session_transitioning"] = "session_transitioning"
    detail: str | None = None


class SessionReadyFrame(BaseModel):
    """The switched-to session is live; carries its first state snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    msg_type: Literal["session_ready"] = "session_ready"
    snapshot: StateSnapshot


class SessionFailedFrame(BaseModel):
    """A session switch failed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    msg_type: Literal["session_failed"] = "session_failed"
    error: str


# Every message the state socket sends.
StateFeedFrame = Annotated[
    StateSnapshot
    | SessionEphemerisFrame
    | WiringProgressFrame
    | SessionTransitioningFrame
    | SessionReadyFrame
    | SessionFailedFrame,
    Field(discriminator="msg_type"),
]


class HealthStatus(BaseModel):
    """VS-API is serving, and where its session manager stands."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]
    session_status: SessionStatus


class ProjectAbout(BaseModel):
    """The project's attribution and the build serving this API."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    revision: str
    build_date: str
    author: str
    copyright: str
    source: str
    url: str
    license: str
    notice: str


class AuthToken(BaseModel):
    """The API key a client sends as its bearer token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str


class ContinuousTraceStarted(BaseModel):
    """The continuous trace now runs between these two nodes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: Literal[True]
    src: str
    dst: str


class ContinuousTraceStopped(BaseModel):
    """No continuous trace runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: Literal[True]


class ContinuousTraceStatus(BaseModel):
    """The continuous trace's endpoints and latest result; all empty when none runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    active: bool
    src: str | None
    dst: str | None
    result: TracedPath | None


class IntrospectRequest(BaseModel):
    """One whitelisted vtysh command addressed to one runtime node."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    node_id: str = Field(min_length=1)
    command: str = Field(min_length=1)


class IntrospectResult(BaseModel):
    """The exact output of one executed vtysh command."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    node_id: str
    command: str
    output: str
    exit_code: int


class LinkHistoryEvent(BaseModel):
    """One recorded link event: a transition, a latency change, or a link
    active when the recording started (``LinkActive``, reason
    ``recording_start``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    session_id: str
    sim_time: str
    wall_time: str
    event_type: Literal["LinkUp", "LinkDown", "LatencyUpdate", "LinkActive"]
    node_a: str
    node_b: str
    interface_a: str | None
    interface_b: str | None
    latency_ms: float | None
    range_km: float | None
    reason: str | None


# The most link events one history page returns.
LINK_HISTORY_PAGE_MAX = 200


class LinkHistoryPage(BaseModel):
    """One page of a recorded session's link events.

    ``returned`` of ``total`` matching events are in ``events``. A further page
    is requested with ``next_cursor`` and the same filters; it is None when no
    event follows. ``retained_from`` is when the oldest kept data was recorded,
    once the history size budget dropped older rows; None while the recording
    is complete.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    events: list[LinkHistoryEvent]
    returned: int
    total: int
    next_cursor: str | None
    retained_from: str | None
