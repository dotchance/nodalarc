# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The packet limit of every link delay queue, and the rate shaper's burst, for both link classes.

The experiment's terminal files state each terminal's rate and OME states each direction's delay.
A delay queue holds what the transmitting terminal sends during one delay: its configured transmit
rate times that delay, counted at a standard Ethernet MTU, plus a buffer of the kernel's default
netem limit. The rule is the same for a 2 ms link and a light-minute link, so changing a
terminal's rate in its catalog file changes its queues. Rejected alternative: one platform-wide
traffic envelope sizing every long-delay queue whatever its terminals declare, which made a
256 kbit/s and a 100 Mbit/s terminal hold the same queue.

One function sizes a queue wherever a queue is sized: the Node Agent's initial shaping, every
later delay update, and the Operator's per-server memory estimate
(``nodalarc.substrate.queue_budget``). Rejected alternative: sizing at each site. The first delay
update would then restore whatever the initial shaping did not use, and the estimate would count
queues the kernel does not hold.

Traffic above a queue's limit is dropped by netem and counted as substrate resource loss.
"""

from __future__ import annotations

import math

# A queue's in-flight room is counted at a standard Ethernet MTU.
IN_FLIGHT_PACKET_BYTES = 1500
# The kernel's default netem limit, kept as every queue's buffer above its in-flight room.
BUFFER_PACKETS = 1000
# The packet limit of a long-delay end's transmit leaf, the queue behind its rate shaper: the
# kernel's default interface queue length. The Node Agent creates the leaf with it and the memory
# estimate counts the leaf full at it, so the two cannot disagree.
LONG_DELAY_TRANSMIT_LEAF_PACKETS = 1000
# The rate shaper's burst covers this much time at its rate (one HTB tick of traffic).
_SHAPER_BURST_SECONDS_DIVISOR = 250


def mbps_to_bytes_per_second(rate_mbps: float) -> int:
    """Convert a terminal rate in megabits per second to tc's bytes per second.

    tc rate fields count bytes per second; a rate in bits per second would
    shape the link eight times faster than the terminal it emulates.
    """
    if not rate_mbps > 0:
        raise ValueError(f"shaping rate must be positive, got {rate_mbps}")
    return int(math.floor(rate_mbps * 1_000_000 / 8 + 0.5))


def shaper_burst_bytes(rate_mbps: float, mtu_bytes: int) -> int:
    """The burst a terminal's rate shaper lets through above its rate: 4 ms at the rate, and
    always at least one full frame of ``mtu_bytes``."""
    if mtu_bytes <= 0:
        raise ValueError(f"an MTU must be positive, got {mtu_bytes}")
    return max(mtu_bytes, mbps_to_bytes_per_second(rate_mbps) // _SHAPER_BURST_SECONDS_DIVISOR)


def bytes_in_flight(transmit_mbps: float, delay_ms: float) -> float:
    """What a terminal transmitting at ``transmit_mbps`` has in flight over ``delay_ms``."""
    if delay_ms < 0 or not math.isfinite(delay_ms):
        raise ValueError(f"a delay must be non-negative and finite, got {delay_ms}")
    if not (transmit_mbps > 0 and math.isfinite(transmit_mbps)):
        raise ValueError(f"a transmit rate must be positive and finite, got {transmit_mbps}")
    return transmit_mbps * 1_000_000 / 8 * delay_ms / 1000.0


def queue_limit_packets(*, transmit_mbps: float, delay_ms: float) -> int:
    """The netem packet limit of one direction's delay queue: what the transmitting terminal has in
    flight over the delay, at a standard MTU, plus the kernel's default netem limit."""
    in_flight = bytes_in_flight(transmit_mbps, delay_ms) / IN_FLIGHT_PACKET_BYTES
    return math.ceil(in_flight) + BUFFER_PACKETS
