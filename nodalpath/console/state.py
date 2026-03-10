"""Shared state object for the NodalPath operator console.

Written by LiveOrchestrator (async event loop + thread-pool for push results).
Read by FastAPI handlers (async event loop).
All access — read and write — protected by threading.Lock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Ring-buffer caps
MAX_PUSH_HISTORY = 100
MAX_DEVIATION_HISTORY = 100
MAX_ALMANAC_HISTORY = 200
MAX_EVENT_LOG = 300   # Unified chronological log shown in the dashboard


@dataclass
class ConsoleState:
    """Mutable shared state for the NodalPath operator console.

    Instantiated by __main__._run_live() at startup and passed to LiveOrchestrator.
    """

    # ── Identity (set at startup, immutable thereafter) ────────────────────
    session_path: str
    transport: str       # "grpc", "vtysh", or "netconf"
    dry_run: bool
    start_wall_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    nodes_in_registry: int = 0

    # ── Scalar counters (written by async loop) ─────────────────────────────
    transition_count: int = 0
    deviation_count: int = 0
    recomputation_count: int = 0

    # ── Most-recent scalar state (written by async loop) ────────────────────
    last_topology_state_id: str | None = None
    last_sim_time: str | None = None

    # ── History lists (all guarded by _lock) ────────────────────────────────
    _push_history: list[dict] = field(default_factory=list)
    _deviation_history: list[dict] = field(default_factory=list)
    _almanac_history: list[dict] = field(default_factory=list)

    # _event_log: unified chronological log shown in the dashboard.
    _event_log: list[dict] = field(default_factory=list)

    # ── Synchronisation ─────────────────────────────────────────────────────
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── Manual recompute flag (set by HTTP handler, cleared by orchestrator) ─
    _recompute_requested: bool = False

    # ────────────────────────────────────────────────────────────────────────
    # Internal helper — MUST be called with _lock already held
    # ────────────────────────────────────────────────────────────────────────

    def _append_event(self, event_type: str, summary: str, details: dict) -> None:
        """Append one entry to the unified event log. Caller must hold _lock."""
        entry = {
            "wall_time": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "summary": summary,
            "details": details,
        }
        self._event_log.append(entry)
        if len(self._event_log) > MAX_EVENT_LOG:
            self._event_log = self._event_log[-MAX_EVENT_LOG:]

    # ────────────────────────────────────────────────────────────────────────
    # Public write API (called from LiveOrchestrator)
    # ────────────────────────────────────────────────────────────────────────

    def record_transition(
        self,
        sim_time: str,
        topology_state_id: str,
        active_link_count: int,
        forwarding_table_count: int,
    ) -> None:
        """Record a topology transition. Called from the async event loop."""
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
            self._append_event(
                "TRANSITION",
                f"T={sim_time} | {active_link_count} links | {forwarding_table_count} tables",
                entry,
            )

    def record_push_result(self, result: Any) -> None:
        """Record a PushResult. Called from run_in_executor (worker thread) — lock required."""
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
            ok = result.nodes_failed == 0
            summary = (
                f"{result.nodes_succeeded}/{result.nodes_attempted} nodes "
                f"| {result.push_duration_ms:.0f}ms"
                + (f" | FAILED: {result.failed_nodes}" if not ok else "")
            )
            self._append_event("PUSH", summary, entry)

    def record_deviation(
        self,
        sim_time: str,
        topology_state_id: str,
        node_a: str,
        node_b: str,
        reason: str,
    ) -> None:
        """Record a deviation event. Called from the async event loop."""
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
            self._append_event(
                "DEVIATE",
                f"{node_a} \u2194 {node_b} | {reason}",
                entry,
            )

    def record_recomputation(self) -> None:
        """Record that a recomputation was triggered. Called from the async event loop."""
        with self._lock:
            self.recomputation_count += 1
            self._append_event(
                "RECOMPUTE",
                f"Recomputation #{self.recomputation_count} triggered",
                {"recomputation_count": self.recomputation_count},
            )

    # ────────────────────────────────────────────────────────────────────────
    # Manual recompute flag
    # ────────────────────────────────────────────────────────────────────────

    def request_recompute(self) -> None:
        """Set the manual recompute flag. Called from an HTTP handler (async loop)."""
        with self._lock:
            self._recompute_requested = True

    def consume_recompute_request(self) -> bool:
        """Check and atomically clear the recompute flag. Called from the async loop."""
        with self._lock:
            if self._recompute_requested:
                self._recompute_requested = False
                return True
            return False

    # ────────────────────────────────────────────────────────────────────────
    # Read API (called from FastAPI handlers)
    # ────────────────────────────────────────────────────────────────────────

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
                # Lists: newest-last in storage, returned newest-first for display
                "push_history": list(reversed(self._push_history)),
                "almanac_history": list(reversed(self._almanac_history)),
                "deviation_history": list(reversed(self._deviation_history)),
                "event_log": list(reversed(self._event_log)),
            }
