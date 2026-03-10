"""Tests for nodalpath.console.state.ConsoleState."""
import threading
import time
import pytest
from nodalpath.console.state import ConsoleState, MAX_PUSH_HISTORY, MAX_ALMANAC_HISTORY, MAX_DEVIATION_HISTORY, MAX_EVENT_LOG


def _make_state(**kw) -> ConsoleState:
    defaults = dict(session_path="/tmp/test", transport="grpc", dry_run=False)
    defaults.update(kw)
    return ConsoleState(**defaults)


def _push_result(state_id="s1", sim_time="2026-01-01T00:00:00Z",
                 attempted=3, succeeded=3, failed=0, skipped=0,
                 duration_ms=12.5, failed_nodes=None):
    """Minimal object mimicking PushResult."""
    class R:
        topology_state_id = state_id
        pass
    r = R()
    r.sim_time = sim_time
    r.nodes_attempted = attempted
    r.nodes_succeeded = succeeded
    r.nodes_failed = failed
    r.nodes_skipped = skipped
    r.push_duration_ms = duration_ms
    r.failed_nodes = failed_nodes or []
    return r


def test_initial_state_fields():
    s = _make_state(session_path="/data/sess", transport="vtysh", dry_run=True, nodes_in_registry=10)
    snap = s.snapshot()
    assert snap["session_path"] == "/data/sess"
    assert snap["transport"] == "vtysh"
    assert snap["dry_run"] is True
    assert snap["nodes_in_registry"] == 10
    assert snap["transition_count"] == 0
    assert snap["deviation_count"] == 0
    assert snap["recomputation_count"] == 0


def test_record_transition_increments_count():
    s = _make_state()
    s.record_transition("2026-01-01T00:01:00Z", "state-1", 40, 35)
    s.record_transition("2026-01-01T00:02:00Z", "state-2", 42, 35)
    assert s.snapshot()["transition_count"] == 2


def test_record_transition_appends_almanac_history_and_event_log():
    s = _make_state()
    s.record_transition("2026-01-01T00:01:00Z", "state-abc", 40, 35)
    snap = s.snapshot()
    assert len(snap["almanac_history"]) == 1
    assert snap["almanac_history"][0]["topology_state_id"] == "state-abc"
    # Event log should also have one TRANSITION entry
    assert any(e["event_type"] == "TRANSITION" for e in snap["event_log"])


def test_record_push_result_appends_history_and_event_log():
    s = _make_state()
    r = _push_result(state_id="s1", succeeded=3, failed=0)
    s.record_push_result(r)
    snap = s.snapshot()
    assert len(snap["push_history"]) == 1
    assert snap["push_history"][0]["nodes_succeeded"] == 3
    assert any(e["event_type"] == "PUSH" for e in snap["event_log"])


def test_record_push_result_thread_safe():
    """Concurrent writes from threads must not corrupt the list."""
    s = _make_state()
    errors = []

    def writer():
        try:
            for _ in range(20):
                s.record_push_result(_push_result())
                time.sleep(0)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
    snap = s.snapshot()
    assert len(snap["push_history"]) <= MAX_PUSH_HISTORY


def test_record_deviation_increments_count_and_event_log():
    s = _make_state()
    s.record_deviation("2026-01-01T00:01:00Z", "state-1", "sat-P01S03", "sat-P01S04", "scenario_inject_down")
    snap = s.snapshot()
    assert snap["deviation_count"] == 1
    assert len(snap["deviation_history"]) == 1
    assert any(e["event_type"] == "DEVIATE" for e in snap["event_log"])


def test_record_deviation_appends_history():
    s = _make_state()
    s.record_deviation("2026-01-01T00:01:00Z", "state-1", "sat-P00S00", "sat-P01S00", "vis_lost")
    snap = s.snapshot()
    assert snap["deviation_history"][0]["node_a"] == "sat-P00S00"
    assert snap["deviation_history"][0]["reason"] == "vis_lost"


def test_record_recomputation_increments_count_and_event_log():
    s = _make_state()
    s.record_recomputation()
    s.record_recomputation()
    snap = s.snapshot()
    assert snap["recomputation_count"] == 2
    recompute_events = [e for e in snap["event_log"] if e["event_type"] == "RECOMPUTE"]
    assert len(recompute_events) == 2


def test_request_and_consume_recompute_flag():
    s = _make_state()
    assert s.consume_recompute_request() is False   # no request yet
    s.request_recompute()
    assert s.consume_recompute_request() is True    # consumed
    assert s.consume_recompute_request() is False   # already consumed


def test_snapshot_is_independent_copy():
    s = _make_state()
    s.record_transition("2026-01-01T00:00:00Z", "s1", 10, 10)
    snap1 = s.snapshot()
    snap1["almanac_history"].clear()                # mutate the returned dict
    snap2 = s.snapshot()
    assert len(snap2["almanac_history"]) == 1       # original state is unaffected


def test_almanac_history_capped_at_max():
    s = _make_state()
    for i in range(MAX_ALMANAC_HISTORY + 20):
        s.record_transition(f"2026-01-01T00:{i:02d}:00Z", f"state-{i}", i, i)
    snap = s.snapshot()
    assert len(snap["almanac_history"]) == MAX_ALMANAC_HISTORY


def test_event_log_capped_at_max():
    s = _make_state()
    # Each record_transition() adds one event log entry
    for i in range(MAX_EVENT_LOG + 50):
        s.record_transition(f"2026-01-01T00:00:{i:02d}Z", f"s{i}", i, i)
    snap = s.snapshot()
    assert len(snap["event_log"]) == MAX_EVENT_LOG


def test_event_log_newest_first():
    """snapshot() returns event_log newest-first."""
    s = _make_state()
    s.record_transition("2026-01-01T00:00:00Z", "first", 1, 1)
    s.record_transition("2026-01-01T00:00:10Z", "second", 2, 2)
    snap = s.snapshot()
    # Newest entry (second) should be first in the returned list
    assert "second" in snap["event_log"][0]["summary"] or snap["event_log"][0]["details"].get("topology_state_id") == "second"
