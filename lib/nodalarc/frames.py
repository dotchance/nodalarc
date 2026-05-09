# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Coordinate frame types shared by geometry and propagation code."""

from __future__ import annotations

from typing import NamedTuple, NewType


class Vec3(NamedTuple):
    """3D vector in kilometers or kilometers per second.

    Use the ECI/ECEF NewType wrappers in function signatures when the frame
    matters. The wrappers are zero-cost at runtime and prevent frame confusion
    in static checking.
    """

    x: float
    y: float
    z: float


EciVec3 = NewType("EciVec3", Vec3)
EcefVec3 = NewType("EcefVec3", Vec3)


class GeoPosition(NamedTuple):
    """WGS84 geodetic position."""

    lat_deg: float
    lon_deg: float
    alt_km: float
