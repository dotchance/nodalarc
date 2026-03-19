"""Node Agent gRPC servicer — delegates to handler functions.

Each RPC method delegates to the corresponding function in handlers.py,
which calls the appropriate namespace_ops.py and ground_bridge.py functions
(migrated from link_manager.py).

The servicer holds the local pid_map (node_id -> PID) discovered at startup
or lazily via the K8s API. When the Scheduler sends pid=0 in gRPC messages
(containerized Scheduler can't discover PIDs), the handler resolves the PID
from this local map.
"""

from __future__ import annotations

import logging

from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_get_topology,
    handle_set_latency,
)
from node_agent.proto.node_agent_pb2_grpc import NodeAgentServiceServicer

log = logging.getLogger(__name__)


class NodeAgentServicer(NodeAgentServiceServicer):
    """gRPC servicer for the Node Agent DaemonSet."""

    def __init__(self, pid_map: dict[str, int] | None = None) -> None:
        self._pid_map = pid_map or {}

    def set_pid_map(self, pid_map: dict[str, int]) -> None:
        """Update the PID map (e.g., after periodic refresh)."""
        self._pid_map = pid_map

    def _ensure_pid_map(self) -> dict[str, int]:
        """Return pid_map, lazily discovering if empty."""
        if not self._pid_map:
            try:
                from node_agent.pid_discovery import discover_local_pod_pids

                self._pid_map = discover_local_pod_pids()
                log.info("Lazy PID discovery: %d pods", len(self._pid_map))
            except Exception as exc:
                log.warning("Lazy PID discovery failed: %s", exc)
        return self._pid_map

    def BatchLinkDown(self, request, context):
        return handle_batch_link_down(request, context, pid_map=self._ensure_pid_map())

    def BatchLinkUp(self, request, context):
        return handle_batch_link_up(request, context, pid_map=self._ensure_pid_map())

    def SetLatency(self, request, context):
        return handle_set_latency(request, context, pid_map=self._ensure_pid_map())

    def GetTopology(self, request, context):
        return handle_get_topology(request, context, pid_map=self._ensure_pid_map())
