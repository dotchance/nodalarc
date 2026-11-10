# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Geodetic coordinates on a body's catalog ellipsoid, through ANISE.

Latitude is geodetic latitude on the catalog body's ellipsoid for every body,
as NodalArc has always defined it. The ellipsoid is the catalog's, never the
dataset's: an ANISE frame taken from a loaded dataset carries that dataset's
radii, so the frame here is built from the catalog radii explicitly. In a
comparison with SPICE a surface point built this way matched ``georec`` with
the same radii exactly, and moved 20 km when the radii changed by 20 km.

Body-fixed to geodetic is an explicit exception to the library rule. ANISE's
conversion (``Orbit.latlongalt`` in 0.10.6, Heikkinen's closed form) returned
NaN for latitude and height at every point within about 12 m of the polar
axis, including the exact pole that a 90 degree orbit crosses. A NaN position
would be published as if it were a fact. NodalArc therefore keeps its Bowring
iteration, vectorized here, which handles the axis; the tests compare it with
SPICE ``recgeo``. The reverse direction, geodetic to body-fixed, is ANISE's.
"""

from __future__ import annotations

import numpy as np
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


def geodetic_from_body_fixed_batch(
    positions_km: np.ndarray, body: BodyFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Geodetic latitude (deg), longitude (deg) and height (km) for (N, 3) positions.

    Longitude is atan2's range, (-180, 180], the range NodalArc has always
    published. On the polar axis atan2(0, 0) gives longitude 0, latitude is
    +/-90 and the height is measured from the pole.
    """
    x = positions_km[:, 0]
    y = positions_km[:, 1]
    z = positions_km[:, 2]
    a = body.equatorial_radius_km
    b = body.polar_radius_km
    e2 = 1.0 - (b * b) / (a * a)
    lon = np.arctan2(y, x)
    p = np.sqrt(x * x + y * y)
    lat = np.arctan2(z, p * (1.0 - e2))
    for _ in range(10):
        sin_lat = np.sin(lat)
        n = a / np.sqrt(1.0 - e2 * sin_lat * sin_lat)
        lat = np.arctan2(z + e2 * n * sin_lat, p)
    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    n = a / np.sqrt(1.0 - e2 * sin_lat * sin_lat)
    near_axis = np.abs(cos_lat) <= 1e-10
    safe_cos = np.where(near_axis, 1.0, cos_lat)
    alt = np.where(near_axis, np.abs(z) - n * (1.0 - e2), p / safe_cos - n)
    return np.degrees(lat), np.degrees(lon), alt


def geodetic_from_body_fixed(position_km: Vec3, body: BodyFrame) -> GeoPosition:
    lat, lon, alt = geodetic_from_body_fixed_batch(
        np.array([[position_km[0], position_km[1], position_km[2]]], dtype=np.float64), body
    )
    return GeoPosition(lat_deg=float(lat[0]), lon_deg=float(lon[0]), alt_km=float(alt[0]))


def body_fixed_from_geodetic(position: GeoPosition, body: BodyFrame) -> Vec3:
    orbit = Orbit.from_latlongalt(
        position.lat_deg, position.lon_deg, position.alt_km, _ANY_EPOCH, _surface_frame(body)
    )
    return Vec3(orbit.x_km, orbit.y_km, orbit.z_km)
