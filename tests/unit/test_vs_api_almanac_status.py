"""Tests for vs_api GET /api/v1/almanac/status."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


def _import_app():
    from vs_api.main import app
    return app


def test_almanac_status_unavailable_when_nodalpath_not_running():
    """Returns available=false when NodalPath console is unreachable."""
    app = _import_app()
    with patch("vs_api.main._fetch_nodalpath_status", new=AsyncMock(return_value=None)):
        client = TestClient(app)
        r = client.get("/api/v1/almanac/status")
    assert r.status_code == 200
    assert r.json() == {"available": False}


def test_almanac_status_available_with_data():
    """Returns structured summary when NodalPath is running."""
    fake_snap = {
        "session_path": "/data/test",
        "transport": "grpc",
        "dry_run": False,
        "start_wall_time": "2026-01-01T00:00:00+00:00",
        "nodes_in_registry": 10,
        "transition_count": 5,
        "deviation_count": 1,
        "recomputation_count": 2,
        "last_topology_state_id": "state-007",
        "last_sim_time": "2026-01-01T00:10:00Z",
        "push_history": [{"nodes_succeeded": 10}],
        "deviation_history": [{"reason": "vis_lost"}],
    }
    app = _import_app()
    with patch("vs_api.main._fetch_nodalpath_status", new=AsyncMock(return_value=fake_snap)):
        client = TestClient(app)
        r = client.get("/api/v1/almanac/status")
    data = r.json()
    assert r.status_code == 200
    assert data["available"] is True
    assert data["nodes_in_registry"] == 10
    assert data["transition_count"] == 5
    assert data["last_topology_state_id"] == "state-007"


def test_almanac_status_recent_pushes_capped_at_5():
    """Only the first 5 push history entries are returned."""
    fake_snap = {
        "transition_count": 0, "deviation_count": 0, "recomputation_count": 0,
        "nodes_in_registry": 5, "push_history": [{"n": i} for i in range(20)],
        "deviation_history": [],
    }
    app = _import_app()
    with patch("vs_api.main._fetch_nodalpath_status", new=AsyncMock(return_value=fake_snap)):
        r = TestClient(app).get("/api/v1/almanac/status")
    assert len(r.json()["recent_pushes"]) == 5


def test_almanac_status_empty_histories_do_not_crash():
    """available=true with empty push/deviation lists is fine."""
    fake_snap = {
        "transition_count": 0, "deviation_count": 0, "recomputation_count": 0,
        "nodes_in_registry": 0, "push_history": [], "deviation_history": [],
    }
    app = _import_app()
    with patch("vs_api.main._fetch_nodalpath_status", new=AsyncMock(return_value=fake_snap)):
        r = TestClient(app).get("/api/v1/almanac/status")
    data = r.json()
    assert data["available"] is True
    assert data["recent_pushes"] == []
    assert data["recent_deviations"] == []


def test_almanac_status_dry_run_field_propagated():
    """dry_run flag passes through correctly."""
    fake_snap = {
        "dry_run": True, "transport": "vtysh",
        "transition_count": 0, "deviation_count": 0, "recomputation_count": 0,
        "nodes_in_registry": 3, "push_history": [], "deviation_history": [],
    }
    app = _import_app()
    with patch("vs_api.main._fetch_nodalpath_status", new=AsyncMock(return_value=fake_snap)):
        r = TestClient(app).get("/api/v1/almanac/status")
    assert r.json()["dry_run"] is True
    assert r.json()["transport"] == "vtysh"
