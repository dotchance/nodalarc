# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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

import nats
from nodalarc.nats_channels import node_agent_subject
from nodalarc.proto import node_agent_pb2

log = logging.getLogger(__name__)


class NodeAgentClient:
    """NATS request/reply client for a single Node Agent host.

    The addr parameter is the K8s node hostname (e.g. "nodal"), used
    to build the NATS subject nodalarc.agent.{hostname}.
    """

    def __init__(self, addr: str) -> None:
        """Initialize client.

        Args:
            addr: K8s node name (e.g. "nodal"). Used as NATS subject.
        """
        self._host = addr
        self._subject = node_agent_subject(self._host)
        self._nc: nats.NATS | None = None
        log.info("NodeAgentClient target: %s (subject=%s)", addr, self._subject)

    def set_nc(self, nc: nats.NATS) -> None:
        """Set shared NATS connection (from Scheduler's connection)."""
        self._nc = nc

    async def _request_async(self, msg_type: bytes, request_bytes: bytes) -> bytes:
        """Send a NATS request and wait for reply."""
        if self._nc is None:
            raise ConnectionError(f"NATS not connected for {self._subject}")
        payload = msg_type + b"\x00" + request_bytes
        resp = await self._nc.request(self._subject, payload, timeout=60)
        return resp.data

    async def async_batch_link_down(
        self, request: node_agent_pb2.BatchLinkDownRequest
    ) -> node_agent_pb2.BatchLinkDownResponse:
        resp_bytes = await self._request_async(b"BatchLinkDown", request.SerializeToString())
        resp = node_agent_pb2.BatchLinkDownResponse()
        resp.ParseFromString(resp_bytes)
        return resp

    async def async_batch_link_up(
        self, request: node_agent_pb2.BatchLinkUpRequest
    ) -> node_agent_pb2.BatchLinkUpResponse:
        resp_bytes = await self._request_async(b"BatchLinkUp", request.SerializeToString())
        resp = node_agent_pb2.BatchLinkUpResponse()
        resp.ParseFromString(resp_bytes)
        return resp

    async def async_set_latency(
        self, request: node_agent_pb2.SetLatencyRequest
    ) -> node_agent_pb2.SetLatencyResponse:
        resp_bytes = await self._request_async(b"SetLatency", request.SerializeToString())
        resp = node_agent_pb2.SetLatencyResponse()
        resp.ParseFromString(resp_bytes)
        return resp

    def close(self) -> None:
        pass  # Shared connection — closed by Scheduler
