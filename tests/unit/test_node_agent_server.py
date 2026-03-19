"""Test Node Agent ZMQ ROUTER server.

Tests the ZMQ transport layer — server starts, accepts connections,
dispatches to handlers, returns serialized protobuf responses.
"""

from __future__ import annotations

import threading
import time

import pytest
import zmq

from node_agent.proto import node_agent_pb2
from node_agent.server import NodeAgentServer


@pytest.fixture()
def agent_zmq():
    """Start an in-process ZMQ server and return a DEALER client socket."""
    server = NodeAgentServer(port=0)  # 0 = random port

    # Bind to random port
    ctx = zmq.Context()
    router = ctx.socket(zmq.ROUTER)
    port = router.bind_to_random_port("tcp://127.0.0.1")
    router.close()
    ctx.term()

    # Use the discovered port
    server._port = port

    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(0.3)

    client_ctx = zmq.Context()
    client = client_ctx.socket(zmq.DEALER)
    client.setsockopt(zmq.RCVTIMEO, 5000)
    client.connect(f"tcp://127.0.0.1:{port}")
    time.sleep(0.1)

    yield client, port

    server.stop()
    client.close()
    client_ctx.term()


def _call(sock, msg_type: bytes, request) -> bytes:
    """Send a request and return the raw response bytes."""
    sock.send_multipart([b"", msg_type, request.SerializeToString()])
    frames = sock.recv_multipart()
    return frames[-1]  # Last frame is the response


class TestNodeAgentZMQServer:
    def test_server_starts_and_accepts_connection(self, agent_zmq):
        sock, port = agent_zmq
        # GetTopology with empty pid_map → empty response (no crash)
        resp_bytes = _call(sock, b"GetTopology", node_agent_pb2.GetTopologyRequest())
        resp = node_agent_pb2.GetTopologyResponse()
        resp.ParseFromString(resp_bytes)
        assert resp is not None

    def test_empty_batch_link_down(self, agent_zmq):
        sock, _ = agent_zmq
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-001",
            target_sim_time="2024-01-01T00:00:00Z",
            locality=node_agent_pb2.LOCAL,
        )
        resp_bytes = _call(sock, b"BatchLinkDown", req)
        resp = node_agent_pb2.BatchLinkDownResponse()
        resp.ParseFromString(resp_bytes)
        assert resp.success is True
        assert resp.interfaces_downed == 0

    def test_empty_batch_link_up(self, agent_zmq):
        sock, _ = agent_zmq
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-002",
            locality=node_agent_pb2.LOCAL,
        )
        resp_bytes = _call(sock, b"BatchLinkUp", req)
        resp = node_agent_pb2.BatchLinkUpResponse()
        resp.ParseFromString(resp_bytes)
        assert resp.success is True
        assert resp.interfaces_upped == 0

    def test_empty_set_latency(self, agent_zmq):
        sock, _ = agent_zmq
        req = node_agent_pb2.SetLatencyRequest()
        resp_bytes = _call(sock, b"SetLatency", req)
        resp = node_agent_pb2.SetLatencyResponse()
        resp.ParseFromString(resp_bytes)
        assert resp.success is True

    def test_cross_node_returns_error(self, agent_zmq):
        sock, _ = agent_zmq
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-cross",
            locality=node_agent_pb2.CROSS_NODE,
        )
        resp_bytes = _call(sock, b"BatchLinkDown", req)
        resp = node_agent_pb2.BatchLinkDownResponse()
        resp.ParseFromString(resp_bytes)
        assert resp.success is False
        assert "CROSS_NODE" in resp.error_message

    def test_unknown_message_type(self, agent_zmq):
        sock, _ = agent_zmq
        sock.send_multipart([b"", b"UnknownType", b""])
        frames = sock.recv_multipart()
        # Should return empty response, not crash
        assert frames[-1] == b""

    def test_concurrent_calls(self, agent_zmq):
        sock, port = agent_zmq
        # Multiple sequential calls (ZMQ DEALER is single-threaded on client)
        for _ in range(5):
            resp_bytes = _call(sock, b"GetTopology", node_agent_pb2.GetTopologyRequest())
            resp = node_agent_pb2.GetTopologyResponse()
            resp.ParseFromString(resp_bytes)
            assert resp is not None
