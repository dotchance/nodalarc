"""Node Agent handler tests — call handlers directly, no transport.

Tests handler logic:
- Per-interface locality (LOCAL/CROSS_NODE)
- Empty batches succeed
- Bad PIDs return structured errors
- None pid_map raises ValueError (wiring never happened)
"""

from __future__ import annotations

import pytest
from nodalarc.proto import node_agent_pb2
from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_set_latency,
)

# All tests pass pid_map={} — an initialized but empty map.
# This represents a node where wiring completed but no session pods
# are scheduled. pid_map=None means wiring never happened and is
# rejected by the handler (ValueError).
EMPTY_PID_MAP: dict[str, int] = {}


class TestBatchLinkDown:
    def test_cross_node_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkDownRequest(batch_id="test-cross-down")
        resp = handle_batch_link_down(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is True
        assert resp.interfaces_downed == 0

    def test_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkDownRequest(batch_id="test-empty-down")
        resp = handle_batch_link_down(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is True
        assert resp.interfaces_downed == 0
        assert resp.error_message == ""

    def test_nonexistent_pid_returns_error_in_response(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-bad-pid",
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = handle_batch_link_down(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is False
        assert resp.interfaces_downed == 0
        assert resp.error_message != ""

    def test_multiple_links_one_fails(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            batch_id="test-partial",
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.ISL,
                ),
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S01",
                    interface_name="isl1",
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = handle_batch_link_down(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is False
        assert resp.interfaces_downed == 0

    def test_none_pid_map_raises(self):
        req = node_agent_pb2.BatchLinkDownRequest(batch_id="test-none")
        with pytest.raises(ValueError, match="pid_map is None"):
            handle_batch_link_down(req, pid_map=None)


class TestBatchLinkUp:
    def test_cross_node_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkUpRequest(batch_id="test-cross-up")
        resp = handle_batch_link_up(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is True
        assert resp.interfaces_upped == 0

    def test_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkUpRequest(batch_id="test-empty-up")
        resp = handle_batch_link_up(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is True
        assert resp.interfaces_upped == 0

    def test_nonexistent_pid_returns_error_in_response(self):
        req = node_agent_pb2.BatchLinkUpRequest(
            batch_id="test-bad-pid-up",
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.ISL,
                    latency_ms=3.0,
                    bandwidth_mbps=1000.0,
                ),
            ],
        )
        resp = handle_batch_link_up(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is False
        assert resp.error_message != ""

    def test_none_pid_map_raises(self):
        req = node_agent_pb2.BatchLinkUpRequest(batch_id="test-none")
        with pytest.raises(ValueError, match="pid_map is None"):
            handle_batch_link_up(req, pid_map=None)


class TestSetLatency:
    def test_empty_request_succeeds(self):
        req = node_agent_pb2.SetLatencyRequest()
        resp = handle_set_latency(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is True
        assert resp.entries_updated == 0

    def test_nonexistent_pid_returns_error(self):
        req = node_agent_pb2.SetLatencyRequest(
            entries=[
                node_agent_pb2.LatencyEntry(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    latency_ms=5.0,
                    link_type=node_agent_pb2.ISL,
                ),
            ],
        )
        resp = handle_set_latency(req, pid_map=EMPTY_PID_MAP)
        assert resp.success is False
        assert resp.entries_updated == 0
