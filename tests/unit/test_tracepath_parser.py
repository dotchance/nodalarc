"""Tests for lib/nodalarc/tracepath_parser.py."""

from nodalarc.tracepath_parser import parse_tracepath


def test_parse_clean_3_hop():
    """Parse clean 3-hop output with destination reached."""
    output = """\
 1?: [LOCALHOST]                        pmtu 9000
 1:  10.0.0.1                           8.613ms
 2:  10.0.0.2                          22.147ms
 3:  10.0.100.2                        35.512ms reached
     Resume: pmtu 9000 hops 3 back 3
"""
    result = parse_tracepath(output)
    assert len(result.hops) == 3
    assert result.hops[0].ip == "10.0.0.1"
    assert result.hops[0].rtt_ms == 8.613
    assert result.hops[0].reached is False
    assert result.hops[1].ip == "10.0.0.2"
    assert result.hops[2].ip == "10.0.100.2"
    assert result.hops[2].reached is True
    assert result.pmtu == 9000
    assert result.forward_hops == 3
    assert result.return_hops == 3


def test_parse_asymmetric():
    """Parse output with asymmetric return path."""
    output = """\
 1?: [LOCALHOST]                        pmtu 9000
 1:  10.0.0.1                           8.613ms
 2:  10.0.0.2                          22.147ms asymm  3
 3:  10.0.100.2                        35.512ms reached
     Resume: pmtu 9000 hops 3 back 4
"""
    result = parse_tracepath(output)
    assert len(result.hops) == 3
    assert result.hops[1].asymm == 3
    assert result.hops[0].asymm is None
    assert result.forward_hops == 3
    assert result.return_hops == 4


def test_parse_pmtu_change():
    """Parse output with mid-path PMTU change."""
    output = """\
 1?: [LOCALHOST]                        pmtu 9000
 1:  10.0.0.1                           8.613ms
 2:  pmtu 1500
 2:  10.0.0.2                          22.147ms
 3:  10.0.100.2                        35.512ms reached
     Resume: pmtu 1500 hops 3 back 3
"""
    result = parse_tracepath(output)
    # PMTU change creates a hop entry + the regular hop
    pmtu_hops = [h for h in result.hops if h.pmtu is not None]
    assert len(pmtu_hops) == 1
    assert pmtu_hops[0].pmtu == 1500
    ip_hops = [h for h in result.hops if h.ip is not None]
    assert len(ip_hops) == 3
    assert result.pmtu == 1500  # Resume line overrides


def test_parse_resume_line():
    """Parse Resume line for forward/return hops and PMTU."""
    output = """\
 1:  10.0.0.1                           8.613ms
 3:  10.0.100.2                        35.512ms reached
     Resume: pmtu 9000 hops 3 back 4
"""
    result = parse_tracepath(output)
    assert result.forward_hops == 3
    assert result.return_hops == 4
    assert result.pmtu == 9000


def test_parse_unreached():
    """Parse output where destination was not reached."""
    output = """\
 1?: [LOCALHOST]                        pmtu 9000
 1:  10.0.0.1                           8.613ms
 2:  10.0.0.2                          22.147ms
 3:  no reply
"""
    result = parse_tracepath(output)
    ip_hops = [h for h in result.hops if h.ip is not None]
    assert len(ip_hops) == 2
    assert all(not h.reached for h in ip_hops)


def test_parse_empty_string():
    """Parse empty string returns empty result."""
    result = parse_tracepath("")
    assert len(result.hops) == 0
    assert result.pmtu is None
    assert result.forward_hops is None
    assert result.return_hops is None


def test_parse_localhost_skipped():
    """LOCALHOST line is not in the hop list (parsed as PMTU only)."""
    output = """\
 1?: [LOCALHOST]                        pmtu 9000
 1:  10.0.0.1                           8.613ms reached
     Resume: pmtu 9000 hops 1 back 1
"""
    result = parse_tracepath(output)
    assert all(h.ip != "LOCALHOST" for h in result.hops if h.ip)
    ip_hops = [h for h in result.hops if h.ip is not None]
    assert len(ip_hops) == 1
    assert ip_hops[0].ip == "10.0.0.1"


def test_parse_with_b_flag():
    """Parse output from tracepath -n -b (IP followed by (IP) in parens)."""
    output = """\
 1?: [LOCALHOST]                      pmtu 9000
 1:  10.1.2.1 (10.1.2.1)                                   7.106ms
 1:  10.1.2.1 (10.1.2.1)                                   7.103ms
 2:  10.2.2.1 (10.2.2.1)                                   7.136ms
 3:  10.2.1.1 (10.2.1.1)                                  20.598ms
 4:  10.255.5.1 (10.255.5.1)                              30.389ms reached
     Resume: pmtu 9000 hops 4 back 4
"""
    result = parse_tracepath(output)
    ip_hops = [h for h in result.hops if h.ip is not None]
    # Duplicate hop 1 lines both parse (tracepath retries)
    assert len(ip_hops) == 5
    assert ip_hops[0].ip == "10.1.2.1"
    assert ip_hops[0].rtt_ms == 7.106
    assert ip_hops[-1].ip == "10.255.5.1"
    assert ip_hops[-1].reached is True
    assert result.forward_hops == 4
    assert result.return_hops == 4
