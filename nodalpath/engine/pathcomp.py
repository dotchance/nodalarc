from __future__ import annotations

import heapq

from nodalpath.engine.graph import TopologyGraph, GraphEdge
from nodalpath.models.path import ComputedPath, PathHop


def dijkstra(
    graph: TopologyGraph,
    src: str,
    dst: str,
) -> ComputedPath | None:
    """Compute shortest path from src to dst using Dijkstra's algorithm.

    Returns None if no path exists (disconnected graph).
    Edge weight is latency_ms (lower is better).
    """
    if src == dst:
        return None

    # dist[node] = shortest distance from src
    dist: dict[str, float] = {node: float("inf") for node in graph.adjacency}
    dist[src] = 0.0

    # prev[node] = (predecessor_node_id, edge_used_to_reach_node)
    prev: dict[str, tuple[str, GraphEdge]] = {}

    # Min-heap: (distance, node_id)
    heap: list[tuple[float, str]] = [(0.0, src)]

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        if u == dst:
            break
        for edge in graph.adjacency[u]:
            new_dist = d + edge.weight
            if new_dist < dist[edge.dst]:
                dist[edge.dst] = new_dist
                prev[edge.dst] = (u, edge)
                heapq.heappush(heap, (new_dist, edge.dst))

    # Check if dst is reachable
    if dst not in prev:
        return None

    # Reconstruct path: walk predecessors from dst back to src
    path_nodes: list[str] = []
    path_edges: list[GraphEdge] = []
    current = dst
    while current != src:
        predecessor, edge = prev[current]
        path_nodes.append(current)
        path_edges.append(edge)
        current = predecessor
    path_nodes.append(src)
    path_nodes.reverse()
    path_edges.reverse()

    # Build PathHop entries
    hops: list[PathHop] = []
    for i, node_id in enumerate(path_nodes):
        sid = graph.node_sids[node_id]

        # in_interface: dst_interface of the edge arriving at this node (None for first hop)
        in_interface: str | None = None
        if i > 0:
            in_interface = path_edges[i - 1].dst_interface

        # out_interface: src_interface of the edge departing this node (None for last hop)
        out_interface: str | None = None
        if i < len(path_edges):
            out_interface = path_edges[i].src_interface

        # latency_to_next_ms: weight of the departing edge (None for last hop)
        latency_to_next: float | None = None
        if i < len(path_edges):
            latency_to_next = path_edges[i].weight

        hops.append(PathHop(
            node_id=node_id,
            sid=sid,
            in_interface=in_interface,
            out_interface=out_interface,
            latency_to_next_ms=latency_to_next,
        ))

    total_latency = sum(e.weight for e in path_edges)
    # label_stack = SIDs of hops[1:] (transit nodes, excluding ingress LER)
    label_stack = [hop.sid for hop in hops[1:]]

    return ComputedPath(
        path_id=f"{src}->{dst}",
        src_node_id=src,
        dst_node_id=dst,
        hops=hops,
        total_latency_ms=total_latency,
        hop_count=len(hops),
        label_stack=label_stack,
        is_backup=False,
    )


def compute_all_gs_paths(
    graph: TopologyGraph,
) -> list[ComputedPath]:
    """Compute shortest paths between all ground station pairs.

    For N ground stations, computes N*(N-1) directed paths
    (A->B and B->A are separate paths with potentially different routes).
    Returns only paths that exist (skips disconnected pairs).
    """
    paths: list[ComputedPath] = []
    gs_list = graph.ground_stations
    for src in gs_list:
        for dst in gs_list:
            if src == dst:
                continue
            path = dijkstra(graph, src, dst)
            if path is not None:
                paths.append(path)
    return paths
