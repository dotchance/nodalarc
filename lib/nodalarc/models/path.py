"""Canonical path model for the NodalArc platform.

Used by NodalPath (derived paths from MPLS forwarding tables),
measurement infrastructure (probed paths from live packet injection),
VS-API (unified path endpoint), and VF (path overlay rendering).

All path producers emit PathResult. All path consumers accept PathResult.
The method field tells the consumer how the path was computed.
"""

from __future__ import annotations

from pydantic import BaseModel


class PathHop(BaseModel, frozen=True):
    """A single hop in a computed or probed path."""
    node_id: str                    # e.g., "sat-P02S05" or "gs-ashburn"
    node_type: str                  # "satellite" or "ground_station"
    in_label: int | None = None     # MPLS label arriving at this node (None at ingress)
    out_label: int | None = None    # MPLS label leaving this node (None at egress/pop)
    action: str | None = None       # "push", "swap", "pop", or None at src/dst
    out_interface: str | None = None  # interface used to reach next hop
    latency_to_next_ms: float | None = None  # propagation delay to next hop


class PathResult(BaseModel, frozen=True):
    """A complete path from source to destination.

    Produced by NodalPath (derived) or measurement infrastructure (probed).
    Consumed by VS-API, VF, and NodalPath console.
    """
    src: str                        # source node_id (ground station)
    dst: str                        # destination node_id (ground station)
    hops: list[PathHop]             # ordered list from src to dst inclusive
    total_latency_ms: float         # sum of latency_to_next_ms across all hops
    method: str                     # "derived" | "probed"
    sim_time: str                   # ISO 8601 — when this path is valid
    topology_state_id: str          # links to AlmanacEntry
    reachable: bool                 # False if no path exists
    unreachable_reason: str | None = None  # e.g., "no ingress rule for dst_prefix"


class PathQuery(BaseModel):
    """A request to compute or retrieve a path."""
    src: str                        # source node_id
    dst: str                        # destination node_id
    sim_time: str | None = None     # None = current live state
