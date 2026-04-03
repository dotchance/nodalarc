# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
"""FRR IS-IS protocol adapter — vtysh polling + log file parsing.

Collects IS-IS adjacency state via `show isis neighbor` polling and
SPF/LSP events from IS-IS log file tailing. Both via kubectl exec.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from datetime import UTC, datetime

from nodalarc.models.metrics import AdapterEvent

log = logging.getLogger(__name__)

# Regex patterns for IS-IS log events
_SPF_START_RE = re.compile(
    r"ISIS-SPF.*Scheduling.*SPF|isis_spf.*schedule|SPF.*algorithm.*started",
    re.IGNORECASE,
)
_SPF_END_RE = re.compile(
    r"ISIS-SPF.*completed|isis_spf.*run.*completed|SPF.*algorithm.*complete",
    re.IGNORECASE,
)
_LSP_FLOOD_RE = re.compile(
    r"LSP.*flood|isis_lsp.*generated|Sending.*LSP",
    re.IGNORECASE,
)


def parse_isis_neighbors(output: str) -> dict[str, dict[str, str]]:
    """Parse `show isis neighbor` output into {system_id: {state, interface}}.

    Example output:
      Area NODAL:
        System Id           Interface   L  State        Holdtime SNPA
        0000.0001.0001      isl0        1  Up           29       P2P
        0000.0001.0001      isl0        2  Up           28       P2P
    """
    neighbors: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Area") or line.startswith("System"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        system_id = parts[0]
        interface = parts[1]
        # Level is parts[2], State is parts[3]
        state = parts[3]
        # Use system_id + interface as key for deduplication across levels
        key = f"{system_id}:{interface}"
        # Keep highest-priority state (Up > Initializing > Down)
        if key not in neighbors or state == "Up":
            neighbors[key] = {
                "system_id": system_id,
                "interface": interface,
                "state": state,
            }
    return neighbors


def parse_isis_log_line(line: str) -> dict[str, str] | None:
    """Parse a single IS-IS log line for SPF/LSP events.

    Returns event dict with type and details, or None if not relevant.
    """
    if _SPF_START_RE.search(line):
        return {"type": "spf_start", "detail": line.strip()}
    if _SPF_END_RE.search(line):
        return {"type": "spf_end", "detail": line.strip()}
    if _LSP_FLOOD_RE.search(line):
        return {"type": "lsp_flood", "detail": line.strip()}
    return None


class FrrIsisAdapter:
    """FRR IS-IS adapter — collects adjacency and SPF events."""

    def __init__(self) -> None:
        self._nodes: dict[str, _NodeState] = {}
        self._lock = threading.Lock()

    def start(self, node_id: str, management_ip: str) -> None:
        """Start collecting IS-IS events from a node."""
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
            name=f"isis-log-{node_id}",
        )
        t.start()
        state.log_thread = t

        # Do initial neighbor poll
        self._poll_neighbors(node_id)
        log.info(f"IS-IS adapter started for {node_id}")

    def stop(self, node_id: str) -> None:
        """Stop collection for a node."""
        with self._lock:
            state = self._nodes.pop(node_id, None)
        if state and state.tail_proc:
            try:
                state.tail_proc.terminate()
                state.tail_proc.wait(timeout=5)
            except Exception:
                pass
        log.info(f"IS-IS adapter stopped for {node_id}")

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
        """Trace IS-IS forwarding path to destination."""
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
                # Extract next-hop IP
                match = re.search(r"via\s+([\d.]+)", line)
                if match:
                    hops.append(match.group(1))
        return hops

    def _poll_neighbors(self, node_id: str) -> None:
        """Poll vtysh for current IS-IS neighbor state and diff."""
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
                    "show isis neighbor",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            log.warning(f"IS-IS neighbor poll timed out for {node_id}")
            return

        if result.returncode != 0:
            log.warning(f"IS-IS neighbor poll failed for {node_id}: {result.stderr}")
            return

        current = parse_isis_neighbors(result.stdout)
        now = datetime.now(UTC)

        with self._lock:
            # Diff against previous state
            prev = state.last_neighbors

            # New adjacencies or state changes to Up
            for key, info in current.items():
                prev_info = prev.get(key)
                if prev_info is None and info["state"] == "Up":
                    state.events.append(
                        AdapterEvent(
                            sim_time=now,
                            wall_time=now,
                            node_id=node_id,
                            event_type="adjacency_up",
                            event_data={
                                "source": "vtysh_poll",
                                "system_id": info["system_id"],
                                "interface": info["interface"],
                                "state": info["state"],
                            },
                        )
                    )
                elif prev_info and prev_info["state"] != "Up" and info["state"] == "Up":
                    state.events.append(
                        AdapterEvent(
                            sim_time=now,
                            wall_time=now,
                            node_id=node_id,
                            event_type="adjacency_up",
                            event_data={
                                "source": "vtysh_poll",
                                "system_id": info["system_id"],
                                "interface": info["interface"],
                                "state": info["state"],
                                "previous_state": prev_info["state"],
                            },
                        )
                    )

            # Lost adjacencies
            for key, info in prev.items():
                if key not in current and info["state"] == "Up":
                    state.events.append(
                        AdapterEvent(
                            sim_time=now,
                            wall_time=now,
                            node_id=node_id,
                            event_type="adjacency_down",
                            event_data={
                                "source": "vtysh_poll",
                                "system_id": info["system_id"],
                                "interface": info["interface"],
                                "previous_state": info["state"],
                            },
                        )
                    )

            state.last_neighbors = current

    def _tail_log(self, node_id: str) -> None:
        """Background thread: tail IS-IS log file via kubectl exec."""
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
                    "/var/log/frr/isisd.log",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            state.tail_proc = proc

            for line in proc.stdout:
                parsed = parse_isis_log_line(line)
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
            log.warning(f"IS-IS log tailer for {node_id} failed: {exc}")


class _NodeState:
    """Per-node tracking state for the IS-IS adapter."""

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
        # Fallback
        self._namespace = "nodalarc"
        self._pod_name = self.node_id
