"""Shared state object for the NodalPath operator console.

Written by LiveOrchestrator (async loop + thread pool).
Read by FastAPI handlers (async loop).
All access protected by threading.Lock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Maximum history entries kept in memory
MAX_PUSH_HISTORY = 100
MAX_ALMANAC_HISTORY = 200
MAX_DEVIATION_HISTORY = 100


@dataclass
class ConsoleState:
    """Mutable shared state for the operator console."""

    # Set at startup — never change
    session_path: str
    transport: str
    dry_run: bool
    start_wall_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    nodes_in_registry: int = 0

    # Counters — written by async loop
    transition_count: int = 0
    deviation_count: int = 0
    recomputation_count: int = 0

    # Current topology state — written by async loop
    last_topology_state_id: str | None = None
    last_sim_time: str | None = None

    # History — written by async loop AND thread pool (push results)
    _push_history: list[dict] = field(default_factory=list)
    _almanac_history: list[dict] = field(default_factory=list)
    _deviation_history: list[dict] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock)

    # Manual recompute request flag — set by HTTP handler, cleared by orchestrator
    _recompute_requested: bool = False

    def record_transition(
        self,
        sim_time: str,
        topology_state_id: str,
        active_link_count: int,
        forwarding_table_count: int,
    ) -> None:
        """Record a topology transition (called from async loop)."""
        with self._lock:
            self.transition_count += 1
            self.last_topology_state_id = topology_state_id
            self.last_sim_time = sim_time
            entry = {
                "sim_time": sim_time,
                "topology_state_id": topology_state_id,
                "active_link_count": active_link_count,
                "forwarding_table_count": forwarding_table_count,
            }
            self._almanac_history.append(entry)
            if len(self._almanac_history) > MAX_ALMANAC_HISTORY:
                self._almanac_history = self._almanac_history[-MAX_ALMANAC_HISTORY:]

    def record_push_result(self, result: Any) -> None:
        """Record a PushResult (called from run_in_executor thread — lock required)."""
        with self._lock:
            entry = {
                "topology_state_id": result.topology_state_id,
                "sim_time": result.sim_time,
                "nodes_attempted": result.nodes_attempted,
                "nodes_succeeded": result.nodes_succeeded,
                "nodes_failed": result.nodes_failed,
                "nodes_skipped": result.nodes_skipped,
                "push_duration_ms": result.push_duration_ms,
                "failed_nodes": list(result.failed_nodes),
            }
            self._push_history.append(entry)
            if len(self._push_history) > MAX_PUSH_HISTORY:
                self._push_history = self._push_history[-MAX_PUSH_HISTORY:]

    def record_deviation(
        self,
        sim_time: str,
        topology_state_id: str,
        node_a: str,
        node_b: str,
        reason: str,
    ) -> None:
        """Record a deviation event (called from async loop)."""
        with self._lock:
            self.deviation_count += 1
            entry = {
                "sim_time": sim_time,
                "topology_state_id": topology_state_id,
                "node_a": node_a,
                "node_b": node_b,
                "reason": reason,
            }
            self._deviation_history.append(entry)
            if len(self._deviation_history) > MAX_DEVIATION_HISTORY:
                self._deviation_history = self._deviation_history[-MAX_DEVIATION_HISTORY:]

    def record_recomputation(self) -> None:
        with self._lock:
            self.recomputation_count += 1

    def request_recompute(self) -> None:
        """Set the manual recompute flag (called from HTTP handler)."""
        with self._lock:
            self._recompute_requested = True

    def consume_recompute_request(self) -> bool:
        """Check and clear the recompute flag (called from async loop). Returns True if set."""
        with self._lock:
            if self._recompute_requested:
                self._recompute_requested = False
                return True
            return False

    def snapshot(self) -> dict:
        """Return a thread-safe dict snapshot of current state."""
        with self._lock:
            return {
                "session_path": self.session_path,
                "transport": self.transport,
                "dry_run": self.dry_run,
                "start_wall_time": self.start_wall_time.isoformat(),
                "nodes_in_registry": self.nodes_in_registry,
                "transition_count": self.transition_count,
                "deviation_count": self.deviation_count,
                "recomputation_count": self.recomputation_count,
                "last_topology_state_id": self.last_topology_state_id,
                "last_sim_time": self.last_sim_time,
                "push_history": list(self._push_history),
                "almanac_history": list(self._almanac_history),
                "deviation_history": list(self._deviation_history),
            }
