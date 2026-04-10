# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Keplerian orbital propagator — shared library.

Pure math, no I/O. Circular orbit simplification (e=0).

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
from typing import NamedTuple, NewType

from nodalarc.constants import (
    EARTH_MU,
    EARTH_RADIUS_KM,
    TWO_PI,
    WGS84_A,
    WGS84_E2,
)
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
    "propagate_eci",
    "gmst",
    "eci_to_ecef",
    "ecef_to_eci",
    "ecef_to_geodetic",
    "geodetic_to_ecef",
    "eci_to_ecef_velocity",
    "propagate_keplerian",
    "distance_km",
]

# Earth's rotation rate (rad/s)
EARTH_ROTATION_RATE = 7.2921159e-5

# J2000 epoch: 2000-01-01T12:00:00 UTC as Unix timestamp
J2000_UNIX = 946728000.0


class Vec3(NamedTuple):
    """3D vector — frame-unaware base type.

    Use EciVec3 or EcefVec3 in function signatures to document which
    coordinate frame a vector belongs to. These are zero-cost NewType
    wrappers — erased at runtime, enforced by mypy --strict.

    Frame-agnostic functions (e.g., distance_km, relative-vector math
    in visibility.py) should accept plain Vec3.
    """

    x: float
    y: float
    z: float


# Zero-cost frame tags for type-checker enforcement (PEP 484 NewType).
# At runtime: EciVec3 IS Vec3, EcefVec3 IS Vec3. No overhead.
# At type-check time: passing EciVec3 where EcefVec3 is expected is an error.
# This prevents the Mars Climate Orbiter class of frame confusion bugs.
EciVec3 = NewType("EciVec3", Vec3)
EcefVec3 = NewType("EcefVec3", Vec3)


class GeoPosition(NamedTuple):
    """Geodetic position."""

    lat_deg: float
    lon_deg: float
    alt_km: float


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


def geodetic_to_ecef(pos: GeoPosition) -> EcefVec3:
    """Convert geodetic (lat_deg, lon_deg, alt_km) to ECEF (km)."""
    lat_rad = math.radians(pos.lat_deg)
    lon_rad = math.radians(pos.lon_deg)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)

    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    x = (n + pos.alt_km) * cos_lat * cos_lon
    y = (n + pos.alt_km) * cos_lat * sin_lon
    z = (n * (1.0 - WGS84_E2) + pos.alt_km) * sin_lat
    return EcefVec3(Vec3(x, y, z))


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


def distance_km(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two 3D points."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)
