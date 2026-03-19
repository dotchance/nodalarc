"""Node Agent ZMQ ROUTER server — replaces gRPC transport.

The gRPC C extension is incompatible with hostPID:true containers
(accept4 SOCK_CLOEXEC|SOCK_NONBLOCK fails intermittently). ZMQ ROUTER/
DEALER provides the same async request/response pattern without the
C extension dependency.

Message envelope (ROUTER socket):
  Frame 0: identity (from ROUTER — pass back unchanged)
  Frame 1: empty delimiter
  Frame 2: message type (b"BatchLinkDown", b"BatchLinkUp", etc.)
  Frame 3: serialized protobuf request bytes

Response:
  Frame 0: identity (echoed back)
  Frame 1: empty delimiter
  Frame 2: serialized protobuf response bytes

Proto message definitions are unchanged — still used for serialization.
"""

from __future__ import annotations

import logging

import zmq

from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_get_topology,
    handle_set_latency,
)
from node_agent.proto import node_agent_pb2

log = logging.getLogger(__name__)


class NodeAgentServer:
    """ZMQ ROUTER server for the Node Agent DaemonSet."""

    def __init__(self, port: int = 50100, pid_map: dict[str, int] | None = None) -> None:
        self._port = port
        self._pid_map = pid_map or {}
        self._running = False
        self._ctx: zmq.Context | None = None
        self._sock: zmq.Socket | None = None

    def set_pid_map(self, pid_map: dict[str, int]) -> None:
        self._pid_map = pid_map

    def _ensure_pid_map(self) -> dict[str, int]:
        """Return pid_map, refreshing if stale (fewer than expected pods)."""
        import time as _time

        # Refresh at most once every 10 seconds to avoid hammering K8s API
        now = _time.monotonic()
        if not hasattr(self, "_last_refresh"):
            self._last_refresh = 0.0
        if now - self._last_refresh < 10.0 and self._pid_map:
            return self._pid_map

        try:
            from node_agent.pid_discovery import discover_local_pod_pids

            new_map = discover_local_pod_pids()
            if len(new_map) > len(self._pid_map):
                log.info("PID map refreshed: %d -> %d pods", len(self._pid_map), len(new_map))
                self._pid_map = new_map
            elif not self._pid_map:
                self._pid_map = new_map
                log.info("Initial PID discovery: %d pods", len(new_map))
            self._last_refresh = now
        except Exception as exc:
            log.warning("PID discovery failed: %s", exc)
        return self._pid_map

    def run(self) -> None:
        """Run the ZMQ ROUTER poll loop. Blocks until stop() is called."""
        self._running = True
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.ROUTER)
        self._sock.bind(f"tcp://0.0.0.0:{self._port}")
        log.info("NodeAgent ZMQ ROUTER listening on port %d", self._port)

        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)

        while self._running:
            socks = dict(poller.poll(timeout=1000))
            if self._sock not in socks:
                continue

            frames = self._sock.recv_multipart()
            if len(frames) < 4:
                log.warning("Malformed message: %d frames", len(frames))
                continue

            identity = frames[0]
            # frames[1] is empty delimiter
            msg_type = frames[2]
            payload = frames[3]

            try:
                response_bytes = self._dispatch(msg_type, payload)
            except Exception as exc:
                log.error("Handler error for %s: %s", msg_type, exc, exc_info=True)
                response_bytes = b""

            self._sock.send_multipart([identity, b"", response_bytes])

        self._sock.close()
        self._ctx.term()
        log.info("NodeAgent ZMQ server stopped")

    def stop(self) -> None:
        self._running = False

    def _dispatch(self, msg_type: bytes, payload: bytes) -> bytes:
        """Dispatch a request to the appropriate handler, return serialized response."""
        pid_map = self._ensure_pid_map()

        if msg_type == b"BatchLinkDown":
            request = node_agent_pb2.BatchLinkDownRequest()
            request.ParseFromString(payload)
            response = handle_batch_link_down(request, context=None, pid_map=pid_map)
            return response.SerializeToString()

        elif msg_type == b"BatchLinkUp":
            request = node_agent_pb2.BatchLinkUpRequest()
            request.ParseFromString(payload)
            response = handle_batch_link_up(request, context=None, pid_map=pid_map)
            return response.SerializeToString()

        elif msg_type == b"SetLatency":
            request = node_agent_pb2.SetLatencyRequest()
            request.ParseFromString(payload)
            response = handle_set_latency(request, context=None, pid_map=pid_map)
            return response.SerializeToString()

        elif msg_type == b"GetTopology":
            request = node_agent_pb2.GetTopologyRequest()
            request.ParseFromString(payload)
            response = handle_get_topology(request, context=None, pid_map=pid_map)
            return response.SerializeToString()

        else:
            log.warning("Unknown message type: %s", msg_type)
            return b""
