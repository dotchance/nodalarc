"""Synchronous push scheduler — translates almanac entries to FRR config pushes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from nodalpath.models.almanac import AlmanacEntry, ForwardingTable
from nodalpath.models.topology import TopologyNode
from nodalpath.push.kubectl_exec import push_to_nodes
from nodalpath.push.vtysh_push import diff_forwarding_tables, forwarding_table_to_vtysh

log = logging.getLogger(__name__)


class PushStatusCode(str, Enum):
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PushResult:
    """Result of pushing forwarding tables for one almanac entry."""
    topology_state_id: str
    sim_time: str
    nodes_attempted: int
    nodes_succeeded: int
    nodes_failed: int
    nodes_skipped: int
    push_duration_ms: float
    failed_nodes: list[str] = field(default_factory=list)


@dataclass
class PushSchedulerConfig:
    """Configuration for the push scheduler."""
    namespace: str = "nodalarc"
    timeout_seconds: int = 10
    use_incremental_diff: bool = True
    dry_run: bool = False


class PushScheduler:
    """Translates AlmanacEntry forwarding tables into FRR vtysh pushes."""

    def __init__(
        self,
        node_registry: dict[str, TopologyNode],
        interface_map: dict[tuple[str, str], tuple[str, str]],
        config: PushSchedulerConfig | None = None,
    ) -> None:
        self.config = config or PushSchedulerConfig()
        self._sid_to_loopback: dict[int, str] = {
            node.sid: node.loopback_ipv4 for node in node_registry.values()
        }
        self._iface_to_peer_loopback: dict[tuple[str, str], str] = {}
        for (src, dst), (src_iface, dst_iface) in interface_map.items():
            src_lo = node_registry[src].loopback_ipv4 if src in node_registry else None
            dst_lo = node_registry[dst].loopback_ipv4 if dst in node_registry else None
            if dst_lo is not None:
                self._iface_to_peer_loopback[(src, src_iface)] = dst_lo
            if src_lo is not None:
                self._iface_to_peer_loopback[(dst, dst_iface)] = src_lo
        self._installed: dict[str, ForwardingTable] = {}
        self._results: list[PushResult] = []

    @property
    def results(self) -> list[PushResult]:
        """All push results in chronological order."""
        return list(self._results)

    def push_entry(
        self,
        entry: AlmanacEntry,
        previous_entry: AlmanacEntry | None = None,
    ) -> PushResult:
        """Push forwarding tables from an almanac entry to FRR pods.

        Returns a PushResult summarizing the operation.
        """
        push_tasks: list[tuple[str, str]] = []
        skipped = 0

        for table in entry.forwarding_tables:
            node_id = table.node_id
            if self.config.use_incremental_diff:
                commands = diff_forwarding_tables(
                    self._installed.get(node_id), table,
                    self._sid_to_loopback, self._iface_to_peer_loopback,
                )
            else:
                commands = forwarding_table_to_vtysh(
                    table, self._sid_to_loopback, self._iface_to_peer_loopback,
                )
            if not commands:
                skipped += 1
                continue
            push_tasks.append((node_id, commands))

        if not push_tasks:
            result = PushResult(
                topology_state_id=entry.topology_state_id,
                sim_time=entry.sim_time,
                nodes_attempted=0,
                nodes_succeeded=0,
                nodes_failed=0,
                nodes_skipped=skipped,
                push_duration_ms=0.0,
            )
            self._results.append(result)
            return result

        if self.config.dry_run:
            log.debug(
                "Dry run: would push to %d nodes for state %s",
                len(push_tasks), entry.topology_state_id,
            )
            # Fabricate success for all attempted nodes
            for node_id, _ in push_tasks:
                self._installed[node_id] = next(
                    t for t in entry.forwarding_tables if t.node_id == node_id
                )
            result = PushResult(
                topology_state_id=entry.topology_state_id,
                sim_time=entry.sim_time,
                nodes_attempted=len(push_tasks),
                nodes_succeeded=len(push_tasks),
                nodes_failed=0,
                nodes_skipped=skipped,
                push_duration_ms=0.0,
            )
            self._results.append(result)
            return result

        start = time.monotonic()
        exec_results = push_to_nodes(
            push_tasks,
            namespace=self.config.namespace,
            timeout=self.config.timeout_seconds,
        )
        duration_ms = (time.monotonic() - start) * 1000

        succeeded = 0
        failed = 0
        failed_nodes: list[str] = []

        for (node_id, _commands), exec_result in zip(push_tasks, exec_results):
            if exec_result.success:
                succeeded += 1
                # Update installed state on success
                self._installed[node_id] = next(
                    t for t in entry.forwarding_tables if t.node_id == node_id
                )
            else:
                failed += 1
                failed_nodes.append(node_id)
                log.error(
                    "Push failed for %s: rc=%d stderr=%s",
                    node_id, exec_result.returncode, exec_result.stderr,
                )

        result = PushResult(
            topology_state_id=entry.topology_state_id,
            sim_time=entry.sim_time,
            nodes_attempted=len(push_tasks),
            nodes_succeeded=succeeded,
            nodes_failed=failed,
            nodes_skipped=skipped,
            push_duration_ms=duration_ms,
            failed_nodes=failed_nodes,
        )

        log.info(
            "Push for state %s: %d attempted, %d succeeded, %d failed, %d skipped (%.1fms)",
            entry.topology_state_id, len(push_tasks), succeeded, failed, skipped, duration_ms,
        )

        self._results.append(result)
        return result
