# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Per-terminal directional link shaping commanded through tc."""

from __future__ import annotations

import math

import pytest
from nodalarc.substrate.queue_sizing import (
    mbps_to_bytes_per_second,
    queue_limit_packets,
    shaper_burst_bytes,
)


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
    limit = queue_limit_packets(transmit_mbps=transmit_mbps, delay_ms=delay_ms)
    assert limit == packets


@pytest.mark.parametrize(
    ("transmit_mbps", "delay_ms", "packets"),
    [
        # A long-delay queue follows its terminal's configured rate by the same rule: 256 kbit/s
        # over 338 s is 10.8 MB in flight (7,211 frames of 1,500 bytes), 100 Mbit/s over the same
        # delay is 4.2 GB (2,816,667 frames), 2.048 Mbit/s over 1,335.2 s is 341.8 MB.
        (0.256, 338_000.0, 7_211 + 1000),
        (100.0, 338_000.0, 2_816_667 + 1000),
        (2.048, 1_335_200.0, 227_875 + 1000),
    ],
)
def test_a_long_delay_queue_follows_the_terminal_rate_by_the_same_rule(
    transmit_mbps: float, delay_ms: float, packets: int
) -> None:
    assert queue_limit_packets(transmit_mbps=transmit_mbps, delay_ms=delay_ms) == packets


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan])
def test_a_queue_without_a_usable_rate_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="transmit rate must be positive"):
        queue_limit_packets(transmit_mbps=bad, delay_ms=1.0)


@pytest.mark.parametrize(
    ("rate_mbps", "burst"),
    [
        # 4 ms at the rate, never less than one 8,800-byte frame.
        (0.256, 8_800),
        (15.0, 8_800),
        (1200.0, 600_000),
    ],
)
def test_the_shaper_burst_is_four_milliseconds_and_at_least_one_frame(
    rate_mbps: float, burst: int
) -> None:
    assert shaper_burst_bytes(rate_mbps, 8_800) == burst
