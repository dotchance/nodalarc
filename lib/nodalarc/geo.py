# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Range between two positions in one shared frame.

A link's delay is the light time of each direction from the shared physics module
(``nodalarc.physics.lighttime``), not this same-instant distance over c. Geodetic conversion also
lives in the physics module.
"""

from __future__ import annotations

import math

from nodalarc.frames import CommonVec3, Vec3


def compute_range_km(
    pos_a: Vec3 | CommonVec3,
    pos_b: Vec3 | CommonVec3,
) -> float:
    """Euclidean distance in km between two positions in one shared frame."""
    dx = pos_a[0] - pos_b[0]
    dy = pos_a[1] - pos_b[1]
    dz = pos_a[2] - pos_b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)
