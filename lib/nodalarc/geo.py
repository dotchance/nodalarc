# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Geodetic/ECEF conversion and range/latency computation.

Pure math on WGS84 constants. OME is the geometry authority; the
Scheduler may use these functions only for explicit validation/cross-check
paths, not to invent missing actuation values.

One formula, one place.
"""

from __future__ import annotations

import math

from nodalarc.body_frames import EARTH_BODY_FRAME, BodyFrame
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.frames import EcefVec3, GeoPosition, Vec3


def geodetic_to_body_fixed(
    pos: GeoPosition,
    body_frame: BodyFrame = EARTH_BODY_FRAME,
) -> EcefVec3:
    """Convert geodetic latitude/longitude/altitude to a body-fixed XYZ vector."""
    lat_rad = math.radians(pos.lat_deg)
    lon_rad = math.radians(pos.lon_deg)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    a = body_frame.equatorial_radius_km
    b = body_frame.polar_radius_km
    e2 = 1.0 - (b * b) / (a * a)
    n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    x = (n + pos.alt_km) * cos_lat * math.cos(lon_rad)
    y = (n + pos.alt_km) * cos_lat * math.sin(lon_rad)
    z = (n * (1.0 - e2) + pos.alt_km) * sin_lat
    return EcefVec3(Vec3(x, y, z))


def geodetic_to_ecef(
    pos: GeoPosition,
    body_frame: BodyFrame = EARTH_BODY_FRAME,
) -> EcefVec3:
    """Convert geodetic (lat, lon, alt) to body-fixed xyz in km.

    With the default body this is Earth ECEF. Passing a different body frame
    returns that body's local fixed frame; callers must name the body explicitly
    at the call site when non-Earth geometry is intended.
    """
    return geodetic_to_body_fixed(pos, body_frame)


def compute_range_km(
    pos_a: Vec3,
    pos_b: Vec3,
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
