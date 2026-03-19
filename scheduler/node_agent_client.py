"""ZMQ DEALER client for Node Agent communication.

Replaces the gRPC stub with ZMQ DEALER socket. Sends serialized
protobuf messages and receives serialized protobuf responses.

The proto message definitions are unchanged — only the transport is ZMQ.
"""

from __future__ import annotations

import logging
import threading

import zmq

from node_agent.proto import node_agent_pb2

log = logging.getLogger(__name__)


class NodeAgentClient:
    """ZMQ DEALER client for a single Node Agent."""

    def __init__(self, addr: str) -> None:
        self._addr = addr
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.DEALER)
        self._sock.setsockopt(zmq.RCVTIMEO, 30000)  # 30s timeout
        self._sock.setsockopt(zmq.SNDTIMEO, 5000)
        self._sock.connect(f"tcp://{addr}")
        self._lock = threading.Lock()
        log.info("NodeAgentClient connected to %s", addr)

    def batch_link_down(
        self, request: node_agent_pb2.BatchLinkDownRequest
    ) -> node_agent_pb2.BatchLinkDownResponse:
        with self._lock:
            self._sock.send_multipart(
                [
                    b"",
                    b"BatchLinkDown",
                    request.SerializeToString(),
                ]
            )
            frames = self._sock.recv_multipart()
            resp = node_agent_pb2.BatchLinkDownResponse()
            resp.ParseFromString(frames[-1])  # Last frame is the response
            return resp

    def batch_link_up(
        self, request: node_agent_pb2.BatchLinkUpRequest
    ) -> node_agent_pb2.BatchLinkUpResponse:
        with self._lock:
            self._sock.send_multipart(
                [
                    b"",
                    b"BatchLinkUp",
                    request.SerializeToString(),
                ]
            )
            frames = self._sock.recv_multipart()
            resp = node_agent_pb2.BatchLinkUpResponse()
            resp.ParseFromString(frames[-1])
            return resp

    def set_latency(
        self, request: node_agent_pb2.SetLatencyRequest
    ) -> node_agent_pb2.SetLatencyResponse:
        with self._lock:
            self._sock.send_multipart(
                [
                    b"",
                    b"SetLatency",
                    request.SerializeToString(),
                ]
            )
            frames = self._sock.recv_multipart()
            resp = node_agent_pb2.SetLatencyResponse()
            resp.ParseFromString(frames[-1])
            return resp

    def get_topology(
        self, request: node_agent_pb2.GetTopologyRequest | None = None
    ) -> node_agent_pb2.GetTopologyResponse:
        if request is None:
            request = node_agent_pb2.GetTopologyRequest()
        with self._lock:
            self._sock.send_multipart(
                [
                    b"",
                    b"GetTopology",
                    request.SerializeToString(),
                ]
            )
            frames = self._sock.recv_multipart()
            resp = node_agent_pb2.GetTopologyResponse()
            resp.ParseFromString(frames[-1])
            return resp

    def close(self) -> None:
        self._sock.close()
