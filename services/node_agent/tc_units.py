# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit conversion helpers for Linux tc operations."""

from __future__ import annotations

import math
from typing import NamedTuple

from pyroute2.netlink.rtnl.tcmsg import common as tc_common


def delay_ms_to_netem_us(delay_ms: float) -> int:
    """Convert milliseconds to the integer microseconds accepted by netem.

    tc/netem cannot represent fractional microseconds. Use nearest integer
    microsecond so the programmed value is the closest kernel-representable
    value to the scheduler's floating-point latency.
    """
    if delay_ms < 0:
        raise ValueError(f"netem delay must be non-negative, got {delay_ms}")
    return int(math.floor(delay_ms * 1000.0 + 0.5))


def mbps_to_bytes_per_second(rate_mbps: float) -> int:
    """Convert a terminal rate in megabits per second to tc's bytes per second.

    tc rate fields count bytes per second; a rate in bits per second would
    shape the link eight times faster than the terminal it emulates.
    """
    if not rate_mbps > 0:
        raise ValueError(f"shaping rate must be positive, got {rate_mbps}")
    return int(math.floor(rate_mbps * 1_000_000 / 8 + 0.5))


# The shaper has one class, so its quantum only has to cover one jumbo frame.
SHAPER_QUANTUM_BYTES = 16384


class HtbClass(NamedTuple):
    """HTB class 1:1 as it is commanded: rates in bytes per second, bursts in bytes."""

    rate: int
    ceil: int
    burst: int
    cburst: int
    quantum: int


def htb_class(rate_mbps: float, mtu_bytes: int) -> HtbClass:
    """The shaper class for a link at ``rate_mbps``: ceiling at the rate, and a
    burst of 4 ms at line rate that always holds one full frame of ``mtu_bytes``."""
    rate = mbps_to_bytes_per_second(rate_mbps)
    burst = max(mtu_bytes, rate // 250)
    return HtbClass(rate=rate, ceil=rate, burst=burst, cburst=burst, quantum=SHAPER_QUANTUM_BYTES)


def htb_burst_ticks(rate: int, burst: int) -> int:
    """A burst as HTB carries it: the time to send it at ``rate``, in scheduler
    ticks. pyroute2 sends this value and the kernel reports it back unchanged."""
    return tc_common.calc_xmittime(rate, burst)


# Netem counts packets, and it holds every packet for the link delay. Its limit
# is the packets a link carries in flight at the terminal's transmit rate,
# counted at a standard Ethernet MTU, plus a buffer for packets waiting to be
# sent. The buffer is the kernel's default netem limit, which every link ran
# with before the in-flight room was added.
NETEM_IN_FLIGHT_PACKET_BYTES = 1500
NETEM_BUFFER_PACKETS = 1000


def netem_limit_packets(transmit_mbps: float, delay_ms: float) -> int:
    """The netem queue limit for a link end transmitting at ``transmit_mbps``."""
    if delay_ms < 0:
        raise ValueError(f"netem delay must be non-negative, got {delay_ms}")
    in_flight_bytes = mbps_to_bytes_per_second(transmit_mbps) * delay_ms / 1000.0
    return math.ceil(in_flight_bytes / NETEM_IN_FLIGHT_PACKET_BYTES) + NETEM_BUFFER_PACKETS


def netem_us_to_ticks(delay_us: int) -> int:
    """Convert netem microseconds to the scheduler ticks reported by pyroute2."""
    if delay_us < 0:
        raise ValueError(f"netem delay must be non-negative, got {delay_us}")
    return int(tc_common.time2tick(delay_us))
