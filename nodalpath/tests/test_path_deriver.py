"""Tests for PathDeriver — path derivation from forwarding tables.

The path deriver uses SID-based next-hop resolution:
  - push_label at ingress encodes the first transit node's SID
  - in_label in LSR bindings = the current node's own SID
  - out_label = the next hop's SID
  - _sid_to_node maps SID -> node_id for traversal
"""

from unittest.mock import MagicMock

from nodalpath.engine.path_deriver import PathDeriver
from nodalpath.models.almanac import (
    AlmanacEntry, ForwardingTable, LabelBinding, IngressRule,
)
from nodalpath.models.topology import TopologyNode


# ── helpers ────────────────────────────────────────────────────────────────

def _node(node_id: str, node_type: str, sid: int,
          plane=None, slot=None) -> TopologyNode:
    return TopologyNode(
        node_id=node_id, node_type=node_type,
        sid=sid, loopback_ipv4="10.0.0.1",
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
            out_iface: str, topology_state_id: str = "s1",
            extra_bindings: list[LabelBinding] | None = None) -> ForwardingTable:
    bindings = [
        LabelBinding(in_label=in_label, action=action, out_label=out_label,
                     out_interface=out_iface)
    ]
    if extra_bindings:
        bindings.extend(extra_bindings)
    return ForwardingTable(
        node_id=node_id,
        topology_state_id=topology_state_id,
        sim_time="2026-01-01T00:01:00Z",
        lsr_bindings=bindings,
        ler_ingress_rules=[],
    )


def _deriver(fts: list, prefix_map: dict, nodes: list) -> PathDeriver:
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
        interface_map={},  # unused by SID-based traversal
    )


# ── basic path tests ──────────────────────────────────────────────────────

def test_simple_path_gs_sat_gs():
    """gs-a -> sat-P00S00 -> gs-b, single hop through one satellite."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        # Ingress: push sat's SID as label
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        # Transit: sat receives its own SID as in_label, pops (penultimate hop)
        _sat_ft("sat-P00S00", in_label=16001, action="pop", out_label=None, out_iface="gnd1"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
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
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        _sat_ft("sat-P00S00", in_label=16001, action="swap", out_label=16002, out_iface="isl0"),
        _sat_ft("sat-P01S00", in_label=16002, action="pop", out_label=None, out_iface="gnd1"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    assert len(result.hops) == 4  # gs-a, sat0, sat1, gs-b
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["gs-a", "sat-P00S00", "sat-P01S00", "gs-b"]
    assert result.hops[1].action == "swap"
    assert result.hops[1].out_label == 16002
    assert result.hops[2].action == "pop"


def test_path_three_satellite_hops():
    """gs-a -> sat0 -> sat1 -> sat2 -> gs-b, longer chain."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("sat-P01S00", "satellite", sid=16002, plane=1, slot=0),
        _node("sat-P02S00", "satellite", sid=16003, plane=2, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        _sat_ft("sat-P00S00", in_label=16001, action="swap", out_label=16002, out_iface="isl0"),
        _sat_ft("sat-P01S00", in_label=16002, action="swap", out_label=16003, out_iface="isl0"),
        _sat_ft("sat-P02S00", in_label=16003, action="pop", out_label=None, out_iface="gnd1"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    assert len(result.hops) == 5
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["gs-a", "sat-P00S00", "sat-P01S00", "sat-P02S00", "gs-b"]


def test_bidirectional_path():
    """Path from gs-a->gs-b and gs-b->gs-a both work (reverse direction)."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        # Forward: gs-a -> sat -> gs-b
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        _sat_ft("sat-P00S00", in_label=16001, action="pop", out_label=None, out_iface="gnd1"),
        # Reverse: gs-b -> sat -> gs-a
        ForwardingTable(
            node_id="gs-b", topology_state_id="s1",
            sim_time="2026-01-01T00:01:00Z",
            lsr_bindings=[],
            ler_ingress_rules=[
                IngressRule(dst_prefix="10.0.1.0/24", push_label=16001, out_interface="gnd0"),
            ],
        ),
    ]
    # Add reverse pop binding to satellite's table
    fts[1] = ForwardingTable(
        node_id="sat-P00S00", topology_state_id="s1",
        sim_time="2026-01-01T00:01:00Z",
        lsr_bindings=[
            LabelBinding(in_label=16001, action="pop", out_label=None, out_interface="gnd1"),
            LabelBinding(in_label=16001, action="pop", out_label=None, out_interface="gnd0"),
        ],
        ler_ingress_rules=[],
    )
    deriver = _deriver(fts, prefix_map, nodes)

    fwd = deriver.derive("gs-a", "gs-b")
    assert fwd.reachable is True
    assert [h.node_id for h in fwd.hops] == ["gs-a", "sat-P00S00", "gs-b"]

    rev = deriver.derive("gs-b", "gs-a")
    assert rev.reachable is True
    assert [h.node_id for h in rev.hops] == ["gs-b", "sat-P00S00", "gs-a"]


# ── binding disambiguation ────────────────────────────────────────────────

def test_binding_disambiguation_prefers_swap_over_pop():
    """When multiple bindings match, prefer swap to unvisited node over pop."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("sat-P01S00", "satellite", sid=16002, plane=1, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    # sat-P00S00 has both a pop (for direct gs-b traffic) and a swap (to sat-P01S00)
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        ForwardingTable(
            node_id="sat-P00S00", topology_state_id="s1",
            sim_time="2026-01-01T00:01:00Z",
            lsr_bindings=[
                LabelBinding(in_label=16001, action="pop", out_label=None, out_interface="gnd1"),
                LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0"),
            ],
            ler_ingress_rules=[],
        ),
        _sat_ft("sat-P01S00", in_label=16002, action="pop", out_label=None, out_iface="gnd1"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    # Should take the swap path: gs-a -> sat0 -> sat1 -> gs-b
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["gs-a", "sat-P00S00", "sat-P01S00", "gs-b"]
    assert result.hops[1].action == "swap"


def test_binding_disambiguation_falls_back_to_pop():
    """When only pop bindings match (no swap with unvisited out_label), use pop."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    # Only pop binding available
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        ForwardingTable(
            node_id="sat-P00S00", topology_state_id="s1",
            sim_time="2026-01-01T00:01:00Z",
            lsr_bindings=[
                LabelBinding(in_label=16001, action="pop", out_label=None, out_interface="gnd1"),
                LabelBinding(in_label=16001, action="pop", out_label=None, out_interface="gnd0"),
            ],
            ler_ingress_rules=[],
        ),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is True
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["gs-a", "sat-P00S00", "gs-b"]


# ── any-to-any src/dst (not just ground stations) ─────────────────────────

def test_satellite_to_satellite_path():
    """Satellites can be src/dst if they have ingress rules and prefixes."""
    nodes = [
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("sat-P01S00", "satellite", sid=16002, plane=1, slot=0),
    ]
    prefix_map = {"sat-P00S00": "10.1.0.0/24", "sat-P01S00": "10.1.1.0/24"}
    fts = [
        ForwardingTable(
            node_id="sat-P00S00", topology_state_id="s1",
            sim_time="2026-01-01T00:01:00Z",
            lsr_bindings=[],
            ler_ingress_rules=[
                IngressRule(dst_prefix="10.1.1.0/24", push_label=16002, out_interface="isl0"),
            ],
        ),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("sat-P00S00", "sat-P01S00")
    assert result.reachable is True
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["sat-P00S00", "sat-P01S00"]


def test_satellite_src_without_prefix_still_works():
    """A satellite src without a prefix is valid — only dst needs a prefix."""
    nodes = [
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-b": "10.0.2.0/24"}
    fts = [
        ForwardingTable(
            node_id="sat-P00S00", topology_state_id="s1",
            sim_time="2026-01-01T00:01:00Z",
            lsr_bindings=[],
            ler_ingress_rules=[
                IngressRule(dst_prefix="10.0.2.0/24", push_label=24002, out_interface="gnd0"),
            ],
        ),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("sat-P00S00", "gs-b")
    assert result.reachable is True
    hop_ids = [h.node_id for h in result.hops]
    assert hop_ids == ["sat-P00S00", "gs-b"]


# ── unreachable cases ─────────────────────────────────────────────────────

def test_unreachable_no_ingress_rule():
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        ForwardingTable(
            node_id="gs-a", topology_state_id="s1",
            sim_time="2026-01-01T00:01:00Z",
            lsr_bindings=[], ler_ingress_rules=[],
        )
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False
    assert "ingress rule" in result.unreachable_reason


def test_unreachable_src_not_in_registry():
    nodes = [_node("gs-b", "ground_station", sid=24002)]
    prefix_map = {"gs-b": "10.0.2.0/24"}
    deriver = _deriver([], prefix_map, nodes)
    result = deriver.derive("gs-nonexistent", "gs-b")
    assert result.reachable is False
    assert "not in registry" in result.unreachable_reason


def test_unreachable_dst_no_prefix():
    """Dst without an advertised prefix is unreachable."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24"}  # no prefix for sat
    deriver = _deriver([], prefix_map, nodes)
    result = deriver.derive("gs-a", "sat-P00S00")
    assert result.reachable is False
    assert "no advertised prefix" in result.unreachable_reason


def test_unreachable_no_forwarding_table():
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    deriver = _deriver([], prefix_map, nodes)  # no FTs at all
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False
    assert "no forwarding table" in result.unreachable_reason


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


def test_unreachable_push_label_unknown_sid():
    """Push label that doesn't match any node SID is unreachable."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        # push_label=99999 doesn't match any node SID
        _gs_ft("gs-a", "10.0.2.0/24", push_label=99999, out_iface="gnd0"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False
    assert "does not match any node SID" in result.unreachable_reason


def test_unreachable_no_lsr_binding():
    """Transit node with no LSR binding for current label is unreachable."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        # sat has a forwarding table but no binding for label 16001
        ForwardingTable(
            node_id="sat-P00S00", topology_state_id="s1",
            sim_time="2026-01-01T00:01:00Z",
            lsr_bindings=[],
            ler_ingress_rules=[],
        ),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False
    assert "no LSR binding" in result.unreachable_reason


# ── loop detection ─────────────────────────────────────────────────────────

def test_loop_detection():
    """Routing loop should return unreachable, not hang."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("sat-P01S00", "satellite", sid=16002, plane=1, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        _sat_ft("sat-P00S00", in_label=16001, action="swap", out_label=16002, out_iface="isl0"),
        _sat_ft("sat-P01S00", in_label=16002, action="swap", out_label=16001, out_iface="isl1"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.reachable is False
    assert "loop" in result.unreachable_reason


# ── metadata ──────────────────────────────────────────────────────────────

def test_path_method_is_derived():
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        _sat_ft("sat-P00S00", in_label=16001, action="pop", out_label=None, out_iface="gnd1"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.method == "derived"


def test_path_total_latency_sums_hops():
    """Total latency should sum latency_to_next_ms across hops (None when no edges)."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        _sat_ft("sat-P00S00", in_label=16001, action="pop", out_label=None, out_iface="gnd1"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.total_latency_ms == 0.0
    assert result.reachable is True


def test_historical_path_uses_sim_time():
    """derive() with sim_time calls get_entry_at, not entries[-1]."""
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
    """Result should carry sim_time and topology_state_id from the entry."""
    nodes = [
        _node("gs-a", "ground_station", sid=24001),
        _node("sat-P00S00", "satellite", sid=16001, plane=0, slot=0),
        _node("gs-b", "ground_station", sid=24002),
    ]
    prefix_map = {"gs-a": "10.0.1.0/24", "gs-b": "10.0.2.0/24"}
    fts = [
        _gs_ft("gs-a", "10.0.2.0/24", push_label=16001, out_iface="gnd0"),
        _sat_ft("sat-P00S00", in_label=16001, action="pop", out_label=None, out_iface="gnd1"),
    ]
    deriver = _deriver(fts, prefix_map, nodes)
    result = deriver.derive("gs-a", "gs-b")
    assert result.sim_time == "2026-01-01T00:01:00Z"
    assert result.topology_state_id == "s1"
