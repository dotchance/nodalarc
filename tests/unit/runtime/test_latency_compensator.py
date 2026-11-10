# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for Scheduler latency compensation semantics."""

from __future__ import annotations

import pytest
from scheduler.latency_compensator import compensate_latency


def test_half_rtt_compensation_uses_one_way_substrate_delay():
    result = compensate_latency(
        orbital_a_to_b_ms=10.0, orbital_b_to_a_ms=10.0, substrate_rtt_ms=4.0
    )

    assert result.substrate_one_way_ms == 2.0
    assert result.netem_a_to_b_ms == 8.0
    assert result.netem_b_to_a_ms == 8.0
    assert result.rtt_to_one_way_policy == "half-rtt"


@pytest.mark.parametrize(("a_to_b", "b_to_a"), [(10.0, 7.5), (7.5, 10.0)])
def test_each_direction_keeps_its_own_delay_less_the_same_substrate_share(a_to_b, b_to_a):
    """Unequal directions, both ways round: each netem delay is its own direction's delay less
    the one substrate one-way share (half the 4 ms RTT)."""
    result = compensate_latency(
        orbital_a_to_b_ms=a_to_b, orbital_b_to_a_ms=b_to_a, substrate_rtt_ms=4.0
    )

    assert (result.orbital_a_to_b_ms, result.orbital_b_to_a_ms) == (a_to_b, b_to_a)
    assert result.substrate_one_way_ms == 2.0
    assert (result.netem_a_to_b_ms, result.netem_b_to_a_ms) == (a_to_b - 2.0, b_to_a - 2.0)


def test_negative_compensation_is_unrepresentable():
    with pytest.raises(ValueError, match="Unrepresentable latency"):
        compensate_latency(orbital_a_to_b_ms=1.0, orbital_b_to_a_ms=1.0, substrate_rtt_ms=4.0)


@pytest.mark.parametrize("direction", ["a_to_b", "b_to_a"])
def test_each_direction_is_refused_below_the_substrate_share_on_its_own(direction):
    """One direction at the substrate share is representable (netem 0) and just below it the
    link is refused, while the other direction stays well above it."""
    other = "b_to_a" if direction == "a_to_b" else "a_to_b"

    at_share = compensate_latency(
        **{f"orbital_{direction}_ms": 2.0, f"orbital_{other}_ms": 10.0}, substrate_rtt_ms=4.0
    )
    assert getattr(at_share, f"netem_{direction}_ms") == 0.0
    assert getattr(at_share, f"netem_{other}_ms") == 8.0

    with pytest.raises(ValueError, match=f"Unrepresentable latency.*{direction}"):
        compensate_latency(
            **{f"orbital_{direction}_ms": 1.999, f"orbital_{other}_ms": 10.0},
            substrate_rtt_ms=4.0,
        )


def test_unsupported_rtt_policy_fails_loudly():
    with pytest.raises(ValueError, match="Unsupported RTT conversion policy"):
        compensate_latency(
            orbital_a_to_b_ms=10.0,
            orbital_b_to_a_ms=10.0,
            substrate_rtt_ms=4.0,
            rtt_to_one_way_policy="zero",
        )


def test_negative_inputs_fail_loudly():
    with pytest.raises(ValueError, match="orbital_a_to_b_ms"):
        compensate_latency(orbital_a_to_b_ms=-1.0, orbital_b_to_a_ms=1.0, substrate_rtt_ms=0.0)

    with pytest.raises(ValueError, match="orbital_b_to_a_ms"):
        compensate_latency(orbital_a_to_b_ms=1.0, orbital_b_to_a_ms=-1.0, substrate_rtt_ms=0.0)

    with pytest.raises(ValueError, match="substrate_rtt_ms"):
        compensate_latency(orbital_a_to_b_ms=1.0, orbital_b_to_a_ms=1.0, substrate_rtt_ms=-1.0)
