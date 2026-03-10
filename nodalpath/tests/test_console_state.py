"""Tests for ConsoleState — shared operator console state."""

from __future__ import annotations

import threading
import types

import pytest

from nodalpath.console.state import (
    MAX_ALMANAC_HISTORY,
    MAX_DEVIATION_HISTORY,
    MAX_PUSH_HISTORY,
    ConsoleState,
)


def _make_state(**kwargs) -> ConsoleState:
    defaults = {"session_path": "/tmp/test", "transport": "grpc", "dry_run": False}
    defaults.update(kwargs)
    return ConsoleState(**defaults)


def _mock_push_result(**overrides) -> types.SimpleNamespace:
    defaults = {
        "topology_state_id": "topo-abc",
        "sim_time": "2026-03-01T14:30:00+00:00",
        "nodes_attempted": 3,
        "nodes_succeeded": 3,
        "nodes_failed": 0,
        "nodes_skipped": 0,
        "push_duration_ms": 50.0,
        "failed_nodes": [],
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


class TestConsoleStateInit:
    def test_initial_state_fields(self):
        state = _make_state(session_path="/data/session", transport="vtysh", dry_run=True, nodes_in_registry=10)
        assert state.session_path == "/data/session"
        assert state.transport == "vtysh"
        assert state.dry_run is True
        assert state.nodes_in_registry == 10
        assert state.transition_count == 0
        assert state.deviation_count == 0
        assert state.recomputation_count == 0


class TestConsoleStateTransitions:
    def test_record_transition_increments_count(self):
        state = _make_state()
        state.record_transition("2026-03-01T14:30:00", "topo-1", 5, 3)
        assert state.transition_count == 1
        state.record_transition("2026-03-01T14:31:00", "topo-2", 6, 4)
        assert state.transition_count == 2

    def test_record_transition_appends_almanac_history(self):
        state = _make_state()
        state.record_transition("2026-03-01T14:30:00", "topo-1", 5, 3)
        snap = state.snapshot()
        assert len(snap["almanac_history"]) == 1
        assert snap["almanac_history"][0]["topology_state_id"] == "topo-1"
        assert snap["almanac_history"][0]["active_link_count"] == 5


class TestConsoleStatePush:
    def test_record_push_result_appends_history(self):
        state = _make_state()
        pr = _mock_push_result()
        state.record_push_result(pr)
        snap = state.snapshot()
        assert len(snap["push_history"]) == 1
        assert snap["push_history"][0]["nodes_attempted"] == 3

    def test_record_push_result_thread_safe(self):
        state = _make_state()
        errors = []

        def writer():
            try:
                for i in range(10):
                    pr = _mock_push_result(topology_state_id=f"topo-{threading.current_thread().name}-{i}")
                    state.record_push_result(pr)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        snap = state.snapshot()
        assert len(snap["push_history"]) == MAX_PUSH_HISTORY


class TestConsoleStateDeviations:
    def test_record_deviation_increments_count(self):
        state = _make_state()
        state.record_deviation("2026-03-01T14:30:00", "topo-1", "sat-A", "sat-B", "scenario_inject_down")
        assert state.deviation_count == 1

    def test_record_deviation_appends_history(self):
        state = _make_state()
        state.record_deviation("2026-03-01T14:30:00", "topo-1", "sat-A", "sat-B", "scenario_inject_down")
        snap = state.snapshot()
        assert len(snap["deviation_history"]) == 1
        assert snap["deviation_history"][0]["node_a"] == "sat-A"
        assert snap["deviation_history"][0]["reason"] == "scenario_inject_down"


class TestConsoleStateRecompute:
    def test_record_recomputation_increments_count(self):
        state = _make_state()
        state.record_recomputation()
        state.record_recomputation()
        assert state.recomputation_count == 2

    def test_request_recompute_sets_flag(self):
        state = _make_state()
        state.request_recompute()
        assert state.consume_recompute_request() is True

    def test_consume_recompute_request_clears_flag(self):
        state = _make_state()
        state.request_recompute()
        assert state.consume_recompute_request() is True
        assert state.consume_recompute_request() is False


class TestConsoleStateSnapshot:
    def test_snapshot_is_independent_copy(self):
        state = _make_state()
        state.record_transition("2026-03-01T14:30:00", "topo-1", 5, 3)
        snap = state.snapshot()
        snap["almanac_history"].clear()
        snap["transition_count"] = 999
        snap2 = state.snapshot()
        assert len(snap2["almanac_history"]) == 1
        assert snap2["transition_count"] == 1

    def test_almanac_history_capped_at_max(self):
        state = _make_state()
        for i in range(MAX_ALMANAC_HISTORY + 10):
            state.record_transition(f"2026-03-01T{i:06d}", f"topo-{i}", i, i)
        snap = state.snapshot()
        assert len(snap["almanac_history"]) == MAX_ALMANAC_HISTORY
        # Oldest entries trimmed — first entry should be index 10
        assert snap["almanac_history"][0]["topology_state_id"] == "topo-10"
