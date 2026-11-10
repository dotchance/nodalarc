# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Three-layer link state model.

Every link has three causally dependent layers:
  1. Admin state — is the interface administratively enabled?
  2. Carrier state — does the physical signal exist? (only if admin UP)
  3. Routing adjacency — has the routing protocol formed a neighbor? (only if carrier UP)

LinkStateSnapshot is the complete authoritative state of all links at a point
in time. Subscribers apply it as replace-not-merge — discard all prior state
and replace with snapshot contents. Published to nodalarc.links.state with
MaxMsgsPerSubject=1 so only the latest snapshot is retained.

Published via NATS JetStream with MaxMsgsPerSubject=1 retention.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from nodalarc.models.link_rules import TopologyMode

# Whether OME's ground allocator holds a link open only for its make-before-break
# teardown ("teardown") or schedules it ("active").
type SchedulingState = Literal["active", "teardown"]


class AdminState(StrEnum):
    """Interface administrative state. Lowest layer."""

    UP = "UP"
    DOWN = "DOWN"


class CarrierState(StrEnum):
    """Physical signal state. Only meaningful when admin is UP."""

    UP = "UP"
    LOWERLAYERDOWN = "LOWERLAYERDOWN"  # admin UP, no carrier (GS idle state)
    DOWN = "DOWN"  # admin DOWN — carrier question is moot


class RoutingState(StrEnum):
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
    range_km: float | None = None  # None when carrier is not UP
    # One-way light time of a signal sent at sim_time, each direction: node_a to node_b and
    # node_b to node_a. None when carrier is not UP.
    latency_a_to_b_ms: float | None
    latency_b_to_a_ms: float | None
    link_type: Literal["isl", "ground"]
    link_rule_id: str | None = None
    topology_mode: TopologyMode | None = None
    endpoint_segments: tuple[str, str] | None = None
    gs_terminal_index: int | None = None
    sat_terminal_index: int | None = None
    scheduling_state: SchedulingState = "active"
    teardown_remaining_ticks: int | None = None
    successor_pair: tuple[str, str] | None = None
    # The sim time the link's current contact opened, on a long-delay link (fixed, or a ground
    # pass between long-delay peers) whose carrier is up; None on every other link. With the
    # snapshot's epoch it names the contact
    # (``contact_id``): the Node Agent keeps one delay queue per contact, and a command for one
    # contact never changes another.
    contact_opened_at: datetime | None = None
    sim_time: datetime

    @model_validator(mode="after")
    def _contact_only_on_an_up_isl(self) -> LinkState:
        if self.contact_opened_at is None:
            return self
        if self.carrier != CarrierState.UP:
            raise ValueError(
                f"link {self.node_a}<->{self.node_b}: a contact opening belongs only to a "
                f"long-delay link whose carrier is up (carrier={self.carrier})"
            )
        if self.contact_opened_at > self.sim_time:
            raise ValueError(
                f"link {self.node_a}<->{self.node_b}: its contact opened at "
                f"{self.contact_opened_at.isoformat()}, after the snapshot time "
                f"{self.sim_time.isoformat()}"
            )
        return self


def contact_id(epoch_id: int, opened_at: datetime) -> str:
    """The name of one long-delay contact: the OME epoch and the sim time it opened. A seek starts
    a new epoch, so a contact that opens at the same sim time after a seek is a different contact.
    The Node Agent records it on the receiving host-side veth and refuses to change a contact it
    does not carry."""
    return f"{epoch_id}@{opened_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}"


class LinkStateSnapshot(BaseModel):
    """Complete authoritative link state. Apply as replace-not-merge.

    Published to nodalarc.links.state with MaxMsgsPerSubject=1.
    Any subscriber applying this snapshot is consistent with OME
    ground truth at this sim_time regardless of missed transitions.

    Multi-node: N Scheduler instances applying the same snapshot
    arrive at identical _active_links state. No coordination needed.

    epoch_id identifies which epoch produced this snapshot. Edges must
    not apply a LinkStateSnapshot until SessionEphemeris with the same
    epoch_id has been loaded. snapshot_seq continues monotonically
    across epochs (never reset).
    """

    model_config = ConfigDict(frozen=True)

    sim_time: datetime
    snapshot_seq: int  # monotonically increasing, discard if <= current
    links: tuple[LinkState, ...]
    interval_s: float  # publication interval in sim-seconds
    epoch_id: int = 0
