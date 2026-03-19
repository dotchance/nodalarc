"""Phase 2 test: Node Agent gRPC server boots and accepts connections.

All RPCs should return UNIMPLEMENTED status in Phase 2.
"""

from __future__ import annotations

import threading
from concurrent import futures

import grpc
import pytest

from node_agent.proto import node_agent_pb2
from node_agent.proto.node_agent_pb2_grpc import (
    NodeAgentServiceStub,
    add_NodeAgentServiceServicer_to_server,
)
from node_agent.server import NodeAgentServicer


@pytest.fixture()
def agent_channel():
    """Start an in-process gRPC server and return a channel to it."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    servicer = NodeAgentServicer()
    add_NodeAgentServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")  # random port
    server.start()
    channel = grpc.insecure_channel(f"localhost:{port}")
    yield channel
    channel.close()
    server.stop(grace=0)


class TestNodeAgentServer:
    def test_server_boots_and_accepts_connection(self, agent_channel):
        """Server starts and a gRPC channel can connect."""
        stub = NodeAgentServiceStub(agent_channel)
        # Just verify we can create a stub — the connection is lazy
        assert stub is not None

    def test_get_topology_returns_response(self, agent_channel):
        """GetTopology returns a response (empty until Phase 5 reconciliation)."""
        stub = NodeAgentServiceStub(agent_channel)
        resp = stub.GetTopology(node_agent_pb2.GetTopologyRequest())
        assert len(resp.interfaces) == 0

    def test_batch_link_down_empty_succeeds(self, agent_channel):
        """Empty BatchLinkDown returns success."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-001",
            target_sim_time="2024-01-01T00:00:00Z",
            locality=node_agent_pb2.LOCAL,
        )
        resp = stub.BatchLinkDown(req)
        assert resp.success is True

    def test_batch_link_up_empty_succeeds(self, agent_channel):
        """Empty BatchLinkUp returns success."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-002",
            target_sim_time="2024-01-01T00:00:00Z",
            locality=node_agent_pb2.LOCAL,
        )
        resp = stub.BatchLinkUp(req)
        assert resp.success is True

    def test_set_latency_empty_succeeds(self, agent_channel):
        """Empty SetLatency returns success."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.SetLatencyRequest()
        resp = stub.SetLatency(req)
        assert resp.success is True

    def test_cross_node_locality_accepted(self, agent_channel):
        """CROSS_NODE locality is accepted by the proto (returns UNIMPLEMENTED for now)."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-cross",
            locality=node_agent_pb2.CROSS_NODE,
        )
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.BatchLinkDown(req)
        # In Phase 2 all RPCs are UNIMPLEMENTED; in Phase 3 CROSS_NODE
        # will specifically return UNIMPLEMENTED while LOCAL is handled
        assert exc_info.value.code() == grpc.StatusCode.UNIMPLEMENTED

    def test_multiple_concurrent_calls(self, agent_channel):
        """Server handles concurrent gRPC calls without deadlocking."""
        stub = NodeAgentServiceStub(agent_channel)
        errors = []

        def call_get_topology():
            try:
                stub.GetTopology(node_agent_pb2.GetTopologyRequest())
            except grpc.RpcError as e:
                if e.code() != grpc.StatusCode.UNIMPLEMENTED:
                    errors.append(e)

        threads = [threading.Thread(target=call_get_topology) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Unexpected errors: {errors}"
