# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Unit conversion helpers for Linux tc operations."""

from __future__ import annotations

import math
from typing import NamedTuple

from nodalarc.substrate.queue_sizing import mbps_to_bytes_per_second, shaper_burst_bytes
from pyroute2.netlink.rtnl.tcmsg import common as tc_common


def delay_ms_to_netem_ns(delay_ms: float) -> int:
    """Convert milliseconds to the integer nanoseconds netem keeps.

    Netem holds a qdisc's delay as signed 64-bit nanoseconds and reports it
    back unchanged in TCA_NETEM_LATENCY64. The nearest nanosecond is the
    kernel-representable value closest to the scheduler's floating-point
    latency.
    """
    if not delay_ms >= 0:
        raise ValueError(f"netem delay must be non-negative, got {delay_ms}")
    return int(math.floor(delay_ms * 1_000_000.0 + 0.5))


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
    """The shaper class for a link at ``rate_mbps``: ceiling at the rate, and the shared burst
    (``queue_sizing.shaper_burst_bytes``), which the memory estimate counts too."""
    rate = mbps_to_bytes_per_second(rate_mbps)
    burst = shaper_burst_bytes(rate_mbps, mtu_bytes)
    return HtbClass(rate=rate, ceil=rate, burst=burst, cburst=burst, quantum=SHAPER_QUANTUM_BYTES)


def htb_burst_ticks(rate: int, burst: int) -> int:
    """A burst as HTB carries it: the time to send it at ``rate``, in scheduler
    ticks. pyroute2 sends this value and the kernel reports it back unchanged."""
    return tc_common.calc_xmittime(rate, burst)
