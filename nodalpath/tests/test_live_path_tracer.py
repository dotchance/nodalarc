"""Tests for LivePathTracer trace mode and parsing."""

from nodalpath.engine.live_path_tracer import LivePathTracer, _HOP_RE, _STAR_RE
from nodalpath.models.topology import TopologyNode


def _make_registry() -> dict[str, TopologyNode]:
    """Build a small test node registry."""
    return {
        "gs-ashburn": TopologyNode(
            node_id="gs-ashburn", node_type="ground_station",
            sid=16100, loopback_ipv4="10.0.100.1",
        ),
        "sat-P00S00": TopologyNode(
            node_id="sat-P00S00", node_type="satellite",
            sid=16000, loopback_ipv4="10.0.0.1", plane=0, slot=0,
        ),
        "sat-P00S01": TopologyNode(
            node_id="sat-P00S01", node_type="satellite",
            sid=16001, loopback_ipv4="10.0.0.2", plane=0, slot=1,
        ),
        "sat-P01S00": TopologyNode(
            node_id="sat-P01S00", node_type="satellite",
            sid=16010, loopback_ipv4="10.0.1.1", plane=1, slot=0,
        ),
        "gs-frankfurt": TopologyNode(
            node_id="gs-frankfurt", node_type="ground_station",
            sid=16101, loopback_ipv4="10.0.100.2",
        ),
    }


def _tracer(mode: str = "ip") -> LivePathTracer:
    """Build a tracer with a fake socket path (won't connect)."""
    return LivePathTracer(
        node_registry=_make_registry(),
        trace_mode=mode,
        deploy_socket="/dev/null",
    )


# ── Regex tests ──────────────────────────────────────────────────────────

def test_hop_re_matches():
    line = "  1  10.0.0.1  0.234 ms"
    m = _HOP_RE.match(line)
    assert m is not None
    assert m.group(1) == "1"
    assert m.group(2) == "10.0.0.1"
    assert m.group(3) == "0.234"


def test_star_re_matches():
    line = "  3  *"
    m = _STAR_RE.match(line)
    assert m is not None
    assert m.group(1) == "3"


def test_star_re_matches_double_star():
    """With -q 2, all-timeout shows '  3  * *'."""
    for line in ("  3  * *", "  3  *  *"):
        m = _STAR_RE.match(line)
        assert m is not None, f"Failed to match: {line!r}"


def test_hop_re_with_leading_star():
    """With -q 2, first probe timeout shows '  3  *  10.0.0.1  1.000 ms'."""
    line = "  3  *  10.0.0.1  1.000 ms"
    m = _HOP_RE.match(line)
    assert m is not None
    assert m.group(2) == "10.0.0.1"
    assert m.group(3) == "1.000"


def test_hop_re_with_two_rtts():
    """With -q 2, both probes succeed: '  1  10.0.0.1  0.500 ms  1.200 ms'."""
    line = "  1  10.0.0.1  0.500 ms  1.200 ms"
    m = _HOP_RE.match(line)
    assert m is not None
    assert m.group(2) == "10.0.0.1"
    assert m.group(3) == "0.500"  # captures first RTT


def test_star_re_no_match_on_hop_line():
    """Star RE must not match lines with an IP (only all-star lines)."""
    line = "  3  *  10.0.0.1  1.000 ms"
    m = _STAR_RE.match(line)
    assert m is None


# ── Parse tests ──────────────────────────────────────────────────────────

def test_parse_plain_ip_traceroute():
    """Plain IP output — all hops visible, all resolved."""
    tracer = _tracer("ip")
    output = """\
traceroute to 10.0.100.2 (10.0.100.2), 30 hops max, 60 byte packets
 1  10.0.0.1  0.500 ms
 2  10.0.0.2  1.200 ms
 3  10.0.1.1  2.100 ms
 4  10.0.100.2  3.000 ms
"""
    src_node = _make_registry()["gs-ashburn"]
    hops = tracer._parse(output, src_node)

    assert len(hops) == 5  # src + 4 traced hops
    assert hops[0].node_id == "gs-ashburn"
    assert hops[1].node_id == "sat-P00S00"
    assert hops[1].responding_ip == "10.0.0.1"
    assert hops[2].node_id == "sat-P00S01"
    assert hops[3].node_id == "sat-P01S00"
    assert hops[4].node_id == "gs-frankfurt"
    assert hops[4].responding_ip == "10.0.100.2"


def test_parse_star_hops_skipped():
    """Star hops are MPLS transit or timeout — they should be skipped."""
    tracer = _tracer("sr-uniform")
    output = """\
traceroute to 10.0.100.2 (10.0.100.2), 30 hops max, 60 byte packets
 1  10.0.0.1  0.500 ms
 2  *
 3  10.0.100.2  3.000 ms
"""
    src_node = _make_registry()["gs-ashburn"]
    hops = tracer._parse(output, src_node)

    assert len(hops) == 3  # src + sat-P00S00 + gs-frankfurt
    assert hops[0].node_id == "gs-ashburn"
    assert hops[1].node_id == "sat-P00S00"
    assert hops[2].node_id == "gs-frankfurt"


def test_parse_k3s_ips_filtered():
    """IPs not in the registry (K3s infrastructure) are filtered out."""
    tracer = _tracer("ip")
    output = """\
traceroute to 10.0.100.2 (10.0.100.2), 30 hops max, 60 byte packets
 1  172.16.0.1  0.100 ms
 2  10.0.0.1  0.500 ms
 3  10.0.100.2  1.200 ms
"""
    src_node = _make_registry()["gs-ashburn"]
    hops = tracer._parse(output, src_node)

    assert len(hops) == 3  # src + sat-P00S00 + gs-frankfurt (K3s IP skipped)
    assert hops[0].node_id == "gs-ashburn"
    assert hops[1].node_id == "sat-P00S00"
    assert hops[2].node_id == "gs-frankfurt"


def test_parse_pipe_mode_output():
    """Pipe mode — only src and dst visible, core collapsed."""
    tracer = _tracer("sr-pipe")
    output = """\
traceroute to 10.0.100.2 (10.0.100.2), 30 hops max, 60 byte packets
 1  10.0.100.2  5.000 ms
"""
    src_node = _make_registry()["gs-ashburn"]
    hops = tracer._parse(output, src_node)

    assert len(hops) == 2  # src + dst only
    assert hops[0].node_id == "gs-ashburn"
    assert hops[1].node_id == "gs-frankfurt"


def test_parse_q2_partial_timeout():
    """With -q 2, first probe times out but second succeeds at dst hop."""
    tracer = _tracer("ip")
    output = """\
traceroute to 10.0.100.2 (10.0.100.2), 30 hops max, 60 byte packets
 1  10.0.0.1  0.500 ms  0.600 ms
 2  *  10.0.100.2  3.000 ms
"""
    src_node = _make_registry()["gs-ashburn"]
    hops = tracer._parse(output, src_node)

    assert len(hops) == 3  # src + sat-P00S00 + gs-frankfurt
    assert hops[0].node_id == "gs-ashburn"
    assert hops[1].node_id == "sat-P00S00"
    assert hops[2].node_id == "gs-frankfurt"


def test_parse_empty_output():
    """Empty traceroute output should return empty hops."""
    tracer = _tracer("ip")
    src_node = _make_registry()["gs-ashburn"]
    hops = tracer._parse("", src_node)
    assert hops == []


# ── Method and pipe_mode tests ───────────────────────────────────────────

def test_trace_mode_ip_method():
    tracer = _tracer("ip")
    assert tracer.trace_mode == "ip"
    result = LivePathTracer._unreachable(
        "gs-ashburn", "gs-frankfurt", "2026-01-01T00:00:00Z",
        "traceroute", False, "test",
    )
    assert result.method == "traceroute"
    assert result.pipe_mode is False


def test_trace_mode_sr_uniform_method():
    tracer = _tracer("sr-uniform")
    assert tracer.trace_mode == "sr-uniform"
    result = LivePathTracer._unreachable(
        "gs-ashburn", "gs-frankfurt", "2026-01-01T00:00:00Z",
        "traceroute-sr", False, "test",
    )
    assert result.method == "traceroute-sr"
    assert result.pipe_mode is False


def test_trace_mode_sr_pipe_method():
    tracer = _tracer("sr-pipe")
    assert tracer.trace_mode == "sr-pipe"
    result = LivePathTracer._unreachable(
        "gs-ashburn", "gs-frankfurt", "2026-01-01T00:00:00Z",
        "traceroute-sr-pipe", True, "test",
    )
    assert result.method == "traceroute-sr-pipe"
    assert result.pipe_mode is True


# ── responding_ip populated ──────────────────────────────────────────────

def test_responding_ip_populated():
    """Each hop from traceroute output should have responding_ip set."""
    tracer = _tracer("ip")
    output = """\
traceroute to 10.0.100.2 (10.0.100.2), 30 hops max, 60 byte packets
 1  10.0.0.1  0.500 ms
 2  10.0.100.2  1.200 ms
"""
    src_node = _make_registry()["gs-ashburn"]
    hops = tracer._parse(output, src_node)

    # src hop has no responding_ip (prepended, not from traceroute)
    assert hops[0].responding_ip is None
    # traced hops should have responding_ip
    assert hops[1].responding_ip == "10.0.0.1"
    assert hops[2].responding_ip == "10.0.100.2"
