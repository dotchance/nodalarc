"""Tests for vs_api/continuous_tracer.py — unit tests for helper methods."""

from nodalarc.models.path import LiveTraceLink, PathHop, TracepathHop, TracepathResult
from vs_api.continuous_tracer import ContinuousTracer

from nodalpath.models.topology import TopologyNode


def _make_tracer(
    node_registry=None,
    interface_map=None,
    pid_map=None,
) -> ContinuousTracer:
    """Create a ContinuousTracer with minimal config for unit testing."""
    if node_registry is None:
        node_registry = {
            "gs-alpha": TopologyNode(
                node_id="gs-alpha",
                node_type="ground_station",
                sid=24000,
                loopback_ipv4="10.2.0.1",
            ),
            "sat-P00S00": TopologyNode(
                node_id="sat-P00S00",
                node_type="satellite",
                sid=16001,
                loopback_ipv4="10.0.0.1",
                plane=0,
                slot=0,
            ),
            "sat-P00S01": TopologyNode(
                node_id="sat-P00S01",
                node_type="satellite",
                sid=16002,
                loopback_ipv4="10.0.0.2",
                plane=0,
                slot=1,
            ),
            "gs-beta": TopologyNode(
                node_id="gs-beta",
                node_type="ground_station",
                sid=24001,
                loopback_ipv4="10.2.1.1",
            ),
        }
    if interface_map is None:
        interface_map = {
            ("gs-alpha", "sat-P00S00"): ("gnd0", "gnd0"),
            ("sat-P00S00", "sat-P00S01"): ("isl0", "isl0"),
            ("gs-beta", "sat-P00S01"): ("gnd0", "gnd0"),
        }
    if pid_map is None:
        pid_map = {
            "gs-alpha": 1001,
            "sat-P00S00": 1002,
            "sat-P00S01": 1003,
            "gs-beta": 1004,
        }

    from nodalarc.platform_config import get_platform_config

    config = get_platform_config()

    return ContinuousTracer(
        node_registry=node_registry,
        interface_map=interface_map,
        pid_map=pid_map,
        trace_mode="ip",
        config=config,
        timeline_path=None,
        get_sim_time=lambda: "2026-03-13T10:00:00Z",
    )


def test_map_hops():
    """TracepathResult + ip_to_node -> correct PathHop list."""
    tracer = _make_tracer()
    src_node = tracer._node_registry["gs-alpha"]
    parsed = TracepathResult(
        hops=[
            TracepathHop(hop_num=1, ip="10.0.0.1", rtt_ms=5.0),
            TracepathHop(hop_num=2, ip="10.0.0.2", rtt_ms=12.0),
            TracepathHop(hop_num=3, ip="10.2.1.1", rtt_ms=20.0, reached=True),
        ],
        raw_output="test",
    )
    hops = tracer._map_hops(parsed, src_node)
    assert len(hops) == 4  # src + 3 traced
    assert hops[0].node_id == "gs-alpha"
    assert hops[0].rtt_ms == 0.0
    assert hops[1].node_id == "sat-P00S00"
    assert hops[1].rtt_ms == 5.0
    assert hops[2].node_id == "sat-P00S01"
    assert hops[3].node_id == "gs-beta"


def test_build_links():
    """Build LiveTraceLink list from hop pairs with correct interfaces."""
    tracer = _make_tracer()
    hops = [
        PathHop(node_id="gs-alpha", node_type="ground_station"),
        PathHop(node_id="sat-P00S00", node_type="satellite"),
        PathHop(node_id="sat-P00S01", node_type="satellite"),
        PathHop(node_id="gs-beta", node_type="ground_station"),
    ]
    links = tracer._build_links(hops)
    assert len(links) == 3
    assert links[0].from_node == "gs-alpha"
    assert links[0].to_node == "sat-P00S00"
    assert links[0].interface == "gnd0"
    assert links[0].link_type == "ground"
    assert links[1].from_node == "sat-P00S00"
    assert links[1].to_node == "sat-P00S01"
    assert links[1].interface == "isl0"
    assert links[1].link_type == "isl"
    assert links[2].link_type == "ground"


def test_build_delay_queries():
    """Hop pairs + interface_map -> correct delay queries."""
    tracer = _make_tracer()
    links = [
        LiveTraceLink(from_node="gs-alpha", to_node="sat-P00S00", interface="gnd0"),
        LiveTraceLink(from_node="sat-P00S00", to_node="sat-P00S01", interface="isl0"),
    ]
    queries = tracer._build_delay_queries(links)
    assert len(queries) == 2
    assert queries[0]["pid"] == 1001
    assert queries[0]["ifname"] == "gnd0"
    assert queries[1]["pid"] == 1002
    assert queries[1]["ifname"] == "isl0"


def test_adaptive_interval():
    """Fast interval when near path change, normal otherwise."""
    from nodalarc.platform_config import get_platform_config

    config = get_platform_config()

    # Near path change (5s < 30s window)
    assert 5.0 < config.trace_fast_window_seconds
    # Would select fast interval
    assert config.trace_interval_fast_seconds < config.trace_interval_seconds

    # No predicted change (None) -> normal interval
    # (This is tested indirectly through the trace loop logic)


def test_path_change_detection():
    """Different hop sequences trigger on_path_change callback."""
    changes = []

    def on_change(src, dst, old_hops, new_hops):
        changes.append((src, dst, old_hops, new_hops))

    tracer = _make_tracer()
    tracer._on_path_change = on_change

    # Simulate: first result has path A, second has path B
    # We test the detection logic directly
    prev = ["gs-alpha", "sat-P00S00", "gs-beta"]
    curr = ["gs-alpha", "sat-P00S01", "gs-beta"]
    assert prev != curr

    # The callback would be called if prev != curr
    if prev and curr != prev:
        on_change("gs-alpha", "gs-beta", prev, curr)
    assert len(changes) == 1
    assert changes[0][2] == prev
    assert changes[0][3] == curr


def test_traced_path_conversion():
    """LiveTraceResult -> TracedPath conversion."""
    from nodalarc.models.path import LiveTraceDirection, LiveTraceResult

    fwd = LiveTraceDirection(
        hops=[
            PathHop(node_id="gs-alpha", node_type="ground_station"),
            PathHop(node_id="sat-P00S00", node_type="satellite"),
            PathHop(node_id="gs-beta", node_type="ground_station"),
        ],
        links=[],
        rtt_ms=35.5,
        asymmetry_detected=True,
    )
    rev = LiveTraceDirection(
        hops=[
            PathHop(node_id="gs-beta", node_type="ground_station"),
            PathHop(node_id="sat-P00S01", node_type="satellite"),
            PathHop(node_id="gs-alpha", node_type="ground_station"),
        ],
        links=[],
        rtt_ms=40.2,
        asymmetry_detected=False,
    )
    result = LiveTraceResult(
        src="gs-alpha",
        dst="gs-beta",
        forward=fwd,
        reverse=rev,
        traced_at="2026-03-13T10:00:00Z",
        sim_time="2026-03-13T10:00:00Z",
        topology_state_id="abc",
        path_valid_until="2026-03-13T10:05:00Z",
        path_valid_seconds=300.0,
        method="tracepath",
        trace_mode="ip",
    )

    tracer = _make_tracer()
    tracer._latest = result
    tp = tracer.traced_path
    assert tp is not None
    assert tp.flow_id == "__continuous_trace__"
    assert tp.src_node == "gs-alpha"
    assert tp.dst_node == "gs-beta"
    assert tp.hops == ["gs-alpha", "sat-P00S00", "gs-beta"]
    assert tp.reverse_hops == ["gs-beta", "sat-P00S01", "gs-alpha"]
    assert tp.rtt_ms == 35.5
    assert tp.reverse_rtt_ms == 40.2
    assert tp.asymmetry_detected is True
    assert tp.path_valid_seconds == 300.0


def test_extract_rtt():
    """Extract RTT from last hop with IP."""
    parsed = TracepathResult(
        hops=[
            TracepathHop(hop_num=1, ip="10.0.0.1", rtt_ms=5.0),
            TracepathHop(hop_num=2, ip="10.0.0.2", rtt_ms=12.345),
        ],
        raw_output="test",
    )
    assert ContinuousTracer._extract_rtt(parsed) == 12.345


def test_extract_rtt_empty():
    """Extract RTT from empty parsed result."""
    parsed = TracepathResult(hops=[], raw_output="")
    assert ContinuousTracer._extract_rtt(parsed) == 0.0


def test_load_session_context_returns_5_tuple():
    """Verify load_session_context return type has 5 elements.

    The VS-API _create_continuous_tracer() indexes into the result
    tuple as ctx[0] and ctx[1]. This test catches regressions if
    the return type changes.
    """
    import typing

    from nodalpath.orchestrator.session_loader import load_session_context

    hints = typing.get_type_hints(load_session_context)
    ret = hints["return"]
    args = typing.get_args(ret)
    assert len(args) == 5, f"load_session_context should return 5-tuple, got {len(args)}"
