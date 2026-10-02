# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for Scheduler latency compensation semantics."""

from __future__ import annotations

import pytest
from scheduler.latency_compensator import compensate_latency


def test_half_rtt_compensation_uses_one_way_substrate_delay():
    result = compensate_latency(orbital_one_way_ms=10.0, substrate_rtt_ms=4.0)

    assert result.substrate_one_way_ms == 2.0
    assert result.netem_one_way_ms == 8.0
    assert result.rtt_to_one_way_policy == "half-rtt"


def test_negative_compensation_is_unrepresentable():
    with pytest.raises(ValueError, match="Unrepresentable latency"):
        compensate_latency(orbital_one_way_ms=1.0, substrate_rtt_ms=4.0)


def test_unsupported_rtt_policy_fails_loudly():
    with pytest.raises(ValueError, match="Unsupported RTT conversion policy"):
        compensate_latency(
            orbital_one_way_ms=10.0,
            substrate_rtt_ms=4.0,
            rtt_to_one_way_policy="zero",
        )


def test_negative_inputs_fail_loudly():
    with pytest.raises(ValueError, match="orbital_one_way_ms"):
        compensate_latency(orbital_one_way_ms=-1.0, substrate_rtt_ms=0.0)

    with pytest.raises(ValueError, match="substrate_rtt_ms"):
        compensate_latency(orbital_one_way_ms=1.0, substrate_rtt_ms=-1.0)
