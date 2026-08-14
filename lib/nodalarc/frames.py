# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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


class CommonVec3(NamedTuple):
    """A vector in the session's common frame (Earth-relative GCRS).

    A distinct runtime class, not a NewType alias: a body-fixed or
    body-inertial Vec3 cannot pass an isinstance check for it, so the
    frame-relabeling that once fed ITRS into cross-body geometry is
    refused at construction, not just in static checking.
    """

    x: float
    y: float
    z: float


class GcrsVec3(NamedTuple):
    """A body-centered vector on GCRS axes, before common-frame composition.

    Common-frame composition adds the central body's common-frame origin to
    a body-centered vector; that input must already be on the common axes.
    A distinct runtime class for the same reason as CommonVec3: the ITRS
    output of a propagator cannot flow into composition by relabeling. SGP4
    constructs these from Skyfield's GCRS evaluation; the two-body and J2
    propagators construct them from their body-equatorial inertial output,
    which the session contract composes as the common axes.
    """

    x: float
    y: float
    z: float


class GeoPosition(NamedTuple):
    """WGS84 geodetic position."""

    lat_deg: float
    lon_deg: float
    alt_km: float
