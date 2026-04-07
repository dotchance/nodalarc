# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""OME event models — all frozen (immutable after creation).

Published via NATS JetStream.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


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
    """Pacing clock signal — published once per tick during pacing."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    compression_ratio: float


class HeartbeatTick(BaseModel):
    """Liveness signal during window computation — does NOT advance sim_time."""

    model_config = ConfigDict(frozen=True)

    wall_time: datetime
    status: str  # "computing" or "ready"


class TimelinePositionSnapshot(BaseModel):
    """Positions for ALL nodes at a given simulation time.

    Embedded in ClockTick events in the JSON Lines timeline file.
    The TO uses these in Discrete-Event Mode for latency computation.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    positions: dict[str, NodePosition]  # node_id -> position for ALL nodes
