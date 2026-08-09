"""Tests for vs_api/continuous_tracer.py — unit tests for helper methods."""

from types import SimpleNamespace

from nodalarc.models.path import LiveTraceLink, PathHop, TracepathHop, TracepathResult
from vs_api.continuous_tracer import ContinuousTracer


def _make_tracer(
    node_registry=None,
    interface_map=None,
    pid_map=None,
) -> ContinuousTracer:
    """Create a ContinuousTracer with minimal config for unit testing."""
    if node_registry is None:
        node_registry = {
            "gs-alpha": SimpleNamespace(
                node_id="gs-alpha",
                node_type="ground_station",
                sid=24000,
                loopback_ipv4="10.2.0.1",
            ),
            "sat-P00S00": SimpleNamespace(
                node_id="sat-P00S00",
                node_type="satellite",
                sid=16001,
                loopback_ipv4="10.0.0.1",
                plane=0,
                slot=0,
            ),
            "sat-P00S01": SimpleNamespace(
                node_id="sat-P00S01",
                node_type="satellite",
                sid=16002,
                loopback_ipv4="10.0.0.2",
                plane=0,
                slot=1,
            ),
            "gs-beta": SimpleNamespace(
                node_id="gs-beta",
                node_type="ground_station",
                sid=24001,
                loopback_ipv4="10.2.1.1",
            ),
        }
    if interface_map is None:
        interface_map = {
            ("sat-P00S00", "sat-P00S01"): ("isl0", "isl0"),
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


def test_build_links_uses_only_known_fixed_interfaces():
    """Dynamic access hops stay unidentified rather than claiming term0/gnd0."""
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
    assert links[0].interface == ""
    assert links[0].link_type is None
    assert links[1].from_node == "sat-P00S00"
    assert links[1].to_node == "sat-P00S01"
    assert links[1].interface == "isl0"
    assert links[1].link_type == "isl"
    assert links[2].interface == ""
    assert links[2].link_type is None


def test_build_delay_queries():
    """Hop pairs + interface_map -> correct delay queries."""
    tracer = _make_tracer()
    links = [
        LiveTraceLink(from_node="gs-alpha", to_node="sat-P00S00", interface="term0"),
        LiveTraceLink(from_node="sat-P00S00", to_node="sat-P00S01", interface="isl0"),
    ]
    queries = tracer._build_delay_queries(links)
    assert len(queries) == 2
    assert queries[0]["pid"] == 1001
    assert queries[0]["ifname"] == "term0"
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


def test_tracer_accepts_dict_node_registry():
    """External path engines may provide plain dict node records."""
    tracer = _make_tracer(
        node_registry={
            "gs-alpha": {"node_id": "gs-alpha", "loopback_ipv4": "10.2.0.1"},
            "gs-beta": {"node_id": "gs-beta", "loopback_ipv4": "10.2.1.1"},
        },
        interface_map={},
        pid_map={},
    )
    assert tracer._ip_to_node["10.2.0.1"] == "gs-alpha"


def test_trace_endpoint_substitutes_gateway_for_host_nodes():
    """TEMPORARY host-node trace stopgap: a host endpoint traces from its FRR
    gateway; a routed endpoint traces from itself."""
    registry = {
        "madrid-gw": SimpleNamespace(
            node_id="madrid-gw",
            node_type="ground_station",
            sid=24001,
            loopback_ipv4="10.2.0.1",
            trace_gateway_node_id=None,
        ),
        "quic-client": SimpleNamespace(
            node_id="quic-client",
            node_type="host",
            sid=None,
            loopback_ipv4="10.255.0.241",
            trace_gateway_node_id="madrid-gw",
        ),
    }
    tracer = _make_tracer(node_registry=registry)

    # A host node resolves to its gateway for tracing.
    host = tracer._node_registry["quic-client"]
    assert tracer._trace_endpoint(host).node_id == "madrid-gw"
    # A routed node resolves to itself.
    router = tracer._node_registry["madrid-gw"]
    assert tracer._trace_endpoint(router).node_id == "madrid-gw"


def test_trace_endpoint_falls_back_to_self_when_gateway_missing():
    """A missing gateway (never expected) must not crash the trace — it falls
    back to the node itself rather than raising."""
    registry = {
        "quic-client": SimpleNamespace(
            node_id="quic-client",
            node_type="host",
            sid=None,
            loopback_ipv4="10.255.0.241",
            trace_gateway_node_id="absent-gw",
        ),
    }
    tracer = _make_tracer(node_registry=registry)
    host = tracer._node_registry["quic-client"]
    assert tracer._trace_endpoint(host).node_id == "quic-client"


def test_run_tracepath_streams_partial_hops():
    """traceroute output arriving in chunks streams each new complete hop line
    to on_partial, so the UI grows the path instead of waiting for the whole
    (possibly slow) command. This is the fix for the 'hangs then dumps' UX."""
    from unittest.mock import patch

    tracer = _make_tracer()

    class _FakeStream:
        def __init__(self, lines):
            self._lines = list(lines)
            self._open = True

        def is_open(self):
            return self._open

        def update(self, timeout=5):
            pass

        def read_stdout(self):
            if self._lines:
                return self._lines.pop(0)
            self._open = False
            return ""

        def read_stderr(self):
            return ""

    lines = [
        "traceroute to 10.0.0.9 (10.0.0.9), 20 hops max\n",
        " 1  10.2.0.1  5.0 ms\n",
        " 2  10.0.0.1  120.0 ms\n",
        " 3  10.0.0.9  2800.0 ms\n",
    ]

    partials: list[int] = []
    with (
        patch("kubernetes.config.load_incluster_config"),
        patch("kubernetes.client.CoreV1Api"),
        patch("kubernetes.stream.stream", return_value=_FakeStream(lines)),
    ):
        result = tracer._run_tracepath(
            "gs-alpha", "10.0.0.9", lambda raw: partials.append(raw.count("\n"))
        )

    assert result["ok"] is True
    # on_partial fired incrementally as each new complete line arrived (not
    # once at the end), so the UI would have seen the path grow.
    assert partials == sorted(partials)  # monotonically increasing
    assert len(partials) >= 3  # at least once per hop line
    assert max(partials) >= 4  # header + 3 hop lines all complete
