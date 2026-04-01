"""Three-layer link state model — PRD Section 4.1B.

Every link has three causally dependent layers:
  1. Admin state — is the interface administratively enabled?
  2. Carrier state — does the physical signal exist? (only if admin UP)
  3. Routing adjacency — has the routing protocol formed a neighbor? (only if carrier UP)

LinkStateSnapshot is the complete authoritative state of all links at a point
in time. Subscribers apply it as replace-not-merge — discard all prior state
and replace with snapshot contents. Published to nodalarc.links.state with
MaxMsgsPerSubject=1 so only the latest snapshot is retained.

Transport-agnostic — works on ZMQ or NATS identically.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AdminState(str, Enum):
    """Interface administrative state. Lowest layer."""

    UP = "UP"
    DOWN = "DOWN"


class CarrierState(str, Enum):
    """Physical signal state. Only meaningful when admin is UP."""

    UP = "UP"
    LOWERLAYERDOWN = "LOWERLAYERDOWN"  # admin UP, no carrier (GS idle state)
    DOWN = "DOWN"  # admin DOWN — carrier question is moot


class RoutingState(str, Enum):
    """Routing protocol adjacency state. Only meaningful when carrier is UP."""

    ADJACENT = "ADJACENT"  # ISIS Up / OSPF Full / BGP Established
    INITIALIZING = "INITIALIZING"  # Hello sent, not yet established
    DOWN = "DOWN"  # Carrier up but no routing adjacency
    UNKNOWN = "UNKNOWN"  # MI not configured — cannot observe routing state


class LinkState(BaseModel):
    """Complete state of a single link at a point in time.

    Admin DOWN short-circuits everything — a subscriber reading this
    reconstructs the complete state without history.
    """

    model_config = ConfigDict(frozen=True)

    node_a: str
    node_b: str
    interface_a: str
    interface_b: str
    admin: AdminState
    carrier: CarrierState
    routing: RoutingState
    latency_ms: float | None  # None when carrier is not UP
    bandwidth_mbps: float | None  # None when carrier is not UP
    link_type: Literal["isl", "ground"]
    sim_time: datetime


class LinkStateSnapshot(BaseModel):
    """Complete authoritative link state. Apply as replace-not-merge.

    Published to nodalarc.links.state with MaxMsgsPerSubject=1.
    Any subscriber applying this snapshot is consistent with OME
    ground truth at this sim_time regardless of missed transitions.

    Multi-node: N Scheduler instances applying the same snapshot
    arrive at identical _active_links state. No coordination needed.
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    snapshot_seq: int  # monotonically increasing, discard if <= current
    links: tuple[LinkState, ...]
    interval_s: float  # publication interval in sim-seconds
