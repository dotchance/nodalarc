# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Node Agent NATS server.

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
from nodalarc.proto import node_agent_pb2

from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_get_topology,
    handle_set_latency,
)

log = logging.getLogger(__name__)


class NodeAgentServer:
    """NATS request/reply server for the Node Agent DaemonSet.

    The pid_map MUST be populated before run() is called. The server
    does NOT discover PIDs — that is the wiring thread's responsibility.
    This enforces startup ordering: wiring → pid_map → NATS server.
    """

    def __init__(self, pid_map: dict[str, int] | None = None) -> None:
        self._pid_map = pid_map or {}
        self._running = False
        self._nc: nats.NATS | None = None

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

        pid_map is populated by the wiring thread before the server starts.
        No lazy discovery here — if a node_id is missing, the handler returns
        a clear error (PidNotFoundError from handlers.py).
        """
        pid_map = self._pid_map

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
