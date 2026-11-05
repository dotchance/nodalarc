# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Per-terminal directional link shaping commanded through tc."""

from __future__ import annotations

import math

import pytest
from nodalarc.substrate.queue_sizing import TrafficEnvelope, queue_limit_packets
from node_agent.tc_units import mbps_to_bytes_per_second


@pytest.mark.parametrize(
    ("rate_mbps", "bytes_per_second"),
    [
        (2.0, 250_000),
        (23.6, 2_950_000),
        (1000.0, 125_000_000),
        (2000.0, 250_000_000),
        (200_000.0, 25_000_000_000),
    ],
)
def test_terminal_rates_convert_to_tc_bytes_per_second(
    rate_mbps: float, bytes_per_second: int
) -> None:
    assert mbps_to_bytes_per_second(rate_mbps) == bytes_per_second


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan])
def test_unusable_rate_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        mbps_to_bytes_per_second(bad)


@pytest.mark.parametrize(
    ("transmit_mbps", "delay_ms", "packets"),
    [
        # In flight at 1500 bytes per packet, rounded up, plus the 1000-packet buffer.
        (600.0, 120.0, 6000 + 1000),
        (2000.0, 10.0, 1667 + 1000),
        (1000.0, 1501.0, 125084 + 1000),
        (2.0, 0.0, 0 + 1000),
    ],
)
def test_netem_limit_holds_the_packets_in_flight_plus_the_buffer(
    transmit_mbps: float, delay_ms: float, packets: int
) -> None:
    limit = queue_limit_packets(
        long_delay=False, transmit_mbps=transmit_mbps, delay_ms=delay_ms, envelope=None
    )
    assert limit == packets


@pytest.mark.parametrize(
    ("delay_ms", "packets"),
    [
        # 1 Mbit/s at 1,250-byte packets is 100 packets a second: 38,200 in flight over 382 s,
        # plus the 500-packet burst. The terminal's 15 Mbit/s rate plays no part.
        (382_000.0, 38_200 + 500),
        (1_200_000.0, 120_000 + 500),
    ],
)
def test_a_long_delay_queue_holds_the_envelope_for_the_delay_not_the_line_rate(
    delay_ms: float, packets: int
) -> None:
    envelope = TrafficEnvelope(sustained_mbps=1.0, packet_bytes=1250, burst_packets=500)
    limit = queue_limit_packets(
        long_delay=True, transmit_mbps=15.0, delay_ms=delay_ms, envelope=envelope
    )
    assert limit == packets


def test_a_long_delay_queue_without_an_envelope_is_refused() -> None:
    with pytest.raises(ValueError, match="envelope"):
        queue_limit_packets(long_delay=True, transmit_mbps=15.0, delay_ms=1.0, envelope=None)
