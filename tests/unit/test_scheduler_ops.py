# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler operational model contracts."""

from nodalarc.models.scheduler_ops import ActuationState, parse_actuation_state


def test_parse_actuation_state_preserves_known_values() -> None:
    assert parse_actuation_state("clean") is ActuationState.CLEAN
    assert parse_actuation_state(ActuationState.KERNEL_DIRTY) is ActuationState.KERNEL_DIRTY


def test_parse_actuation_state_never_defaults_garbage_to_clean() -> None:
    assert parse_actuation_state("garbage") is ActuationState.UNKNOWN
    assert parse_actuation_state(None) is ActuationState.UNKNOWN
    assert parse_actuation_state(123) is ActuationState.UNKNOWN
