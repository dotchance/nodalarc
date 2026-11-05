# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The packet limit of every link delay queue, for both link classes.

One function sizes a queue wherever a queue is sized: the Node Agent's initial shaping, every later
delay update, and the Operator's per-server memory budget. Rejected alternative: sizing at each
site. The first delay update would then restore whatever the initial shaping did not use, and the
budget would count queues the kernel does not hold.

A local link's queue holds what its terminal transmits in flight at line rate, counted at a
standard Ethernet MTU, plus a buffer of the kernel's default netem limit, which every link ran with
before the in-flight room was added. A long-delay link never runs at line rate: its terminal rate
over a light-minute delay would ask for gigabytes of kernel memory per queue. Its queue holds the
traffic envelope the platform declares (a sustained load per direction at a packet size, plus a
burst) for the link's delay. Traffic above the envelope fills the queue, and netem drops it at the
limit; those drops are substrate resource loss.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# A local queue's in-flight room is counted at a standard Ethernet MTU.
LOCAL_IN_FLIGHT_PACKET_BYTES = 1500
# The kernel's default netem limit, kept as every local queue's buffer.
LOCAL_BUFFER_PACKETS = 1000


@dataclass(frozen=True, slots=True)
class TrafficEnvelope:
    """The traffic NodalArc carries on one direction of a long-delay link: a sustained load at a
    packet size, plus a burst of packets above it."""

    sustained_mbps: float
    packet_bytes: int
    burst_packets: int

    def __post_init__(self) -> None:
        if not (self.sustained_mbps > 0 and math.isfinite(self.sustained_mbps)):
            raise ValueError(f"sustained_mbps must be positive, got {self.sustained_mbps}")
        if self.packet_bytes <= 0:
            raise ValueError(f"packet_bytes must be positive, got {self.packet_bytes}")
        if self.burst_packets < 0:
            raise ValueError(f"burst_packets must be non-negative, got {self.burst_packets}")


def _bytes_in_flight(rate_mbps: float, delay_ms: float) -> float:
    return rate_mbps * 1_000_000 / 8 * delay_ms / 1000.0


def queue_limit_packets(
    *,
    long_delay: bool,
    transmit_mbps: float,
    delay_ms: float,
    envelope: TrafficEnvelope | None,
) -> int:
    """The netem packet limit of one direction's delay queue. A long-delay queue needs the
    platform's envelope; a local queue does not read it."""
    if delay_ms < 0 or not math.isfinite(delay_ms):
        raise ValueError(f"a delay must be non-negative and finite, got {delay_ms}")
    if long_delay:
        if envelope is None:
            raise ValueError("a long-delay queue is sized from the traffic envelope; none given")
        sustained = _bytes_in_flight(envelope.sustained_mbps, delay_ms) / envelope.packet_bytes
        return math.ceil(sustained) + envelope.burst_packets
    if not (transmit_mbps > 0 and math.isfinite(transmit_mbps)):
        raise ValueError(f"a transmit rate must be positive and finite, got {transmit_mbps}")
    in_flight = _bytes_in_flight(transmit_mbps, delay_ms) / LOCAL_IN_FLIGHT_PACKET_BYTES
    return math.ceil(in_flight) + LOCAL_BUFFER_PACKETS
