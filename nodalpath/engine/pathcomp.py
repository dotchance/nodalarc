"""Constrained Shortest Path First (CSPF) computation.

Standard Dijkstra with configurable metric and hard constraints:

  **Metric** (what Dijkstra minimizes):
    "latency"   — total propagation delay in ms (default)
    "hop_count" — number of hops
    "composite" — lexicographic (hops, latency): minimize hops first,
                  break ties by latency

  **Hard constraints** (prune edges or reject partial paths):
    max_hops           — discard partial paths exceeding N hops
    max_latency_ms     — discard partial paths exceeding delay budget
    min_bandwidth_mbps — prune edges below bandwidth floor

When metric is "latency", the max_hops constraint prevents the
ring-wandering problem on uniform-spacing constellations (where all
link latencies are ~equal, so Dijkstra is indifferent to hop count).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from nodalpath.engine.graph import TopologyGraph, GraphEdge
from nodalpath.models.path import ComputedPath, PathHop


@dataclass(frozen=True)
class PathConstraints:
    """Configuration for CSPF path computation.

    metric: What Dijkstra minimizes.
    max_hops: Hard ceiling on path length. Partial paths that exceed
        this are pruned during search. For satellite constellations,
        a value of ~15 prevents ring-wandering while permitting any
        physically reasonable route across 5-6 orbital planes.
    max_latency_ms: Hard ceiling on total propagation delay.
    min_bandwidth_mbps: Links below this bandwidth are pruned from
        the graph before search.
    """
    metric: str = "latency"
    max_hops: int = 15
    max_latency_ms: float | None = None
    min_bandwidth_mbps: float | None = None


# Module-level default used when callers don't pass constraints.
DEFAULT_CONSTRAINTS = PathConstraints()


def dijkstra(
    graph: TopologyGraph,
    src: str,
    dst: str,
    constraints: PathConstraints = DEFAULT_CONSTRAINTS,
) -> ComputedPath | None:
    """Compute constrained shortest path from src to dst.

    Returns None if no feasible path exists.

    Heap key depends on metric:
      "latency":   (total_latency, hops, node_id)
      "hop_count": (hops, total_latency, node_id)
      "composite": (hops, total_latency, node_id)

    The secondary value and node_id provide deterministic tiebreaking.
    """
    if src == dst:
        return None

    min_bw = constraints.min_bandwidth_mbps
    max_hops = constraints.max_hops
    max_lat = constraints.max_latency_ms
    metric = constraints.metric

    # Per-node best known state: (primary_cost, secondary_cost)
    INF = (float("inf"), float("inf"))
    best: dict[str, tuple[float, float]] = {}
    for node in graph.adjacency:
        best[node] = INF
    best[src] = (0.0, 0.0)

    # prev[node] = (predecessor, edge)
    prev: dict[str, tuple[str, GraphEdge]] = {}

    def _costs(hops: int, lat: float) -> tuple[float, float]:
        if metric == "latency":
            return (lat, float(hops))
        return (float(hops), lat)

    # Heap: (primary, secondary, node_id, hops, total_latency)
    heap: list[tuple[float, float, str, int, float]] = [
        (*_costs(0, 0.0), src, 0, 0.0),
    ]

    while heap:
        pri, sec, u, hops_u, lat_u = heapq.heappop(heap)

        # Stale entry
        if (pri, sec) > best[u]:
            continue

        # Found destination
        if u == dst:
            break

        # Max hops constraint — don't expand further from this node
        if max_hops is not None and hops_u >= max_hops:
            continue

        for edge in graph.adjacency[u]:
            # Bandwidth constraint — skip this edge
            if min_bw is not None and edge.bandwidth_mbps < min_bw:
                continue

            new_hops = hops_u + 1
            new_lat = lat_u + edge.latency_ms

            # Max latency constraint — skip this partial path
            if max_lat is not None and new_lat > max_lat:
                continue

            new_cost = _costs(new_hops, new_lat)
            if new_cost < best.get(edge.dst, INF):
                best[edge.dst] = new_cost
                prev[edge.dst] = (u, edge)
                heapq.heappush(heap, (*new_cost, edge.dst, new_hops, new_lat))

    # Destination unreachable
    if dst not in prev:
        return None

    # Reconstruct path: walk predecessors from dst to src
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
    hops_list: list[PathHop] = []
    for i, node_id in enumerate(path_nodes):
        sid = graph.node_sids[node_id]

        in_interface: str | None = None
        if i > 0:
            in_interface = path_edges[i - 1].dst_interface

        out_interface: str | None = None
        if i < len(path_edges):
            out_interface = path_edges[i].src_interface

        latency_to_next: float | None = None
        if i < len(path_edges):
            latency_to_next = path_edges[i].latency_ms

        hops_list.append(PathHop(
            node_id=node_id,
            sid=sid,
            in_interface=in_interface,
            out_interface=out_interface,
            latency_to_next_ms=latency_to_next,
        ))

    total_latency = sum(e.latency_ms for e in path_edges)
    label_stack = [hop.sid for hop in hops_list[1:]]

    return ComputedPath(
        path_id=f"{src}->{dst}",
        src_node_id=src,
        dst_node_id=dst,
        hops=hops_list,
        total_latency_ms=total_latency,
        hop_count=len(hops_list),
        label_stack=label_stack,
        is_backup=False,
    )


def compute_all_gs_paths(
    graph: TopologyGraph,
    constraints: PathConstraints = DEFAULT_CONSTRAINTS,
) -> list[ComputedPath]:
    """Compute shortest paths between all ground station pairs."""
    paths: list[ComputedPath] = []
    for src in graph.ground_stations:
        for dst in graph.ground_stations:
            if src == dst:
                continue
            path = dijkstra(graph, src, dst, constraints)
            if path is not None:
                paths.append(path)
    return paths


def compute_all_paths(
    graph: TopologyGraph,
    prefix_map: dict[str, str],
    constraints: PathConstraints = DEFAULT_CONSTRAINTS,
) -> list[ComputedPath]:
    """Compute shortest paths from every node to every node with a prefix."""
    paths: list[ComputedPath] = []
    destinations = [nid for nid in prefix_map if nid in graph.adjacency]
    sources = list(graph.adjacency)
    for src in sources:
        for dst in destinations:
            if src == dst:
                continue
            path = dijkstra(graph, src, dst, constraints)
            if path is not None:
                paths.append(path)
    return paths
