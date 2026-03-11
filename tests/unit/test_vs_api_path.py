"""Tests for VS-API GET /api/v1/path unified endpoint."""

from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

from vs_api.main import app

client = TestClient(app)


def test_path_proxies_to_nodalpath():
    mock_response = {
        "reachable": True, "src": "gs-a", "dst": "gs-b",
        "hops": [], "total_latency_ms": 42.0, "method": "derived",
        "sim_time": "2026-01-01T00:01:00Z", "topology_state_id": "s1",
        "unreachable_reason": None,
    }
    with patch("vs_api.main._fetch_nodalpath_path", new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        r = client.get("/api/v1/path?src=gs-a&dst=gs-b")
        assert r.status_code == 200
        assert r.json()["reachable"] is True
        assert r.json()["method"] == "derived"


def test_path_graceful_when_nodalpath_unavailable():
    with patch("vs_api.main._fetch_nodalpath_path", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "reachable": False, "unreachable_reason": "NodalPath not available",
            "src": "gs-a", "dst": "gs-b", "hops": [], "total_latency_ms": 0.0,
            "method": "derived", "sim_time": "", "topology_state_id": "",
        }
        r = client.get("/api/v1/path?src=gs-a&dst=gs-b")
        assert r.status_code == 200
        assert r.json()["reachable"] is False


def test_path_passes_sim_time_param():
    with patch("vs_api.main._fetch_nodalpath_path", new_callable=AsyncMock) as mock:
        mock.return_value = {"reachable": False, "src": "gs-a", "dst": "gs-b",
                             "hops": [], "total_latency_ms": 0.0, "method": "derived",
                             "sim_time": "", "topology_state_id": "", "unreachable_reason": None}
        client.get("/api/v1/path?src=gs-a&dst=gs-b&sim_time=2026-01-01T00%3A01%3A00Z")
        called_params = mock.call_args[0][0]
        assert "sim_time" in called_params


def test_path_endpoint_exists():
    """Endpoint is reachable even with no backend wired."""
    with patch("vs_api.main._fetch_nodalpath_path", new_callable=AsyncMock) as mock:
        mock.return_value = {"reachable": False, "src": "", "dst": "", "hops": [],
                             "total_latency_ms": 0.0, "method": "derived",
                             "sim_time": "", "topology_state_id": "", "unreachable_reason": None}
        r = client.get("/api/v1/path?src=gs-a&dst=gs-b")
        assert r.status_code == 200
