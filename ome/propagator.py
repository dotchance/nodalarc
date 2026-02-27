"""Keplerian orbital propagator for Nodal Arc.

Pure math, no I/O. Circular orbit simplification (e=0).
Under 300 lines.

Coordinate frames:
- ECI (Earth-Centered Inertial): fixed stars frame
- ECEF (Earth-Centered Earth-Fixed): rotates with Earth
- Geodetic: lat/lon/alt on WGS84 ellipsoid
"""

from __future__ import annotations

import math
from typing import NamedTuple

from nodalarc.constants import (
    EARTH_MU,
    EARTH_RADIUS_KM,
    TWO_PI,
    WGS84_A,
    WGS84_E2,
)

# Earth's rotation rate (rad/s)
EARTH_ROTATION_RATE = 7.2921159e-5

# J2000 epoch: 2000-01-01T12:00:00 UTC as Unix timestamp
J2000_UNIX = 946728000.0


class Vec3(NamedTuple):
    """3D vector."""
    x: float
    y: float
    z: float


class OrbitalElements(NamedTuple):
    """Keplerian orbital elements for circular orbits."""
    semi_major_axis_km: float  # a = altitude + Earth radius
    inclination_rad: float     # i
    raan_rad: float            # Ω (Right Ascension of Ascending Node)
    true_anomaly_rad: float    # ν at epoch


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


def elements_from_params(
    altitude_km: float,
    inclination_deg: float,
    raan_deg: float,
    true_anomaly_deg: float,
) -> OrbitalElements:
    """Create OrbitalElements from human-readable parameters."""
    return OrbitalElements(
        semi_major_axis_km=EARTH_RADIUS_KM + altitude_km,
        inclination_rad=math.radians(inclination_deg),
        raan_rad=math.radians(raan_deg),
        true_anomaly_rad=math.radians(true_anomaly_deg),
    )


def propagate_eci(elements: OrbitalElements, dt: float) -> tuple[Vec3, Vec3]:
    """Propagate circular orbit by dt seconds in ECI frame.

    Returns (position_km, velocity_km_s) in ECI coordinates.

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

    return Vec3(x_eci, y_eci, z_eci), Vec3(vx_eci, vy_eci, vz_eci)


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
        + 0.000387933 * t**2
        - t**3 / 38710000.0
    )
    return math.radians(gmst_deg % 360.0)


def eci_to_ecef(pos_eci: Vec3, unix_timestamp: float) -> Vec3:
    """Convert ECI position to ECEF via GMST rotation about Z axis."""
    theta = gmst(unix_timestamp)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return Vec3(
        cos_t * pos_eci.x + sin_t * pos_eci.y,
        -sin_t * pos_eci.x + cos_t * pos_eci.y,
        pos_eci.z,
    )


def ecef_to_eci(pos_ecef: Vec3, unix_timestamp: float) -> Vec3:
    """Convert ECEF position to ECI via inverse GMST rotation."""
    theta = gmst(unix_timestamp)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return Vec3(
        cos_t * pos_ecef.x - sin_t * pos_ecef.y,
        sin_t * pos_ecef.x + cos_t * pos_ecef.y,
        pos_ecef.z,
    )


def ecef_to_geodetic(pos_ecef: Vec3) -> GeoPosition:
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

    if abs(cos_lat) > 1e-10:
        alt_km = p / cos_lat - n
    else:
        alt_km = abs(z) - n * (1.0 - WGS84_E2)

    return GeoPosition(
        lat_deg=math.degrees(lat_rad),
        lon_deg=math.degrees(lon_rad),
        alt_km=alt_km,
    )


def geodetic_to_ecef(pos: GeoPosition) -> Vec3:
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
    return Vec3(x, y, z)


def propagate_keplerian(
    elements: OrbitalElements,
    epoch_unix: float,
    dt: float,
) -> tuple[Vec3, Vec3, GeoPosition]:
    """Propagate and return (ecef_pos, ecef_vel, geodetic).

    This is the primary public API for the propagator.

    Args:
        elements: Orbital elements at epoch
        epoch_unix: Unix timestamp of the epoch
        dt: Time delta in seconds from epoch

    Returns:
        (ecef_position_km, eci_velocity_km_s, geodetic_position)
    """
    pos_eci, vel_eci = propagate_eci(elements, dt)
    current_time = epoch_unix + dt
    pos_ecef = eci_to_ecef(pos_eci, current_time)
    geo = ecef_to_geodetic(pos_ecef)
    return pos_ecef, vel_eci, geo


def distance_km(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two 3D points."""
    return math.sqrt((a.x - b.x)**2 + (a.y - b.y)**2 + (a.z - b.z)**2)
