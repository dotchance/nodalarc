# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME event models — all frozen (immutable after creation).

Published via NATS JetStream.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodePosition(BaseModel):
    """Position and velocity of a single node.

    Position is geodetic (WGS84). Velocity is ECEF (Earth-Centered Earth-Fixed)
    in km/s — includes Earth rotation subtraction, so it represents motion
    relative to the rotating Earth. Ground stations have zero velocity.

    The frontend's worldVelocity() function in astronomy.ts expects ECEF
    velocity and applies the view-frame rotation to produce world-frame velocity.
    """

    model_config = ConfigDict(frozen=True)

    lat_deg: float
    lon_deg: float
    alt_km: float
    vel_x_km_s: float  # ECEF velocity X component (km/s)
    vel_y_km_s: float  # ECEF velocity Y component (km/s)
    vel_z_km_s: float  # ECEF velocity Z component (km/s)


class PositionEvent(BaseModel):
    """Position update for a single node, published via NATS JetStream."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    node_id: str
    lat_deg: float
    lon_deg: float
    alt_km: float
    vel_x_km_s: float
    vel_y_km_s: float
    vel_z_km_s: float


VisibilityRejectReason = Literal[
    "ok",
    "los_blocked",
    "elevation_below_min",
    "range_exceeded",
    "field_of_regard",
    "tracking_exceeded",
    "polar_seam",
    "terminal_type_mismatch",
    "terminal_role_mismatch",
]
"""Full union of physical/geometric rejection reasons across ground + ISL.

`VisibilityEvent` carries either axis but is constrained at runtime by
its model validator to the link-type-specific subset (see
``_GROUND_REJECT_REASONS`` and ``_ISL_REJECT_REASONS`` below). The
narrower wire form for ground decisions
(``GroundVisibilityRejectReason`` in
``nodalarc.models.link_decisions``) is the type used on
``GroundVisibilityDecisionWire``."""


UnscheduledReason = Literal[
    "gs_capacity",
    "sat_capacity",
    "isl_terminal_capacity",
    "hysteresis_hold",
    "incumbent_held",
    "bbm_no_spare",
    "mbb_overlap_locked",
    "replaced_by_successor",
    "successor_aborted",
    "failed_successor",
    "failed_acquire",
]
"""Full union of allocation rejection reasons across ground + ISL.

`VisibilityEvent` carries either axis but is constrained at runtime by
its model validator to the link-type-specific subset (see
``_GROUND_UNSCHEDULED_REASONS`` and ``_ISL_UNSCHEDULED_REASONS``
below). The narrower wire form for ground decisions
(``GroundUnscheduledReason`` in ``nodalarc.models.link_decisions``)
is the type used on ``UnscheduledPair``."""


# Link-type-specific reason taxonomy. The validator on `VisibilityEvent`
# enforces these at construction so an ISL-only value (``polar_seam``,
# ``terminal_type_mismatch``, ``terminal_role_mismatch``,
# ``isl_terminal_capacity``) cannot land on a ground event, and a
# ground-only value (``elevation_below_min``, ``gs_capacity``,
# ``sat_capacity``, ``hysteresis_hold``, ``incumbent_held``,
# ``bbm_no_spare``, ``mbb_overlap_locked``,
# ``replaced_by_successor``, ``successor_aborted``, ``failed_successor``,
# ``failed_acquire``) cannot land on an ISL event. The overlap set (``ok``, ``los_blocked``, ``range_exceeded``,
# ``field_of_regard``, ``tracking_exceeded``) is legitimately shared
# because the underlying physics gate applies to both terminal types.
_GROUND_REJECT_REASONS: frozenset[str] = frozenset(
    {
        "ok",
        "los_blocked",
        "elevation_below_min",
        "range_exceeded",
        "field_of_regard",
        "tracking_exceeded",
    }
)
_ISL_REJECT_REASONS: frozenset[str] = frozenset(
    {
        "ok",
        "los_blocked",
        "range_exceeded",
        "field_of_regard",
        "tracking_exceeded",
        "polar_seam",
        "terminal_type_mismatch",
        "terminal_role_mismatch",
    }
)
_GROUND_UNSCHEDULED_REASONS: frozenset[str] = frozenset(
    {
        "gs_capacity",
        "sat_capacity",
        "hysteresis_hold",
        "incumbent_held",
        "bbm_no_spare",
        "mbb_overlap_locked",
        "replaced_by_successor",
        "successor_aborted",
        "failed_successor",
        "failed_acquire",
    }
)
_ISL_UNSCHEDULED_REASONS: frozenset[str] = frozenset({"isl_terminal_capacity"})


class VisibilityEvent(BaseModel):
    """Visibility state change between two nodes.

    node_a is always alphabetically < node_b (enforced by validator).

    Reason fields: every transition must carry both axes of the typed reason
    taxonomy so consumers can explain the transition from the event stream
    alone — without correlating against the decision snapshot.

    - ``visibility_reject_reason``: physical / geometric attribution.
      ``"ok"`` when the pair is visible; one of the typed rejection
      reasons (``"los_blocked"``, ``"elevation_below_min"``,
      ``"range_exceeded"``, ``"field_of_regard"``,
      ``"tracking_exceeded"``) when not visible.
    - ``unscheduled_reason``: scheduling attribution. ``None`` when the
      pair is allocated (``scheduled=True``) or invisible. One of
      ``"gs_capacity"``, ``"sat_capacity"``, ``"hysteresis_hold"``,
      ``"incumbent_held"``, ``"bbm_no_spare"``,
      ``"mbb_overlap_locked"``, ``"replaced_by_successor"``,
      ``"successor_aborted"``,
      ``"failed_successor"``, or ``"failed_acquire"`` when the pair is
      visible but the allocator did not schedule it.

    Field-level consistency invariants:
    - ``visible == (visibility_reject_reason == "ok")``.
    - ``unscheduled_reason is None`` whenever ``scheduled`` is True OR
      ``visible`` is False. (A scheduled pair has no unscheduled
      reason; an invisible pair never reached the allocator.)
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    node_a: str
    node_b: str
    visible: bool
    scheduled: bool
    range_km: float
    latency_ms: float | None = None  # authoritative OME one-way propagation delay
    elevation_deg: float | None  # None for ISLs, float for ground links
    terminal_type: str  # "optical" or "rf"
    link_type: Literal["isl", "ground"]  # set by OME from node type registry
    gs_terminal_index: int | None = None  # None for ISL events
    sat_terminal_index: int | None = None  # None for ISL events
    scheduling_state: str = "active"  # "active" | "teardown"
    visibility_reject_reason: VisibilityRejectReason
    unscheduled_reason: UnscheduledReason | None

    @model_validator(mode="before")
    @classmethod
    def _order_nodes(cls, values: dict) -> dict:
        a = values.get("node_a", "")
        b = values.get("node_b", "")
        if a > b:
            values["node_a"] = b
            values["node_b"] = a
        return values

    @model_validator(mode="after")
    def _reasons_match_link_type(self) -> VisibilityEvent:
        """Link-type-domain enforcement: ground reasons on ground events,
        ISL reasons on ISL events.

        The Literal unions are unioned across both domains so a single
        model can serialize either, but a ground event stamped with an
        ISL-only value (``polar_seam``, ``isl_terminal_capacity``, etc.)
        is an impossible state — producer bug. Reject at construction so
        consumers downstream do not have to defensively re-validate.
        """
        if self.link_type == "ground":
            if self.visibility_reject_reason not in _GROUND_REJECT_REASONS:
                raise ValueError(
                    f"link_type='ground' rejects visibility_reject_reason="
                    f"{self.visibility_reject_reason!r}. Allowed for ground: "
                    f"{sorted(_GROUND_REJECT_REASONS)!r}."
                )
            if (
                self.unscheduled_reason is not None
                and self.unscheduled_reason not in _GROUND_UNSCHEDULED_REASONS
            ):
                raise ValueError(
                    f"link_type='ground' rejects unscheduled_reason="
                    f"{self.unscheduled_reason!r}. Allowed for ground: "
                    f"{sorted(_GROUND_UNSCHEDULED_REASONS)!r}."
                )
        elif self.link_type == "isl":
            if self.visibility_reject_reason not in _ISL_REJECT_REASONS:
                raise ValueError(
                    f"link_type='isl' rejects visibility_reject_reason="
                    f"{self.visibility_reject_reason!r}. Allowed for ISL: "
                    f"{sorted(_ISL_REJECT_REASONS)!r}."
                )
            if (
                self.unscheduled_reason is not None
                and self.unscheduled_reason not in _ISL_UNSCHEDULED_REASONS
            ):
                raise ValueError(
                    f"link_type='isl' rejects unscheduled_reason="
                    f"{self.unscheduled_reason!r}. Allowed for ISL: "
                    f"{sorted(_ISL_UNSCHEDULED_REASONS)!r}."
                )
        return self

    @model_validator(mode="after")
    def _reasons_consistent_with_state(self) -> VisibilityEvent:
        """Both axes of the reason taxonomy must be consistent with
        (visible, scheduled). The four states are:

        - visible=True,  scheduled=True  → reject='ok',     unscheduled=None
        - visible=True,  scheduled=False → reject='ok',     unscheduled=<set>
        - visible=False, scheduled=False → reject=<non-ok>, unscheduled=None
        - visible=False, scheduled=True  → impossible (a non-visible pair
          cannot be scheduled — the OME would never have allocated it).

        A visible-but-unscheduled pair without an unscheduled_reason is a
        producer bug: the allocator must attribute every visible pair it
        did not schedule. We refuse to emit an event that elides the
        attribution because consumers downstream then cannot explain the
        transition from the event alone.
        """
        if self.visible and self.visibility_reject_reason != "ok":
            raise ValueError(
                f"visible=True requires visibility_reject_reason='ok', got "
                f"{self.visibility_reject_reason!r}."
            )
        if not self.visible and self.visibility_reject_reason == "ok":
            raise ValueError("visible=False requires a non-'ok' visibility_reject_reason.")
        if not self.visible and self.scheduled:
            raise ValueError(
                "visible=False with scheduled=True is impossible — a "
                "non-visible pair cannot be scheduled. The producer must "
                "set scheduled=False whenever visible=False."
            )
        if self.visible and not self.scheduled and self.unscheduled_reason is None:
            raise ValueError(
                "visible=True with scheduled=False requires "
                "unscheduled_reason to be set — the allocator must "
                "attribute every visible pair it did not schedule. "
                "Missing attribution is a producer bug."
            )
        if self.unscheduled_reason is not None:
            if not self.visible:
                raise ValueError(
                    "unscheduled_reason set on a non-visible pair — an "
                    "invisible pair never reached the allocator."
                )
            if self.scheduled:
                raise ValueError(
                    "unscheduled_reason set on a scheduled pair — a "
                    "scheduled pair has no unscheduled reason."
                )
        return self


class ClockTick(BaseModel):
    """Pacing clock signal — published once per tick during pacing.

    epoch_id identifies which epoch this tick belongs to. Edges in
    SUSPENDED state drop ClockTick messages until epoch_id matches
    the expected epoch. See PRD v0.71 epoch synchronization protocol.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    compression_ratio: float
    epoch_id: int = 0


class HeartbeatTick(BaseModel):
    """Liveness signal during window computation — does NOT advance sim_time."""

    model_config = ConfigDict(frozen=True)

    wall_time: datetime
    status: str  # "computing" or "ready"


class TimelinePositionSnapshot(BaseModel):
    """DEPRECATED (PRD v0.71) — Retained as historical reference only.

    No component publishes or subscribes to this model. Position data is
    distributed via SessionEphemeris (once per epoch) and computed locally
    by each edge using the shared Keplerian propagator.

    Previously: Positions for ALL nodes at a given simulation time,
    published every tick via NATS JetStream.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    positions: dict[str, NodePosition]  # node_id -> position for ALL nodes


# ---------------------------------------------------------------------------
# Distributed ephemeris model (PRD v0.71)
# ---------------------------------------------------------------------------


class EphemerisNodeKeplerian(BaseModel):
    """Circular-element ephemeris for a satellite.

    Fields mirror OrbitalElements from constellation.py. The `propagator`
    field is part of the contract because the same circular element fields can
    drive either the synthetic Keplerian engine or the J2 mean-element engine.
    Consumers must not silently treat J2 sessions as Keplerian.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["keplerian"] = "keplerian"
    propagator: Literal["keplerian-circular", "j2-mean-elements"]
    altitude_km: float
    inclination_deg: float
    raan_deg: float
    true_anomaly_deg: float
    plane: int
    slot: int
    segment_id: str | None = None
    local_node_id: str | None = None
    namespace: str | None = None
    tags: tuple[str, ...] = ()
    reference_body: str = "earth"
    frame_id: str = "earth"


class EphemerisNodeTLE(BaseModel):
    """TLE-backed satellite ephemeris for SGP4 propagation."""

    model_config = ConfigDict(frozen=True)

    type: Literal["tle"] = "tle"
    tle_line_1: str
    tle_line_2: str
    plane: int
    slot: int
    norad_id: int | None = None
    segment_id: str | None = None
    local_node_id: str | None = None
    namespace: str | None = None
    tags: tuple[str, ...] = ()
    reference_body: str = "earth"
    frame_id: str = "earth"

    @model_validator(mode="after")
    def _validate_tle_pair(self):
        from nodalarc.tle import validate_tle_pair

        validate_tle_pair(self.tle_line_1, self.tle_line_2)
        return self


class EphemerisNodeFixed(BaseModel):
    """Fixed geodetic position for a ground station."""

    model_config = ConfigDict(frozen=True)

    type: Literal["fixed"] = "fixed"
    lat_deg: float
    lon_deg: float
    alt_km: float
    segment_id: str | None = None
    local_node_id: str | None = None
    namespace: str | None = None
    tags: tuple[str, ...] = ()
    reference_body: str = "earth"
    frame_id: str = "earth"


EphemerisNode = Annotated[
    EphemerisNodeKeplerian | EphemerisNodeTLE | EphemerisNodeFixed,
    Field(discriminator="type"),
]


class EphemerisBodyFrame(BaseModel):
    """Body origin in the session common frame at ``epoch_unix``.

    Positions and velocities are Earth-relative GCRS-like km vectors supplied by
    the backend ephemeris provider. The renderer may apply a visual scale or
    camera-relative transform, but these numbers remain the authoritative
    physical frame facts used to place bodies relative to one another.
    """

    model_config = ConfigDict(frozen=True)

    body_id: str
    radius_km: float
    origin_x_km: float
    origin_y_km: float
    origin_z_km: float
    vel_x_km_s: float
    vel_y_km_s: float
    vel_z_km_s: float
    provider: str
    kernel_id: str
    quality_tier: str
    frame: str


class SessionEphemeris(BaseModel):
    """Orbital elements for all nodes, distributed once per epoch.

    Published to NODALARC_SESSION stream (MaxMsgsPerSubject=1) at
    session start (epoch_id=0) and immediately after each Tier 2 seek.
    Late-joining subscribers always get the current ephemeris.

    Edges instantiate local propagators from this payload and compute
    positions on demand. No per-tick position data is broadcast.
    """

    model_config = ConfigDict(frozen=True)

    epoch_id: int
    sim_time: datetime
    epoch_unix: float  # Unix timestamp for propagation dt calculation
    nodes: dict[str, EphemerisNode]
    body_frames: dict[str, EphemerisBodyFrame] = Field(default_factory=dict)


class PlaybackState(BaseModel):
    """OME playback state — seeking/playing/paused.

    Published to NODALARC_SESSION stream (MaxMsgsPerSubject=1) at every
    state transition. Late-joining subscribers immediately know current
    state. The 'seeking' state is a mutex — edges must suspend until
    all epoch dependencies are satisfied.
    """

    model_config = ConfigDict(frozen=True)

    epoch_id: int
    state: Literal["seeking", "playing", "paused"]


class PlaybackControlCommand(BaseModel):
    """Validated command payload for the OME playback-control subject."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["pause", "resume", "set_speed", "seek", "get_status"]
    factor: float | None = None
    target_sim_time: datetime | None = None

    @model_validator(mode="after")
    def _validate_action_payload(self):
        if self.action == "set_speed":
            if self.factor is None:
                raise ValueError("set_speed requires factor")
            if self.target_sim_time is not None:
                raise ValueError("set_speed does not accept target_sim_time")
            return self

        if self.factor is not None:
            raise ValueError(f"{self.action} does not accept factor")
        if self.action != "seek" and self.target_sim_time is not None:
            raise ValueError(f"{self.action} does not accept target_sim_time")
        return self


class ValidationResult(BaseModel):
    """Result from session pre-deployment validation.

    level="error" blocks deployment; level="warning" is logged but allowed.
    """

    model_config = ConfigDict(frozen=True)

    level: str  # "error" or "warning"
    code: str  # e.g., "E001", "W003"
    message: str
    remediation: str | None = None
    field_path: str | None = None


class ValidationReport(BaseModel):
    """User-facing validation report for CLI/API/wizard surfaces."""

    model_config = ConfigDict(frozen=True)

    status: Literal["valid", "invalid"]
    normalized_schema_version: int
    effective_config: dict
    errors: tuple[ValidationResult, ...] = ()
    warnings: tuple[ValidationResult, ...] = ()
    dispatchable: bool


class TeardownEntry(BaseModel):
    """Single pending MBB teardown in a SchedulingCheckpoint."""

    model_config = ConfigDict(frozen=True)

    start_step: int
    remaining_ticks: int
    gs_id: str
    sat_id: str
    successor_node_a: str
    successor_node_b: str


class CheckpointAssociation(BaseModel):
    """Single ground association retained in a SchedulingCheckpoint.

    A ground station can temporarily carry multiple concurrent associations
    during MBB overlap. Keying only by gs_id loses state; terminal indices are
    part of the scheduling contract and must survive recovery.
    """

    model_config = ConfigDict(frozen=True)

    gs_id: str
    sat_id: str
    gs_terminal_index: int
    sat_terminal_index: int


class SchedulingCheckpoint(BaseModel):
    """OME ground-scheduling state checkpoint.

    Published to NODALARC_SESSION stream (MaxMsgsPerSubject=1) alongside
    LinkStateSnapshot at the same interval. Provides the Scheduler and
    other consumers with the OME's current ground-station association
    state for recovery after restart.

    snapshot_seq: last LinkStateSnapshot sequence published with this checkpoint
    associations: pair_key → association, preserving terminal assignments
    pending_teardowns: pair_key → TeardownEntry (MBB teardowns in progress)
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    epoch_id: int
    snapshot_seq: int
    step: int
    associations: dict[str, CheckpointAssociation]  # pair_key → association
    pending_teardowns: dict[str, TeardownEntry]  # pair_key → entry
    paused: bool
    time_accel: float
    written_at: float  # wall clock (time.time()) when checkpoint was published


class OpsEvent(BaseModel):
    """Operational event for the NODALARC_OPS JetStream stream.

    Published by any component that needs to surface operational
    telemetry (validation failures, deployment progress, runtime
    anomalies) to a centralized event bus.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    tenant_id: str = ""
    session_id: str
    source: str  # "operator", "scheduler", "ome", "node_agent", "validator"
    hostname: str
    level: str  # "critical", "error", "warning", "info", "debug"
    code: str
    message: str
    details: dict | None = None
