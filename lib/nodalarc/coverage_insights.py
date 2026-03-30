"""Coverage insights — physics-based explanations for every aspect of a preview.

Pure functions. No OME imports. Takes analysis results and produces
plain-English explanations grounded in orbital mechanics. Every ground
station, every ISL topology, every combination gets an explanation —
not just the failures.
"""

from __future__ import annotations

import math

from nodalarc.models.coverage import GsStationPreview, IslFailureBreakdown

EARTH_RADIUS_KM = 6371.0


def generate_insights(
    *,
    sim_min: int,
    max_gap: float,
    per_station: dict[str, GsStationPreview],
    failure_reasons: IslFailureBreakdown | None,
    has_cross_plane: bool,
    max_cross_per_sat: int,
    plane_count: int,
    isl_terminal_count: int,
) -> list[str]:
    """Generate physics-based insights for a constellation + terminal combination."""
    insights: list[str] = []
    insights.extend(
        _isl_topology_insights(
            plane_count,
            isl_terminal_count,
            has_cross_plane,
            max_cross_per_sat,
        )
    )
    insights.extend(_isl_intermittency_insights(failure_reasons))
    insights.extend(_gs_connectivity_insights(sim_min, max_gap, per_station))
    return insights


def describe_gs_coverage(
    station_name: str,
    station_lat: float,
    inclination_deg: float,
    altitude_km: float,
    coverage_pct: float,
    longest_gap_s: float,
    min_elevation_deg: float = 25.0,
) -> str:
    """Physics-based description of a ground station's coverage.

    Always returns a meaningful explanation — not just for failures.
    Uses orbital geometry: a satellite at altitude h has a ground
    footprint extending arccos(R/(R+h)) degrees beyond the sub-satellite
    point. The maximum elevation angle at a station depends on how
    close the orbital track passes to the station's latitude.
    """
    footprint_deg = _footprint_half_angle(altitude_km)
    max_visible_lat = inclination_deg + footprint_deg
    abs_lat = abs(station_lat)

    # --- Station beyond all visibility ---
    if abs_lat > max_visible_lat:
        return (
            f"At {abs_lat:.0f}\u00b0 latitude, this station is beyond the constellation's "
            f"maximum visibility ({inclination_deg:.0f}\u00b0 inclination + "
            f"{footprint_deg:.0f}\u00b0 footprint = {max_visible_lat:.0f}\u00b0). "
            f"No satellites ever rise above the horizon. Use a higher-inclination "
            f"constellation for polar coverage."
        )

    # --- Station beyond inclination but within footprint edge ---
    if abs_lat > inclination_deg:
        max_elev = _max_elevation_at_latitude(abs_lat, inclination_deg, altitude_km)
        return (
            f"At {abs_lat:.0f}\u00b0 latitude, this station is beyond the orbital "
            f"inclination ({inclination_deg:.0f}\u00b0) but within the footprint edge "
            f"({max_visible_lat:.0f}\u00b0). Satellites reach a maximum elevation of "
            f"~{max_elev:.0f}\u00b0. "
            + (
                f"With a {min_elevation_deg:.0f}\u00b0 minimum elevation requirement, "
                f"most passes are too low to use. "
                if max_elev < min_elevation_deg + 15
                else ""
            )
            + f"Coverage: {coverage_pct:.0f}%."
        )

    # --- Station near edge of inclination band ---
    if abs_lat > inclination_deg - 10:
        max_elev = _max_elevation_at_latitude(abs_lat, inclination_deg, altitude_km)
        gap_desc = _gap_description(longest_gap_s)
        return (
            f"At {abs_lat:.0f}\u00b0 latitude, near the edge of the {inclination_deg:.0f}\u00b0 "
            f"inclination band. Satellites reach up to ~{max_elev:.0f}\u00b0 elevation. "
            f"Coverage: {coverage_pct:.0f}%{gap_desc}. "
            + (
                "Lowering the minimum elevation angle would extend contact windows. "
                if coverage_pct < 80
                else ""
            )
        )

    # --- Station well within coverage band ---
    max_elev = _max_elevation_at_latitude(abs_lat, inclination_deg, altitude_km)
    gap_desc = _gap_description(longest_gap_s)

    if coverage_pct >= 95:
        return (
            f"At {abs_lat:.0f}\u00b0 latitude, well within the {inclination_deg:.0f}\u00b0 "
            f"inclination band. Satellites pass nearly overhead (up to ~{max_elev:.0f}\u00b0 "
            f"elevation). Coverage: {coverage_pct:.0f}%{gap_desc}."
        )

    if coverage_pct >= 70:
        return (
            f"At {abs_lat:.0f}\u00b0 latitude, within the {inclination_deg:.0f}\u00b0 "
            f"inclination band. Maximum satellite elevation ~{max_elev:.0f}\u00b0. "
            f"Coverage: {coverage_pct:.0f}%{gap_desc}. "
            f"Gaps occur between consecutive satellite passes."
        )

    # Low coverage despite being in-band — likely min_elevation or terminal limit
    return (
        f"At {abs_lat:.0f}\u00b0 latitude, within the {inclination_deg:.0f}\u00b0 "
        f"inclination band (max elevation ~{max_elev:.0f}\u00b0). "
        f"Coverage is only {coverage_pct:.0f}%{gap_desc}. "
        f"This may be caused by a high minimum elevation angle "
        f"({min_elevation_deg:.0f}\u00b0) filtering out low passes, or by "
        f"limited ground terminal availability. Try lowering the minimum "
        f"elevation angle."
    )


# --- Private helpers ---


def _footprint_half_angle(altitude_km: float) -> float:
    """Ground footprint half-angle in degrees from orbital altitude."""
    return math.degrees(math.acos(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + altitude_km)))


def _max_elevation_at_latitude(
    station_lat_abs: float,
    inclination_deg: float,
    altitude_km: float,
) -> float:
    """Approximate maximum elevation angle a satellite reaches at this station.

    Uses the geometry: when the sub-satellite point is at angular distance
    delta from the station, the elevation angle is:
        el = arctan((cos(delta) - R/(R+h)) / sin(delta))
    where delta is the great-circle angular distance.

    When delta=0 (satellite directly overhead), elevation = 90°.
    """
    if inclination_deg <= 0:
        return 0.0

    r = EARTH_RADIUS_KM
    h = altitude_km

    # Minimum angular distance between station and nearest sub-satellite point.
    # For a station within the inclination band, the track passes directly
    # overhead at some longitude → delta ≈ 0 → elevation ≈ 90°.
    # For a station beyond inclination, the closest the track gets is
    # |station_lat| - inclination degrees away.
    lat_offset = station_lat_abs - inclination_deg
    if lat_offset <= 0:
        # Station is within the inclination band. The orbital track passes
        # at or above this latitude. At the station's latitude, the track
        # passes directly overhead → delta ≈ 0 → elevation ≈ 90°.
        return 90.0
    else:
        delta_deg = lat_offset

    if delta_deg <= 0.1:
        return 90.0

    delta_rad = math.radians(delta_deg)
    cos_d = math.cos(delta_rad)
    sin_d = math.sin(delta_rad)
    ratio = r / (r + h)

    elev_rad = math.atan2(cos_d - ratio, sin_d)
    return max(0.0, min(90.0, math.degrees(elev_rad)))


def _gap_description(longest_gap_s: float) -> str:
    """Format gap duration for inline use."""
    if longest_gap_s <= 0:
        return ""
    if longest_gap_s < 60:
        return f", longest gap {longest_gap_s:.0f}s"
    return f", longest gap {longest_gap_s / 60:.0f} min"


def _isl_topology_insights(
    plane_count: int,
    isl_terminal_count: int,
    has_cross_plane: bool,
    max_cross_per_sat: int,
) -> list[str]:
    """Explain the structural topology produced by this terminal count."""
    if plane_count <= 1:
        if isl_terminal_count >= 2:
            return [
                f"Single orbital plane with {isl_terminal_count} ISL terminals per "
                f"satellite. Each satellite links to its forward and aft neighbors, "
                f"forming a ring."
            ]
        return []

    if not has_cross_plane:
        return [
            f"Each satellite has {isl_terminal_count} ISL terminal(s), which are all "
            f"used for links within the same orbital plane. With {plane_count} planes, "
            f"there are no connections between planes \u2014 each plane is an isolated ring. "
            f"Traffic between planes can only route through ground stations. "
            f"To connect planes directly, choose a satellite type with more ISL terminals."
        ]

    if max_cross_per_sat == 1:
        return [
            "Each satellite connects to both neighbors in its own plane plus one "
            "neighboring plane. With only one cross-plane link per satellite, there is "
            "no redundancy between planes \u2014 if that single link drops, the plane is "
            "isolated until it reforms. A satellite type with 4+ ISL terminals would "
            "provide connections to both adjacent planes."
        ]

    if max_cross_per_sat >= 2:
        return [
            f"Each satellite has {isl_terminal_count} ISL terminals: 2 for intra-plane "
            f"neighbors (forward and aft) and up to {max_cross_per_sat} for cross-plane "
            f"connections to adjacent orbital planes. This provides full mesh connectivity "
            f"with cross-plane redundancy."
        ]

    return []


def _isl_intermittency_insights(
    failure_reasons: IslFailureBreakdown | None,
) -> list[str]:
    """Explain why ISL links drop during the orbital period."""
    if not failure_reasons:
        return []

    total = (
        failure_reasons.range_exceeded
        + failure_reasons.tracking_exceeded
        + failure_reasons.los_blocked
        + failure_reasons.polar_seam
        + failure_reasons.field_of_regard
    )
    if total == 0:
        return []

    parts: list[str] = []
    if failure_reasons.polar_seam > 0:
        parts.append(
            "cross-plane links drop at polar latitudes where a hard cutoff "
            "disables them \u2014 this is the \u201cpolar seam\u201d effect typical of "
            "near-polar orbits"
        )
    if failure_reasons.tracking_exceeded > 0:
        parts.append(
            "cross-plane links drop at high latitudes because satellites "
            "in converging planes move too fast relative to each other for "
            "the terminals to track. Terminals with a higher tracking rate "
            "would reduce these dropouts"
        )
    if failure_reasons.range_exceeded > 0:
        parts.append(
            "some satellite pairs are too far apart for this terminal's "
            "maximum range. A terminal with longer range would enable "
            "more connections"
        )
    if failure_reasons.los_blocked > 0:
        parts.append(
            "some satellite pairs are on opposite sides of the Earth and "
            "cannot see each other \u2014 this is normal geometry"
        )
    if failure_reasons.field_of_regard > 0:
        parts.append(
            "some neighbors are outside the terminal's pointing cone. "
            "A wider field of regard would help"
        )

    if not parts:
        return []
    return ["Some links are intermittent during the orbital period: " + "; ".join(parts) + "."]


def _gs_connectivity_insights(
    sim_min: int,
    max_gap: float,
    per_station: dict[str, GsStationPreview],
) -> list[str]:
    """Explain overall ground station connectivity.

    Per-station descriptions are in the per_station table, not here.
    This function only generates constellation-wide connectivity insights.
    """
    if sim_min == 0:
        if max_gap > 600:
            return [
                f"There are gaps of up to {max_gap / 60:.0f} minutes with no ground "
                f"station connectivity at all. During these windows, all traffic stays "
                f"in orbit with no path to the ground. Adding more ground stations at "
                f"different longitudes would reduce or eliminate these gaps."
            ]
        return [
            "There are brief periods with no ground station connectivity. "
            "Routing protocols will reconverge when links restore. Adding "
            "ground stations at different longitudes would help."
        ]
    if max_gap > 300:
        return [
            f"The longest gap at any single ground station is "
            f"{max_gap / 60:.0f} minutes. Overall constellation coverage is good, "
            f"but individual stations experience intermittent connectivity."
        ]
    return []
