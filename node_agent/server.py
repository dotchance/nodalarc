"""Node Agent NATS server — replaces ZMQ ROUTER transport.

Subscribes to NATS subject nodalarc.agent.{hostname} for batch commands
from the Scheduler. Uses NATS request/reply — Scheduler publishes a request,
Node Agent processes the batch (concurrent execution preserved via thread pool),
and replies with the aggregated result.

Proto message definitions are unchanged — still used for serialization.
"""

from __future__ import annotations

import asyncio
import logging
import socket

import nats
from nodalarc.nats_channels import NATS_CONNECT_OPTIONS, nats_url

from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_get_topology,
    handle_set_latency,
)
from node_agent.proto import node_agent_pb2

log = logging.getLogger(__name__)


class NodeAgentServer:
    """NATS request/reply server for the Node Agent DaemonSet."""

    def __init__(self, port: int = 50100, pid_map: dict[str, int] | None = None) -> None:
        self._port = port  # kept for backward compat / logging
        self._pid_map = pid_map or {}
        self._running = False
        self._nc: nats.NATS | None = None

    def set_pid_map(self, pid_map: dict[str, int]) -> None:
        self._pid_map = pid_map

    def _ensure_pid_map(self) -> dict[str, int]:
        """Return pid_map, refreshing if stale (fewer than expected pods)."""
        import time as _time

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
        """Run the NATS request/reply server. Blocks until stop() is called."""
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Async NATS subscription loop."""
        self._running = True

        nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
        self._nc = nc

        # Subscribe to per-host subject — one Node Agent per K8s node
        hostname = socket.gethostname()
        subject = f"nodalarc.agent.{hostname}"

        async def _handle_request(msg):
            """Handle a single NATS request — dispatch to handler, reply."""
            try:
                # Message format: first line is message type, rest is payload
                # Using a simple header format: type\0payload
                data = msg.data
                sep = data.find(b"\x00")
                if sep < 0:
                    log.warning("Malformed NATS message: no type separator")
                    await msg.respond(b"")
                    return

                msg_type = data[:sep]
                payload = data[sep + 1 :]

                # Run handler in executor to keep event loop responsive
                # (handlers do blocking namespace operations)
                loop = asyncio.get_running_loop()
                response_bytes = await loop.run_in_executor(None, self._dispatch, msg_type, payload)
                await msg.respond(response_bytes)
            except Exception as exc:
                log.error("Handler error: %s", exc, exc_info=True)
                await msg.respond(b"")

        sub = await nc.subscribe(subject, cb=_handle_request)
        log.info("NodeAgent NATS listening on subject %s", subject)

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await sub.unsubscribe()
            await nc.close()
            log.info("NodeAgent NATS server stopped")

    def stop(self) -> None:
        self._running = False

    def _dispatch(self, msg_type: bytes, payload: bytes) -> bytes:
        """Dispatch a request to the appropriate handler, return serialized response.

        Runs in a thread pool executor — handlers use concurrent.futures
        internally for batch parallelism (ThreadPoolExecutor in handlers.py).
        """
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
