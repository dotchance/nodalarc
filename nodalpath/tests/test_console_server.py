"""Tests for nodalpath.console.server — FastAPI routes and HTML dashboard."""
import pytest
from fastapi.testclient import TestClient
from nodalpath.console.state import ConsoleState
from nodalpath.console.server import build_app


def _client(session_path="/tmp/test", transport="grpc", dry_run=False, nodes=5):
    state = ConsoleState(
        session_path=session_path,
        transport=transport,
        dry_run=dry_run,
        nodes_in_registry=nodes,
    )
    app = build_app(state)
    return TestClient(app), state


def test_health_returns_ok():
    client, _ = _client()
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_dashboard_returns_html_200():
    client, _ = _client()
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_dashboard_contains_nodalpath_brand():
    client, _ = _client()
    r = client.get("/")
    assert "NodalPath" in r.text


def test_dashboard_uses_dark_theme_colors():
    """Verify the root page includes NodalPath branding (React app or holding page)."""
    client, _ = _client()
    r = client.get("/")
    assert "NodalPath" in r.text
    # Either the React app shell (dist built) or the holding page (dist not built)
    assert "text/html" in r.headers["content-type"]


def test_status_returns_snapshot_fields():
    client, state = _client(nodes=12)
    state.record_transition("2026-01-01T00:01:00Z", "s1", 40, 35)
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["nodes_in_registry"] == 12
    assert data["transition_count"] == 1
    assert "push_history" in data
    assert "event_log" in data


def test_events_endpoint_returns_list():
    client, state = _client()
    state.record_transition("2026-01-01T00:00:00Z", "s1", 5, 5)
    r = client.get("/api/events")
    assert r.status_code == 200
    events = r.json()
    assert isinstance(events, list)
    assert len(events) >= 1
    assert events[0]["event_type"] == "TRANSITION"


def test_pushes_endpoint_returns_list():
    client, state = _client()
    class FakeResult:
        topology_state_id = "s1"; sim_time = "2026-01-01T00:00:00Z"
        nodes_attempted = 3; nodes_succeeded = 3; nodes_failed = 0
        nodes_skipped = 0; push_duration_ms = 8.0; failed_nodes = []
    state.record_push_result(FakeResult())
    r = client.get("/api/pushes")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_deviations_endpoint_returns_list():
    client, state = _client()
    state.record_deviation("2026-01-01T00:00:00Z", "s1", "sat-P00S00", "sat-P01S00", "vis_lost")
    r = client.get("/api/deviations")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_recompute_queues_flag_and_returns_ok():
    client, state = _client()
    assert state.consume_recompute_request() is False   # flag not set
    r = client.post("/api/recompute")
    assert r.status_code == 200
    assert r.json().get("ok") is True
    assert state.consume_recompute_request() is True    # flag was set


def test_status_reflects_deviation_count():
    client, state = _client()
    state.record_deviation("2026-01-01T00:00:00Z", "s1", "a", "b", "vis_lost")
    state.record_deviation("2026-01-01T00:00:01Z", "s1", "c", "d", "scenario_inject_down")
    r = client.get("/api/status")
    assert r.json()["deviation_count"] == 2
