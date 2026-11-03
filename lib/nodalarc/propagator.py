# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""SGP4/TLE propagation through Skyfield.

Analytic orbits (two-body and J2 mean elements) and body orientation live in
the shared physics module, ``nodalarc.physics``. TLE propagation stays on
Skyfield's TEME conversion until ANISE's TEME frame is qualified against a
published reference: in a comparison the two TEME conversions differed by
48 milliarcseconds, about 1.6 m at LEO, and which one is correct was not
established.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import NamedTuple

from nodalarc.body_frames import BodyFrame
from nodalarc.frames import EcefVec3, GcrsVec3, GeoPosition, Vec3
from nodalarc.physics.geodesy import geodetic_from_body_fixed


@lru_cache(maxsize=1)
def _skyfield_timescale():
    from skyfield.api import load

    return load.timescale()


@lru_cache(maxsize=4096)
def _skyfield_satellite(tle_line_1: str, tle_line_2: str):
    """Return a cached Skyfield EarthSatellite for a TLE pair."""
    from skyfield.api import EarthSatellite

    from nodalarc.tle import validate_tle_pair

    validate_tle_pair(tle_line_1, tle_line_2)
    timescale = _skyfield_timescale()
    return EarthSatellite(tle_line_1, tle_line_2, None, timescale), timescale


class Sgp4States(NamedTuple):
    """Both frames of one SGP4 evaluation at one instant.

    ``position_itrs``/``velocity_itrs`` are Earth-fixed, for same-body
    geometry. ``position_gcrs``/``velocity_gcrs`` are Skyfield's geocentric
    GCRS state, the only vectors that may enter the Earth-relative common
    frame. Raw SGP4 TEME sits about 30 km and 0.04 km/s from either.
    """

    position_itrs: EcefVec3
    velocity_itrs: EcefVec3
    geodetic: GeoPosition
    position_gcrs: GcrsVec3
    velocity_gcrs: GcrsVec3


def propagate_sgp4_tle_states(
    tle_line_1: str,
    tle_line_2: str,
    epoch_unix: float,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> Sgp4States:
    """Propagate a TLE once and return typed ITRS and GCRS states."""
    if body_frame.name != "earth":
        raise ValueError("SGP4/TLE propagation requires an explicit Earth body frame")
    from skyfield.framelib import itrs

    unix_timestamp = epoch_unix + dt
    sat, ts = _skyfield_satellite(tle_line_1, tle_line_2)
    t = ts.from_datetime(datetime.fromtimestamp(unix_timestamp, UTC))
    geocentric = sat.at(t)
    position, velocity = geocentric.frame_xyz_and_velocity(itrs)
    pos_ecef = EcefVec3(Vec3(*position.km))
    vel_ecef = EcefVec3(Vec3(*velocity.km_per_s))
    return Sgp4States(
        position_itrs=pos_ecef,
        velocity_itrs=vel_ecef,
        geodetic=geodetic_from_body_fixed(pos_ecef, body_frame),
        position_gcrs=GcrsVec3(*geocentric.position.km),
        velocity_gcrs=GcrsVec3(*geocentric.velocity.km_per_s),
    )


def propagate_sgp4_tle(
    tle_line_1: str,
    tle_line_2: str,
    epoch_unix: float,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> tuple[EcefVec3, EcefVec3, GeoPosition]:
    """Propagate a TLE with SGP4 and return typed ECEF state.

    SGP4's native output is TEME, not ECEF. Treating TEME as ECEF would make
    range, visibility, and latency wrong while looking numerically plausible.
    Skyfield owns the TEME-to-ITRS frame conversion here; the rest of NodalArc
    receives the same typed ECEF/GeoPosition contract as the Keplerian engines.
    """
    states = propagate_sgp4_tle_states(
        tle_line_1,
        tle_line_2,
        epoch_unix,
        dt,
        body_frame=body_frame,
    )
    return states.position_itrs, states.velocity_itrs, states.geodetic
