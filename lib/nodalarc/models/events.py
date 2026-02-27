"""OME event models — all frozen (immutable after creation).

Published on ZeroMQ port 5560 (OME_EVENTS_PORT).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class NodePosition(BaseModel):
    """Position and velocity of a single node."""

    model_config = ConfigDict(frozen=True)

    lat_deg: float
    lon_deg: float
    alt_km: float
    vel_x_km_s: float
    vel_y_km_s: float
    vel_z_km_s: float


class PositionEvent(BaseModel):
    """Position update for a single node, published via ZeroMQ."""

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
    """Periodic time marker emitted every step_seconds."""

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    wall_time: datetime
    compression_ratio: float


class TimelinePositionSnapshot(BaseModel):
    """Positions for ALL nodes at a given simulation time.

    Embedded in ClockTick events in the JSON Lines timeline file.
    The TO uses these in Discrete-Event Mode for latency computation.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    positions: dict[str, NodePosition]  # node_id -> position for ALL nodes
