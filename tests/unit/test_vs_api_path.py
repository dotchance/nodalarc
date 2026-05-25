"""Tests for VS-API GET /api/v1/path unified endpoint."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from vs_api.main import app

client = TestClient(app)


def _path_response(**overrides):
    response = {
        "reachable": True,
        "src": "gs-a",
        "dst": "gs-b",
        "hops": ["gs-a", "sat-P00S00", "gs-b"],
        "total_latency_ms": 42.0,
        "method": "derived",
        "sim_time": "2026-01-01T00:01:00Z",
        "topology_state_id": "s1",
        "unreachable_reason": None,
    }
    response.update(overrides)
    return response


def test_path_proxies_exact_src_dst_contract_to_nodalpath():
    mock_response = _path_response()

    with patch("vs_api.main._fetch_nodalpath_path", new_callable=AsyncMock) as mock:
        mock.return_value = mock_response

        response = client.get("/api/v1/path?src=gs-a&dst=gs-b")

    assert response.status_code == 200
    assert response.json() == mock_response
    mock.assert_awaited_once_with({"src": "gs-a", "dst": "gs-b"})


def test_path_returns_backend_unavailable_payload_without_rewriting_contract():
    unavailable = _path_response(
        reachable=False,
        hops=[],
        total_latency_ms=0.0,
        sim_time="",
        topology_state_id="",
        unreachable_reason="NodalPath not available",
    )

    with patch("vs_api.main._fetch_nodalpath_path", new_callable=AsyncMock) as mock:
        mock.return_value = unavailable

        response = client.get("/api/v1/path?src=gs-a&dst=gs-b")

    assert response.status_code == 200
    assert response.json() == unavailable
    mock.assert_awaited_once_with({"src": "gs-a", "dst": "gs-b"})


def test_path_passes_exact_sim_time_param_to_nodalpath():
    sim_time = "2026-01-01T00:01:00Z"

    with patch("vs_api.main._fetch_nodalpath_path", new_callable=AsyncMock) as mock:
        mock.return_value = _path_response(sim_time=sim_time)

        response = client.get("/api/v1/path?src=gs-a&dst=gs-b&sim_time=2026-01-01T00%3A01%3A00Z")

    assert response.status_code == 200
    assert response.json()["sim_time"] == sim_time
    mock.assert_awaited_once_with({"src": "gs-a", "dst": "gs-b", "sim_time": sim_time})


def test_path_rejects_missing_required_endpoint_parameters_before_backend_call():
    with patch("vs_api.main._fetch_nodalpath_path", new_callable=AsyncMock) as mock:
        response = client.get("/api/v1/path?src=gs-a")

    assert response.status_code == 422
    mock.assert_not_called()
