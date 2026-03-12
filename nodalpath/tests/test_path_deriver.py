"""Tests for PathDeriver — CSPF-based path computation for console display.

The deriver runs Dijkstra on the topology graph built from the
SnapshotBuilder's current state. Tests provide topology edges and
verify that correct shortest paths are returned with MPLS annotations.
"""

from unittest.mock import MagicMock

from nodalpath.engine.path_deriver import PathDeriver
from nodalpath.models.almanac import AlmanacEntry
from nodalpath.models.topology import TopologyNode, TopologyEdge
from nodalpath.orchestrator.snapshot_builder import SnapshotBuilder


# ── helpers ────────────────────────────────────────────────────────────────

def _node(node_id: str, node_type: str, sid: int,
          loopback: str = "10.0.0.1", plane=None, slot=None) -> TopologyNode:
    return TopologyNode(
        node_id=node_id, node_type=node_type,
        sid=sid, loopback_ipv4=loopback,
        plane=plane, slot=slot,
    )


def _deriver(
    nodes: list[TopologyNode],
    edges: list[tuple[str, str, str, str, float]],
    prefix_map: dict[str, str],
) -> PathDeriver:
    """Build a PathDeriver with a SnapshotBuilder seeded with the given edges.

    edges: list of (src, dst, src_iface, dst_iface, latency_ms) tuples.
    """
    node_registry = {n.node_id: n for n in nodes}

    # Build interface_map and bandwidth_map from edges
    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    bandwidth_map: dict[tuple[str, str], float] = {}
    for src, dst, src_if, dst_if, _lat in edges:
        pair = (min(src, dst), max(src, dst))
        if pair not in interface_map:
            if src < dst:
                interface_map[pair] = (src_if, dst_if)
            else:
                interface_map[pair] = (dst_if, src_if)
        bandwidth_map[pair] = 1000.0

    builder = SnapshotBuilder(node_registry, interface_map, bandwidth_map)

    # Seed the builder with link-up events via direct state injection
    from nodalarc.models.events import VisibilityEvent
    from datetime import datetime, timezone
    t = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
    for src, dst, _si, _di, lat in edges:
        range_km = lat * 299.792458  # reverse the latency->range formula
        event = VisibilityEvent(
            sim_time=t, node_a=min(src, dst), node_b=max(src, dst),
            visible=True, scheduled=True, range_km=range_km,
            elevation_deg=None, terminal_type="optical",
        )
        builder.apply_link_event(event)

    # Create a minimal almanac entry so the deriver has sim_time/state_id
    entry = AlmanacEntry(
        topology_state_id="s1",
        sim_time="2026-01-01T00:01:00Z",
        forwarding_tables=[],
        computed_paths=[],
        computation_time_ms=1.0,
    )
    store = MagicMock()
    store.entries = [entry]
    store.get_entry_at.return_value = entry

    return PathDeriver(
        almanac_store=store,
        prefix_map=prefix_map,
        node_registry=node_registry,
        interface_map=interface_map,
        snapshot_builder=builder,
    )


# ── basic path tests ──────────────────────────────────────────────────────

def test_simple_path_gs_sat_gs():
    """gs-a -> sat-P00S00 -> gs-b, single hop through one satellite."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = [
        ("gs-a", "sat-P00S00", "gnd0", "gnd0", 5.0),
        ("sat-P00S00", "gs-b", "gnd1", "gnd0", 5.0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["gs-a", "sat-P00S00", "gs-b"]
    assert result.hops[0].action == "push"
    assert result.hops[0].out_label == 16001
    assert result.hops[1].action == "pop"
    assert result.hops[1].in_label == 16001


def test_path_two_satellite_hops():
    """gs-a -> sat-P00S00 -> sat-P01S00 -> gs-b"""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("sat-P01S00", "satellite", sid=16002, plane=1, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = [
        ("gs-a", "sat-P00S00", "gnd0", "gnd0", 5.0),
        ("sat-P00S00", "sat-P01S00", "isl0", "isl0", 14.0),
        ("sat-P01S00", "gs-b", "gnd0", "gnd0", 5.0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    assert len(result.hops) == 4
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["gs-a", "sat-P00S00", "sat-P01S00", "gs-b"]
    assert result.hops[1].action == "swap"
    assert result.hops[1].out_label == 16002
    assert result.hops[2].action == "pop"


def test_path_three_satellite_hops():
    """gs-a -> sat0 -> sat1 -> sat2 -> gs-b"""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("sat-P01S00", "satellite", sid=16002, plane=1, slot=0),
        _node("sat-P02S00", "satellite", sid=16003, plane=2, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = [
        ("gs-a", "sat-P00S00", "gnd0", "gnd0", 5.0),
        ("sat-P00S00", "sat-P01S00", "isl0", "isl0", 14.0),
        ("sat-P01S00", "sat-P02S00", "isl0", "isl0", 14.0),
        ("sat-P02S00", "gs-b", "gnd0", "gnd0", 5.0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    assert len(result.hops) == 5
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["gs-a", "sat-P00S00", "sat-P01S00", "sat-P02S00", "gs-b"]


def test_bidirectional_path():
    """Path from gs-a->gs-b and gs-b->gs-a both work."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = [
        ("gs-a", "sat-P00S00", "gnd0", "gnd0", 5.0),
        ("sat-P00S00", "gs-b", "gnd1", "gnd0", 5.0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)

    fwd = deriver.derive("gs-a", "gs-b")
    assert fwd.reachable is True
    assert [h.node_id for h in fwd.hops] == ["gs-a", "sat-P00S00", "gs-b"]

    rev = deriver.derive("gs-b", "gs-a")
    assert rev.reachable is True
    assert [h.node_id for h in rev.hops] == ["gs-b", "sat-P00S00", "gs-a"]


# ── any-to-any src/dst ───────────────────────────────────────────────────

def test_satellite_to_satellite_path():
    """Satellites can be src/dst if they have prefixes."""
    nodes = [
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("sat-P01S00", "satellite", sid=16002, plane=1, slot=0),
    ]
    edges = [
        ("sat-P00S00", "sat-P01S00", "isl0", "isl0", 14.0),
    ]
    prefix_map = {"sat-P00S00": "10.1.0.0/24", "sat-P01S00": "10.1.1.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("sat-P00S00", "sat-P01S00")
    assert result.reachable is True
    assert [h.node_id for h in result.hops] == ["sat-P00S00", "sat-P01S00"]


def test_satellite_src_without_prefix_still_works():
    """A satellite src without a prefix can still route to a dst with a prefix."""
    nodes = [
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = [
        ("sat-P00S00", "gs-b", "gnd0", "gnd0", 5.0),
    ]
    prefix_map = {"gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("sat-P00S00", "gs-b")
    assert result.reachable is True
    assert [h.node_id for h in result.hops] == ["sat-P00S00", "gs-b"]


# ── unreachable cases ────────────────────────────────────────────────────

def test_unreachable_disconnected():
    """Disconnected nodes return unreachable."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = []  # no edges
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False
    assert "no feasible path" in result.unreachable_reason


def test_unreachable_no_almanac_entries():
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("gs-b", "ground_station", sid=24002),
    ]
    store = MagicMock()
    store.entries = []
    store.get_entry_at.return_value = None
    deriver = PathDeriver(
        almanac_store=store,
        prefix_map={"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"},
        node_registry={n.node_id: n for n in nodes},
        interface_map={},
    )
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False


# ── metadata ─────────────────────────────────────────────────────────────

def test_path_method_is_cspf():
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = [
        ("gs-a", "sat-P00S00", "gnd0", "gnd0", 5.0),
        ("sat-P00S00", "gs-b", "gnd1", "gnd0", 5.0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("gs-a", "gs-b")
    assert result.method == "cspf"


def test_path_total_latency_sums_hops():
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = [
        ("gs-a", "sat-P00S00", "gnd0", "gnd0", 5.0),
        ("sat-P00S00", "gs-b", "gnd1", "gnd0", 4.0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    assert result.total_latency_ms > 0


def test_historical_path_uses_sim_time():
    """derive() with sim_time calls get_entry_at."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("gs-b", "ground_station", sid=24002),
    ]
    store = MagicMock()
    store.entries = []
    store.get_entry_at.return_value = None
    deriver = PathDeriver(
        almanac_store=store,
        prefix_map={"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"},
        node_registry={n.node_id: n for n in nodes},
        interface_map={},
    )
    deriver.derive("gs-a", "gs-b", sim_time="2026-01-01T00:01:00Z")
    store.get_entry_at.assert_called_once_with("2026-01-01T00:01:00Z")


def test_path_sim_time_and_state_id_in_result():
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    edges = [
        ("gs-a", "sat-P00S00", "gnd0", "gnd0", 5.0),
        ("sat-P00S00", "gs-b", "gnd1", "gnd0", 5.0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("gs-a", "gs-b")
    assert result.sim_time == "2026-01-01T00:01:00Z"
    assert result.topology_state_id == "s1"


def test_prefers_fewer_hops_over_ring_wandering():
    """With uniform latencies, CSPF should take the direct 3-hop path,
    not the 8-hop ring path."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("sat-P00S01", "satellite", sid=16002, plane=0, slot=1),
        _node("sat-P00S02", "satellite", sid=16003, plane=0, slot=2),
        _node("sat-P01S00", "satellite", sid=16004, plane=1, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    # Ring: S00 -- S01 -- S02, cross-plane: S00 -- P01S00
    # Direct: gs-a -> S00 -> P01S00 -> gs-b (3 hops)
    # Ring: gs-a -> S00 -> S01 -> S02 -> ... longer
    edges = [
        ("gs-a", "sat-P00S00", "gnd0", "gnd0", 5.0),
        ("sat-P00S00", "sat-P00S01", "isl0", "isl0", 14.0),
        ("sat-P00S01", "sat-P00S02", "isl0", "isl0", 14.0),
        ("sat-P00S00", "sat-P01S00", "isl2", "isl3", 14.5),
        ("sat-P01S00", "gs-b", "gnd0", "gnd0", 5.0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver(nodes, edges, prefix_map)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    hop_ids = [h.node_id for h in result.hops]
    # Direct cross-plane path, not ring path
    assert hop_ids == ["gs-a", "sat-P00S00", "sat-P01S00", "gs-b"]
    assert len(result.hops) == 4
