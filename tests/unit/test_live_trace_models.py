"""Tests for live trace models — serialization round-trip."""

from nodalarc.models.path import (
    LiveTraceDirection,
    LiveTraceLink,
    LiveTraceResult,
    PathHop,
    TracepathHop,
    TracepathResult,
)
from nodalarc.models.vs_api import TracedPath


def test_tracepath_hop_roundtrip():
    hop = TracepathHop(hop_num=1, ip="10.0.0.1", rtt_ms=8.5, asymm=3, reached=True)
    data = hop.model_dump()
    rebuilt = TracepathHop.model_validate(data)
    assert rebuilt == hop


def test_tracepath_result_roundtrip():
    result = TracepathResult(
        hops=[
            TracepathHop(hop_num=1, ip="10.0.0.1", rtt_ms=8.5),
            TracepathHop(hop_num=2, ip="10.0.0.2", rtt_ms=22.1, reached=True),
        ],
        pmtu=9000,
        forward_hops=2,
        return_hops=3,
        raw_output="test output",
    )
    data = result.model_dump()
    rebuilt = TracepathResult.model_validate(data)
    assert rebuilt == result


def test_live_trace_link_roundtrip():
    link = LiveTraceLink(
        from_node="gs-fairbanks",
        to_node="sat-P00S00",
        interface="gnd0",
        netem_delay_ms=12.5,
        link_type="ground",
    )
    data = link.model_dump()
    rebuilt = LiveTraceLink.model_validate(data)
    assert rebuilt == link


def test_live_trace_direction_roundtrip():
    direction = LiveTraceDirection(
        hops=[PathHop(node_id="gs-fairbanks", node_type="ground_station")],
        links=[LiveTraceLink(from_node="gs-fairbanks", to_node="sat-P00S00", interface="gnd0")],
        rtt_ms=35.5,
        asymmetry_detected=False,
        pmtu=9000,
    )
    data = direction.model_dump()
    rebuilt = LiveTraceDirection.model_validate(data)
    assert rebuilt == direction


def test_live_trace_result_roundtrip():
    fwd = LiveTraceDirection(
        hops=[PathHop(node_id="gs-fairbanks", node_type="ground_station")],
        links=[],
        rtt_ms=35.5,
        asymmetry_detected=False,
    )
    rev = LiveTraceDirection(
        hops=[PathHop(node_id="gs-ashburn", node_type="ground_station")],
        links=[],
        rtt_ms=40.2,
        asymmetry_detected=True,
    )
    result = LiveTraceResult(
        src="gs-fairbanks",
        dst="gs-ashburn",
        forward=fwd,
        reverse=rev,
        traced_at="2026-03-13T10:00:00Z",
        sim_time="2026-03-13T10:00:00Z",
        topology_state_id="abc123",
        path_valid_until="2026-03-13T10:05:00Z",
        path_valid_seconds=300.0,
        method="tracepath",
        trace_mode="ip",
    )
    data = result.model_dump()
    rebuilt = LiveTraceResult.model_validate(data)
    assert rebuilt == result


def test_traced_path_backward_compat():
    """TracedPath can be constructed with just original required fields."""
    tp = TracedPath(
        flow_id="test",
        src_node="gs-fairbanks",
        dst_node="gs-ashburn",
        hops=["gs-fairbanks", "sat-P00S00", "gs-ashburn"],
    )
    assert tp.reverse_hops == []
    assert tp.rtt_ms == 0.0
    assert tp.asymmetry_detected is False
    assert tp.method == "tracepath"
    assert tp.path_valid_until is None


def test_traced_path_with_enrichment():
    """TracedPath with all enrichment fields."""
    tp = TracedPath(
        flow_id="__continuous_trace__",
        src_node="gs-fairbanks",
        dst_node="gs-ashburn",
        hops=["gs-fairbanks", "sat-P00S00", "gs-ashburn"],
        reverse_hops=["gs-ashburn", "sat-P01S01", "gs-fairbanks"],
        rtt_ms=35.5,
        reverse_rtt_ms=40.2,
        asymmetry_detected=True,
        method="tracepath",
        path_valid_until="2026-03-13T10:05:00Z",
        path_valid_seconds=300.0,
        traced_at="2026-03-13T10:00:00Z",
    )
    data = tp.model_dump()
    rebuilt = TracedPath.model_validate(data)
    assert rebuilt == tp
