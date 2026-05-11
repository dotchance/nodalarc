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
GroundVisibilityDetails = dict[tuple[str, str], tuple[bool, float, float | None]]
