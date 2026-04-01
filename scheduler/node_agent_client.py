"""NATS request/reply client for Node Agent communication.

Sends serialized protobuf requests to nodalarc.agent.{host} and
receives serialized protobuf responses. The proto message definitions
are unchanged — only the transport is NATS.

The Scheduler groups operations by host (PodLocationMap.agent_addr)
and sends one batch per host. The Node Agent on that host processes
the batch with concurrent execution (ThreadPoolExecutor) and replies.
"""

from __future__ import annotations

import logging
import threading

import nats
from nodalarc.nats_channels import nats_url

from node_agent.proto import node_agent_pb2

log = logging.getLogger(__name__)


class NodeAgentClient:
    """NATS request/reply client for a single Node Agent host.

    The addr parameter is the K8s node hostname (e.g. "nodal"), used
    to build the NATS subject nodalarc.agent.{hostname}.
    """

    def __init__(self, addr: str) -> None:
        """Initialize client.

        Args:
            addr: K8s node hostname or legacy "host:port" address.
                  If contains ":", extracts hostname before the colon.
        """
        # Legacy addr format is "192.168.10.202:50100" — extract hostname
        # For NATS we need the K8s node name, not IP:port
        self._host = addr.split(":")[0] if ":" in addr else addr
        self._subject = f"nodalarc.agent.{self._host}"
        self._nc: nats.NATS | None = None
        self._lock = threading.Lock()
        log.info("NodeAgentClient target: %s (subject=%s)", addr, self._subject)

    def _get_nc(self) -> nats.NATS:
        """Get or create NATS connection (lazy, thread-safe).

        Uses a single connect attempt with timeout — does not retry
        indefinitely on first connect (unlike the long-lived subscriber
        connections which use max_reconnect_attempts=-1).
        """
        if self._nc is not None:
            return self._nc
        with self._lock:
            if self._nc is not None:
                return self._nc
            import asyncio

            loop = asyncio.new_event_loop()
            # First connect: fail fast with hard timeout wrapper.
            # nats-py's connect_timeout and allow_reconnect don't reliably
            # prevent blocking on connection failure. asyncio.wait_for
            # ensures we don't block longer than 3 seconds.
            self._nc = loop.run_until_complete(
                asyncio.wait_for(
                    nats.connect(
                        nats_url(),
                        connect_timeout=2,
                        allow_reconnect=False,
                    ),
                    timeout=3,
                )
            )
            loop.close()
            return self._nc

    def _request_sync(self, msg_type: bytes, request_bytes: bytes) -> bytes:
        """Send a NATS request and wait for reply (synchronous wrapper)."""
        import asyncio

        try:
            nc = self._get_nc()
        except Exception as exc:
            raise ConnectionError(f"NATS connection failed for {self._subject}: {exc}") from exc

        payload = msg_type + b"\x00" + request_bytes

        async def _do_request():
            resp = await nc.request(self._subject, payload, timeout=30)
            return resp.data

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_do_request())
        finally:
            loop.close()

    def batch_link_down(
        self, request: node_agent_pb2.BatchLinkDownRequest
    ) -> node_agent_pb2.BatchLinkDownResponse:
        resp_bytes = self._request_sync(b"BatchLinkDown", request.SerializeToString())
        resp = node_agent_pb2.BatchLinkDownResponse()
        resp.ParseFromString(resp_bytes)
        return resp

    def batch_link_up(
        self, request: node_agent_pb2.BatchLinkUpRequest
    ) -> node_agent_pb2.BatchLinkUpResponse:
        resp_bytes = self._request_sync(b"BatchLinkUp", request.SerializeToString())
        resp = node_agent_pb2.BatchLinkUpResponse()
        resp.ParseFromString(resp_bytes)
        return resp

    def set_latency(
        self, request: node_agent_pb2.SetLatencyRequest
    ) -> node_agent_pb2.SetLatencyResponse:
        resp_bytes = self._request_sync(b"SetLatency", request.SerializeToString())
        resp = node_agent_pb2.SetLatencyResponse()
        resp.ParseFromString(resp_bytes)
        return resp

    def get_topology(
        self, request: node_agent_pb2.GetTopologyRequest | None = None
    ) -> node_agent_pb2.GetTopologyResponse:
        if request is None:
            request = node_agent_pb2.GetTopologyRequest()
        resp_bytes = self._request_sync(b"GetTopology", request.SerializeToString())
        resp = node_agent_pb2.GetTopologyResponse()
        resp.ParseFromString(resp_bytes)
        return resp

    def close(self) -> None:
        if self._nc is not None:
            import asyncio

            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._nc.close())
            loop.close()
            self._nc = None
