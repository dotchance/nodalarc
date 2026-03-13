"""Path deriver — computes display paths using CSPF on the live topology.

Runs Dijkstra directly on the topology graph built from the
SnapshotBuilder's current state. This avoids the ambiguity of walking
SR-MPLS forwarding tables (where every path through a node shares the
same in_label = node SID, making hop-by-hop traversal ambiguous).

The forwarding tables remain the source of truth for the data plane
(what gets pushed to nodes). This module is only for the console path
overlay (control plane view).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nodalarc.models.path import PathResult
from nodalpath.engine.graph import build_graph
from nodalpath.engine.pathcomp import dijkstra, PathConstraints, DEFAULT_CONSTRAINTS

if TYPE_CHECKING:
    from nodalpath.orchestrator.almanac_store import AlmanacStore
    from nodalpath.orchestrator.snapshot_builder import SnapshotBuilder

log = logging.getLogger(__name__)


class PathDeriver:
    """Derives shortest paths for console display using CSPF."""

    def __init__(
        self,
        almanac_store: AlmanacStore,
        prefix_map: dict[str, list[str]],
        node_registry: dict,
        interface_map: dict[tuple[str, str], tuple[str, str]],
        snapshot_builder: SnapshotBuilder | None = None,
        constraints: PathConstraints = DEFAULT_CONSTRAINTS,
    ) -> None:
        self._almanac_store = almanac_store
        self._prefix_map = prefix_map
        self._node_registry = node_registry
        self._interface_map = interface_map
        self._snapshot_builder = snapshot_builder
        self._constraints = constraints

    def derive(self, src: str, dst: str, sim_time: str | None = None) -> PathResult:
        """Compute the shortest path from src to dst.

        Builds a graph from the SnapshotBuilder's current topology
        state and runs CSPF. Returns a PathResult with MPLS label
        annotations derived from node SIDs.
        """
        # Get sim_time and topology_state_id from the latest almanac entry
        if sim_time is None:
            entries = self._almanac_store.entries
            if not entries:
                return self._unreachable(src, dst, "", "", "no almanac entries available")
            entry = entries[-1]
        else:
            entry = self._almanac_store.get_entry_at(sim_time)
            if entry is None:
                return self._unreachable(src, dst, sim_time or "", "",
                                         "no almanac entry at requested sim_time")

        entry_sim_time = entry.sim_time
        entry_state_id = entry.topology_state_id

        # Build graph from current snapshot builder state
        if self._snapshot_builder is None:
            return self._unreachable(src, dst, entry_sim_time, entry_state_id,
                                     "no snapshot builder available")

        snapshot = self._snapshot_builder.build_snapshot(entry_sim_time)
        graph = build_graph(snapshot)
        path = dijkstra(graph, src, dst, self._constraints)

        if path is None:
            return self._unreachable(src, dst, entry_sim_time, entry_state_id,
                                     f"no feasible path from '{src}' to '{dst}'")

        # pathcomp now produces fully-annotated canonical PathHop instances
        return PathResult(
            src=src,
            dst=dst,
            hops=path.hops,
            total_latency_ms=path.total_latency_ms,
            method="cspf",
            sim_time=entry_sim_time,
            topology_state_id=entry_state_id,
            reachable=True,
        )

    @staticmethod
    def _unreachable(src, dst, sim_time, state_id, reason) -> PathResult:
        return PathResult(
            src=src,
            dst=dst,
            hops=[],
            total_latency_ms=0.0,
            method="cspf",
            sim_time=sim_time,
            topology_state_id=state_id,
            reachable=False,
            unreachable_reason=reason,
        )
