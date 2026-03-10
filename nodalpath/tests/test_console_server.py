"""Tests for the FastAPI operator console server."""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from nodalpath.console.server import build_app
from nodalpath.console.state import ConsoleState


def _make_state(**kwargs) -> ConsoleState:
    defaults = {"session_path": "/tmp/test-session", "transport": "grpc", "dry_run": False, "nodes_in_registry": 4}
    defaults.update(kwargs)
    return ConsoleState(**defaults)


def _mock_push_result(**overrides) -> types.SimpleNamespace:
    defaults = {
        "topology_state_id": "topo-abc123",
        "sim_time": "2026-03-01T14:30:00+00:00",
        "nodes_attempted": 4,
        "nodes_succeeded": 4,
        "nodes_failed": 0,
        "nodes_skipped": 0,
        "push_duration_ms": 42.5,
        "failed_nodes": [],
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


@pytest.fixture
def state():
    return _make_state()


@pytest.fixture
def client(state):
    app = build_app(state)
    return TestClient(app)


class TestConsoleServerHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestConsoleServerDashboard:
    def test_dashboard_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "NodalPath Operator Console" in r.text


class TestConsoleServerStatus:
    def test_status_returns_snapshot(self, client, state):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["session_path"] == "/tmp/test-session"
        assert data["transport"] == "grpc"
        assert data["nodes_in_registry"] == 4
        assert data["transition_count"] == 0

    def test_status_reflects_recorded_transition(self, client, state):
        state.record_transition("2026-03-01T14:30:00", "topo-xyz", 5, 3)
        r = client.get("/api/status")
        data = r.json()
        assert data["transition_count"] == 1
        assert data["last_topology_state_id"] == "topo-xyz"
        assert len(data["almanac_history"]) == 1

    def test_status_reflects_recorded_push(self, client, state):
        pr = _mock_push_result()
        state.record_push_result(pr)
        r = client.get("/api/status")
        data = r.json()
        assert len(data["push_history"]) == 1
        assert data["push_history"][0]["nodes_attempted"] == 4


class TestConsoleServerListEndpoints:
    def test_almanac_endpoint_returns_list(self, client, state):
        state.record_transition("2026-03-01T14:30:00", "topo-1", 5, 3)
        r = client.get("/api/almanac")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_pushes_endpoint_returns_list(self, client, state):
        state.record_push_result(_mock_push_result())
        r = client.get("/api/pushes")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_deviations_endpoint_returns_list(self, client, state):
        state.record_deviation("2026-03-01T14:30:00", "topo-1", "sat-A", "sat-B", "inject")
        r = client.get("/api/deviations")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 1


class TestConsoleServerRecompute:
    def test_recompute_queues_flag(self, client, state):
        r = client.post("/api/recompute")
        assert r.status_code == 200
        assert state.consume_recompute_request() is True

    def test_recompute_response_ok_true(self, client):
        r = client.post("/api/recompute")
        data = r.json()
        assert data["ok"] is True
        assert "message" in data
