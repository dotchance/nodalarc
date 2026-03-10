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
    transport: str = "vtysh"
    grpc_port: int = 50051


@dataclass
class _NormalizedResult:
    """Internal transport-agnostic push result."""
    node_id: str
    success: bool
    error: str


class PushScheduler:
    """Translates AlmanacEntry forwarding tables into node pushes.

    Supports two transports:
    - "vtysh": pushes via kubectl exec / deploy daemon (default)
    - "grpc": pushes via gRPC to nodalpath-fwd containers
    """

    def __init__(
        self,
        node_registry: dict[str, TopologyNode],
        interface_map: dict[tuple[str, str], tuple[str, str]],
        config: PushSchedulerConfig | None = None,
        pod_ip_map: dict[str, str] | None = None,
    ) -> None:
        self.config = config or PushSchedulerConfig()
        if self.config.transport == "grpc" and pod_ip_map is None:
            raise ValueError("pod_ip_map is required when transport is 'grpc'")
        self._pod_ip_map: dict[str, str] = pod_ip_map or {}
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
        """Push forwarding tables from an almanac entry to nodes.

        Returns a PushResult summarizing the operation.
        """
        # ------------------------------------------------------------------
        # Phase 1: Determine which nodes need pushing (transport-agnostic)
        # ------------------------------------------------------------------
        nodes_to_push: list[str] = []
        vtysh_commands_cache: dict[str, str] = {}
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
            nodes_to_push.append(node_id)
            if self.config.transport == "vtysh":
                vtysh_commands_cache[node_id] = commands

        if not nodes_to_push:
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

        # Dry-run path (transport-agnostic)
        if self.config.dry_run:
            log.debug(
                "Dry run: would push to %d nodes for state %s",
                len(nodes_to_push), entry.topology_state_id,
            )
            for node_id in nodes_to_push:
                self._installed[node_id] = next(
                    t for t in entry.forwarding_tables if t.node_id == node_id
                )
            result = PushResult(
                topology_state_id=entry.topology_state_id,
                sim_time=entry.sim_time,
                nodes_attempted=len(nodes_to_push),
                nodes_succeeded=len(nodes_to_push),
                nodes_failed=0,
                nodes_skipped=skipped,
                push_duration_ms=0.0,
            )
            self._results.append(result)
            return result

        # ------------------------------------------------------------------
        # Phase 2: Execute the push (transport-specific)
        # ------------------------------------------------------------------
        start = time.monotonic()
        if self.config.transport == "grpc":
            normalized = self._push_grpc(entry, nodes_to_push)
        else:
            normalized = self._push_vtysh(entry, nodes_to_push, vtysh_commands_cache)
        duration_ms = (time.monotonic() - start) * 1000

        # ------------------------------------------------------------------
        # Phase 3: Process results (transport-agnostic)
        # ------------------------------------------------------------------
        succeeded = 0
        failed = 0
        failed_nodes: list[str] = []

        for nr in normalized:
            if nr.success:
                succeeded += 1
                self._installed[nr.node_id] = next(
                    t for t in entry.forwarding_tables if t.node_id == nr.node_id
                )
            else:
                failed += 1
                failed_nodes.append(nr.node_id)
                log.error("Push failed for %s: %s", nr.node_id, nr.error)

        result = PushResult(
            topology_state_id=entry.topology_state_id,
            sim_time=entry.sim_time,
            nodes_attempted=len(nodes_to_push),
            nodes_succeeded=succeeded,
            nodes_failed=failed,
            nodes_skipped=skipped,
            push_duration_ms=duration_ms,
            failed_nodes=failed_nodes,
        )

        log.info(
            "Push for state %s: %d attempted, %d succeeded, %d failed, %d skipped (%.1fms)",
            entry.topology_state_id, len(nodes_to_push), succeeded, failed, skipped, duration_ms,
        )

        self._results.append(result)
        return result

    def _push_vtysh(
        self,
        entry: AlmanacEntry,
        nodes_to_push: list[str],
        vtysh_commands_cache: dict[str, str],
    ) -> list[_NormalizedResult]:
        """Execute push via vtysh/kubectl exec transport."""
        push_tasks: list[tuple[str, str]] = []
        for node_id in nodes_to_push:
            if node_id in vtysh_commands_cache:
                commands = vtysh_commands_cache[node_id]
            else:
                table = next(t for t in entry.forwarding_tables if t.node_id == node_id)
                commands = forwarding_table_to_vtysh(
                    table, self._sid_to_loopback, self._iface_to_peer_loopback,
                )
            push_tasks.append((node_id, commands))

        exec_results = push_to_nodes(
            push_tasks,
            namespace=self.config.namespace,
            timeout=self.config.timeout_seconds,
        )

        return [
            _NormalizedResult(
                node_id=er.node_id,
                success=er.success,
                error=er.stderr if not er.success else "",
            )
            for er in exec_results
        ]

    def _push_grpc(
        self,
        entry: AlmanacEntry,
        nodes_to_push: list[str],
    ) -> list[_NormalizedResult]:
        """Execute push via gRPC transport."""
        from nodalpath.push.grpc_push import (
            build_forwarding_update,
            push_to_nodes_grpc,
        )

        grpc_tasks: list[tuple[str, str, object]] = []
        for node_id in nodes_to_push:
            table = next(t for t in entry.forwarding_tables if t.node_id == node_id)
            update = build_forwarding_update(
                table,
                topology_state_id=entry.topology_state_id,
                sim_time=entry.sim_time,
            )
            pod_ip = self._pod_ip_map.get(node_id, "")
            grpc_tasks.append((node_id, pod_ip, update))

        grpc_results = push_to_nodes_grpc(
            grpc_tasks,
            port=self.config.grpc_port,
            timeout=self.config.timeout_seconds,
        )

        return [
            _NormalizedResult(
                node_id=gr.node_id,
                success=gr.success,
                error=gr.error_message if not gr.success else "",
            )
            for gr in grpc_results
        ]
