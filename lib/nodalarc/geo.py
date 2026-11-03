# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Range and one-way latency between two positions in one shared frame.

Geodetic conversion lives in the shared physics module (``nodalarc.physics``).
"""

from __future__ import annotations

import math

from nodalarc.constants import SPEED_OF_LIGHT_KM_S
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


def compute_latency_ms(range_km: float) -> float:
    """Compute one-way propagation delay from range in km.

    one_way_latency_ms = range_km / 299792.458 * 1000
    """
    return range_km / SPEED_OF_LIGHT_KM_S * 1000.0
