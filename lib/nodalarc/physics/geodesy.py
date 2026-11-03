# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Geodetic coordinates on a body's catalog ellipsoid, through ANISE.

Latitude is geodetic latitude on the catalog body's ellipsoid for every body,
as NodalArc has always defined it. The ellipsoid is the catalog's, never the
dataset's: an ANISE frame taken from a loaded dataset carries that dataset's
radii, so the frame here is built from the catalog radii explicitly. In a
comparison with SPICE a surface point built this way matched ``georec`` with
the same radii exactly, and moved 20 km when the radii changed by 20 km.

ANISE converts body-fixed coordinates to geodetic ones with Heikkinen's
closed-form method, in 0.35 ms per 1,000 points on a development machine.
"""

from __future__ import annotations

from anise.astro import Ellipsoid, Frame, Orbit
from anise.time import Epoch

from nodalarc.body_frames import BodyFrame
from nodalarc.frames import GeoPosition, Vec3
from nodalarc.physics.ephemeris_table import body_data

# The conversion is geometry on the ellipsoid and does not depend on time;
# ANISE's Orbit type requires an epoch, so a fixed one is supplied.
_ANY_EPOCH = Epoch.init_from_et_seconds(0.0)


def _surface_frame(body: BodyFrame) -> Frame:
    data = body_data(body.name)
    return Frame(
        data.naif_id,
        data.fixed_orientation_id,
        mu_km3_s2=body.gravitational_parameter_km3_s2,
        shape=Ellipsoid(body.equatorial_radius_km, body.polar_radius_km, body.equatorial_radius_km),
    )


def geodetic_from_body_fixed(position_km: Vec3, body: BodyFrame) -> GeoPosition:
    orbit = Orbit.from_cartesian(
        position_km[0],
        position_km[1],
        position_km[2],
        0.0,
        0.0,
        0.0,
        _ANY_EPOCH,
        _surface_frame(body),
    )
    lat_deg, lon_deg, alt_km = orbit.latlongalt()
    # ANISE returns longitude in [0, 360). NodalArc's published longitude has
    # always been atan2's range, (-180, 180], and the page and every wire
    # model read it that way, so the representation is kept.
    if lon_deg > 180.0:
        lon_deg -= 360.0
    return GeoPosition(lat_deg=lat_deg, lon_deg=lon_deg, alt_km=alt_km)


def body_fixed_from_geodetic(position: GeoPosition, body: BodyFrame) -> Vec3:
    orbit = Orbit.from_latlongalt(
        position.lat_deg, position.lon_deg, position.alt_km, _ANY_EPOCH, _surface_frame(body)
    )
    return Vec3(orbit.x_km, orbit.y_km, orbit.z_km)
