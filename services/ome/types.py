# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Shared OME structured types that cross engine boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MbbTeardown:
    """Pending make-before-break teardown for a superseded ground link."""

    start_step: int
    successor_pair: tuple[str, str]


MbbTeardownState = dict[tuple[str, str], MbbTeardown]
# TODO(trust-gap-closure#10): Replace this positional tuple with a frozen
# dataclass (visible: bool, range_km: float, elevation_deg: float | None)
# so field access is by name, not position. This type crosses OME engine
# boundaries (ground_visibility_engine -> event_diff -> snapshot_builder).
GroundVisibilityDetails = dict[tuple[str, str], tuple[bool, float, float | None]]
