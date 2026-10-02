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
from node_agent.command_contract import RuntimeFence, WriterEpochFloor
from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_set_latency,
)

pytestmark = pytest.mark.usefixtures("_node_agent_ops_spool_path")


# All tests pass handles={} — an initialized but empty map.
# This represents a node where wiring completed but no session pods
# are scheduled. handles=None means wiring never happened and is
# rejected by the handler (ValueError).
EMPTY_PID_MAP: dict = {}


@pytest.fixture(autouse=True)
def _handles_verify_live(monkeypatch):
    """Handler tests exercise handler logic; handle liveness has its own tests."""
    monkeypatch.setattr("node_agent.handlers.verify_handle", lambda handle: True)


FENCE = RuntimeFence(
    session_id="demo",
    wiring_generation="sha256:" + "a" * 64,
    writer_floor=WriterEpochFloor(lambda: None),
)


def _env(kind: str, op: str) -> node_agent_pb2.CommandEnvelope:
    return node_agent_pb2.CommandEnvelope(
        operation_id=op,
        session_id=FENCE.session_id,
        wiring_generation=FENCE.wiring_generation,
        operation_kind=kind,
        writer_epoch=1,
    )


class TestBatchLinkDown:
    def test_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkDownRequest(envelope=_env("BatchLinkDown", "test-empty-down"))
        resp = handle_batch_link_down(req, handles=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is True
        assert resp.interfaces_downed == 0
        assert resp.error_message == ""

    def test_nonexistent_pid_returns_error_in_response(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            envelope=_env("BatchLinkDown", "test-bad-pid"),
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_LOCAL,
                    peer_node_id="sat-P00S01",
                    peer_interface_name="isl1",
                ),
            ],
        )
        resp = handle_batch_link_down(req, handles=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is False
        assert resp.interfaces_downed == 0
        assert resp.error_message != ""
        assert len(resp.interface_results) == 1
        assert resp.interface_results[0].node_id == "sat-P00S00"
        assert resp.interface_results[0].interface_name == "isl0"
        assert resp.interface_results[0].success is False

    def test_multiple_links_one_fails(self):
        req = node_agent_pb2.BatchLinkDownRequest(
            envelope=_env("BatchLinkDown", "test-partial"),
            interfaces=[
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_LOCAL,
                    peer_node_id="sat-P00S01",
                    peer_interface_name="isl1",
                ),
                node_agent_pb2.InterfaceDown(
                    node_id="sat-P00S01",
                    interface_name="isl1",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_LOCAL,
                    peer_node_id="sat-P00S00",
                    peer_interface_name="isl0",
                ),
            ],
        )
        resp = handle_batch_link_down(req, handles=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is False
        assert resp.interfaces_downed == 0
        assert len(resp.interface_results) == 2
        assert {r.interface_name for r in resp.interface_results} == {"isl0", "isl1"}
        assert all(not r.success for r in resp.interface_results)

    def test_none_pid_map_raises(self):
        req = node_agent_pb2.BatchLinkDownRequest(envelope=_env("BatchLinkDown", "test-none"))
        with pytest.raises(ValueError, match="handles is None"):
            handle_batch_link_down(req, handles=None, fence=FENCE)


class TestBatchLinkUp:
    def test_empty_batch_succeeds(self):
        req = node_agent_pb2.BatchLinkUpRequest(envelope=_env("BatchLinkUp", "test-empty-up"))
        resp = handle_batch_link_up(req, handles=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is True
        assert resp.interfaces_upped == 0

    def test_nonexistent_pid_returns_error_in_response(self):
        req = node_agent_pb2.BatchLinkUpRequest(
            envelope=_env("BatchLinkUp", "test-bad-pid-up"),
            interfaces=[
                node_agent_pb2.InterfaceUp(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                    locality=node_agent_pb2.LOCALITY_LOCAL,
                    latency_ms=3.0,
                    rates=node_agent_pb2.TerminalRates(transmit_mbps=1000.0, receive_mbps=1000.0),
                    peer_node_id="sat-P00S01",
                    peer_interface_name="isl1",
                ),
            ],
        )
        resp = handle_batch_link_up(req, handles=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is False
        assert resp.error_message != ""
        assert len(resp.interface_results) == 1
        assert resp.interface_results[0].node_id == "sat-P00S00"
        assert resp.interface_results[0].interface_name == "isl0"
        assert resp.interface_results[0].success is False

    def test_none_pid_map_raises(self):
        req = node_agent_pb2.BatchLinkUpRequest(envelope=_env("BatchLinkUp", "test-none"))
        with pytest.raises(ValueError, match="handles is None"):
            handle_batch_link_up(req, handles=None, fence=FENCE)


class TestSetLatency:
    def test_nonexistent_pid_returns_error(self):
        req = node_agent_pb2.SetLatencyRequest(
            envelope=_env("SetLatency", "test-bad-lat"),
            entries=[
                node_agent_pb2.LatencyEntry(
                    node_id="sat-P00S00",
                    interface_name="isl0",
                    latency_ms=5.0,
                    rates=node_agent_pb2.TerminalRates(transmit_mbps=2000.0, receive_mbps=2000.0),
                    link_type=node_agent_pb2.LINK_TYPE_ISL,
                ),
            ],
        )
        resp = handle_set_latency(req, handles=EMPTY_PID_MAP, fence=FENCE)
        assert resp.success is False
        assert resp.entries_updated == 0
