# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Visibility computation for ISL and ground station links.

Handles:
- Line-of-sight (Earth body occlusion)
- Range constraints
- Elevation angle for ground stations
- Angular velocity for polar seam tracking rate limits
- Ground link scheduling (highest-elevation, lowest-elevation,
  longest-remaining-pass)
- ISL terminal scheduling (priority-based with symmetric constraint)
"""

from __future__ import annotations

import math
from typing import Literal, NamedTuple

from nodalarc.body_frames import EARTH_BODY_FRAME, BodyFrame
from nodalarc.geo import compute_range_km
from nodalarc.models.link_decisions import (
    GroundVisibilityRejectingEndpoint,
    GroundVisibilityRejectReason,
)
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight

from ome.propagator import GeoPosition, Vec3


class VisibilityResult(NamedTuple):
    """Result of a visibility check between two nodes."""

    visible: bool
    range_km: float
    reason: str  # "ok", "los_blocked", "range_exceeded", "field_of_regard", "elevation_below_min", "tracking_exceeded"


class GroundVisibility(NamedTuple):
    """Ground station to satellite visibility details.

    ``reject_reason`` carries the specific reason a non-visible pair
    failed (``los_blocked``, ``elevation_below_min``, etc.). For a
    visible pair it is ``"ok"``. ``rejecting_endpoint`` identifies the
    endpoint whose terminal-bound constraint rejected the pair. It is
    ``"none"`` for visible pairs and for non-terminal rejections such
    as LOS and elevation mask failures.

    ``remaining_visible_s`` is populated only for policies that
    explicitly require future dwell prediction. ``None`` here means
    "not applicable for this policy"; for ``longest-remaining-pass``
    the producer must populate a non-None value or downstream
    scoring fails loudly.
    """

    sat_id: str
    visible: bool
    elevation_deg: float
    range_km: float
    remaining_visible_s: float | None
    reject_reason: GroundVisibilityRejectReason
    rejecting_endpoint: GroundVisibilityRejectingEndpoint = "none"
    azimuth_deg: float | None = None


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


def has_line_of_sight(
    pos_a: Vec3,
    pos_b: Vec3,
    body_frame: BodyFrame = EARTH_BODY_FRAME,
) -> bool:
    """Check if two points have line of sight through a body-fixed frame."""
    dx = pos_b.x - pos_a.x
    dy = pos_b.y - pos_a.y
    dz = pos_b.z - pos_a.z

    dot_ad = pos_a.x * dx + pos_a.y * dy + pos_a.z * dz
    dot_dd = dx * dx + dy * dy + dz * dz

    if dot_dd == 0:
        return True

    t = max(0.0, min(1.0, -dot_ad / dot_dd))
    cx = pos_a.x + t * dx
    cy = pos_a.y + t * dy
    cz = pos_a.z + t * dz

    a = body_frame.equatorial_radius_km
    b = body_frame.polar_radius_km
    norm_sq = (cx / a) ** 2 + (cy / a) ** 2 + (cz / b) ** 2
    return norm_sq >= 1.0


def _enu_components(
    observer_ecef: Vec3, observer_geo: GeoPosition, target_ecef: Vec3
) -> tuple[float, float, float]:
    dx = target_ecef.x - observer_ecef.x
    dy = target_ecef.y - observer_ecef.y
    dz = target_ecef.z - observer_ecef.z

    lat_rad = math.radians(observer_geo.lat_deg)
    lon_rad = math.radians(observer_geo.lon_deg)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)

    e = -sin_lon * dx + cos_lon * dy
    n = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    u = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
    return e, n, u


def compute_look_angles(
    gs_ecef: Vec3,
    gs_geo: GeoPosition,
    sat_ecef: Vec3,
) -> tuple[float, float]:
    """Return (elevation_deg, azimuth_deg) in the observer body-local frame."""
    e, n, u = _enu_components(gs_ecef, gs_geo, sat_ecef)
    horizontal_dist = math.sqrt(e**2 + n**2)
    if horizontal_dist < 1e-10:
        return 90.0, 0.0
    elevation = math.degrees(math.atan2(u, horizontal_dist))
    azimuth = math.degrees(math.atan2(e, n)) % 360.0
    return elevation, azimuth


def compute_elevation_angle(
    gs_ecef: Vec3,
    gs_geo: GeoPosition,
    sat_ecef: Vec3,
) -> float:
    """Compute elevation angle of satellite as seen from ground station."""
    elevation, _azimuth = compute_look_angles(gs_ecef, gs_geo, sat_ecef)
    return elevation


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


def _unit(vec: Vec3) -> Vec3:
    mag = math.sqrt(vec.x**2 + vec.y**2 + vec.z**2)
    if mag < 1e-12:
        return Vec3(0.0, 0.0, 0.0)
    return Vec3(vec.x / mag, vec.y / mag, vec.z / mag)


def _angle_between_deg(a: Vec3, b: Vec3) -> float:
    au = _unit(a)
    bu = _unit(b)
    dot = max(-1.0, min(1.0, au.x * bu.x + au.y * bu.y + au.z * bu.z))
    return math.degrees(math.acos(dot))


def _local_unit_from_az_el(az_deg: float, el_deg: float) -> Vec3:
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    cos_el = math.cos(el)
    # ENU components, with azimuth clockwise from north.
    return Vec3(cos_el * math.sin(az), cos_el * math.cos(az), math.sin(el))


def _ground_for_allows(
    *,
    gs_ecef: Vec3,
    gs_geo: GeoPosition,
    sat_ecef: Vec3,
    boresight: TerminalBoresight,
    field_of_regard_deg: float,
) -> bool:
    elevation, azimuth = compute_look_angles(gs_ecef, gs_geo, sat_ecef)
    if boresight.mode == "steerable_envelope":
        if (
            boresight.min_az_deg is None
            or boresight.max_az_deg is None
            or boresight.min_el_deg is None
            or boresight.max_el_deg is None
        ):
            raise ValueError("steerable_envelope boresight is missing azimuth/elevation bounds")
        return (
            boresight.min_az_deg <= azimuth <= boresight.max_az_deg
            and boresight.min_el_deg <= elevation <= boresight.max_el_deg
        )
    target = _local_unit_from_az_el(azimuth, elevation)
    if boresight.mode == "local_vertical":
        bore = Vec3(0.0, 0.0, 1.0)
    else:
        if boresight.configured_az_deg is None or boresight.configured_el_deg is None:
            raise ValueError(
                "configured_topocentric boresight is missing configured azimuth/elevation"
            )
        bore = _local_unit_from_az_el(boresight.configured_az_deg, boresight.configured_el_deg)
    return _angle_between_deg(bore, target) <= field_of_regard_deg / 2.0


def _sat_ground_for_allows(
    *,
    sat_ecef: Vec3,
    gs_ecef: Vec3,
    boresight: SatGroundTerminalBoresight,
    field_of_regard_deg: float,
) -> bool:
    if boresight.mode != "nadir":
        raise ValueError(f"Unsupported satellite ground-terminal boresight mode={boresight.mode!r}")
    los_to_gs = Vec3(gs_ecef.x - sat_ecef.x, gs_ecef.y - sat_ecef.y, gs_ecef.z - sat_ecef.z)
    nadir = Vec3(-sat_ecef.x, -sat_ecef.y, -sat_ecef.z)
    return _angle_between_deg(nadir, los_to_gs) <= field_of_regard_deg / 2.0


def _limiting_endpoint(
    gs_value: float | None,
    sat_value: float | None,
) -> GroundVisibilityRejectingEndpoint:
    """Return which endpoint supplied the stricter terminal constraint."""
    if gs_value is None and sat_value is None:
        return "both"
    if gs_value is None:
        return "satellite"
    if sat_value is None:
        return "ground"
    if math.isclose(gs_value, sat_value, rel_tol=0.0, abs_tol=1e-12):
        return "both"
    return "ground" if gs_value < sat_value else "satellite"


def compute_topocentric_angular_velocity(
    observer_ecef: Vec3,
    target_ecef: Vec3,
    target_velocity_km_s: Vec3,
    body_frame: BodyFrame = EARTH_BODY_FRAME,
    velocity_frame: Literal["body_fixed", "inertial"] = "body_fixed",
) -> float:
    """Apparent angular rate in the body-fixed topocentric frame of the observer.

    Production propagation supplies ECEF/MCMF body-fixed velocities. In that
    production path, ``body_frame`` is intentionally not used: a fixed ground
    observer has zero body-fixed velocity, and the supplied target velocity is
    already in the observer's rotating frame. The inertial path exists so tests
    and future propagators must account for the observing body's rotation
    instead of accidentally treating inertial velocity as topocentric-body-fixed
    velocity.
    """
    if velocity_frame == "body_fixed":
        target_velocity_body_fixed_km_s = target_velocity_km_s
    elif velocity_frame == "inertial":
        omega = body_frame.rotation_rate_rad_s
        target_velocity_body_fixed_km_s = Vec3(
            target_velocity_km_s.x + omega * target_ecef.y,
            target_velocity_km_s.y - omega * target_ecef.x,
            target_velocity_km_s.z,
        )
    else:
        raise ValueError(f"Unsupported velocity_frame={velocity_frame!r}")
    return compute_angular_velocity(
        observer_ecef,
        Vec3(0.0, 0.0, 0.0),
        target_ecef,
        target_velocity_body_fixed_km_s,
    )


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
    range_km = compute_range_km(pos_a, pos_b)

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
    *,
    max_range_km: float | None = None,
    gs_max_range_km: float | None = None,
    sat_max_range_km: float | None = None,
    gs_boresight: TerminalBoresight | None = None,
    sat_boresight: SatGroundTerminalBoresight | None = None,
    gs_field_of_regard_deg: float | None = None,
    sat_field_of_regard_deg: float | None = None,
    max_tracking_rate_deg_s: float | None = None,
    gs_max_tracking_rate_deg_s: float | None = None,
    sat_max_tracking_rate_deg_s: float | None = None,
    sat_velocity_ecef_km_s: Vec3 | None = None,
    body_frame: BodyFrame = EARTH_BODY_FRAME,
) -> GroundVisibility:
    """Check ground-station-to-satellite visibility with physics constraints.

    Rejection precedence is deterministic and documented: LOS occlusion → range
    → elevation mask → ground field-of-regard → satellite field-of-regard →
    tracking rate. Terminal-bound rejects set ``rejecting_endpoint`` to the
    endpoint whose declared constraint rejected the pair.
    """
    range_km = compute_range_km(gs_ecef, sat_ecef)

    if gs_max_range_km is not None or sat_max_range_km is not None:
        effective_max_range_km = min(
            v for v in (gs_max_range_km, sat_max_range_km) if v is not None
        )
        if max_range_km is not None and not math.isclose(
            max_range_km,
            effective_max_range_km,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("max_range_km conflicts with endpoint max-range constraints")
        range_rejecting_endpoint = _limiting_endpoint(gs_max_range_km, sat_max_range_km)
    else:
        effective_max_range_km = max_range_km
        range_rejecting_endpoint: GroundVisibilityRejectingEndpoint = (
            "both" if max_range_km is not None else "none"
        )

    if gs_max_tracking_rate_deg_s is not None or sat_max_tracking_rate_deg_s is not None:
        effective_max_tracking_rate_deg_s = min(
            v for v in (gs_max_tracking_rate_deg_s, sat_max_tracking_rate_deg_s) if v is not None
        )
        if max_tracking_rate_deg_s is not None and not math.isclose(
            max_tracking_rate_deg_s,
            effective_max_tracking_rate_deg_s,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("max_tracking_rate_deg_s conflicts with endpoint tracking constraints")
        tracking_rejecting_endpoint = _limiting_endpoint(
            gs_max_tracking_rate_deg_s,
            sat_max_tracking_rate_deg_s,
        )
    else:
        effective_max_tracking_rate_deg_s = max_tracking_rate_deg_s
        tracking_rejecting_endpoint: GroundVisibilityRejectingEndpoint = (
            "both" if max_tracking_rate_deg_s is not None else "none"
        )

    if not has_line_of_sight(gs_ecef, sat_ecef, body_frame):
        return GroundVisibility(
            sat_id="",
            visible=False,
            elevation_deg=-90.0,
            range_km=range_km,
            remaining_visible_s=None,
            reject_reason="los_blocked",
            rejecting_endpoint="none",
            azimuth_deg=None,
        )

    elevation, azimuth = compute_look_angles(gs_ecef, gs_geo, sat_ecef)

    if effective_max_range_km is not None and range_km > effective_max_range_km:
        return GroundVisibility(
            sat_id="",
            visible=False,
            elevation_deg=elevation,
            range_km=range_km,
            remaining_visible_s=None,
            reject_reason="range_exceeded",
            rejecting_endpoint=range_rejecting_endpoint,
            azimuth_deg=azimuth,
        )

    if elevation < min_elevation_deg:
        return GroundVisibility(
            sat_id="",
            visible=False,
            elevation_deg=elevation,
            range_km=range_km,
            remaining_visible_s=None,
            reject_reason="elevation_below_min",
            rejecting_endpoint="none",
            azimuth_deg=azimuth,
        )

    if gs_boresight is not None and gs_field_of_regard_deg is None:
        raise ValueError("Ground boresight visibility requires gs_field_of_regard_deg")
    if gs_boresight is not None and not _ground_for_allows(
        gs_ecef=gs_ecef,
        gs_geo=gs_geo,
        sat_ecef=sat_ecef,
        boresight=gs_boresight,
        field_of_regard_deg=gs_field_of_regard_deg,
    ):
        return GroundVisibility(
            sat_id="",
            visible=False,
            elevation_deg=elevation,
            range_km=range_km,
            remaining_visible_s=None,
            reject_reason="field_of_regard",
            rejecting_endpoint="ground",
            azimuth_deg=azimuth,
        )

    if sat_boresight is not None and sat_field_of_regard_deg is None:
        raise ValueError("Satellite boresight visibility requires sat_field_of_regard_deg")
    if sat_boresight is not None and not _sat_ground_for_allows(
        sat_ecef=sat_ecef,
        gs_ecef=gs_ecef,
        boresight=sat_boresight,
        field_of_regard_deg=sat_field_of_regard_deg,
    ):
        return GroundVisibility(
            sat_id="",
            visible=False,
            elevation_deg=elevation,
            range_km=range_km,
            remaining_visible_s=None,
            reject_reason="field_of_regard",
            rejecting_endpoint="satellite",
            azimuth_deg=azimuth,
        )

    if effective_max_tracking_rate_deg_s is not None:
        if sat_velocity_ecef_km_s is None:
            raise ValueError(
                "Ground tracking-rate visibility requires same-frame satellite velocity"
            )
        angular_velocity = compute_topocentric_angular_velocity(
            gs_ecef,
            sat_ecef,
            sat_velocity_ecef_km_s,
            body_frame,
        )
        if angular_velocity > effective_max_tracking_rate_deg_s:
            return GroundVisibility(
                sat_id="",
                visible=False,
                elevation_deg=elevation,
                range_km=range_km,
                remaining_visible_s=None,
                reject_reason="tracking_exceeded",
                rejecting_endpoint=tracking_rejecting_endpoint,
                azimuth_deg=azimuth,
            )

    return GroundVisibility(
        sat_id="",
        visible=True,
        elevation_deg=elevation,
        range_km=range_km,
        remaining_visible_s=None,
        reject_reason="ok",
        rejecting_endpoint="none",
        azimuth_deg=azimuth,
    )


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
