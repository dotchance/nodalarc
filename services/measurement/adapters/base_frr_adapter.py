# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Base FRR protocol adapter — shared logic for IS-IS and OSPF adapters.

Collects adjacency state via vtysh polling and protocol-specific events
from log file tailing, both via kubectl exec.
"""

from __future__ import annotations

import abc
import logging
import re
import subprocess
import threading
from datetime import UTC, datetime

from nodalarc.models.metrics import AdapterEvent

log = logging.getLogger(__name__)


class _NodeState:
    """Per-node tracking state for FRR protocol adapters."""

    __slots__ = (
        "node_id",
        "management_ip",
        "_pod_name",
        "_namespace",
        "last_neighbors",
        "events",
        "log_thread",
        "tail_proc",
    )

    def __init__(self, node_id: str, management_ip: str) -> None:
        self.node_id = node_id
        self.management_ip = management_ip
        self._pod_name: str | None = None
        self._namespace: str | None = None
        self.last_neighbors: dict[str, dict[str, str]] = {}
        self.events: list[AdapterEvent] = []
        self.log_thread: threading.Thread | None = None
        self.tail_proc: subprocess.Popen | None = None

    @property
    def pod_name(self) -> str:
        if self._pod_name is None:
            self._resolve_pod()
        return self._pod_name

    @property
    def namespace(self) -> str:
        if self._namespace is None:
            self._resolve_pod()
        return self._namespace

    def _resolve_pod(self) -> None:
        """Resolve pod_name and namespace from management_ip via kubectl."""
        try:
            result = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "pods",
                    "--all-namespaces",
                    "--field-selector",
                    f"status.podIP={self.management_ip}",
                    "-o",
                    "jsonpath={.items[0].metadata.namespace} {.items[0].metadata.name}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split()
                if len(parts) == 2:
                    self._namespace = parts[0]
                    self._pod_name = parts[1]
                    return
        except Exception as exc:
            log.warning(f"Pod resolution failed for {self.management_ip}: {exc}")
        # No hardcoded fallback — use platform config as the source of truth.
        from nodalarc.platform_config import get_platform_config

        self._namespace = get_platform_config().kubernetes_namespace
        self._pod_name = self.node_id


class BaseFrrAdapter(abc.ABC):
    """Base class for FRR protocol adapters (IS-IS, OSPF).

    Subclasses set class variables and implement two abstract methods:
    - parse_neighbors: parse vtysh neighbor output
    - is_adjacency_up: determine if a neighbor state means adjacency is up
    """

    # Class variable annotations — subclasses MUST set these.
    protocol_name: str
    neighbor_command: str
    log_file_path: str
    log_patterns: list[tuple[re.Pattern, str]]

    def __init__(self) -> None:
        self._nodes: dict[str, _NodeState] = {}
        self._lock = threading.Lock()

    @abc.abstractmethod
    def parse_neighbors(self, output: str) -> dict[str, dict[str, str]]:
        """Parse protocol-specific neighbor output into {key: {field: value}}."""
        ...

    @abc.abstractmethod
    def is_adjacency_up(self, state: str) -> bool:
        """Return True if the given neighbor state indicates full adjacency."""
        ...

    def start(self, node_id: str, management_ip: str) -> None:
        """Start collecting protocol events from a node."""
        with self._lock:
            if node_id in self._nodes:
                return
            state = _NodeState(
                node_id=node_id,
                management_ip=management_ip,
            )
            self._nodes[node_id] = state

        # Start log tailer thread
        t = threading.Thread(
            target=self._tail_log,
            args=(node_id,),
            daemon=True,
            name=f"{self.protocol_name.lower()}-log-{node_id}",
        )
        t.start()
        state.log_thread = t

        # Do initial neighbor poll
        self._poll_neighbors(node_id)
        log.info(f"{self.protocol_name} adapter started for {node_id}")

    def stop(self, node_id: str) -> None:
        """Stop collection for a node."""
        with self._lock:
            state = self._nodes.pop(node_id, None)
        if state and state.tail_proc:
            try:
                state.tail_proc.terminate()
                state.tail_proc.wait(timeout=5)
            except Exception as exc:
                log.warning(
                    "%s log tailer cleanup failed for %s: %s",
                    self.protocol_name,
                    node_id,
                    exc,
                )
        log.info(f"{self.protocol_name} adapter stopped for {node_id}")

    def get_events(self, node_id: str) -> list[AdapterEvent]:
        """Drain buffered events for a node."""
        with self._lock:
            state = self._nodes.get(node_id)
            if state is None:
                return []
            events = list(state.events)
            state.events.clear()
            return events

    def poll(self, node_id: str) -> None:
        """Manually trigger a neighbor poll (called by MI main loop)."""
        self._poll_neighbors(node_id)

    def trace_path(self, node_id: str, dst_ip: str) -> list[str]:
        """Trace forwarding path to destination."""
        with self._lock:
            state = self._nodes.get(node_id)
            if state is None:
                return []

        result = subprocess.run(
            [
                "kubectl",
                "exec",
                "-n",
                state.namespace,
                state.pod_name,
                "-c",
                "frr",
                "--",
                "vtysh",
                "-c",
                f"show ip route {dst_ip}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            log.warning(f"trace_path failed for {node_id}: {result.stderr}")
            return []

        # Parse route output for nexthop chain
        hops = [node_id]
        for line in result.stdout.splitlines():
            line = line.strip()
            if "via" in line:
                match = re.search(r"via\s+([\d.]+)", line)
                if match:
                    hops.append(match.group(1))
        return hops

    def _poll_neighbors(self, node_id: str) -> None:
        """Poll vtysh for current neighbor state and diff against previous."""
        with self._lock:
            state = self._nodes.get(node_id)
            if state is None:
                return

        try:
            result = subprocess.run(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    state.namespace,
                    state.pod_name,
                    "-c",
                    "frr",
                    "--",
                    "vtysh",
                    "-c",
                    self.neighbor_command,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            log.warning(f"{self.protocol_name} neighbor poll timed out for {node_id}")
            return

        if result.returncode != 0:
            log.warning(f"{self.protocol_name} neighbor poll failed for {node_id}: {result.stderr}")
            return

        current = self.parse_neighbors(result.stdout)
        now = datetime.now(UTC)

        with self._lock:
            prev = state.last_neighbors

            # New adjacencies or state transitions to up
            for key, info in current.items():
                prev_info = prev.get(key)
                if prev_info is None and self.is_adjacency_up(info["state"]):
                    state.events.append(
                        AdapterEvent(
                            sim_time=now,
                            wall_time=now,
                            node_id=node_id,
                            event_type="adjacency_up",
                            event_data={
                                "source": "vtysh_poll",
                                **info,
                            },
                        )
                    )
                elif (
                    prev_info
                    and not self.is_adjacency_up(prev_info["state"])
                    and self.is_adjacency_up(info["state"])
                ):
                    state.events.append(
                        AdapterEvent(
                            sim_time=now,
                            wall_time=now,
                            node_id=node_id,
                            event_type="adjacency_up",
                            event_data={
                                "source": "vtysh_poll",
                                **info,
                                "previous_state": prev_info["state"],
                            },
                        )
                    )

            # Lost adjacencies
            for key, info in prev.items():
                if self.is_adjacency_up(info["state"]):
                    cur_info = current.get(key)
                    if cur_info is None or not self.is_adjacency_up(cur_info["state"]):
                        state.events.append(
                            AdapterEvent(
                                sim_time=now,
                                wall_time=now,
                                node_id=node_id,
                                event_type="adjacency_down",
                                event_data={
                                    "source": "vtysh_poll",
                                    **info,
                                    "previous_state": info["state"],
                                },
                            )
                        )

            state.last_neighbors = current

    def _tail_log(self, node_id: str) -> None:
        """Background thread: tail protocol log file via kubectl exec."""
        with self._lock:
            state = self._nodes.get(node_id)
            if state is None:
                return

        try:
            proc = subprocess.Popen(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    state.namespace,
                    state.pod_name,
                    "-c",
                    "frr",
                    "--",
                    "tail",
                    "-f",
                    self.log_file_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            state.tail_proc = proc

            for line in proc.stdout:
                parsed = self._parse_log_line(line)
                if parsed is None:
                    continue

                now = datetime.now(UTC)
                with self._lock:
                    s = self._nodes.get(node_id)
                    if s is None:
                        break
                    s.events.append(
                        AdapterEvent(
                            sim_time=now,
                            wall_time=now,
                            node_id=node_id,
                            event_type=parsed["type"],
                            event_data={
                                "source": "syslog_parse",
                                "detail": parsed["detail"],
                            },
                        )
                    )

        except Exception as exc:
            log.warning(f"{self.protocol_name} log tailer for {node_id} failed: {exc}")

    def _parse_log_line(self, line: str) -> dict[str, str] | None:
        """Match a log line against the protocol's log_patterns."""
        for pattern, event_type in self.log_patterns:
            if pattern.search(line):
                return {"type": event_type, "detail": line.strip()}
        return None
