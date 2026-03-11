"""Tests for GET /api/v1/path on the console server."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from nodalarc.models.path import PathResult
from nodalpath.console.state import ConsoleState
from nodalpath.console.server import build_app


def _client(deriver=None):
    state = ConsoleState(session_path="/tmp/t", transport="grpc", dry_run=False)
    app = build_app(state, path_deriver=deriver)
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
