# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Orbital propagators — shared library.

Pure math, no I/O. Current element support is circular (e=0), with a
selectable two-body Keplerian model and a first-order secular J2 mean-element
model.

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

from nodalarc.constants import (
    EARTH_J2,
    EARTH_MU,
    EARTH_RADIUS_KM,
    TWO_PI,
    WGS84_A,
    WGS84_E2,
)
from nodalarc.frames import EcefVec3, EciVec3, GeoPosition, Vec3
from nodalarc.geo import geodetic_to_ecef
from nodalarc.orbital import (
    OrbitalElements,
    elements_from_params,
)

# Re-export so consumers can get everything from one place
__all__ = [
    "Vec3",
    "EciVec3",
    "EcefVec3",
    "GeoPosition",
    "OrbitalElements",
    "elements_from_params",
    "EARTH_ROTATION_RATE",
    "J2000_UNIX",
    "orbital_period",
    "orbital_velocity",
    "j2_circular_secular_rates",
    "propagate_eci",
    "propagate_eci_j2_mean_elements",
    "gmst",
    "eci_to_ecef",
    "ecef_to_eci",
    "ecef_to_geodetic",
    "geodetic_to_ecef",
    "eci_to_ecef_velocity",
    "propagate_keplerian",
    "propagate_j2_mean_elements",
    "distance_km",
]

# Earth's rotation rate (rad/s)
EARTH_ROTATION_RATE = 7.2921159e-5

# J2000 epoch: 2000-01-01T12:00:00 UTC as Unix timestamp
J2000_UNIX = 946728000.0


def orbital_period(altitude_km: float) -> float:
    """Compute orbital period in seconds for a circular orbit.

    T = 2π √(a³/μ)
    """
    a = EARTH_RADIUS_KM + altitude_km
    return TWO_PI * math.sqrt(a**3 / EARTH_MU)


def orbital_velocity(altitude_km: float) -> float:
    """Compute orbital velocity in km/s for a circular orbit.

    v = √(μ/a)
    """
    a = EARTH_RADIUS_KM + altitude_km
    return math.sqrt(EARTH_MU / a)


def j2_circular_secular_rates(elements: OrbitalElements) -> tuple[float, float]:
    """Return (RAAN rate, mean-anomaly rate) for the circular J2 model.

    Rates are radians per second. The second value includes the Keplerian
    mean motion plus the first-order secular J2 correction.
    """
    a = elements.semi_major_axis_km
    i = elements.inclination_rad
    n = math.sqrt(EARTH_MU / a**3)
    cos_i = math.cos(i)
    j2_factor = EARTH_J2 * (WGS84_A / a) ** 2
    raan_dot = -1.5 * j2_factor * n * cos_i
    mean_anomaly_dot = n * (1.0 + 0.75 * j2_factor * (3.0 * cos_i**2 - 1.0))
    return raan_dot, mean_anomaly_dot


def propagate_eci(elements: OrbitalElements, dt: float) -> tuple[EciVec3, EciVec3]:
    """Propagate circular orbit by dt seconds in ECI frame.

    Returns (position_km_ECI, velocity_km_s_ECI) in Earth-Centered Inertial
    coordinates. Both vectors are in the inertial frame — they do NOT include
    Earth's rotation.

    For circular orbits (e=0): M = E = ν, so true anomaly
    advances linearly: ν(t) = ν₀ + n·dt where n = √(μ/a³).
    """
    a = elements.semi_major_axis_km
    i = elements.inclination_rad
    raan = elements.raan_rad

    # Mean motion (rad/s)
    n = math.sqrt(EARTH_MU / a**3)

    # True anomaly at time dt (circular: ν = ν₀ + n·dt)
    nu = elements.true_anomaly_rad + n * dt

    # Position in orbital plane (perifocal frame, circular)
    r = a  # constant for circular orbit
    x_pf = r * math.cos(nu)
    y_pf = r * math.sin(nu)

    # Velocity in orbital plane
    v = math.sqrt(EARTH_MU / a)
    vx_pf = -v * math.sin(nu)
    vy_pf = v * math.cos(nu)

    # Rotation from perifocal to ECI
    cos_raan = math.cos(raan)
    sin_raan = math.sin(raan)
    cos_i = math.cos(i)
    sin_i = math.sin(i)

    # ECI position
    x_eci = cos_raan * x_pf - sin_raan * cos_i * y_pf
    y_eci = sin_raan * x_pf + cos_raan * cos_i * y_pf
    z_eci = sin_i * y_pf

    # ECI velocity
    vx_eci = cos_raan * vx_pf - sin_raan * cos_i * vy_pf
    vy_eci = sin_raan * vx_pf + cos_raan * cos_i * vy_pf
    vz_eci = sin_i * vy_pf

    return EciVec3(Vec3(x_eci, y_eci, z_eci)), EciVec3(Vec3(vx_eci, vy_eci, vz_eci))


def propagate_eci_j2_mean_elements(elements: OrbitalElements, dt: float) -> tuple[EciVec3, EciVec3]:
    """Propagate a circular mean-element orbit with first-order secular J2.

    This model is intentionally explicit and limited: the current constellation
    element type has no eccentricity or argument of perigee, so this propagates
    circular mean elements by applying secular RAAN precession and the standard
    first-order J2 correction to mean anomaly. It does not include short-period
    terms and must not be represented as an SGP4/TLE-quality ephemeris.
    """
    a = elements.semi_major_axis_km
    i = elements.inclination_rad
    cos_i = math.cos(i)
    sin_i = math.sin(i)
    raan_dot, mean_anomaly_dot = j2_circular_secular_rates(elements)

    raan = elements.raan_rad + raan_dot * dt
    u = elements.true_anomaly_rad + mean_anomaly_dot * dt

    cos_raan = math.cos(raan)
    sin_raan = math.sin(raan)
    cos_u = math.cos(u)
    sin_u = math.sin(u)

    x = a * (cos_raan * cos_u - sin_raan * cos_i * sin_u)
    y = a * (sin_raan * cos_u + cos_raan * cos_i * sin_u)
    z = a * sin_i * sin_u

    # Differentiate the circular mean-element position. This includes the
    # secular node regression term, which is needed for tracking-rate checks.
    vx = a * (
        raan_dot * (-sin_raan * cos_u - cos_raan * cos_i * sin_u)
        + mean_anomaly_dot * (-cos_raan * sin_u - sin_raan * cos_i * cos_u)
    )
    vy = a * (
        raan_dot * (cos_raan * cos_u - sin_raan * cos_i * sin_u)
        + mean_anomaly_dot * (-sin_raan * sin_u + cos_raan * cos_i * cos_u)
    )
    vz = a * mean_anomaly_dot * sin_i * cos_u

    return EciVec3(Vec3(x, y, z)), EciVec3(Vec3(vx, vy, vz))


def gmst(unix_timestamp: float) -> float:
    """Compute Greenwich Mean Sidereal Time in radians.

    Uses a simplified model based on centuries from J2000.
    """
    # Julian centuries since J2000
    jd = 2440587.5 + unix_timestamp / 86400.0
    t = (jd - 2451545.0) / 36525.0

    # GMST in degrees (IAU 1982 model, simplified)
    gmst_deg = (
        280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t**2 - t**3 / 38710000.0
    )
    return math.radians(gmst_deg % 360.0)


def eci_to_ecef(pos_eci: EciVec3, unix_timestamp: float) -> EcefVec3:
    """Convert ECI position to ECEF via GMST rotation about Z axis."""
    theta = gmst(unix_timestamp)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return EcefVec3(
        Vec3(
            cos_t * pos_eci.x + sin_t * pos_eci.y,
            -sin_t * pos_eci.x + cos_t * pos_eci.y,
            pos_eci.z,
        )
    )


def ecef_to_eci(pos_ecef: EcefVec3, unix_timestamp: float) -> EciVec3:
    """Convert ECEF position to ECI via inverse GMST rotation."""
    theta = gmst(unix_timestamp)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return EciVec3(
        Vec3(
            cos_t * pos_ecef.x - sin_t * pos_ecef.y,
            sin_t * pos_ecef.x + cos_t * pos_ecef.y,
            pos_ecef.z,
        )
    )


def ecef_to_geodetic(pos_ecef: EcefVec3) -> GeoPosition:
    """Convert ECEF (km) to geodetic (lat_deg, lon_deg, alt_km).

    Uses iterative Bowring method on WGS84 ellipsoid.
    """
    x, y, z = pos_ecef
    lon_rad = math.atan2(y, x)

    # Distance from Z axis
    p = math.sqrt(x**2 + y**2)

    # Initial estimate using spherical approximation
    lat_rad = math.atan2(z, p * (1.0 - WGS84_E2))

    # Iterate for convergence
    for _ in range(10):
        sin_lat = math.sin(lat_rad)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat**2)
        lat_rad = math.atan2(z + WGS84_E2 * n * sin_lat, p)

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat**2)

    alt_km = p / cos_lat - n if abs(cos_lat) > 1e-10 else abs(z) - n * (1.0 - WGS84_E2)

    return GeoPosition(
        lat_deg=math.degrees(lat_rad),
        lon_deg=math.degrees(lon_rad),
        alt_km=alt_km,
    )


def eci_to_ecef_velocity(pos_eci: EciVec3, vel_eci: EciVec3, unix_timestamp: float) -> EcefVec3:
    """Convert ECI velocity to ECEF velocity.

    v_ecef = R_z(-θ) · v_eci - ω×r_ecef
    where θ = GMST, ω = (0, 0, Ω_earth)
    """
    theta = gmst(unix_timestamp)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    # Rotate velocity vector ECI → ECEF
    vx = cos_t * vel_eci.x + sin_t * vel_eci.y
    vy = -sin_t * vel_eci.x + cos_t * vel_eci.y
    vz = vel_eci.z

    # Subtract Earth rotation: ω × r_ecef
    pos_ecef = eci_to_ecef(pos_eci, unix_timestamp)
    vx -= -EARTH_ROTATION_RATE * pos_ecef.y
    vy -= EARTH_ROTATION_RATE * pos_ecef.x

    return EcefVec3(Vec3(vx, vy, vz))


def propagate_keplerian(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
) -> tuple[EcefVec3, EcefVec3, GeoPosition]:
    """Propagate and return ECEF position, ECEF velocity, and geodetic.

    This is the primary public API for the propagator. All outputs are in
    the Earth-Centered Earth-Fixed frame. The ECEF velocity includes the
    subtraction of Earth's rotation (v_ecef = R·v_eci - ω×r_ecef), so it
    represents motion relative to the rotating Earth.

    Args:
        elements: Orbital elements at epoch
        epoch_unix: Unix timestamp of the epoch
        dt: Time delta in seconds from epoch

    Returns:
        (pos_ecef_km, vel_ecef_km_s, geodetic_position)
    """
    pos_eci, vel_eci = propagate_eci(elements, dt)
    current_time = epoch_unix + dt
    pos_ecef = eci_to_ecef(pos_eci, current_time)
    vel_ecef = eci_to_ecef_velocity(pos_eci, vel_eci, current_time)
    geo = ecef_to_geodetic(pos_ecef)
    return pos_ecef, vel_ecef, geo


def propagate_j2_mean_elements(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
) -> tuple[EcefVec3, EcefVec3, GeoPosition]:
    """Propagate with the explicit circular J2 mean-element model."""
    pos_eci, vel_eci = propagate_eci_j2_mean_elements(elements, dt)
    current_time = epoch_unix + dt
    pos_ecef = eci_to_ecef(pos_eci, current_time)
    vel_ecef = eci_to_ecef_velocity(pos_eci, vel_eci, current_time)
    geo = ecef_to_geodetic(pos_ecef)
    return pos_ecef, vel_ecef, geo


def distance_km(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two 3D points."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)
