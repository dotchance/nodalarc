# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Orbital propagators — shared library.

Pure math, no I/O. Supports two-body Keplerian elements and a first-order
secular J2 mean-element model for elliptical or circular orbits.

Coordinate frames:
- ECI (Earth-Centered Inertial): fixed stars frame
- ECEF (Earth-Centered Earth-Fixed): rotates with Earth
- Geodetic: lat/lon/alt on WGS84 ellipsoid

This module lives in lib/nodalarc so it can be imported by any component
(OME, Scheduler, VS-API) without cross-service dependencies. The OME's
services/ome/propagator.py re-exports everything from here.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from functools import lru_cache

from nodalarc.body_frames import BodyFrame
from nodalarc.frames import EcefVec3, EciVec3, GeoPosition, Vec3
from nodalarc.geo import geodetic_to_ecef
from nodalarc.orbital import (
    OrbitalElements,
    elements_from_params,
    elements_from_params_for_radius,
    mean_anomaly_to_eccentric_anomaly,
)

# Re-export so consumers can get everything from one place
__all__ = [
    "Vec3",
    "EciVec3",
    "EcefVec3",
    "GeoPosition",
    "OrbitalElements",
    "elements_from_params",
    "elements_from_params_for_radius",
    "J2000_UNIX",
    "orbital_period",
    "orbital_period_for_body",
    "orbital_velocity",
    "j2_circular_secular_rates",
    "j2_mean_element_secular_rates",
    "propagate_eci",
    "propagate_eci_for_body",
    "propagate_eci_j2_mean_elements",
    "gmst",
    "eci_to_ecef",
    "eci_to_body_fixed",
    "ecef_to_eci",
    "ecef_to_geodetic",
    "body_fixed_to_geodetic",
    "geodetic_to_ecef",
    "eci_to_ecef_velocity",
    "propagate_keplerian",
    "propagate_keplerian_for_body",
    "propagate_j2_mean_elements",
    "propagate_j2_mean_elements_for_body",
    "propagate_sgp4_tle",
]

# J2000 epoch: 2000-01-01T12:00:00 UTC as Unix timestamp
J2000_UNIX = 946728000.0


def orbital_period(altitude_km: float, *, body_frame: BodyFrame) -> float:
    """Compute orbital period in seconds for a circular orbit.

    T = 2π √(a³/μ)
    """
    a = body_frame.mean_radius_km + altitude_km
    return math.tau * math.sqrt(a**3 / body_frame.gravitational_parameter_km3_s2)


def orbital_period_for_body(elements: OrbitalElements, body_frame: BodyFrame) -> float:
    """Compute orbital period in seconds for a body-specific elliptical orbit."""
    return math.tau * math.sqrt(
        elements.semi_major_axis_km**3 / body_frame.gravitational_parameter_km3_s2
    )


def orbital_velocity(altitude_km: float, *, body_frame: BodyFrame) -> float:
    """Compute orbital velocity in km/s for a circular orbit.

    v = √(μ/a)
    """
    a = body_frame.mean_radius_km + altitude_km
    return math.sqrt(body_frame.gravitational_parameter_km3_s2 / a)


def j2_circular_secular_rates(
    elements: OrbitalElements,
    *,
    body_frame: BodyFrame,
) -> tuple[float, float]:
    """Return (RAAN rate, mean-anomaly rate) for the circular J2 model.

    Rates are radians per second. The second value includes the Keplerian
    mean motion plus the first-order secular J2 correction.
    """
    a = elements.semi_major_axis_km
    i = elements.inclination_rad
    # Powers are spelled as explicit multiplication, never ** — libm's
    # pow() and numpy's integer-power fast paths disagree at the ulp
    # level on a few percent of inputs, and this scalar path must
    # produce bit-identical results to the vectorized kernel.
    n = math.sqrt(body_frame.gravitational_parameter_km3_s2 / (a * a * a))
    cos_i = math.cos(i)
    radius_ratio = body_frame.equatorial_radius_km / a
    j2_factor = body_frame.j2 * (radius_ratio * radius_ratio)
    raan_dot = -1.5 * j2_factor * n * cos_i
    mean_anomaly_dot = n * (1.0 + 0.75 * j2_factor * (3.0 * (cos_i * cos_i) - 1.0))
    return raan_dot, mean_anomaly_dot


def j2_mean_element_secular_rates(
    elements: OrbitalElements,
    *,
    body_frame: BodyFrame,
) -> tuple[float, float, float]:
    """Return (RAAN, argument-of-perigee, mean-anomaly) secular rates.

    Rates are radians per second for the explicit first-order J2 mean-element
    model. For circular orbits, argument of perigee is undefined; this returns
    ``0`` for that rate so circular inputs preserve the existing NodalArc
    circular J2 contract.
    """
    a = elements.semi_major_axis_km
    e = elements.eccentricity
    i = elements.inclination_rad
    p = a * (1.0 - e * e)
    if p <= 0.0:
        raise ValueError("semi-latus rectum must be positive")
    # Same multiply-only spelling rule as j2_circular_secular_rates:
    # bit-parity with the vectorized kernel forbids **.
    n = math.sqrt(body_frame.gravitational_parameter_km3_s2 / (a * a * a))
    cos_i = math.cos(i)
    radius_ratio = body_frame.equatorial_radius_km / p
    j2_factor = body_frame.j2 * (radius_ratio * radius_ratio)
    raan_dot = -1.5 * j2_factor * n * cos_i
    argp_dot = 0.0 if e == 0.0 else 0.75 * j2_factor * n * (5.0 * (cos_i * cos_i) - 1.0)
    mean_anomaly_dot = n * (
        1.0 + 0.75 * j2_factor * math.sqrt(1.0 - e * e) * (3.0 * (cos_i * cos_i) - 1.0)
    )
    return raan_dot, argp_dot, mean_anomaly_dot


def _perifocal_state(
    elements: OrbitalElements,
    mean_anomaly_rad: float,
    mean_anomaly_dot: float,
) -> tuple[float, float, float, float]:
    """Return perifocal x/y position and velocity from mean anomaly."""
    a = elements.semi_major_axis_km
    e = elements.eccentricity
    eccentric_anomaly = mean_anomaly_to_eccentric_anomaly(mean_anomaly_rad, e)
    cos_e = math.cos(eccentric_anomaly)
    sin_e = math.sin(eccentric_anomaly)
    sqrt_one_minus_e2 = math.sqrt(1.0 - e * e)
    denom = 1.0 - e * cos_e
    if denom <= 0.0:
        raise ValueError("invalid elliptical state: radius denominator is non-positive")
    x_pf = a * (cos_e - e)
    y_pf = a * sqrt_one_minus_e2 * sin_e
    eccentric_anomaly_dot = mean_anomaly_dot / denom
    vx_pf = -a * sin_e * eccentric_anomaly_dot
    vy_pf = a * sqrt_one_minus_e2 * cos_e * eccentric_anomaly_dot
    return x_pf, y_pf, vx_pf, vy_pf


def _perifocal_rotation(
    raan_rad: float,
    inclination_rad: float,
    argument_of_perigee_rad: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    cos_raan = math.cos(raan_rad)
    sin_raan = math.sin(raan_rad)
    cos_i = math.cos(inclination_rad)
    sin_i = math.sin(inclination_rad)
    cos_argp = math.cos(argument_of_perigee_rad)
    sin_argp = math.sin(argument_of_perigee_rad)
    return (
        (
            cos_raan * cos_argp - sin_raan * sin_argp * cos_i,
            -cos_raan * sin_argp - sin_raan * cos_argp * cos_i,
        ),
        (
            sin_raan * cos_argp + cos_raan * sin_argp * cos_i,
            -sin_raan * sin_argp + cos_raan * cos_argp * cos_i,
        ),
        (sin_argp * sin_i, cos_argp * sin_i),
    )


def _perifocal_rotation_derivatives(
    raan_rad: float,
    inclination_rad: float,
    argument_of_perigee_rad: float,
) -> tuple[
    tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
]:
    cos_raan = math.cos(raan_rad)
    sin_raan = math.sin(raan_rad)
    cos_i = math.cos(inclination_rad)
    sin_i = math.sin(inclination_rad)
    cos_argp = math.cos(argument_of_perigee_rad)
    sin_argp = math.sin(argument_of_perigee_rad)
    d_raan = (
        (
            -sin_raan * cos_argp - cos_raan * sin_argp * cos_i,
            sin_raan * sin_argp - cos_raan * cos_argp * cos_i,
        ),
        (
            cos_raan * cos_argp - sin_raan * sin_argp * cos_i,
            -cos_raan * sin_argp - sin_raan * cos_argp * cos_i,
        ),
        (0.0, 0.0),
    )
    d_argp = (
        (
            -cos_raan * sin_argp - sin_raan * cos_argp * cos_i,
            -cos_raan * cos_argp + sin_raan * sin_argp * cos_i,
        ),
        (
            -sin_raan * sin_argp + cos_raan * cos_argp * cos_i,
            -sin_raan * cos_argp - cos_raan * sin_argp * cos_i,
        ),
        (cos_argp * sin_i, -sin_argp * sin_i),
    )
    return d_raan, d_argp


def _apply_perifocal_rotation(
    matrix: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    x_pf: float,
    y_pf: float,
) -> Vec3:
    return Vec3(
        matrix[0][0] * x_pf + matrix[0][1] * y_pf,
        matrix[1][0] * x_pf + matrix[1][1] * y_pf,
        matrix[2][0] * x_pf + matrix[2][1] * y_pf,
    )


def propagate_eci_for_body(
    elements: OrbitalElements,
    dt: float,
    *,
    mu_km3_s2: float,
) -> tuple[EciVec3, EciVec3]:
    """Propagate orbit by dt seconds in the body-centered inertial frame.

    Returns (position_km_ECI, velocity_km_s_ECI) in Earth-Centered Inertial
    coordinates. Both vectors are in the inertial frame — they do NOT include
    Earth's rotation.

    For circular orbits (e=0): M = E = ν, so this exactly preserves the
    previous circular contract. For eccentric orbits, Kepler's equation is
    solved at each call and velocity follows the same two-body state.
    """
    a = elements.semi_major_axis_km
    n = math.sqrt(mu_km3_s2 / a**3)
    mean_anomaly = elements.mean_anomaly_rad + n * dt
    x_pf, y_pf, vx_pf, vy_pf = _perifocal_state(elements, mean_anomaly, n)
    rotation = _perifocal_rotation(
        elements.raan_rad,
        elements.inclination_rad,
        elements.argument_of_perigee_rad,
    )
    pos = _apply_perifocal_rotation(rotation, x_pf, y_pf)
    vel = _apply_perifocal_rotation(rotation, vx_pf, vy_pf)

    return EciVec3(pos), EciVec3(vel)


def propagate_eci(
    elements: OrbitalElements,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> tuple[EciVec3, EciVec3]:
    """Propagate orbit by dt seconds in the supplied body-centered inertial frame."""
    return propagate_eci_for_body(
        elements,
        dt,
        mu_km3_s2=body_frame.gravitational_parameter_km3_s2,
    )


def propagate_eci_j2_mean_elements_for_body(
    elements: OrbitalElements,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> tuple[EciVec3, EciVec3]:
    """Propagate a mean-element orbit with first-order secular J2.

    This model applies secular RAAN, argument-of-perigee, and mean-anomaly
    rates to the mean elements, then solves the resulting Keplerian state. It
    does not include short-period terms and must not be represented as an
    SGP4/TLE-quality ephemeris.
    """
    raan_dot, argument_of_perigee_dot, mean_anomaly_dot = j2_mean_element_secular_rates(
        elements,
        body_frame=body_frame,
    )

    raan = elements.raan_rad + raan_dot * dt
    argument_of_perigee = elements.argument_of_perigee_rad + argument_of_perigee_dot * dt
    mean_anomaly = elements.mean_anomaly_rad + mean_anomaly_dot * dt
    x_pf, y_pf, vx_pf, vy_pf = _perifocal_state(elements, mean_anomaly, mean_anomaly_dot)
    rotation = _perifocal_rotation(
        raan,
        elements.inclination_rad,
        argument_of_perigee,
    )
    d_raan, d_argp = _perifocal_rotation_derivatives(
        raan,
        elements.inclination_rad,
        argument_of_perigee,
    )
    pos = _apply_perifocal_rotation(rotation, x_pf, y_pf)
    vel_base = _apply_perifocal_rotation(rotation, vx_pf, vy_pf)
    vel_raan = _apply_perifocal_rotation(d_raan, x_pf, y_pf)
    vel_argp = _apply_perifocal_rotation(d_argp, x_pf, y_pf)
    vel = Vec3(
        vel_base.x + raan_dot * vel_raan.x + argument_of_perigee_dot * vel_argp.x,
        vel_base.y + raan_dot * vel_raan.y + argument_of_perigee_dot * vel_argp.y,
        vel_base.z + raan_dot * vel_raan.z + argument_of_perigee_dot * vel_argp.z,
    )

    return EciVec3(pos), EciVec3(vel)


def propagate_eci_j2_mean_elements(
    elements: OrbitalElements,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> tuple[EciVec3, EciVec3]:
    """Propagate mean elements with first-order secular J2 for the supplied body."""
    return propagate_eci_j2_mean_elements_for_body(elements, dt, body_frame=body_frame)


def gmst(unix_timestamp: float) -> float:
    """Compute Greenwich Mean Sidereal Time in radians.

    Uses a simplified model based on centuries from J2000.
    """
    # Julian centuries since J2000
    jd = 2440587.5 + unix_timestamp / 86400.0
    t = (jd - 2451545.0) / 36525.0

    # GMST in degrees (IAU 1982 model, simplified)
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * (t * t)
        - (t * t * t) / 38710000.0
    )
    return math.radians(gmst_deg % 360.0)


def _body_rotation_angle(body_frame: BodyFrame, unix_timestamp: float) -> float:
    if body_frame.name == "earth":
        return gmst(unix_timestamp)
    return (unix_timestamp - J2000_UNIX) * body_frame.rotation_rate_rad_s


def eci_to_body_fixed(
    pos_eci: EciVec3,
    unix_timestamp: float,
    body_frame: BodyFrame,
) -> EcefVec3:
    """Convert body-centered inertial position to the body's rotating local frame."""
    theta = _body_rotation_angle(body_frame, unix_timestamp)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return EcefVec3(
        Vec3(
            cos_t * pos_eci.x + sin_t * pos_eci.y,
            -sin_t * pos_eci.x + cos_t * pos_eci.y,
            pos_eci.z,
        )
    )


def eci_to_ecef(
    pos_eci: EciVec3,
    unix_timestamp: float,
    *,
    body_frame: BodyFrame,
) -> EcefVec3:
    """Convert inertial position to the supplied body's fixed frame."""
    return eci_to_body_fixed(pos_eci, unix_timestamp, body_frame)


def ecef_to_eci(
    pos_ecef: EcefVec3,
    unix_timestamp: float,
    *,
    body_frame: BodyFrame,
) -> EciVec3:
    """Convert body-fixed position to the supplied body's inertial frame."""
    theta = _body_rotation_angle(body_frame, unix_timestamp)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return EciVec3(
        Vec3(
            cos_t * pos_ecef.x - sin_t * pos_ecef.y,
            sin_t * pos_ecef.x + cos_t * pos_ecef.y,
            pos_ecef.z,
        )
    )


def body_fixed_to_geodetic(pos_body_fixed: EcefVec3, body_frame: BodyFrame) -> GeoPosition:
    """Convert a body-fixed XYZ vector to geodetic coordinates on that body."""
    x, y, z = pos_body_fixed
    lon_rad = math.atan2(y, x)
    p = math.sqrt(x**2 + y**2)
    a = body_frame.equatorial_radius_km
    b = body_frame.polar_radius_km
    e2 = 1.0 - (b * b) / (a * a)
    lat_rad = math.atan2(z, p * (1.0 - e2))
    for _ in range(10):
        sin_lat = math.sin(lat_rad)
        n = a / math.sqrt(1.0 - e2 * sin_lat**2)
        lat_rad = math.atan2(z + e2 * n * sin_lat, p)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    n = a / math.sqrt(1.0 - e2 * sin_lat**2)
    alt_km = p / cos_lat - n if abs(cos_lat) > 1e-10 else abs(z) - n * (1.0 - e2)
    return GeoPosition(
        lat_deg=math.degrees(lat_rad),
        lon_deg=math.degrees(lon_rad),
        alt_km=alt_km,
    )


def ecef_to_geodetic(pos_ecef: EcefVec3, *, body_frame: BodyFrame) -> GeoPosition:
    """Convert body-fixed XYZ (km) to geodetic on the supplied body."""
    return body_fixed_to_geodetic(pos_ecef, body_frame)


def eci_to_ecef_velocity(
    pos_eci: EciVec3,
    vel_eci: EciVec3,
    unix_timestamp: float,
    *,
    body_frame: BodyFrame,
) -> EcefVec3:
    """Convert inertial velocity to velocity relative to the supplied body."""
    return eci_to_body_fixed_velocity(pos_eci, vel_eci, unix_timestamp, body_frame)


def eci_to_body_fixed_velocity(
    pos_eci: EciVec3,
    vel_eci: EciVec3,
    unix_timestamp: float,
    body_frame: BodyFrame,
) -> EcefVec3:
    """Convert inertial velocity to velocity relative to a body's rotating frame."""
    theta = _body_rotation_angle(body_frame, unix_timestamp)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    vx = cos_t * vel_eci.x + sin_t * vel_eci.y
    vy = -sin_t * vel_eci.x + cos_t * vel_eci.y
    vz = vel_eci.z
    pos_fixed = eci_to_body_fixed(pos_eci, unix_timestamp, body_frame)
    omega = body_frame.rotation_rate_rad_s
    vx -= -omega * pos_fixed.y
    vy -= omega * pos_fixed.x
    return EcefVec3(Vec3(vx, vy, vz))


def propagate_keplerian_for_body(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> tuple[EcefVec3, EcefVec3, GeoPosition, EciVec3, EciVec3]:
    """Propagate a circular orbit in a body-specific local frame.

    Returns body-fixed position/velocity/geodetic plus body-centered inertial
    position/velocity. The latter is the value that can be translated into a
    common GCRS frame by adding the body's ephemeris state.
    """
    pos_inertial, vel_inertial = propagate_eci_for_body(
        elements,
        dt,
        mu_km3_s2=body_frame.gravitational_parameter_km3_s2,
    )
    current_time = epoch_unix + dt
    pos_fixed = eci_to_body_fixed(pos_inertial, current_time, body_frame)
    vel_fixed = eci_to_body_fixed_velocity(pos_inertial, vel_inertial, current_time, body_frame)
    geo = body_fixed_to_geodetic(pos_fixed, body_frame)
    return pos_fixed, vel_fixed, geo, pos_inertial, vel_inertial


def propagate_keplerian(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> tuple[EcefVec3, EcefVec3, GeoPosition]:
    """Propagate and return body-fixed position, velocity, and geodetic."""
    pos_ecef, vel_ecef, geo, _pos_eci, _vel_eci = propagate_keplerian_for_body(
        elements,
        epoch_unix,
        dt,
        body_frame=body_frame,
    )
    return pos_ecef, vel_ecef, geo


def propagate_j2_mean_elements_for_body(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> tuple[EcefVec3, EcefVec3, GeoPosition, EciVec3, EciVec3]:
    """Propagate body-specific circular mean elements with that body's J2 value."""
    pos_inertial, vel_inertial = propagate_eci_j2_mean_elements_for_body(
        elements,
        dt,
        body_frame=body_frame,
    )
    current_time = epoch_unix + dt
    pos_fixed = eci_to_body_fixed(pos_inertial, current_time, body_frame)
    vel_fixed = eci_to_body_fixed_velocity(pos_inertial, vel_inertial, current_time, body_frame)
    geo = body_fixed_to_geodetic(pos_fixed, body_frame)
    return pos_fixed, vel_fixed, geo, pos_inertial, vel_inertial


def propagate_j2_mean_elements(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
    *,
    body_frame: BodyFrame,
) -> tuple[EcefVec3, EcefVec3, GeoPosition]:
    """Propagate with the explicit J2 mean-element model for the supplied body."""
    pos_ecef, vel_ecef, geo, _pos_eci, _vel_eci = propagate_j2_mean_elements_for_body(
        elements,
        epoch_unix,
        dt,
        body_frame=body_frame,
    )
    return pos_ecef, vel_ecef, geo


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
    geo = body_fixed_to_geodetic(pos_ecef, body_frame)
    return pos_ecef, vel_ecef, geo
