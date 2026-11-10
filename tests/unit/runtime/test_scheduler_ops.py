# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Scheduler operational model contracts."""

from nodalarc.models.scheduler_ops import ActuationState, parse_actuation_state


def test_parse_actuation_state_never_defaults_garbage_to_clean() -> None:
    assert parse_actuation_state("garbage") is ActuationState.UNKNOWN
    assert parse_actuation_state(None) is ActuationState.UNKNOWN
    assert parse_actuation_state(123) is ActuationState.UNKNOWN
