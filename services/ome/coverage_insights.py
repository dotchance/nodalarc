# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Coverage insights — physics-based explanations with severity levels.

Pure functions. No OME imports. Takes analysis results and produces
typed insights that distinguish expected physics (info/note) from
actual problems (warning/error).

Severity levels:
- info: expected physics (Earth occlusion, range limits, polar seam for near-polar)
- note: topology characteristic worth knowing (full mesh, terminal allocation)
- warning: potential issue affecting routing (tracking rate dropouts, coverage gaps)
- error: configuration problem preventing connectivity (no cross-plane, station unreachable)
"""

from __future__ import annotations

import math

from nodalarc.models.coverage import CoverageInsight, GsStationPreview, IslFailureBreakdown


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
) -> list[CoverageInsight]:
    """Generate physics-based insights for a constellation + terminal combination."""
    insights: list[CoverageInsight] = []
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
    body_radius_km: float,
    coverage_pct: float,
    longest_gap_s: float,
    min_elevation_deg: float = 25.0,
) -> str:
    """Physics-based description of a ground station's coverage.

    Always returns a meaningful explanation. Uses orbital geometry.
    """
    footprint_deg = _footprint_half_angle(altitude_km, body_radius_km)
    max_visible_lat = inclination_deg + footprint_deg
    abs_lat = abs(station_lat)

    if abs_lat > max_visible_lat:
        return (
            f"At {abs_lat:.0f}\u00b0 latitude, this station is beyond the constellation's "
            f"maximum visibility ({inclination_deg:.0f}\u00b0 inclination + "
            f"{footprint_deg:.0f}\u00b0 footprint = {max_visible_lat:.0f}\u00b0). "
            f"No satellites ever rise above the horizon. Use a higher-inclination "
            f"constellation for polar coverage."
        )

    if abs_lat > inclination_deg:
        max_elev = _max_elevation_at_latitude(
            abs_lat,
            inclination_deg,
            altitude_km,
            body_radius_km,
        )
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

    if abs_lat > inclination_deg - 10:
        max_elev = _max_elevation_at_latitude(
            abs_lat,
            inclination_deg,
            altitude_km,
            body_radius_km,
        )
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

    max_elev = _max_elevation_at_latitude(
        abs_lat,
        inclination_deg,
        altitude_km,
        body_radius_km,
    )
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


def _footprint_half_angle(altitude_km: float, body_radius_km: float) -> float:
    return math.degrees(math.acos(body_radius_km / (body_radius_km + altitude_km)))


def _max_elevation_at_latitude(
    station_lat_abs: float,
    inclination_deg: float,
    altitude_km: float,
    body_radius_km: float,
) -> float:
    if inclination_deg <= 0:
        return 0.0
    lat_offset = station_lat_abs - inclination_deg
    if lat_offset <= 0:
        return 90.0
    delta_rad = math.radians(lat_offset)
    ratio = body_radius_km / (body_radius_km + altitude_km)
    elev_rad = math.atan2(math.cos(delta_rad) - ratio, math.sin(delta_rad))
    return max(0.0, min(90.0, math.degrees(elev_rad)))


def _gap_description(longest_gap_s: float) -> str:
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
) -> list[CoverageInsight]:
    if plane_count <= 1:
        if isl_terminal_count >= 2:
            return [
                CoverageInsight(
                    severity="note",
                    message=(
                        f"Single orbital plane with {isl_terminal_count} ISL terminals per "
                        f"satellite. Each satellite links to its forward and aft neighbors, "
                        f"forming a ring."
                    ),
                )
            ]
        return []

    if not has_cross_plane:
        return [
            CoverageInsight(
                severity="error",
                message=(
                    f"Each satellite has {isl_terminal_count} ISL terminal(s), which are all "
                    f"used for links within the same orbital plane. With {plane_count} planes, "
                    f"there are no connections between planes \u2014 each plane is an isolated ring. "
                    f"Traffic between planes can only route through ground stations. "
                    f"To connect planes directly, choose a satellite type with more ISL terminals."
                ),
            )
        ]

    if max_cross_per_sat == 1:
        return [
            CoverageInsight(
                severity="warning",
                message=(
                    "Each satellite connects to both neighbors in its own plane plus one "
                    "neighboring plane. With only one cross-plane link per satellite, there is "
                    "no redundancy between planes \u2014 if that single link drops, the plane is "
                    "isolated until it reforms. A satellite type with 4+ ISL terminals would "
                    "provide connections to both adjacent planes."
                ),
            )
        ]

    return [
        CoverageInsight(
            severity="note",
            message=(
                f"Each satellite has {isl_terminal_count} ISL terminals: 2 for intra-plane "
                f"neighbors (forward and aft) and up to {max_cross_per_sat} for cross-plane "
                f"connections to adjacent orbital planes. This provides full mesh connectivity "
                f"with cross-plane redundancy."
            ),
        )
    ]


def _isl_intermittency_insights(
    failure_reasons: IslFailureBreakdown | None,
) -> list[CoverageInsight]:
    if not failure_reasons:
        return []

    results: list[CoverageInsight] = []

    # Transient LOS blocked and range exceeded are normal ISL cycling —
    # neighbor pairs temporarily lose visibility as orbits progress, then
    # reconnect. This is what routing protocols handle. Not reported.

    # Polar seam — expected for near-polar Walker-star, worth noting
    if failure_reasons.polar_seam > 0:
        results.append(
            CoverageInsight(
                severity="note",
                message=(
                    "Cross-plane links cycle on and off at polar latitudes due to the "
                    "polar seam \u2014 expected behavior for near-polar Walker-star orbits "
                    "where counter-rotating planes converge. The routing protocol will "
                    "reconverge around these transitions."
                ),
            )
        )

    # Tracking rate exceeded — actionable, user can change terminal hardware
    if failure_reasons.tracking_exceeded > 0:
        results.append(
            CoverageInsight(
                severity="warning",
                message=(
                    "Cross-plane links drop at high latitudes because satellites in "
                    "converging planes move too fast for the terminals to track. "
                    "Terminals with a higher tracking rate would reduce these dropouts."
                ),
            )
        )

    # Field of regard — actionable
    if failure_reasons.field_of_regard > 0:
        results.append(
            CoverageInsight(
                severity="warning",
                message=(
                    "Some neighbors are outside the terminal's pointing cone. "
                    "A wider field of regard would enable more connections."
                ),
            )
        )

    # Terminal exhaustion — actionable
    if failure_reasons.terminal_exhausted > 0:
        results.append(
            CoverageInsight(
                severity="warning",
                message=(
                    "Some physically reachable ISLs cannot form because all terminals "
                    "are already allocated to higher-priority neighbors. More ISL "
                    "terminals per satellite would improve connectivity."
                ),
            )
        )

    return results


def _gs_connectivity_insights(
    sim_min: int,
    max_gap: float,
    per_station: dict[str, GsStationPreview],
) -> list[CoverageInsight]:
    results: list[CoverageInsight] = []

    if sim_min == 0:
        if max_gap > 600:
            results.append(
                CoverageInsight(
                    severity="warning",
                    message=(
                        f"There are gaps of up to {max_gap / 60:.0f} minutes with no ground "
                        f"station connectivity. During these windows, all traffic stays in "
                        f"orbit with no path to the ground. Adding more ground stations at "
                        f"different longitudes would reduce or eliminate these gaps."
                    ),
                )
            )
        else:
            results.append(
                CoverageInsight(
                    severity="note",
                    message=(
                        "There are brief periods with no ground station connectivity. "
                        "Routing protocols will reconverge when links restore. Adding "
                        "ground stations at different longitudes would help."
                    ),
                )
            )
    elif max_gap > 300:
        results.append(
            CoverageInsight(
                severity="note",
                message=(
                    f"The longest gap at any single ground station is "
                    f"{max_gap / 60:.0f} minutes. Overall constellation coverage is good, "
                    f"but individual stations experience intermittent connectivity."
                ),
            )
        )

    return results
