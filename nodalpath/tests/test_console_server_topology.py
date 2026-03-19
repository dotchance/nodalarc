"""Tests for /api/v1/topology/current and /api/v1/node/{node_id}/state endpoints."""

from fastapi.testclient import TestClient

from nodalpath.console.server import build_app
from nodalpath.console.state import ConsoleState


def _client_and_state(with_almanac=False):
    state = ConsoleState(
        session_path="/tmp/t", transport="grpc", dry_run=False, nodes_in_registry=3
    )
    almanac_store = None
    if with_almanac:
        from unittest.mock import MagicMock

        almanac_store = MagicMock()
        almanac_store.get_forwarding_entries_for_node.return_value = None
    app = build_app(state, almanac_store=almanac_store)
    return TestClient(app), state, almanac_store


def test_topology_unavailable_before_first_transition():
    client, _, _ = _client_and_state()
    r = client.get("/api/v1/topology/current")
    assert r.status_code == 200
    assert r.json() == {"available": False}


def test_topology_available_after_record():
    client, state, _ = _client_and_state()
    state.record_topology_snapshot(
        {
            "topology_state_id": "s1",
            "sim_time": "2026-01-01T00:01:00Z",
            "nodes": [
                {
                    "node_id": "sat-P00S00",
                    "node_type": "satellite",
                    "plane": 0,
                    "slot": 0,
                    "routing_area": "49.0001",
                    "neighbor_count": 2,
                    "isl_count": 2,
                    "gnd_count": 0,
                    "prefix": None,
                }
            ],
            "links": [],
        }
    )
    r = client.get("/api/v1/topology/current")
    data = r.json()
    assert data["available"] is True
    assert data["topology_state_id"] == "s1"
    assert len(data["nodes"]) == 1


def test_node_state_unavailable_without_almanac():
    client, state, _ = _client_and_state(with_almanac=False)
    state.record_topology_snapshot(
        {
            "topology_state_id": "s1",
            "sim_time": "2026-01-01T00:00:00Z",
            "nodes": [],
            "links": [],
        }
    )
    r = client.get("/api/v1/node/sat-P00S00/state")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_node_state_queries_almanac():
    client, state, almanac = _client_and_state(with_almanac=True)
    state.record_topology_snapshot(
        {
            "topology_state_id": "s1",
            "sim_time": "2026-01-01T00:00:00Z",
            "nodes": [],
            "links": [],
        }
    )
    r = client.get("/api/v1/node/sat-P00S00/state")
    assert r.status_code == 200
    almanac.get_forwarding_entries_for_node.assert_called_once_with(
        node_id="sat-P00S00", topology_state_id="s1"
    )


def test_node_state_unavailable_before_topology():
    client, state, _ = _client_and_state(with_almanac=True)
    # No topology recorded — no topology_state_id to query against
    r = client.get("/api/v1/node/sat-P00S00/state")
    assert r.json()["available"] is False
