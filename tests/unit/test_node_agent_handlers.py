"""Node Agent handler tests — call handlers directly, no transport.

Tests handler logic:
- CROSS_NODE returns error in response
- Empty batches succeed
- Bad PIDs return structured errors
- GetTopology with various pid_map states
"""

from __future__ import annotations

from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_get_topology,
    handle_set_latency,
)
from node_agent.proto import node_agent_pb2


class TestBatchLinkDown:
    def test_cross_node_returns_error(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-cross-down",
            locality=node_agent_pb2.CROSS_NODE,
        )
        resp = handle_batch_link_down(req)
        assert resp.success is False
        assert "CROSS_NODE" in resp.error_message

    def test_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-empty-down",
            locality=node_agent_pb2.LOCAL,
        )
        resp = handle_batch_link_down(req)
        assert resp.success is True
        assert resp.interfaces_downed == 0
        assert resp.error_message == ""

    def test_nonexistent_pid_returns_error_in_response(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-bad-pid",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    pid=999999,
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = handle_batch_link_down(req)
        assert resp.success is False
        assert resp.interfaces_downed == 0
        assert resp.error_message != ""

    def test_multiple_links_one_fails(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-partial",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    pid=999999,
                    link_type=node_agent_pb2.ISL,
                ),
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S01",
                    interface_name="isl1",
                    pid=999998,
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = handle_batch_link_down(req)
        assert resp.success is False
        assert resp.interfaces_downed == 0


class TestBatchLinkUp:
    def test_cross_node_returns_error(self):
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-cross-up",
            locality=node_agent_pb2.CROSS_NODE,
        )
        resp = handle_batch_link_up(req)
        assert resp.success is False
        assert "CROSS_NODE" in resp.error_message

    def test_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-empty-up",
            locality=node_agent_pb2.LOCAL,
        )
        resp = handle_batch_link_up(req)
        assert resp.success is True
        assert resp.interfaces_upped == 0

    def test_nonexistent_pid_returns_error_in_response(self):
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-bad-pid-up",
            locality=node_agent_pb2.LOCAL,
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    pid=999999,
                    link_type=node_agent_pb2.ISL,
                    latency_ms=3.0,
                    bandwidth_mbps=1000.0,
                ),
            ],
        )
        resp = handle_batch_link_up(req)
        assert resp.success is False
        assert resp.error_message != ""


class TestSetLatency:
    def test_empty_request_succeeds(self):
        req = node_agent_pb2.SetLatencyRequest()
        resp = handle_set_latency(req)
        assert resp.success is True
        assert resp.entries_updated == 0

    def test_nonexistent_pid_returns_error(self):
        req = node_agent_pb2.SetLatencyRequest(
            entries=[
                node_agent_pb2.LatencyEntry(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    pid=999999,
                    latency_ms=5.0,
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = handle_set_latency(req)
        assert resp.success is False
        assert resp.entries_updated == 0


class TestGetTopology:
    def test_empty_pid_map_returns_empty(self):
        resp = handle_get_topology(node_agent_pb2.GetTopologyRequest())
        assert len(resp.interfaces) == 0

    def test_bad_pid_returns_empty(self):
        resp = handle_get_topology(
            node_agent_pb2.GetTopologyRequest(),
            pid_map={"sat-P00S00": 999999},
        )
        assert len(resp.interfaces) == 0

    def test_pid_map_with_real_pid(self):
        # PID 1 (init) has no isl/gnd interfaces
        resp = handle_get_topology(
            node_agent_pb2.GetTopologyRequest(),
            pid_map={"test-node": 1},
        )
        assert resp is not None
