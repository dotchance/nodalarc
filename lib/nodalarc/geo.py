# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Geodetic/ECEF conversion and range/latency computation.

Pure math on WGS84 constants. Used by OME (snapshot latency) and
Scheduler (position table, inter-snapshot latency refinement).

One formula, one place.
"""

from __future__ import annotations

import math

from nodalarc.constants import SPEED_OF_LIGHT_KM_S, WGS84_A, WGS84_E2


def geodetic_to_ecef(
    lat_deg: float,
    lon_deg: float,
    alt_km: float,
) -> tuple[float, float, float]:
    """Convert geodetic (lat, lon, alt) to ECEF xyz in km."""
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + alt_km) * cos_lat * math.cos(lon_rad)
    y = (n + alt_km) * cos_lat * math.sin(lon_rad)
    z = (n * (1.0 - WGS84_E2) + alt_km) * sin_lat
    return (x, y, z)


def compute_range_km(
    pos_a: tuple[float, float, float],
    pos_b: tuple[float, float, float],
) -> float:
    """Euclidean distance between two ECEF positions in km."""
    dx = pos_a[0] - pos_b[0]
    dy = pos_a[1] - pos_b[1]
    dz = pos_a[2] - pos_b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def compute_latency_ms(range_km: float) -> float:
    """Compute one-way propagation delay from range in km.

    one_way_latency_ms = range_km / 299792.458 * 1000
    """
    return range_km / SPEED_OF_LIGHT_KM_S * 1000.0
