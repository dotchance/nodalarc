"""Tests for /api/v1/timeline and historical topology endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from nodalpath.console.server import build_app
from nodalpath.console.state import ConsoleState


def _client():
    state = ConsoleState(session_path="/tmp/t", transport="grpc", dry_run=False)
    almanac = MagicMock()
    almanac.get_timeline_ticks.return_value = []
    almanac.get_entry_at.return_value = None
    almanac.get_topology_at.return_value = None
    app = build_app(state, almanac_store=almanac, prefix_map={})
    return TestClient(app), state, almanac


def test_timeline_empty():
    client, _, almanac = _client()
    r = client.get("/api/v1/timeline")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["ticks"] == []
    assert data["tick_count"] == 0


def test_timeline_includes_lookahead_status():
    client, state, _ = _client()
    state.record_lookahead_status("computing")
    r = client.get("/api/v1/timeline")
    assert r.json()["lookahead_status"] == "computing"


def test_topology_at_returns_unavailable_when_no_entry():
    client, _, almanac = _client()
    almanac.get_topology_at.return_value = None
    r = client.get("/api/v1/topology/at/2026-01-01T00%3A00%3A00Z")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_topology_at_returns_entry():
    client, _, almanac = _client()
    almanac.get_topology_at.return_value = {
        "topology_state_id": "s1",
        "sim_time": "2026-01-01T00:01:00Z",
        "is_future": False,
        "nodes": [],
        "links": [],
    }
    r = client.get("/api/v1/topology/at/2026-01-01T00%3A01%3A00Z")
    data = r.json()
    assert data["available"] is True
    assert data["is_historical"] is True
    assert data["links_available"] is False
    assert data["topology_state_id"] == "s1"


def test_node_state_at_no_entry():
    client, _, almanac = _client()
    almanac.get_entry_at.return_value = None
    r = client.get("/api/v1/node/sat-P00S00/state/at/2026-01-01T00%3A00%3A00Z")
    assert r.json()["available"] is False
