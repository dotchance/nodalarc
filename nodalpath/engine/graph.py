from __future__ import annotations

from dataclasses import dataclass, field

from nodalpath.models.topology import TopologySnapshot, TopologyEdge


@dataclass
class GraphEdge:
    """A weighted directed edge in the computation graph."""
    dst: str                              # destination node_id
    latency_ms: float                     # physical propagation delay
    src_interface: str
    dst_interface: str
    bandwidth_mbps: float


@dataclass
class TopologyGraph:
    """Adjacency list graph built from a TopologySnapshot."""
    adjacency: dict[str, list[GraphEdge]] = field(default_factory=dict)
    node_sids: dict[str, int] = field(default_factory=dict)
    node_loopbacks: dict[str, str] = field(default_factory=dict)
    node_types: dict[str, str] = field(default_factory=dict)
    ground_stations: list[str] = field(default_factory=list)


def build_graph(snapshot: TopologySnapshot) -> TopologyGraph:
    """Build a computation graph from a topology snapshot.

    The graph is bidirectional: each TopologyEdge in the snapshot
    produces two directed GraphEdges (one in each direction).
    """
    graph = TopologyGraph()

    # Initialize all nodes (including isolated ones)
    for node in snapshot.nodes:
        graph.adjacency[node.node_id] = []
        graph.node_sids[node.node_id] = node.sid
        graph.node_loopbacks[node.node_id] = node.loopback_ipv4
        graph.node_types[node.node_id] = node.node_type
        if node.node_type == "ground_station":
            graph.ground_stations.append(node.node_id)

    # Add bidirectional edges
    for edge in snapshot.edges:
        # Forward direction
        graph.adjacency[edge.src_node_id].append(GraphEdge(
            dst=edge.dst_node_id,
            latency_ms=edge.latency_ms,
            src_interface=edge.src_interface,
            dst_interface=edge.dst_interface,
            bandwidth_mbps=edge.bandwidth_mbps,
        ))
        # Reverse direction
        graph.adjacency[edge.dst_node_id].append(GraphEdge(
            dst=edge.src_node_id,
            latency_ms=edge.latency_ms,
            src_interface=edge.dst_interface,
            dst_interface=edge.src_interface,
            bandwidth_mbps=edge.bandwidth_mbps,
        ))

    return graph
