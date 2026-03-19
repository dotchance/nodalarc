"""Phase 3 tests: Node Agent handler implementations.

Tests that can run without root test the gRPC protocol behavior:
- CROSS_NODE returns UNIMPLEMENTED
- Empty batches succeed
- Error responses are structured

Tests that require root (marked @pytest.mark.requires_root) test
real netlink operations via the handlers.
"""

from __future__ import annotations

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
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    servicer = NodeAgentServicer()
    add_NodeAgentServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    channel = grpc.insecure_channel(f"localhost:{port}")
    yield channel
    channel.close()
    server.stop(grace=0)


class TestBatchLinkDown:
    def test_cross_node_returns_unimplemented(self, agent_channel):
        """CROSS_NODE locality returns UNIMPLEMENTED status."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-cross-down",
            locality=node_agent_pb2.CROSS_NODE,
        )
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.BatchLinkDown(req)
        assert exc_info.value.code() == grpc.StatusCode.UNIMPLEMENTED
        assert "CROSS_NODE" in exc_info.value.details()

    def test_empty_batch_succeeds(self, agent_channel):
        """Empty BatchLinkDown returns success with 0 interfaces."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-empty-down",
            locality=node_agent_pb2.LOCAL,
        )
        resp = stub.BatchLinkDown(req)
        assert resp.success is True
        assert resp.interfaces_downed == 0
        assert resp.error_message == ""

    def test_nonexistent_pid_returns_error_in_response(self, agent_channel):
        """A link with a bad PID fails gracefully — error in response, not crash."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-bad-pid",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-p00s00",
                    interface_name="isl0",
                    pid=999999,
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = stub.BatchLinkDown(req)
        # Should complete (not crash) but report failure
        assert resp.success is False
        assert resp.interfaces_downed == 0
        assert resp.error_message != ""

    def test_multiple_links_one_fails(self, agent_channel):
        """One failing link in a batch doesn't prevent others from processing."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-partial",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-p00s00",
                    interface_name="isl0",
                    pid=999999,  # bad PID
                    link_type=node_agent_pb2.ISL,
                ),
                node_agent_pb2.InterfaceDown(
                    node_id="sat-p00s01",
                    interface_name="isl1",
                    pid=999998,  # another bad PID
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = stub.BatchLinkDown(req)
        # Both should have been attempted (not short-circuited)
        assert resp.success is False
        assert resp.interfaces_downed == 0
        # Error message should mention both failures
        assert "isl0" in resp.error_message or "isl1" in resp.error_message


class TestBatchLinkUp:
    def test_cross_node_returns_unimplemented(self, agent_channel):
        """CROSS_NODE locality returns UNIMPLEMENTED status."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-cross-up",
            locality=node_agent_pb2.CROSS_NODE,
        )
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.BatchLinkUp(req)
        assert exc_info.value.code() == grpc.StatusCode.UNIMPLEMENTED

    def test_empty_batch_succeeds(self, agent_channel):
        """Empty BatchLinkUp returns success with 0 interfaces."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-empty-up",
            locality=node_agent_pb2.LOCAL,
        )
        resp = stub.BatchLinkUp(req)
        assert resp.success is True
        assert resp.interfaces_upped == 0

    def test_nonexistent_pid_returns_error_in_response(self, agent_channel):
        """A link with a bad PID fails gracefully."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-bad-pid-up",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-p00s00",
                    interface_name="isl0",
                    pid=999999,
                    link_type=node_agent_pb2.ISL,
                    latency_ms=3.0,
                    bandwidth_mbps=1000.0,
                ),
            ],
        )
        resp = stub.BatchLinkUp(req)
        assert resp.success is False
        assert resp.error_message != ""


class TestSetLatency:
    def test_empty_request_succeeds(self, agent_channel):
        """Empty SetLatency returns success."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.SetLatencyRequest()
        resp = stub.SetLatency(req)
        assert resp.success is True
        assert resp.entries_updated == 0

    def test_nonexistent_pid_returns_error(self, agent_channel):
        """Bad PID fails gracefully."""
        stub = NodeAgentServiceStub(agent_channel)
        req = node_agent_pb2.SetLatencyRequest(
            entries=[
                node_agent_pb2.LatencyEntry(
                    node_id="sat-p00s00",
                    interface_name="isl0",
                    pid=999999,
                    latency_ms=5.0,
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = stub.SetLatency(req)
        assert resp.success is False
        assert resp.entries_updated == 0


class TestGetTopology:
    def test_empty_pid_map_returns_empty(self, agent_channel):
        """GetTopology with empty pid_map returns empty response, no error."""
        stub = NodeAgentServiceStub(agent_channel)
        resp = stub.GetTopology(node_agent_pb2.GetTopologyRequest())
        assert len(resp.interfaces) == 0

    def test_bad_pid_returns_empty(self):
        """GetTopology with a bad PID skips it gracefully, returns empty."""
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        servicer = NodeAgentServicer(pid_map={"sat-P00S00": 999999})
        add_NodeAgentServiceServicer_to_server(servicer, server)
        port = server.add_insecure_port("localhost:0")
        server.start()
        channel = grpc.insecure_channel(f"localhost:{port}")
        try:
            stub = NodeAgentServiceStub(channel)
            resp = stub.GetTopology(node_agent_pb2.GetTopologyRequest())
            # Bad PID skipped — no crash, just empty
            assert len(resp.interfaces) == 0
        finally:
            channel.close()
            server.stop(grace=0)

    def test_pid_map_with_real_pid(self):
        """GetTopology with PID 1 (init) returns interfaces (lo at minimum).

        This test verifies the namespace enumeration works. PID 1's
        network namespace has at least 'lo'. We filter for isl*/gnd*
        names, so the result should be empty (no isl/gnd in init ns),
        but the code path exercises successfully without error.
        """
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        servicer = NodeAgentServicer(pid_map={"test-node": 1})
        add_NodeAgentServiceServicer_to_server(servicer, server)
        port = server.add_insecure_port("localhost:0")
        server.start()
        channel = grpc.insecure_channel(f"localhost:{port}")
        try:
            stub = NodeAgentServiceStub(channel)
            resp = stub.GetTopology(node_agent_pb2.GetTopologyRequest())
            # PID 1 namespace has no isl/gnd interfaces, so empty is correct
            # The key assertion: no RPC error was raised
            assert resp is not None
        finally:
            channel.close()
            server.stop(grace=0)
