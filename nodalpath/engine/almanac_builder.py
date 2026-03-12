from __future__ import annotations

import time

from nodalpath.engine.graph import build_graph
from nodalpath.engine.pathcomp import compute_all_paths
from nodalpath.engine.labels import build_lsr_bindings, build_ler_ingress_rules
from nodalpath.models.topology import TopologySnapshot
from nodalpath.models.almanac import AlmanacEntry, ForwardingTable


def compute_almanac_entry(
    snapshot: TopologySnapshot,
    prefix_map: dict[str, str],
    topology_state_id: str | None = None,
) -> AlmanacEntry:
    """Compute a complete almanac entry for a topology snapshot.

    This is the top-level function that orchestrates:
    1. Build the topology graph from the snapshot
    2. Compute all ground-station-to-ground-station paths
    3. For each node, build its forwarding table (LSR bindings + LER rules)
    4. Package everything into an AlmanacEntry
    """
    t0 = time.monotonic()

    # 1. Build graph
    graph = build_graph(snapshot)

    # 2. Compute all paths (any node to any node with a prefix)
    paths = compute_all_paths(graph, prefix_map)

    # 3. Generate topology_state_id if not provided
    if topology_state_id is None:
        compact = snapshot.sim_time.replace("-", "").replace(":", "")
        topology_state_id = f"ts-{compact}"

    # 4. Build forwarding tables for every node
    forwarding_tables: list[ForwardingTable] = []
    for node_id in graph.adjacency:
        lsr_bindings = build_lsr_bindings(node_id, paths, graph)
        ler_ingress_rules = build_ler_ingress_rules(node_id, paths, graph, prefix_map)

        forwarding_tables.append(ForwardingTable(
            node_id=node_id,
            topology_state_id=topology_state_id,
            sim_time=snapshot.sim_time,
            lsr_bindings=lsr_bindings,
            ler_ingress_rules=ler_ingress_rules,
        ))

    t1 = time.monotonic()
    computation_time_ms = (t1 - t0) * 1000.0

    return AlmanacEntry(
        topology_state_id=topology_state_id,
        sim_time=snapshot.sim_time,
        forwarding_tables=forwarding_tables,
        computed_paths=[p.path_id for p in paths],
        computation_time_ms=computation_time_ms,
    )
