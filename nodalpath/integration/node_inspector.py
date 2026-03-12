"""Node inspection / feedback loop.

Interrogates nodes via gRPC to verify forwarding state matches planned tables.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime, timezone

from nodalpath.models.almanac import ForwardingTable
from nodalpath.models.inspection import InspectionRun, NodeInspectionResult
from nodalpath.push.grpc_interrogate import interrogate_nodes

log = logging.getLogger(__name__)

def _max_runs() -> int:
    from nodalpath.platform import get_nodalpath_config
    return get_nodalpath_config().inspection_max_retained_runs


class NodeInspector:
    """Manages inspection runs against live nodes."""

    def __init__(
        self,
        pod_ip_map: dict[str, str],
        grpc_port: int = 50051,
        grpc_timeout: float = 10.0,
    ) -> None:
        self._pod_ip_map = dict(pod_ip_map)
        self._grpc_port = grpc_port
        self._grpc_timeout = grpc_timeout
        self._last_pushed_tables: dict[str, ForwardingTable] = {}
        self._last_pushed_state_id: str = ""
        self._runs: deque[InspectionRun] = deque(maxlen=_max_runs())

    def record_push(
        self,
        topology_state_id: str,
        tables: list[ForwardingTable],
    ) -> None:
        """Record the latest pushed tables for future diff comparison."""
        self._last_pushed_state_id = topology_state_id
        self._last_pushed_tables = {t.node_id: t for t in tables}

    async def trigger_push_verify(self, topology_state_id: str) -> InspectionRun:
        """Inspect all nodes after a push to verify installed state."""
        return await self._run_inspection("push_verify", topology_state_id)

    async def trigger_link_event(self, topology_state_id: str) -> InspectionRun:
        """Inspect all nodes after a link event."""
        return await self._run_inspection("link_event", topology_state_id)

    async def trigger_heartbeat(self) -> InspectionRun:
        """Periodic heartbeat inspection of all nodes."""
        return await self._run_inspection(
            "heartbeat", self._last_pushed_state_id,
        )

    async def trigger_operator(
        self,
        node_ids: list[str] | None = None,
    ) -> InspectionRun:
        """Operator-triggered inspection of specific or all nodes."""
        return await self._run_inspection(
            "operator", self._last_pushed_state_id, node_ids=node_ids,
        )

    @property
    def latest_run(self) -> InspectionRun | None:
        """Most recent inspection run."""
        return self._runs[-1] if self._runs else None

    def recent_runs(self, n: int = 10) -> list[InspectionRun]:
        """Return the N most recent runs, newest first."""
        runs = list(self._runs)
        runs.reverse()
        return runs[:n]

    def get_run(self, run_id: str) -> InspectionRun | None:
        """Retrieve a specific run by ID."""
        for run in self._runs:
            if run.run_id == run_id:
                return run
        return None

    async def _run_inspection(
        self,
        trigger: str,
        topology_state_id: str,
        node_ids: list[str] | None = None,
    ) -> InspectionRun:
        """Execute an inspection run against nodes."""
        run = InspectionRun(
            run_id=uuid.uuid4().hex[:12],
            trigger=trigger,
            topology_state_id=topology_state_id or "",
            started_at=datetime.now(timezone.utc),
        )
        self._runs.append(run)

        if not self._last_pushed_tables:
            log.warning("No pushed tables recorded — inspection will be empty")
            run.completed_at = datetime.now(timezone.utc)
            return run

        # Build task list
        target_ids = node_ids if node_ids is not None else list(self._last_pushed_tables.keys())
        tasks: list[tuple[str, str, str, ForwardingTable]] = []
        for nid in target_ids:
            pod_ip = self._pod_ip_map.get(nid)
            table = self._last_pushed_tables.get(nid)
            if pod_ip and table:
                tasks.append((nid, pod_ip, topology_state_id or "", table))
            else:
                log.debug("Skipping %s: no pod_ip or no planned table", nid)

        if not tasks:
            run.completed_at = datetime.now(timezone.utc)
            return run

        # Run gRPC interrogation in default executor (blocking I/O)
        loop = asyncio.get_running_loop()
        results: list[NodeInspectionResult] = await loop.run_in_executor(
            None,
            lambda: interrogate_nodes(
                tasks, port=self._grpc_port, timeout=self._grpc_timeout,
            ),
        )

        run.node_results = results
        run.completed_at = datetime.now(timezone.utc)

        # Log summary
        deviated = run.nodes_with_deviations
        unreachable = run.nodes_unreachable
        if deviated or unreachable:
            log.warning(
                "Inspection %s (%s): %d/%d nodes deviated, %d unreachable",
                run.run_id, trigger, deviated, run.nodes_inspected, unreachable,
            )
        else:
            log.info(
                "Inspection %s (%s): %d nodes nominal",
                run.run_id, trigger, run.nodes_inspected,
            )

        return run

    async def heartbeat_loop(self, interval_s: int) -> None:
        """Background loop that triggers heartbeat inspections."""
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.trigger_heartbeat()
            except Exception:
                log.exception("Heartbeat inspection failed")
