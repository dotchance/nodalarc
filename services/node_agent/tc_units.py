# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Unit conversion helpers for Linux tc operations."""

from __future__ import annotations

import math

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


def netem_us_to_ticks(delay_us: int) -> int:
    """Convert netem microseconds to the scheduler ticks reported by pyroute2."""
    if delay_us < 0:
        raise ValueError(f"netem delay must be non-negative, got {delay_us}")
    return int(tc_common.time2tick(delay_us))
