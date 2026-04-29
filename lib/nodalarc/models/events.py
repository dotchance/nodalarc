# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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


class VisibilityEvent(BaseModel):
    """Visibility state change between two nodes.

    node_a is always alphabetically < node_b (enforced by validator).
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    node_a: str
    node_b: str
    visible: bool
    scheduled: bool
    range_km: float
    elevation_deg: float | None  # None for ISLs, float for ground links
    terminal_type: str  # "optical" or "rf"
    link_type: str = "isl"  # "isl" or "ground" — set by OME from node type registry
    gs_terminal_index: int | None = None  # None for ISL events
    sat_terminal_index: int | None = None  # None for ISL events
    scheduling_state: str = "active"  # "active" | "teardown"

    @model_validator(mode="before")
    @classmethod
    def _order_nodes(cls, values: dict) -> dict:
        a = values.get("node_a", "")
        b = values.get("node_b", "")
        if a > b:
            values["node_a"] = b
            values["node_b"] = a
        return values


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
    """Orbital elements for a parametric satellite.

    Fields mirror OrbitalElements from constellation.py. The propagator
    derives semi_major_axis from altitude_km and assumes circular orbit
    (eccentricity=0).
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["keplerian"] = "keplerian"
    altitude_km: float
    inclination_deg: float
    raan_deg: float
    true_anomaly_deg: float
    plane: int
    slot: int


class EphemerisNodeFixed(BaseModel):
    """Fixed geodetic position for a ground station."""

    model_config = ConfigDict(frozen=True)

    type: Literal["fixed"] = "fixed"
    lat_deg: float
    lon_deg: float
    alt_km: float


EphemerisNode = Annotated[
    EphemerisNodeKeplerian | EphemerisNodeFixed,
    Field(discriminator="type"),
]


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


class ValidationResult(BaseModel):
    """Result from session pre-deployment validation.

    level="error" blocks deployment; level="warning" is logged but allowed.
    """

    model_config = ConfigDict(frozen=True)

    level: str  # "error" or "warning"
    code: str  # e.g., "E001", "W003"
    message: str
    remediation: str | None = None


class TeardownEntry(BaseModel):
    """Single pending MBB teardown in a SchedulingCheckpoint."""

    model_config = ConfigDict(frozen=True)

    remaining_ticks: int
    gs_id: str
    sat_id: str


class SchedulingCheckpoint(BaseModel):
    """OME ground-scheduling state checkpoint.

    Published to NODALARC_SESSION stream (MaxMsgsPerSubject=1) alongside
    LinkStateSnapshot at the same interval. Provides the Scheduler and
    other consumers with the OME's current ground-station association
    state for recovery after restart.

    associations: gs_id → sat_id (current GS-to-satellite assignments)
    pending_teardowns: pair_key → TeardownEntry (MBB teardowns in progress)
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    epoch_id: int
    step: int
    associations: dict[str, str]  # gs_id → sat_id
    pending_teardowns: dict[str, TeardownEntry]  # pair_key → entry


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
