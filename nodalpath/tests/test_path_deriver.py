"""Tests for PathDeriver — path derivation from forwarding tables."""

from unittest.mock import MagicMock

from nodalpath.engine.path_deriver import PathDeriver
from nodalpath.models.almanac import (
    AlmanacEntry, ForwardingTable, LabelBinding, IngressRule,
)
from nodalpath.models.topology import TopologyNode


def _node(node_id: str, node_type: str, plane=None, slot=None) -> TopologyNode:
    return TopologyNode(
        node_id=node_id, node_type=node_type,
        sid=1, loopback_ipv4="10.0.0.1",
        plane=plane, slot=slot,
    )


def _gs_ft(node_id: str, dst_prefix: str, push_label: int, out_iface: str,
           topology_state_id: str = "s1") -> ForwardingTable:
    return ForwardingTable(
        node_id=node_id,
        topology_state_id=topology_state_id,
        sim_time="2026-01-01T00:01:00Z",
        lsr_bindings=[],
        ler_ingress_rules=[
            IngressRule(dst_prefix=dst_prefix, push_label=push_label, out_interface=out_iface)
        ],
    )


def _sat_ft(node_id: str, in_label: int, action: str, out_label: int | None,
            out_iface: str, topology_state_id: str = "s1") -> ForwardingTable:
    return ForwardingTable(
        node_id=node_id,
        topology_state_id=topology_state_id,
        sim_time="2026-01-01T00:01:00Z",
        lsr_bindings=[
            LabelBinding(in_label=in_label, action=action, out_label=out_label,
                        out_interface=out_iface)
        ],
        ler_ingress_rules=[],
    )


def _deriver(fts: list, interface_map: dict, prefix_map: dict, nodes: list) -> PathDeriver:
    entry = AlmanacEntry(
        topology_state_id="s1",
        sim_time="2026-01-01T00:01:00Z",
        forwarding_tables=fts,
        computed_paths=[],
        computation_time_ms=1.0,
    )
    store = MagicMock()
    store.entries = [entry]
    store.get_entry_at.return_value = entry
    node_registry = {n.node_id: n for n in nodes}
    return PathDeriver(
        almanac_store=store,
        prefix_map=prefix_map,
        node_registry=node_registry,
        interface_map=interface_map,
    )


def test_simple_path_gs_sat_gs():
    """gs-a -> sat-P00S00 -> gs-b, single hop through one satellite."""
    nodes = [
        _node("gs-a", "ground_station"),
        _node("sat-P00S00", "satellite", 0, 0),
        _node("gs-b", "ground_station"),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    interface_map = {
        ("gs-a", "sat-P00S00"): ("gnd0", "gnd0"),
        ("gs-b", "sat-P00S00"): ("gnd0", "gnd1"),
    }
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", 100, "gnd0"),
        _sat_ft("sat-P00S00", 100, "pop", None, "gnd1"),
    ]
    deriver = _deriver(fts, interface_map, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    hop_ids = [h.node_id for h in result.hops]
    assert "gs-a" in hop_ids
    assert "sat-P00S00" in hop_ids
    assert "gs-b" in hop_ids


def test_path_two_satellite_hops():
    """gs-a -> sat-P00S00 -> sat-P01S00 -> gs-b"""
    nodes = [
        _node("gs-a", "ground_station"),
        _node("sat-P00S00", "satellite", 0, 0),
        _node("sat-P01S00", "satellite", 1, 0),
        _node("gs-b", "ground_station"),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    interface_map = {
        ("gs-a", "sat-P00S00"): ("gnd0", "gnd0"),
        ("sat-P00S00", "sat-P01S00"): ("isl0", "isl0"),
        ("gs-b", "sat-P01S00"): ("gnd0", "gnd1"),
    }
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", 100, "gnd0"),
        _sat_ft("sat-P00S00", 100, "swap", 200, "isl0"),
        _sat_ft("sat-P01S00", 200, "pop", None, "gnd1"),
    ]
    deriver = _deriver(fts, interface_map, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    assert len(result.hops) == 4   # gs-a, sat0, sat1, gs-b


def test_unreachable_no_ingress_rule():
    nodes = [_node("gs-a", "ground_station"), _node("gs-b", "ground_station")]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        ForwardingTable(
            node_id="gs-a", topology_state_id="s1",
            sim_time="2026-01-01T00:01:00Z",
            lsr_bindings=[], ler_ingress_rules=[],  # no rules
        )
    ]
    deriver = _deriver(fts, {}, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False
    assert "ingress rule" in result.unreachable_reason


def test_unreachable_src_not_ground_station():
    nodes = [_node("sat-P00S00", "satellite", 0, 0), _node("gs-b", "ground_station")]
    prefix_map = {"gs-b": "10.0.2.0/24"}
    deriver = _deriver([], {}, prefix_map, nodes)
    result = deriver.derive("sat-P00S00", "gs-b")
    assert result.reachable is False
    assert "not a ground station" in result.unreachable_reason


def test_unreachable_dst_not_ground_station():
    nodes = [_node("gs-a", "ground_station"), _node("sat-P00S00", "satellite", 0, 0)]
    prefix_map = {"gs-a": "10.0.1.0/24"}
    deriver = _deriver([], {}, prefix_map, nodes)
    result = deriver.derive("gs-a", "sat-P00S00")
    assert result.reachable is False


def test_unreachable_no_almanac_entries():
    nodes = [_node("gs-a", "ground_station"), _node("gs-b", "ground_station")]
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


def test_path_method_is_derived():
    nodes = [_node("gs-a", "ground_station"), _node("gs-b", "ground_station")]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    interface_map = {("gs-a", "gs-b"): ("gnd0", "gnd0")}
    fts = [_gs_ft("gs-a", "10.0.2.0/24", 100, "gnd0")]
    gs_b_ft = ForwardingTable(
        node_id="gs-b", topology_state_id="s1",
        sim_time="2026-01-01T00:01:00Z",
        lsr_bindings=[LabelBinding(in_label=100, action="pop", out_label=None, out_interface="gnd0")],
        ler_ingress_rules=[],
    )
    deriver = _deriver(fts + [gs_b_ft], interface_map, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.method == "derived"


def test_path_total_latency_sums_hops():
    """Total latency should sum latency_to_next_ms across hops (None when no edges)."""
    nodes = [
        _node("gs-a", "ground_station"),
        _node("sat-P00S00", "satellite", 0, 0),
        _node("gs-b", "ground_station"),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    interface_map = {
        ("gs-a", "sat-P00S00"): ("gnd0", "gnd0"),
        ("gs-b", "sat-P00S00"): ("gnd0", "gnd1"),
    }
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", 100, "gnd0"),
        _sat_ft("sat-P00S00", 100, "pop", None, "gnd1"),
    ]
    deriver = _deriver(fts, interface_map, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    # No edge data in interface_map, so latency_to_next_ms is None for all hops
    assert result.total_latency_ms == 0.0
    assert result.reachable is True


def test_historical_path_uses_sim_time():
    """derive() with sim_time calls get_entry_at, not entries[-1]."""
    nodes = [_node("gs-a", "ground_station"), _node("gs-b", "ground_station")]
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


def test_loop_detection():
    """Routing loop should return unreachable, not hang."""
    nodes = [
        _node("gs-a", "ground_station"),
        _node("sat-P00S00", "satellite", 0, 0),
        _node("sat-P01S00", "satellite", 1, 0),
        _node("gs-b", "ground_station"),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    interface_map = {
        ("gs-a", "sat-P00S00"): ("gnd0", "gnd0"),
        ("sat-P00S00", "sat-P01S00"): ("isl0", "isl0"),
        ("sat-P01S00", "sat-P00S00"): ("isl1", "isl1"),  # loop back
    }
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", 100, "gnd0"),
        _sat_ft("sat-P00S00", 100, "swap", 200, "isl0"),
        _sat_ft("sat-P01S00", 200, "swap", 100, "isl1"),  # loops back
    ]
    deriver = _deriver(fts, interface_map, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False
