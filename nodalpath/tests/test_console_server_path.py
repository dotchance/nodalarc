"""Tests for GET /api/v1/path and GET /api/v1/trace-config on the console server."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from nodalarc.models.path import PathResult
from nodalpath.console.state import ConsoleState
from nodalpath.console.server import build_app


def _client(deriver=None, trace_mode=None):
    state = ConsoleState(session_path="/tmp/t", transport="grpc", dry_run=False)
    app = build_app(state, path_deriver=deriver, trace_mode=trace_mode)
    return TestClient(app)


def _mock_deriver(reachable=True):
    d = MagicMock()
    d.derive.return_value = PathResult(
        src="gs-a", dst="gs-b",
        hops=[], total_latency_ms=42.0,
        method="derived", sim_time="2026-01-01T00:01:00Z",
        topology_state_id="s1",
        reachable=reachable,
        unreachable_reason=None if reachable else "no path",
    )
    return d


def test_path_no_deriver():
    client = _client(deriver=None)
    r = client.get("/api/v1/path?src=gs-a&dst=gs-b")
    assert r.status_code == 200
    assert r.json()["reachable"] is False


def test_path_reachable():
    client = _client(deriver=_mock_deriver(True))
    r = client.get("/api/v1/path?src=gs-a&dst=gs-b")
    assert r.status_code == 200
    data = r.json()
    assert data["reachable"] is True
    assert data["method"] == "derived"


def test_path_unreachable():
    client = _client(deriver=_mock_deriver(False))
    r = client.get("/api/v1/path?src=gs-a&dst=gs-b")
    assert r.json()["reachable"] is False
    assert r.json()["unreachable_reason"] == "no path"


def test_path_passes_sim_time():
    d = _mock_deriver()
    client = _client(deriver=d)
    client.get("/api/v1/path?src=gs-a&dst=gs-b&sim_time=2026-01-01T00%3A01%3A00Z")
    d.derive.assert_called_once_with("gs-a", "gs-b", "2026-01-01T00:01:00Z")


def test_path_without_sim_time():
    d = _mock_deriver()
    client = _client(deriver=d)
    client.get("/api/v1/path?src=gs-a&dst=gs-b")
    d.derive.assert_called_once_with("gs-a", "gs-b", None)


# ── trace-config endpoint tests ────────────────────────────────────────

def test_trace_config_ip_mode():
    client = _client(trace_mode="ip")
    r = client.get("/api/v1/trace-config")
    data = r.json()
    assert data["trace_mode"] == "ip"
    assert data["pipe_mode"] is False
    assert data["has_sr"] is False


def test_trace_config_sr_uniform():
    client = _client(trace_mode="sr-uniform")
    r = client.get("/api/v1/trace-config")
    data = r.json()
    assert data["trace_mode"] == "sr-uniform"
    assert data["pipe_mode"] is False
    assert data["has_sr"] is True


def test_trace_config_sr_pipe():
    client = _client(trace_mode="sr-pipe")
    r = client.get("/api/v1/trace-config")
    data = r.json()
    assert data["trace_mode"] == "sr-pipe"
    assert data["pipe_mode"] is True
    assert data["has_sr"] is True


def test_trace_config_cspf_with_deriver():
    """When path_deriver is wired and no trace_mode, reports cspf."""
    client = _client(deriver=_mock_deriver())
    r = client.get("/api/v1/trace-config")
    data = r.json()
    assert data["trace_mode"] == "cspf"
    assert data["pipe_mode"] is False
    assert data["has_sr"] is False


def test_trace_config_no_session():
    """No deriver, no trace_mode — reports null."""
    client = _client()
    r = client.get("/api/v1/trace-config")
    data = r.json()
    assert data["trace_mode"] is None
    assert data["pipe_mode"] is False


# ── continuous trace proxy endpoint tests ─────────────────────────────


def test_trace_start_route_exists():
    """POST /api/v1/trace/start must not 404 — it must reach the proxy handler."""
    client = _client()
    r = client.post("/api/v1/trace/start", json={"src_node": "a", "dst_node": "b"})
    # We expect 502 or 503 (VS-API unreachable / endpoint missing), never 404
    assert r.status_code != 404, f"Route not found — got 404: {r.json()}"
    # Must have an error field with context, not a bare FastAPI detail
    data = r.json()
    assert "error" in data, f"Error response must have 'error' field: {data}"


def test_trace_stop_route_exists():
    """POST /api/v1/trace/stop must not 404."""
    client = _client()
    r = client.post("/api/v1/trace/stop")
    assert r.status_code != 404, f"Route not found — got 404: {r.json()}"


def test_trace_status_route_exists():
    """GET /api/v1/trace/status must not 404."""
    client = _client()
    r = client.get("/api/v1/trace/status")
    assert r.status_code != 404, f"Route not found — got 404: {r.json()}"
