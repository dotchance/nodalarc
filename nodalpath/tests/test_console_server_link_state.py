"""Tests for /api/v1/topology/at/ with link state attached."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from nodalpath.console.server import build_app
from nodalpath.console.state import ConsoleState
from nodalpath.orchestrator.link_state_store import LinkRecord


def _client_with_link_state():
    state = ConsoleState(session_path="/tmp/t", transport="grpc", dry_run=False)

    almanac = MagicMock()
    almanac.get_topology_at.return_value = {
        "topology_state_id": "s1",
        "sim_time": "2026-01-01T00:01:00Z",
        "is_future": False,
        "nodes": [],
        "links": [],
    }

    link_store = MagicMock()
    link_store.get_by_sim_time.return_value = [
        LinkRecord("sat-P00S00", "sat-P00S01", True, True, 1000.0, "isl"),
        LinkRecord("sat-P00S00", "sat-P01S00", True, False, 800.0, "isl"),
    ]

    app = build_app(state, almanac_store=almanac, prefix_map={}, link_state_store=link_store)
    return TestClient(app)


def test_topology_at_includes_links():
    client = _client_with_link_state()
    r = client.get("/api/v1/topology/at/2026-01-01T00%3A01%3A00Z")
    data = r.json()
    assert data["links_available"] is True
    assert len(data["links"]) == 2


def test_topology_at_link_states_derived():
    client = _client_with_link_state()
    r = client.get("/api/v1/topology/at/2026-01-01T00%3A01%3A00Z")
    links = r.json()["links"]
    states = {(l["node_a"], l["node_b"]): l["state"] for l in links}
    assert states[("sat-P00S00", "sat-P00S01")] == "active"
    assert states[("sat-P00S00", "sat-P01S00")] == "visible_unscheduled"


def test_topology_at_no_link_store():
    state = ConsoleState(session_path="/tmp/t", transport="grpc", dry_run=False)
    almanac = MagicMock()
    almanac.get_topology_at.return_value = {
        "topology_state_id": "s1",
        "sim_time": "2026-01-01T00:01:00Z",
        "is_future": False,
        "nodes": [],
        "links": [],
    }
    app = build_app(state, almanac_store=almanac, prefix_map={}, link_state_store=None)
    client = TestClient(app)
    r = client.get("/api/v1/topology/at/2026-01-01T00%3A01%3A00Z")
    assert r.json()["links_available"] is False


def test_topology_current_adds_state_field():
    state = ConsoleState(session_path="/tmp/t", transport="grpc", dry_run=False)
    state.record_topology_snapshot(
        {
            "topology_state_id": "s1",
            "sim_time": "2026-01-01T00:01:00Z",
            "nodes": [],
            "links": [
                {
                    "node_a": "sat-P00S00",
                    "node_b": "sat-P00S01",
                    "state": "active",
                    "link_type": "isl",
                }
            ],
        }
    )
    app = build_app(state)
    client = TestClient(app)
    r = client.get("/api/v1/topology/current")
    links = r.json()["links"]
    assert all(l["state"] == "active" for l in links)
