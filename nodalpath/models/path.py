from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PathHop(BaseModel, frozen=True):
    """A single hop in a computed path."""
    node_id: str                          # node at this hop
    sid: int                              # node's SR SID
    in_interface: str | None = None       # interface the packet arrives on (None for ingress)
    out_interface: str | None = None      # interface the packet departs on (None for egress)
    latency_to_next_ms: float | None = None  # latency to the next hop (None for last hop)


class ComputedPath(BaseModel, frozen=True):
    """A computed forwarding path between two endpoints."""
    path_id: str                          # deterministic ID: "{src_node_id}->{dst_node_id}"
    src_node_id: str                      # ingress node (ground station)
    dst_node_id: str                      # egress node (ground station)
    hops: list[PathHop]                   # ordered list of hops from src to dst
    total_latency_ms: float               # sum of all hop latencies
    hop_count: int                        # number of hops (len(hops))
    label_stack: list[int]                # MPLS label stack at ingress (list of SIDs)
    is_backup: bool = False               # True if this is a backup path

    @field_validator("hops")
    @classmethod
    def validate_min_hops(cls, v: list[PathHop]) -> list[PathHop]:
        if len(v) < 2:
            raise ValueError("A path must have at least 2 hops (src and dst)")
        return v
