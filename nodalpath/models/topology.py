from __future__ import annotations

from pydantic import BaseModel, field_validator


class TopologyNode(BaseModel, frozen=True):
    """A node in the topology graph."""

    node_id: str  # e.g., "sat-P02S05" or "gs-hawthorne"
    node_type: str  # "satellite" or "ground_station"
    sid: int  # SR node SID from SRGB range
    loopback_ipv4: str  # e.g., "10.0.2.6"
    plane: int | None = None  # orbital plane index (satellites only)
    slot: int | None = None  # slot index within plane (satellites only)

    @field_validator("node_type")
    @classmethod
    def validate_node_type(cls, v: str) -> str:
        if v not in ("satellite", "ground_station"):
            raise ValueError(f"node_type must be 'satellite' or 'ground_station', got '{v}'")
        return v


class TopologyEdge(BaseModel, frozen=True):
    """A directed edge (link) in the topology graph."""

    src_node_id: str
    dst_node_id: str
    src_interface: str  # e.g., "isl0", "isl1", "gnd0"
    dst_interface: str
    latency_ms: float  # one-way propagation delay in milliseconds
    bandwidth_mbps: float  # link capacity
    link_type: str  # "isl" or "ground"

    @field_validator("latency_ms")
    @classmethod
    def validate_latency(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"latency_ms must be non-negative, got {v}")
        return v

    @field_validator("link_type")
    @classmethod
    def validate_link_type(cls, v: str) -> str:
        if v not in ("isl", "ground", "terrestrial"):
            raise ValueError(f"link_type must be 'isl', 'ground', or 'terrestrial', got '{v}'")
        return v


class TopologySnapshot(BaseModel, frozen=True):
    """Complete network topology at a single point in time."""

    sim_time: str  # ISO 8601 timestamp
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]

    @field_validator("nodes")
    @classmethod
    def validate_unique_node_ids(cls, v: list[TopologyNode]) -> list[TopologyNode]:
        ids = [n.node_id for n in v]
        if len(ids) != len(set(ids)):
            dupes = [nid for nid in ids if ids.count(nid) > 1]
            raise ValueError(f"Duplicate node IDs: {set(dupes)}")
        return v
