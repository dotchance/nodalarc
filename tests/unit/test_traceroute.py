"""BusyBox traceroute output parses into hops exactly as printed."""

import pytest
from vs_api.traceroute import TracerouteHop, TracerouteOutputError, parse_traceroute


def test_hops_keep_their_numbers_addresses_and_round_trips() -> None:
    output = (
        "traceroute to 10.0.0.9 (10.0.0.9), 20 hops max, 46 byte packets\n"
        " 1  10.2.0.1  5.123 ms\n"
        " 2  *\n"
        " 3  10.0.0.9  2800.5 ms\n"
    )

    assert parse_traceroute(output) == (
        TracerouteHop(hop=1, address="10.2.0.1", rtt_ms=5.123),
        TracerouteHop(hop=2, address=None, rtt_ms=None),
        TracerouteHop(hop=3, address="10.0.0.9", rtt_ms=2800.5),
    )


def test_an_unreachable_code_after_the_round_trip_is_accepted() -> None:
    assert parse_traceroute(" 4  10.0.0.4  12.0 ms !H\n") == (
        TracerouteHop(hop=4, address="10.0.0.4", rtt_ms=12.0),
    )


def test_output_without_hop_lines_has_no_hops() -> None:
    assert parse_traceroute("traceroute to 10.0.0.9 (10.0.0.9), 20 hops max\n") == ()
    assert parse_traceroute("") == ()


def test_a_line_that_is_not_a_hop_is_refused() -> None:
    with pytest.raises(TracerouteOutputError, match="sendto: Network unreachable"):
        parse_traceroute("traceroute: sendto: Network unreachable\n")
