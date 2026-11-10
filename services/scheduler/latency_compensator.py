# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
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
    """Auditable latency compensation for one link, each direction.

    ``a_to_b`` is the direction node_a transmits (node_a's egress), ``b_to_a`` the direction
    node_b transmits. Both subtract the same measured substrate one-way share.
    """

    orbital_a_to_b_ms: float
    orbital_b_to_a_ms: float
    substrate_rtt_ms: float
    substrate_one_way_ms: float
    netem_a_to_b_ms: float
    netem_b_to_a_ms: float
    rtt_to_one_way_policy: str


def compensate_latency(
    *,
    orbital_a_to_b_ms: float,
    orbital_b_to_a_ms: float,
    substrate_rtt_ms: float,
    rtt_to_one_way_policy: str = "half-rtt",
) -> LatencyCompensation:
    """Compute each direction's netem delay for a link.

    There is intentionally no clamping. If the real substrate is already
    slower than the target orbital link, the requested emulation is not
    representable on this substrate and dispatch must stop for that link.
    """
    for name, orbital in (
        ("orbital_a_to_b_ms", orbital_a_to_b_ms),
        ("orbital_b_to_a_ms", orbital_b_to_a_ms),
    ):
        if orbital < 0:
            raise ValueError(f"{name} must be non-negative, got {orbital}")
    if substrate_rtt_ms < 0:
        raise ValueError(f"substrate_rtt_ms must be non-negative, got {substrate_rtt_ms}")
    if rtt_to_one_way_policy != "half-rtt":
        raise ValueError(f"Unsupported RTT conversion policy: {rtt_to_one_way_policy!r}")

    substrate_one_way_ms = substrate_rtt_ms / 2.0
    netem = {}
    for direction, orbital in (("a_to_b", orbital_a_to_b_ms), ("b_to_a", orbital_b_to_a_ms)):
        netem[direction] = orbital - substrate_one_way_ms
        if netem[direction] < 0:
            raise ValueError(
                "Unrepresentable latency: "
                f"substrate_one_way_ms={substrate_one_way_ms:.6f} exceeds "
                f"OME orbital {direction} one-way {orbital:.6f} ms"
            )

    return LatencyCompensation(
        orbital_a_to_b_ms=orbital_a_to_b_ms,
        orbital_b_to_a_ms=orbital_b_to_a_ms,
        substrate_rtt_ms=substrate_rtt_ms,
        substrate_one_way_ms=substrate_one_way_ms,
        netem_a_to_b_ms=netem["a_to_b"],
        netem_b_to_a_ms=netem["b_to_a"],
        rtt_to_one_way_policy=rtt_to_one_way_policy,
    )


def egress_ms(pair: tuple[str, str], node_id: str, values: tuple[float, float]) -> float:
    """The value of the direction ``node_id`` transmits: (a_to_b, b_to_a) for pair (a, b)."""
    if node_id == pair[0]:
        return values[0]
    if node_id == pair[1]:
        return values[1]
    raise ValueError(f"{node_id!r} is not an end of link {pair}")
