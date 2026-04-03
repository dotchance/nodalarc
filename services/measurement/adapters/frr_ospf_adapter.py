# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
"""FRR OSPF protocol adapter — vtysh polling + log file parsing.

Collects OSPF adjacency state via `show ip ospf neighbor` polling and
SPF/LSA events from OSPF log file tailing. Both via kubectl exec.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from datetime import UTC, datetime

from nodalarc.models.metrics import AdapterEvent

log = logging.getLogger(__name__)

# Regex patterns for OSPF log events
_SPF_START_RE = re.compile(
    r"SPF.*timer.*fire|ospf_spf.*schedule|SPF.*calculation.*started",
    re.IGNORECASE,
)
_SPF_END_RE = re.compile(
    r"SPF.*processing.*completed|ospf_spf.*completed|SPF.*calculation.*complete",
    re.IGNORECASE,
)
_LSA_FLOOD_RE = re.compile(
    r"LSA.*flood|ospf_flood.*lsa|Originating.*LSA",
    re.IGNORECASE,
)


def parse_ospf_neighbors(output: str) -> dict[str, dict[str, str]]:
    """Parse `show ip ospf neighbor` output into {router_id:iface: {state, ...}}.

    Example output:
      Neighbor ID     Pri State           Up Time         Dead Time Address         Interface
      10.0.0.1          1 Full/DROther    0:01:23         0:00:35   10.0.1.1        isl0:10.0.1.2
      10.0.1.1          1 Full/DR         0:01:20         0:00:38   10.0.2.1        isl1:10.0.2.2
    """
    neighbors: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Neighbor") or "---" in line:
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        router_id = parts[0]
        # State is typically parts[2] and may contain "/" (Full/DR, Full/DROther, etc.)
        state = parts[2]
        # Interface is last column, may contain ":"
        interface = parts[-1].split(":")[0] if ":" in parts[-1] else parts[-1]
        key = f"{router_id}:{interface}"
        neighbors[key] = {
            "router_id": router_id,
            "interface": interface,
            "state": state,
        }
    return neighbors


def parse_ospf_log_line(line: str) -> dict[str, str] | None:
    """Parse a single OSPF log line for SPF/LSA events."""
    if _SPF_START_RE.search(line):
        return {"type": "spf_start", "detail": line.strip()}
    if _SPF_END_RE.search(line):
        return {"type": "spf_end", "detail": line.strip()}
    if _LSA_FLOOD_RE.search(line):
        return {"type": "lsa_flood", "detail": line.strip()}
    return None


def _is_full_state(state: str) -> bool:
    """Check if OSPF neighbor state indicates full adjacency."""
    return state.startswith("Full")


class FrrOspfAdapter:
    """FRR OSPF adapter — collects adjacency and SPF events."""

    def __init__(self) -> None:
        self._nodes: dict[str, _NodeState] = {}
        self._lock = threading.Lock()

    def start(self, node_id: str, management_ip: str) -> None:
        """Start collecting OSPF events from a node."""
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
            name=f"ospf-log-{node_id}",
        )
        t.start()
        state.log_thread = t

        # Initial neighbor poll
        self._poll_neighbors(node_id)
        log.info(f"OSPF adapter started for {node_id}")

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
        log.info(f"OSPF adapter stopped for {node_id}")

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
        """Manually trigger a neighbor poll."""
        self._poll_neighbors(node_id)

    def trace_path(self, node_id: str, dst_ip: str) -> list[str]:
        """Trace OSPF forwarding path to destination."""
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

        hops = [node_id]
        for line in result.stdout.splitlines():
            line = line.strip()
            if "via" in line:
                match = re.search(r"via\s+([\d.]+)", line)
                if match:
                    hops.append(match.group(1))
        return hops

    def _poll_neighbors(self, node_id: str) -> None:
        """Poll vtysh for current OSPF neighbor state and diff."""
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
                    "show ip ospf neighbor",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            log.warning(f"OSPF neighbor poll timed out for {node_id}")
            return

        if result.returncode != 0:
            log.warning(f"OSPF neighbor poll failed for {node_id}: {result.stderr}")
            return

        current = parse_ospf_neighbors(result.stdout)
        now = datetime.now(UTC)

        with self._lock:
            prev = state.last_neighbors

            # New adjacencies or transitions to Full
            for key, info in current.items():
                prev_info = prev.get(key)
                if prev_info is None and _is_full_state(info["state"]):
                    state.events.append(
                        AdapterEvent(
                            sim_time=now,
                            wall_time=now,
                            node_id=node_id,
                            event_type="adjacency_up",
                            event_data={
                                "source": "vtysh_poll",
                                "router_id": info["router_id"],
                                "interface": info["interface"],
                                "state": info["state"],
                            },
                        )
                    )
                elif (
                    prev_info
                    and not _is_full_state(prev_info["state"])
                    and _is_full_state(info["state"])
                ):
                    state.events.append(
                        AdapterEvent(
                            sim_time=now,
                            wall_time=now,
                            node_id=node_id,
                            event_type="adjacency_up",
                            event_data={
                                "source": "vtysh_poll",
                                "router_id": info["router_id"],
                                "interface": info["interface"],
                                "state": info["state"],
                                "previous_state": prev_info["state"],
                            },
                        )
                    )

            # Lost adjacencies (was Full, now gone or not Full)
            for key, info in prev.items():
                if _is_full_state(info["state"]):
                    cur_info = current.get(key)
                    if cur_info is None or not _is_full_state(cur_info["state"]):
                        state.events.append(
                            AdapterEvent(
                                sim_time=now,
                                wall_time=now,
                                node_id=node_id,
                                event_type="adjacency_down",
                                event_data={
                                    "source": "vtysh_poll",
                                    "router_id": info["router_id"],
                                    "interface": info["interface"],
                                    "previous_state": info["state"],
                                },
                            )
                        )

            state.last_neighbors = current

    def _tail_log(self, node_id: str) -> None:
        """Background thread: tail OSPF log file via kubectl exec."""
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
                    "/var/log/frr/ospfd.log",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            state.tail_proc = proc

            for line in proc.stdout:
                parsed = parse_ospf_log_line(line)
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
            log.warning(f"OSPF log tailer for {node_id} failed: {exc}")


class _NodeState:
    """Per-node tracking state for the OSPF adapter."""

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
