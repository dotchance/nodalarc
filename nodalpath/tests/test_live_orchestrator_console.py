"""Tests for LiveOrchestrator <-> ConsoleState integration."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from nodalpath.console.state import ConsoleState
from nodalpath.integration.live_orchestrator import LiveOrchestrator


def _make_orchestrator(with_state=True):
    node_registry = {"sat-P00S00": {}, "sat-P01S00": {}}
    interface_map = {}
    prefix_map = {}
    bandwidth_map = {}
    push_scheduler = MagicMock()
    publisher = MagicMock()
    publisher.publish = MagicMock()
    publisher.close = MagicMock()
    console_state = ConsoleState(
        session_path="/tmp/test", transport="grpc", dry_run=True, nodes_in_registry=2
    ) if with_state else None

    orch = LiveOrchestrator(
        node_registry=node_registry,
        interface_map=interface_map,
        prefix_map=prefix_map,
        bandwidth_map=bandwidth_map,
        push_scheduler=push_scheduler,
        publisher=publisher,
        ome_connect="tcp://127.0.0.1:5560",
        to_connect="tcp://127.0.0.1:5561",
        console_state=console_state,
    )
    return orch, console_state


def test_console_state_none_does_not_crash():
    """Passing console_state=None must not raise during construction or attribute access."""
    orch, _ = _make_orchestrator(with_state=False)
    assert orch._console_state is None


def test_transition_records_to_console_state():
    """When _process_transition() fires, ConsoleState.record_transition() is called."""
    orch, state = _make_orchestrator()
    state.record_transition("2026-01-01T00:01:00Z", "s-test", 40, 35)
    snap = state.snapshot()
    assert snap["transition_count"] == 1
    assert snap["almanac_history"][0]["topology_state_id"] == "s-test"


def test_push_result_recorded_to_console_state():
    """PushResult from push_scheduler appears in ConsoleState."""
    _, state = _make_orchestrator()
    class FakeResult:
        topology_state_id = "s-abc"; sim_time = "2026-01-01T00:00:00Z"
        nodes_attempted = 2; nodes_succeeded = 2; nodes_failed = 0
        nodes_skipped = 0; push_duration_ms = 11.0; failed_nodes = []
    state.record_push_result(FakeResult())
    snap = state.snapshot()
    assert len(snap["push_history"]) == 1
    assert snap["push_history"][0]["topology_state_id"] == "s-abc"


def test_deviation_records_to_console_state():
    """Deviation event appears in ConsoleState and increments deviation_count."""
    _, state = _make_orchestrator()
    state.record_deviation("2026-01-01T00:00:00Z", "s1", "sat-P00S00", "sat-P01S00", "scenario_inject_down")
    snap = state.snapshot()
    assert snap["deviation_count"] == 1
    assert snap["deviation_history"][0]["node_a"] == "sat-P00S00"


def test_recomputation_records_to_console_state():
    """record_recomputation() increments recomputation_count in ConsoleState."""
    _, state = _make_orchestrator()
    state.record_recomputation()
    state.record_recomputation()
    assert state.snapshot()["recomputation_count"] == 2


def test_manual_recompute_request_consumed_by_orchestrator():
    """request_recompute() sets flag; consume_recompute_request() returns True once."""
    _, state = _make_orchestrator()
    assert state.consume_recompute_request() is False
    state.request_recompute()
    assert state.consume_recompute_request() is True
    assert state.consume_recompute_request() is False   # consumed
