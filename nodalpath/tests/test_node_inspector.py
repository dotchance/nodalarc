"""Tests for nodalpath.integration.node_inspector — NodeInspector class."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from nodalpath.integration.node_inspector import NodeInspector
from nodalpath.models.almanac import ForwardingTable, LabelBinding
from nodalpath.models.inspection import NodeInspectionResult


def _make_table(node_id: str = "sat-P00S00") -> ForwardingTable:
    return ForwardingTable(
        node_id=node_id,
        topology_state_id="state-1",
        sim_time="2026-03-01T14:30:00Z",
        lsr_bindings=[
            LabelBinding(in_label=100, action="swap", out_label=200, out_interface="isl0"),
        ],
        ler_ingress_rules=[],
    )


def _make_inspector(node_ids=None) -> NodeInspector:
    if node_ids is None:
        node_ids = ["sat-P00S00", "sat-P00S01"]
    pod_ip_map = {nid: f"10.42.0.{i}" for i, nid in enumerate(node_ids)}
    return NodeInspector(pod_ip_map=pod_ip_map, grpc_port=50051, grpc_timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


class TestRecordPush:
    def test_record_push_updates_state(self):
        inspector = _make_inspector()
        tables = [_make_table("sat-P00S00"), _make_table("sat-P00S01")]
        inspector.record_push("state-1", tables)
        assert inspector._last_pushed_state_id == "state-1"
        assert "sat-P00S00" in inspector._last_pushed_tables
        assert "sat-P00S01" in inspector._last_pushed_tables


class TestTriggerPushVerify:
    @patch("nodalpath.integration.node_inspector.interrogate_nodes")
    def test_calls_interrogate(self, mock_interrogate):
        mock_interrogate.return_value = [
            NodeInspectionResult(node_id="sat-P00S00", reachable=True),
            NodeInspectionResult(node_id="sat-P00S01", reachable=True),
        ]
        inspector = _make_inspector()
        tables = [_make_table("sat-P00S00"), _make_table("sat-P00S01")]
        inspector.record_push("state-1", tables)

        run = _run(inspector.trigger_push_verify("state-1"))

        assert run.trigger == "push_verify"
        assert run.topology_state_id == "state-1"
        assert run.completed_at is not None
        assert run.nodes_inspected == 2
        assert run.nodes_reachable == 2
        mock_interrogate.assert_called_once()

    @patch("nodalpath.integration.node_inspector.interrogate_nodes")
    def test_no_tables_returns_empty_run(self, mock_interrogate):
        inspector = _make_inspector()
        run = _run(inspector.trigger_push_verify("state-1"))
        assert run.nodes_inspected == 0
        assert run.completed_at is not None
        mock_interrogate.assert_not_called()


class TestTriggerOperator:
    @patch("nodalpath.integration.node_inspector.interrogate_nodes")
    def test_subset_only_specified_nodes(self, mock_interrogate):
        mock_interrogate.return_value = [
            NodeInspectionResult(node_id="sat-P00S00", reachable=True),
        ]
        inspector = _make_inspector()
        tables = [_make_table("sat-P00S00"), _make_table("sat-P00S01")]
        inspector.record_push("state-1", tables)

        run = _run(inspector.trigger_operator(node_ids=["sat-P00S00"]))

        assert run.trigger == "operator"
        assert run.nodes_inspected == 1
        # Verify only one task was passed
        call_args = mock_interrogate.call_args
        tasks = call_args[0][0]
        assert len(tasks) == 1
        assert tasks[0][0] == "sat-P00S00"


class TestRunRingBuffer:
    @patch("nodalpath.integration.node_inspector.interrogate_nodes")
    def test_oldest_evicted_at_max(self, mock_interrogate):
        mock_interrogate.return_value = []
        inspector = _make_inspector()
        tables = [_make_table("sat-P00S00")]
        inspector.record_push("state-1", tables)

        # Create 51 runs (max is 50)
        for i in range(51):
            _run(inspector.trigger_heartbeat())

        assert len(inspector._runs) == 50
        # The first run should have been evicted
        runs = inspector.recent_runs(50)
        assert len(runs) == 50


class TestGetRun:
    @patch("nodalpath.integration.node_inspector.interrogate_nodes")
    def test_get_run_by_id(self, mock_interrogate):
        mock_interrogate.return_value = [
            NodeInspectionResult(node_id="sat-P00S00", reachable=True),
        ]
        inspector = _make_inspector()
        tables = [_make_table("sat-P00S00")]
        inspector.record_push("state-1", tables)
        run = _run(inspector.trigger_push_verify("state-1"))

        found = inspector.get_run(run.run_id)
        assert found is not None
        assert found.run_id == run.run_id

    def test_get_run_not_found(self):
        inspector = _make_inspector()
        assert inspector.get_run("nonexistent") is None


class TestRecentRuns:
    @patch("nodalpath.integration.node_inspector.interrogate_nodes")
    def test_newest_first(self, mock_interrogate):
        mock_interrogate.return_value = []
        inspector = _make_inspector()
        tables = [_make_table("sat-P00S00")]
        inspector.record_push("state-1", tables)

        _run(inspector.trigger_heartbeat())
        _run(inspector.trigger_heartbeat())

        runs = inspector.recent_runs(2)
        assert len(runs) == 2
        # Newest first
        assert runs[0].started_at >= runs[1].started_at


class TestDeviationCount:
    @patch("nodalpath.integration.node_inspector.interrogate_nodes")
    def test_no_deviation_count(self, mock_interrogate):
        mock_interrogate.return_value = [
            NodeInspectionResult(node_id="sat-P00S00", reachable=True),
        ]
        inspector = _make_inspector()
        tables = [_make_table("sat-P00S00")]
        inspector.record_push("state-1", tables)

        run = _run(inspector.trigger_push_verify("state-1"))
        assert run.nodes_with_deviations == 0


class TestHeartbeatLoop:
    @patch("nodalpath.integration.node_inspector.interrogate_nodes")
    def test_heartbeat_loop(self, mock_interrogate):
        mock_interrogate.return_value = []
        inspector = _make_inspector()
        tables = [_make_table("sat-P00S00")]
        inspector.record_push("state-1", tables)

        call_count = 0

        async def run_heartbeat():
            nonlocal call_count
            task = asyncio.create_task(inspector.heartbeat_loop(0))
            # Let a few iterations run
            for _ in range(5):
                await asyncio.sleep(0)
            call_count = len(inspector._runs)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        _run(run_heartbeat())
        assert call_count > 0
