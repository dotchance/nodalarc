"""Tests for ConsoleState topology snapshot functionality."""
import pytest
from nodalpath.console.state import ConsoleState


def _state():
    return ConsoleState(session_path="/tmp/t", transport="grpc", dry_run=False)


def _topo(state_id="s1", n_sats=2, n_gs=1):
    nodes = [
        {"node_id": f"sat-P0{i}S00", "node_type": "satellite",
         "plane": i, "slot": 0, "routing_area": "49.0001",
         "neighbor_count": 2, "isl_count": 2, "gnd_count": 0, "prefix": None}
        for i in range(n_sats)
    ]
    nodes += [
        {"node_id": "gs-ashburn", "node_type": "ground_station",
         "plane": None, "slot": None, "routing_area": None,
         "neighbor_count": 1, "isl_count": 0, "gnd_count": 1, "prefix": "10.0.0.0/24"}
        for _ in range(n_gs)
    ]
    return {
        "topology_state_id": state_id,
        "sim_time": "2026-01-01T00:01:00Z",
        "nodes": nodes,
        "links": [{"node_a": "sat-P00S00", "node_b": "sat-P01S00", "state": "active", "link_type": "isl"}],
    }


def test_get_topology_returns_none_initially():
    s = _state()
    assert s.get_topology() is None


def test_record_topology_stores_snapshot():
    s = _state()
    s.record_topology_snapshot(_topo("s1"))
    topo = s.get_topology()
    assert topo is not None
    assert topo["topology_state_id"] == "s1"


def test_record_topology_overwrites_previous():
    s = _state()
    s.record_topology_snapshot(_topo("s1"))
    s.record_topology_snapshot(_topo("s2"))
    assert s.get_topology()["topology_state_id"] == "s2"


def test_record_topology_nodes_structure():
    s = _state()
    s.record_topology_snapshot(_topo())
    nodes = s.get_topology()["nodes"]
    assert any(n["node_type"] == "ground_station" for n in nodes)
    assert any(n["node_type"] == "satellite" for n in nodes)


def test_get_topology_is_thread_safe():
    """Concurrent writes must not corrupt the stored topology."""
    import threading
    s = _state()
    errors = []

    def writer(i):
        try:
            s.record_topology_snapshot(_topo(f"s{i}"))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    topo = s.get_topology()
    assert topo is not None
    assert "topology_state_id" in topo


def test_topology_not_in_snapshot_dict():
    """get_topology() is separate from snapshot() — topology not embedded in status."""
    s = _state()
    s.record_topology_snapshot(_topo())
    snap = s.snapshot()
    # The full topology should NOT be embedded in the status snapshot
    # (it's served separately from /api/v1/topology/current to avoid bloating /api/status)
    assert "nodes" not in snap
    assert "links" not in snap
