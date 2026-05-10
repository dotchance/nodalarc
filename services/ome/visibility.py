# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Visibility computation for ISL and ground station links.

Handles:
- Line-of-sight (Earth body occlusion)
- Range constraints
- Elevation angle for ground stations
- Angular velocity for polar seam tracking rate limits
- Ground link scheduling (highest-elevation, lowest-elevation,
  longest-remaining-pass)
- ISL terminal scheduling (priority-based with symmetric constraint)

Under 500 lines.
"""

from __future__ import annotations

import math
from typing import NamedTuple

from nodalarc.constants import WGS84_A, WGS84_B

from ome.propagator import (
    GeoPosition,
    Vec3,
    distance_km,
)


class VisibilityResult(NamedTuple):
    """Result of a visibility check between two nodes."""

    visible: bool
    range_km: float
    reason: str  # "ok", "los_blocked", "range_exceeded", "field_of_regard", "elevation_below_min", "tracking_exceeded"


class GroundVisibility(NamedTuple):
    """Ground station to satellite visibility details."""

    sat_id: str
    visible: bool
    elevation_deg: float
    range_km: float
    # Populated only for policies that explicitly require future dwell
    # prediction. None is fatal for longest-remaining-pass scoring.
    remaining_visible_s: float | None = None


class IslVisibility(NamedTuple):
    """ISL visibility between two satellites."""

    node_a: str
    node_b: str
    visible: bool
    range_km: float
    angular_velocity_deg_s: float
    reason: str


class ScheduledLink(NamedTuple):
    """A link that has been scheduled (terminal allocated)."""

    node_a: str
    node_b: str
    scheduled: bool  # True if terminal allocated, False if visible but unscheduled
    range_km: float


# ---------------------------------------------------------------------------
# Core geometric functions
# ---------------------------------------------------------------------------


def has_line_of_sight(pos_a: Vec3, pos_b: Vec3) -> bool:
    """Check if two points have line of sight (not occluded by Earth).

    Uses closest approach of line segment to Earth center.
    Earth is modeled as an oblate spheroid (WGS84 semi-axes).
    For simplicity, we use the mean Earth radius.
    """
    # Direction vector from A to B
    dx = pos_b.x - pos_a.x
    dy = pos_b.y - pos_a.y
    dz = pos_b.z - pos_a.z

    # Parametric closest approach to origin: t = -(A·D)/(D·D)
    dot_ad = pos_a.x * dx + pos_a.y * dy + pos_a.z * dz
    dot_dd = dx * dx + dy * dy + dz * dz

    if dot_dd == 0:
        return True  # Same point

    t = -dot_ad / dot_dd

    # Clamp t to [0, 1] — only check between the two points
    t = max(0.0, min(1.0, t))

    # Closest point on segment to Earth center
    cx = pos_a.x + t * dx
    cy = pos_a.y + t * dy
    cz = pos_a.z + t * dz

    # Check against the WGS84 oblate ellipsoid by normalizing each axis.
    # (cx/a)² + (cy/a)² + (cz/b)² >= 1 means outside ellipsoid
    norm_sq = (cx / WGS84_A) ** 2 + (cy / WGS84_A) ** 2 + (cz / WGS84_B) ** 2
    return norm_sq >= 1.0


def compute_range(pos_a: Vec3, pos_b: Vec3) -> float:
    """Compute range (distance) in km between two ECEF positions."""
    return distance_km(pos_a, pos_b)


def compute_elevation_angle(
    gs_ecef: Vec3,
    gs_geo: GeoPosition,
    sat_ecef: Vec3,
) -> float:
    """Compute elevation angle of satellite as seen from ground station.

    Uses ENU (East-North-Up) coordinate frame at the ground station.
    Returns elevation angle in degrees (positive = above horizon).
    """
    # Vector from GS to satellite
    dx = sat_ecef.x - gs_ecef.x
    dy = sat_ecef.y - gs_ecef.y
    dz = sat_ecef.z - gs_ecef.z

    # ENU rotation from ECEF
    lat_rad = math.radians(gs_geo.lat_deg)
    lon_rad = math.radians(gs_geo.lon_deg)

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)

    # East
    e = -sin_lon * dx + cos_lon * dy
    # North
    n = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    # Up
    u = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

    horizontal_dist = math.sqrt(e**2 + n**2)
    if horizontal_dist < 1e-10:
        return 90.0  # Directly overhead

    elevation_rad = math.atan2(u, horizontal_dist)
    return math.degrees(elevation_rad)


def compute_angular_velocity(
    pos_a: Vec3,
    vel_a: Vec3,
    pos_b: Vec3,
    vel_b: Vec3,
) -> float:
    """Compute angular velocity between two satellites in deg/s.

    |ω| = |v_perp| / |r| where v_perp is relative velocity
    perpendicular to the line of sight.
    """
    # Relative position and velocity
    rx = pos_b.x - pos_a.x
    ry = pos_b.y - pos_a.y
    rz = pos_b.z - pos_a.z

    vx = vel_b.x - vel_a.x
    vy = vel_b.y - vel_a.y
    vz = vel_b.z - vel_a.z

    r_mag = math.sqrt(rx**2 + ry**2 + rz**2)
    if r_mag < 1e-10:
        return 0.0

    # Project relative velocity onto LOS direction
    r_hat_x = rx / r_mag
    r_hat_y = ry / r_mag
    r_hat_z = rz / r_mag

    v_radial = vx * r_hat_x + vy * r_hat_y + vz * r_hat_z

    # Perpendicular component
    v_perp_x = vx - v_radial * r_hat_x
    v_perp_y = vy - v_radial * r_hat_y
    v_perp_z = vz - v_radial * r_hat_z

    v_perp_mag = math.sqrt(v_perp_x**2 + v_perp_y**2 + v_perp_z**2)

    # Angular velocity in rad/s → deg/s
    omega_rad_s = v_perp_mag / r_mag
    return math.degrees(omega_rad_s)


# ---------------------------------------------------------------------------
# Field of regard
# ---------------------------------------------------------------------------


def check_field_of_regard(
    pos_a: Vec3,
    vel_a: Vec3,
    pos_b: Vec3,
    vel_b: Vec3,
    field_of_regard_deg: float,
) -> bool:
    """Check if both satellites can see each other within their field of regard.

    The FoR defines the maximum angular deviation from the local horizontal
    plane that the ISL terminal can steer.  For LEO ISL terminals the physical
    constraint is how far above or below the orbital plane the beam can point;
    all same-altitude ISL targets are near-horizontal.

    Returns True if the link is within FoR/2 of local horizontal for both ends.
    """
    if field_of_regard_deg >= 360.0:
        return True

    half_angle_rad = math.radians(field_of_regard_deg / 2.0)

    # LOS from A to B (unit vector)
    los_x = pos_b.x - pos_a.x
    los_y = pos_b.y - pos_a.y
    los_z = pos_b.z - pos_a.z
    los_mag = math.sqrt(los_x**2 + los_y**2 + los_z**2)
    if los_mag < 1e-10:
        return True
    los_x /= los_mag
    los_y /= los_mag
    los_z /= los_mag

    # Check from A's perspective: elevation angle of LOS above local horizontal
    r_a_mag = math.sqrt(pos_a.x**2 + pos_a.y**2 + pos_a.z**2)
    if r_a_mag < 1e-10:
        return True
    zenith_a_x = pos_a.x / r_a_mag
    zenith_a_y = pos_a.y / r_a_mag
    zenith_a_z = pos_a.z / r_a_mag

    cos_zenith_a = max(-1.0, min(1.0, los_x * zenith_a_x + los_y * zenith_a_y + los_z * zenith_a_z))
    zenith_angle_a = math.acos(cos_zenith_a)
    elevation_a = abs(zenith_angle_a - math.pi / 2.0)  # deviation from horizontal
    if elevation_a > half_angle_rad:
        return False

    # Check from B's perspective (reversed LOS)
    r_b_mag = math.sqrt(pos_b.x**2 + pos_b.y**2 + pos_b.z**2)
    if r_b_mag < 1e-10:
        return True
    zenith_b_x = pos_b.x / r_b_mag
    zenith_b_y = pos_b.y / r_b_mag
    zenith_b_z = pos_b.z / r_b_mag

    cos_zenith_b = max(
        -1.0, min(1.0, -los_x * zenith_b_x - los_y * zenith_b_y - los_z * zenith_b_z)
    )
    zenith_angle_b = math.acos(cos_zenith_b)
    elevation_b = abs(zenith_angle_b - math.pi / 2.0)
    return not elevation_b > half_angle_rad


# ---------------------------------------------------------------------------
# High-level visibility checks
# ---------------------------------------------------------------------------


def check_isl_visibility(
    pos_a: Vec3,
    vel_a: Vec3,
    pos_b: Vec3,
    vel_b: Vec3,
    max_range_km: float,
    max_tracking_rate_deg_s: float | None = None,
    field_of_regard_deg: float = 360.0,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    geo_a: GeoPosition | None = None,
    geo_b: GeoPosition | None = None,
) -> IslVisibility:
    """Full ISL visibility check: LOS → range → FoR → tracking rate → polar seam.

    Returns IslVisibility with reason for failure if not visible.
    """
    range_km = compute_range(pos_a, pos_b)

    # 1. Line of sight
    if not has_line_of_sight(pos_a, pos_b):
        return IslVisibility("", "", False, range_km, 0.0, "los_blocked")

    # 2. Range
    if range_km > max_range_km:
        return IslVisibility("", "", False, range_km, 0.0, "range_exceeded")

    # 3. Field of regard
    if field_of_regard_deg < 360.0:  # noqa: SIM102
        if not check_field_of_regard(pos_a, vel_a, pos_b, vel_b, field_of_regard_deg):
            return IslVisibility("", "", False, range_km, 0.0, "field_of_regard")

    # 4. Angular velocity / tracking rate
    ang_vel = compute_angular_velocity(pos_a, vel_a, pos_b, vel_b)
    if max_tracking_rate_deg_s is not None and ang_vel > max_tracking_rate_deg_s:
        return IslVisibility("", "", False, range_km, ang_vel, "tracking_exceeded")

    # 5. Polar seam hard latitude cutoff
    if polar_seam_enabled and geo_a is not None and geo_b is not None:  # noqa: SIM102
        if (
            abs(geo_a.lat_deg) > latitude_threshold_deg
            or abs(geo_b.lat_deg) > latitude_threshold_deg
        ):
            # Only applies to cross-plane ISLs — caller handles this
            return IslVisibility("", "", False, range_km, ang_vel, "polar_seam")

    return IslVisibility("", "", True, range_km, ang_vel, "ok")


def check_ground_visibility(
    gs_ecef: Vec3,
    gs_geo: GeoPosition,
    sat_ecef: Vec3,
    min_elevation_deg: float = 25.0,
) -> GroundVisibility:
    """Check if satellite is visible from ground station.

    Checks LOS and elevation angle.
    """
    range_km = compute_range(gs_ecef, sat_ecef)

    if not has_line_of_sight(gs_ecef, sat_ecef):
        return GroundVisibility("", False, -90.0, range_km)

    elevation = compute_elevation_angle(gs_ecef, gs_geo, sat_ecef)
    if elevation < min_elevation_deg:
        return GroundVisibility("", False, elevation, range_km)

    return GroundVisibility("", True, elevation, range_km)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def schedule_isl_terminals(
    node_id: str,
    feasible_isls: list[tuple[str, int, float]],
    terminal_count: int,
) -> list[ScheduledLink]:
    """Schedule ISL terminals based on priority.

    Args:
        node_id: The satellite doing the scheduling
        feasible_isls: List of (peer_id, priority, range_km) tuples
        terminal_count: Number of ISL terminals available

    Returns:
        List of ScheduledLink (scheduled=True for top-priority links)
    """
    # Sort by priority (lower = higher priority)
    sorted_isls = sorted(feasible_isls, key=lambda x: x[1])

    results: list[ScheduledLink] = []
    for i, (peer_id, _priority, range_km) in enumerate(sorted_isls):
        scheduled = i < terminal_count
        results.append(
            ScheduledLink(
                node_a=node_id,
                node_b=peer_id,
                scheduled=scheduled,
                range_km=range_km,
            )
        )

    return results


def enforce_symmetric_scheduling(
    all_schedules: dict[str, list[ScheduledLink]],
) -> dict[str, list[ScheduledLink]]:
    """Enforce symmetric constraint: if A schedules link to B, B must also schedule link to A.

    If B doesn't have capacity, both sides become unscheduled.
    Modifies and returns the schedule dict.
    """
    # Build lookup: (node_a, node_b) -> is_scheduled
    scheduled_pairs: set[tuple[str, str]] = set()
    for _node_id, links in all_schedules.items():
        for link in links:
            if link.scheduled:
                scheduled_pairs.add((link.node_a, link.node_b))

    # Find asymmetric pairs
    to_unschedule: set[tuple[str, str]] = set()
    for a, b in scheduled_pairs:
        if (b, a) not in scheduled_pairs:
            to_unschedule.add((a, b))

    if not to_unschedule:
        return all_schedules

    # Unschedule asymmetric links
    result: dict[str, list[ScheduledLink]] = {}
    for node_id, links in all_schedules.items():
        new_links: list[ScheduledLink] = []
        for link in links:
            if (link.node_a, link.node_b) in to_unschedule:
                new_links.append(
                    ScheduledLink(
                        link.node_a,
                        link.node_b,
                        False,
                        link.range_km,
                    )
                )
            else:
                new_links.append(link)
        result[node_id] = new_links

    return result
