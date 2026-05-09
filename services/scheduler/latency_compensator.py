# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Scheduler latency compensation engine.

OME owns orbital one-way latency. The Scheduler may only subtract explicit
substrate measurements to derive the one-way netem delay sent to Node Agent.
The supported substrate input today is ICMP-style RTT, converted by half-RTT.
Unsupported conversions or negative compensation fail loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RttToOneWayPolicy = Literal["half-rtt"]


@dataclass(frozen=True)
class LatencyCompensation:
    """Auditable result of converting OME latency plus substrate RTT to netem."""

    orbital_one_way_ms: float
    substrate_rtt_ms: float
    substrate_one_way_ms: float
    netem_one_way_ms: float
    rtt_to_one_way_policy: str


def compensate_latency(
    *,
    orbital_one_way_ms: float,
    substrate_rtt_ms: float,
    rtt_to_one_way_policy: str = "half-rtt",
) -> LatencyCompensation:
    """Compute the one-way netem delay to apply for a link.

    There is intentionally no clamping. If the real substrate is already
    slower than the target orbital link, the requested emulation is not
    representable on this substrate and dispatch must stop for that link.
    """
    if orbital_one_way_ms < 0:
        raise ValueError(f"orbital_one_way_ms must be non-negative, got {orbital_one_way_ms}")
    if substrate_rtt_ms < 0:
        raise ValueError(f"substrate_rtt_ms must be non-negative, got {substrate_rtt_ms}")
    if rtt_to_one_way_policy != "half-rtt":
        raise ValueError(f"Unsupported RTT conversion policy: {rtt_to_one_way_policy!r}")

    substrate_one_way_ms = substrate_rtt_ms / 2.0
    netem_one_way_ms = orbital_one_way_ms - substrate_one_way_ms
    if netem_one_way_ms < 0:
        raise ValueError(
            "Unrepresentable latency: "
            f"substrate_one_way_ms={substrate_one_way_ms:.6f} exceeds "
            f"OME orbital_one_way_ms={orbital_one_way_ms:.6f}"
        )

    return LatencyCompensation(
        orbital_one_way_ms=orbital_one_way_ms,
        substrate_rtt_ms=substrate_rtt_ms,
        substrate_one_way_ms=substrate_one_way_ms,
        netem_one_way_ms=netem_one_way_ms,
        rtt_to_one_way_policy=rtt_to_one_way_policy,
    )
