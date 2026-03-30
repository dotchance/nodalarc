"""Coverage preview — runs OME visibility computation at reduced resolution.

Imports precompute_timeline_window directly from ome.event_stream.
No HTTP, no subprocess, no OME class instantiation.  The computation
is CPU-bound and should be called via run_in_executor from async code.

Preview uses 10-second steps (vs 1-second for actual sessions).
At 550 km altitude (~5730s orbital period) this is 573 timesteps
instead of 5730.  Coverage gaps shorter than 10 seconds are
operationally insignificant for routing protocol convergence.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from pathlib import Path

import yaml

from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.constellation import ParametricConstellation
from nodalarc.models.coverage import (
    CoveragePreviewResult,
    GsPreview,
    GsStationPreview,
    IslFailureBreakdown,
    IslPreview,
)
from nodalarc.models.session import AddressingConfig
from ome.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from ome.event_stream import precompute_timeline_window
from ome.propagator import orbital_period, propagate_keplerian
from ome.visibility import check_isl_visibility

log = logging.getLogger(__name__)

_PREVIEW_STEP_SECONDS = 10
_FAILURE_SCAN_SAMPLES = 10  # Number of timesteps to sample for failure reasons


def _scan_isl_failure_reasons(
    satellites,
    addressing,
    neighbors,
    epoch_unix: float,
    period: float,
    max_range_km: float,
    max_tracking_rate_deg_s: float,
    field_of_regard_deg: float,
    polar_seam_enabled: bool,
    latitude_threshold_deg: float,
) -> tuple[IslFailureBreakdown, set[tuple[str, str]]]:
    """Sample a few timesteps and count WHY ISLs fail to form.

    Returns (failure_breakdown, pairs_ever_feasible).
    pairs_ever_feasible is the set of ISL pairs that pass all physics
    checks at least once across the sampled timesteps.

    Calls check_isl_visibility directly on each neighbor pair at
    evenly-spaced timesteps to collect per-reason failure counts.
    This is a focused diagnostic scan, not a full timeline computation.
    """
    from nodalarc.models.addressing import neighbors_by_node

    by_node = neighbors_by_node(neighbors)
    reason_counts: dict[str, int] = defaultdict(int)
    pairs_ever_feasible: set[tuple[str, str]] = set()

    # Build sat lookup: node_id -> (plane, slot, elements)
    sat_map: dict[str, object] = {}
    for sat in satellites:
        nid = addressing.sat_id(sat.plane, sat.slot)
        sat_map[nid] = sat

    # Sample evenly across the orbital period
    sample_times = [period * i / _FAILURE_SCAN_SAMPLES for i in range(_FAILURE_SCAN_SAMPLES)]

    for dt in sample_times:
        # Propagate all satellites to this time
        positions: dict[str, tuple] = {}
        for nid, sat in sat_map.items():
            pos, vel, geo = propagate_keplerian(sat.elements, epoch_unix, dt)
            positions[nid] = (pos, vel, geo)

        # Check each neighbor pair
        seen_pairs: set[tuple[str, str]] = set()
        for nid, assignments in by_node.items():
            for na in assignments:
                pair = tuple(sorted([nid, na.peer_node_id]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                if nid not in positions or na.peer_node_id not in positions:
                    continue

                pos_a, vel_a, geo_a = positions[nid]
                pos_b, vel_b, geo_b = positions[na.peer_node_id]

                is_cross = na.link_type == "cross_plane_isl"
                result = check_isl_visibility(
                    pos_a,
                    vel_a,
                    pos_b,
                    vel_b,
                    max_range_km=max_range_km,
                    max_tracking_rate_deg_s=max_tracking_rate_deg_s if is_cross else None,
                    field_of_regard_deg=field_of_regard_deg,
                    polar_seam_enabled=polar_seam_enabled and is_cross,
                    latitude_threshold_deg=latitude_threshold_deg,
                    geo_a=geo_a,
                    geo_b=geo_b,
                )

                if result.visible:
                    pairs_ever_feasible.add(pair)
                else:
                    reason_counts[result.reason] += 1

    return (
        IslFailureBreakdown(
            range_exceeded=reason_counts.get("range_exceeded", 0),
            tracking_exceeded=reason_counts.get("tracking_exceeded", 0),
            field_of_regard=reason_counts.get("field_of_regard", 0),
            los_blocked=reason_counts.get("los_blocked", 0),
            polar_seam=reason_counts.get("polar_seam", 0),
        ),
        pairs_ever_feasible,
    )


def _diagnose_gs_coverage(
    station_name: str,
    station_lat: float,
    inclination_deg: float,
    coverage_pct: float,
) -> str | None:
    """Diagnose why a ground station has poor coverage."""
    if coverage_pct > 10:
        return None

    abs_lat = abs(station_lat)
    if abs_lat > inclination_deg:
        return (
            f"Station latitude ({station_lat:.1f}) is beyond the constellation's "
            f"orbital inclination ({inclination_deg:.1f}) — satellites never pass overhead"
        )
    if abs_lat > inclination_deg - 5:
        return (
            f"Station latitude ({station_lat:.1f}) is near the edge of the "
            f"constellation's coverage ({inclination_deg:.1f} inclination) — "
            f"only brief, low-elevation passes are possible"
        )
    return "Low coverage despite being within orbital track — may need lower min_elevation_deg"


def compute_coverage_preview(
    constellation_source: str | dict | None,
    satellite_type_override: str | None,
    ground_stations_source: str | list[str] | dict | None,
) -> CoveragePreviewResult:
    """Compute coverage statistics for a constellation + GS combination.

    Args:
        constellation_source: Constellation preset file path, set name that
            resolves to a preset, or inline dict.
        satellite_type_override: When set and constellation_source is a file
            path, overrides the constellation's satellite_type before loading.
        ground_stations_source: GS set name (resolved to set file path),
            list of station names, or inline dict.

    Returns:
        CoveragePreviewResult with ISL and GS statistics + warnings.
    """
    t0 = time.monotonic()

    # --- Resolve constellation ---
    if constellation_source is None:
        raise ValueError("constellation is required for coverage preview")
    if ground_stations_source is None:
        raise ValueError("ground_stations is required for coverage preview")

    if isinstance(constellation_source, dict):
        merged = constellation_source
        if satellite_type_override:
            merged = dict(merged)
            merged["satellite_type"] = satellite_type_override
            merged.pop("default_terminals", None)
        constellation = load_constellation(merged)
    elif isinstance(constellation_source, str):
        # Could be a file path or a preset name — try file first
        source_path = _resolve_constellation_path(constellation_source)
        if satellite_type_override:
            data = yaml.safe_load(Path(source_path).read_text())
            data["satellite_type"] = satellite_type_override
            data.pop("default_terminals", None)
            constellation = load_constellation(data)
        else:
            constellation = load_constellation(source_path)
    else:
        raise ValueError(f"Invalid constellation_source type: {type(constellation_source)}")

    # --- Resolve ground stations ---
    gs_source = ground_stations_source
    if isinstance(gs_source, str):
        gs_source = _resolve_gs_path(gs_source)
    gs_file = load_ground_stations(gs_source)

    # --- Expand and compute ---
    satellites = expand_constellation(constellation)
    if not satellites:
        raise ValueError("No satellites in constellation")

    addressing = AddressingScheme(AddressingConfig())
    neighbors = assign_isl_neighbors(constellation, addressing)

    first_alt = satellites[0].elements.semi_major_axis_km - 6371.0
    period = orbital_period(first_alt)

    # Extract visibility parameters from resolved constellation
    max_range_km = 5016.0
    max_tracking_rate_deg_s = 3.0
    field_of_regard_deg = 360.0
    polar_seam_enabled = False
    latitude_threshold_deg = 70.0
    default_min_elevation_deg = 25.0

    if isinstance(constellation, ParametricConstellation):
        if constellation.default_terminals and constellation.default_terminals.isl:
            isl = constellation.default_terminals.isl[0]
            max_range_km = isl.max_range_km
            max_tracking_rate_deg_s = isl.max_tracking_rate_deg_s
            field_of_regard_deg = isl.field_of_regard_deg
        if constellation.polar_seam:
            polar_seam_enabled = constellation.polar_seam.enabled
            latitude_threshold_deg = constellation.polar_seam.latitude_threshold_deg

    if gs_file and gs_file.default_min_elevation_deg:
        default_min_elevation_deg = gs_file.default_min_elevation_deg

    epoch_unix = 0.0  # Arbitrary — coverage is orbital-period-periodic

    events, isl_state, gs_state = precompute_timeline_window(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=epoch_unix,
        duration_s=period,
        step_seconds=_PREVIEW_STEP_SECONDS,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        field_of_regard_deg=field_of_regard_deg,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_min_elevation_deg=default_min_elevation_deg,
    )

    elapsed = time.monotonic() - t0
    log.info(
        "Coverage preview: %d sats, %d GS, %.1fs period, %d events in %.1fs",
        len(satellites),
        len(gs_file.stations) if gs_file else 0,
        period,
        len(events),
        elapsed,
    )

    # --- Scan ISL failure reasons (fast: 10 sample timesteps) ---
    failure_reasons, pairs_ever_feasible = _scan_isl_failure_reasons(
        satellites=satellites,
        addressing=addressing,
        neighbors=neighbors,
        epoch_unix=epoch_unix,
        period=period,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        field_of_regard_deg=field_of_regard_deg,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
    )

    # Extract inclination for GS diagnostics
    inclination_deg = 0.0
    plane_count = 1
    if isinstance(constellation, ParametricConstellation):
        inclination_deg = constellation.orbit.inclination_deg
        plane_count = constellation.planes.count

    isl_terminal_count = satellites[0].isl_terminal_count if satellites else 4

    # --- Analyze events ---
    return _analyze_events(
        events=events,
        neighbors=neighbors,
        gs_file=gs_file,
        addressing=addressing,
        period=period,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        failure_reasons=failure_reasons,
        pairs_ever_feasible=pairs_ever_feasible,
        inclination_deg=inclination_deg,
        plane_count=plane_count,
        isl_terminal_count=isl_terminal_count,
    )


def _analyze_events(
    events,
    neighbors,
    gs_file,
    addressing,
    period: float,
    max_range_km: float,
    max_tracking_rate_deg_s: float,
    failure_reasons: IslFailureBreakdown | None = None,
    pairs_ever_feasible: set[tuple[str, str]] | None = None,
    inclination_deg: float = 0.0,
    plane_count: int = 1,
    isl_terminal_count: int = 4,
) -> CoveragePreviewResult:
    """Analyze timeline events into coverage statistics.

    TimelineEvent objects have event_type (str) and data (Pydantic model).
    VisibilityEvent data has: node_a, node_b, visible, scheduled, range_km,
    elevation_deg, terminal_type.

    ISL failure reasons are derived from the events themselves:
    - visible=False: physics failure (range, tracking, LOS, polar seam)
    - visible=True, scheduled=False: terminal exhaustion
    - Never appears in events: never in range (geometry)
    """
    # Count total possible UNIQUE ISL pairs from neighbor assignment.
    # neighbors is a frozenset of (node_id, NeighborAssignment) — each ISL
    # appears twice (A→B and B→A). Deduplicate by sorted pair.
    from nodalarc.models.addressing import neighbors_by_node as _nbn

    _by_node = _nbn(neighbors)
    _all_pairs: set[tuple[str, str]] = set()
    for _nid, _assignments in _by_node.items():
        for _na in _assignments:
            _all_pairs.add(tuple(sorted([_nid, _na.peer_node_id])))
    total_isl_pairs = len(_all_pairs)

    # Track ISL state per timestep
    isl_active: dict[tuple[str, str], bool] = {}
    isl_ever_formed: set[tuple[str, str]] = set()
    isl_ever_visible: set[tuple[str, str]] = set()  # visible but maybe not scheduled
    isl_visible_not_scheduled: set[tuple[str, str]] = set()  # terminal exhaustion
    isl_counts_per_step: list[int] = []

    # Track GS state per timestep
    gs_active: dict[str, set[str]] = defaultdict(set)  # gs_id -> set of connected sat_ids
    gs_connected_per_step: list[int] = []  # simultaneous GS count per step
    gs_coverage_steps: dict[str, int] = defaultdict(int)  # gs_id -> steps with connectivity

    # Initialize GS tracking for all stations upfront
    if gs_file:
        for station in gs_file.stations:
            gs_coverage_steps[f"gs-{station.name}"] = 0

    total_steps = 0

    for event in events:
        if event.event_type == "VisibilityEvent":
            vis = event.data
            key = (vis.node_a, vis.node_b)
            is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")

            if is_gs:
                gs_id = vis.node_a if vis.node_a.startswith("gs-") else vis.node_b
                sat_id = vis.node_b if vis.node_a.startswith("gs-") else vis.node_a
                if vis.visible and vis.scheduled:
                    gs_active[gs_id].add(sat_id)
                else:
                    gs_active[gs_id].discard(sat_id)
            else:
                if vis.visible:
                    isl_ever_visible.add(key)
                    if vis.scheduled:
                        isl_active[key] = True
                        isl_ever_formed.add(key)
                    else:
                        # Visible but not scheduled = terminal exhaustion
                        isl_visible_not_scheduled.add(key)
                        isl_active[key] = False
                else:
                    isl_active[key] = False

        elif event.event_type == "ClockTick":
            # Snapshot ISL and GS state at this timestep
            total_steps += 1
            isl_counts_per_step.append(sum(1 for v in isl_active.values() if v))
            connected_gs = sum(1 for gs_id, sats in gs_active.items() if len(sats) > 0)
            gs_connected_per_step.append(connected_gs)
            for gs_id in gs_coverage_steps:
                if len(gs_active.get(gs_id, set())) > 0:
                    gs_coverage_steps[gs_id] += 1

    # If no clock ticks were found, use event-based counting
    if total_steps == 0:
        total_steps = max(1, int(period / _PREVIEW_STEP_SECONDS))

    # Compute per-GS gap analysis from events
    per_station: dict[str, GsStationPreview] = {}
    if gs_file:
        for station in gs_file.stations:
            gs_id = f"gs-{station.name}"
            steps_connected = gs_coverage_steps.get(gs_id, 0)
            coverage_pct = (steps_connected / total_steps) * 100.0 if total_steps > 0 else 0.0
            gap_steps = total_steps - steps_connected
            longest_gap_s = gap_steps * _PREVIEW_STEP_SECONDS if gap_steps > 0 else 0.0
            reason = _diagnose_gs_coverage(
                station.name, station.lat_deg, inclination_deg, coverage_pct
            )
            per_station[station.name] = GsStationPreview(
                coverage_pct=round(coverage_pct, 1),
                longest_gap_s=round(longest_gap_s, 1),
                reason=reason,
            )

    # ISL stats — derive failure reasons from event data
    formed = len(isl_ever_formed)
    never = total_isl_pairs - formed
    feasibility_pct = (formed / total_isl_pairs * 100.0) if total_isl_pairs > 0 else 100.0

    # Build all neighbor pair keys for comparison
    all_pairs: set[tuple[str, str]] = set()
    from nodalarc.models.addressing import neighbors_by_node

    by_node = neighbors_by_node(neighbors)
    for nid, assignments in by_node.items():
        for na in assignments:
            pair = tuple(sorted([nid, na.peer_node_id]))
            all_pairs.add(pair)

    # Pairs never emitted by the OME = either physics failure or terminal exhaustion.
    # The OME doesn't emit visible-but-not-scheduled events — it silently drops
    # ISLs that lose the terminal allocation contest. The scan tells us which
    # pairs are physically feasible (pass all physics checks at least once).
    never_emitted = all_pairs - isl_ever_formed
    # Feasible but never scheduled = terminal exhaustion
    if pairs_ever_feasible is not None:
        feasible_never_scheduled = pairs_ever_feasible - isl_ever_formed
        terminal_failures = len(feasible_never_scheduled)
    else:
        terminal_failures = 0
    # Never feasible = physics failure (already counted by scan per-reason)

    min_isl = min(isl_counts_per_step) if isl_counts_per_step else 0
    max_isl = max(isl_counts_per_step) if isl_counts_per_step else 0

    # GS simultaneous stats
    sim_min = min(gs_connected_per_step) if gs_connected_per_step else 0
    sim_max = max(gs_connected_per_step) if gs_connected_per_step else 0
    sim_mean = (
        sum(gs_connected_per_step) / len(gs_connected_per_step) if gs_connected_per_step else 0.0
    )
    max_gap = max((s.longest_gap_s for s in per_station.values()), default=0.0)

    # Merge event-derived and scan-derived failure reasons
    # Event data tells us terminal exhaustion; scan tells us physics causes
    merged_reasons = IslFailureBreakdown(
        range_exceeded=(failure_reasons.range_exceeded if failure_reasons else 0),
        tracking_exceeded=(failure_reasons.tracking_exceeded if failure_reasons else 0),
        field_of_regard=(failure_reasons.field_of_regard if failure_reasons else 0),
        los_blocked=(failure_reasons.los_blocked if failure_reasons else 0),
        polar_seam=(failure_reasons.polar_seam if failure_reasons else 0),
        terminal_exhausted=terminal_failures,
    )

    # Analyze topology structure for user-facing insights
    intra_per_sat: list[int] = []
    cross_per_sat: list[int] = []
    for _nid, _assignments in _by_node.items():
        intra = sum(1 for a in _assignments if a.link_type == "intra_plane_isl")
        cross = sum(1 for a in _assignments if a.link_type == "cross_plane_isl")
        intra_per_sat.append(intra)
        cross_per_sat.append(cross)

    has_cross_plane = any(c > 0 for c in cross_per_sat)
    max_cross = max(cross_per_sat) if cross_per_sat else 0

    # Generate warnings
    warnings = _generate_insights(
        sim_min=sim_min,
        max_gap=max_gap,
        per_station=per_station,
        failure_reasons=merged_reasons,
        has_cross_plane=has_cross_plane,
        max_cross_per_sat=max_cross,
        plane_count=plane_count,
        isl_terminal_count=isl_terminal_count,
    )

    return CoveragePreviewResult(
        orbital_period_s=round(period, 1),
        preview_step_s=_PREVIEW_STEP_SECONDS,
        isl=IslPreview(
            total_possible=total_isl_pairs,
            formed_at_least_once=formed,
            never_formed=never,
            feasibility_pct=round(feasibility_pct, 1),
            min_active=min_isl,
            max_active=max_isl,
            failure_reasons=merged_reasons,
        ),
        ground_stations=GsPreview(
            per_station=per_station,
            simultaneous_min=sim_min,
            simultaneous_max=sim_max,
            simultaneous_mean=round(sim_mean, 2),
            max_gap_s=round(max_gap, 1),
        ),
        warnings=warnings,
    )


def _generate_insights(
    sim_min: int,
    max_gap: float,
    per_station: dict[str, GsStationPreview],
    failure_reasons: IslFailureBreakdown | None,
    has_cross_plane: bool,
    max_cross_per_sat: int,
    plane_count: int,
    isl_terminal_count: int,
) -> list[str]:
    """Generate user-facing insights about this constellation + terminal combination.

    Answers: does this work, why not, and what should I change?
    No jargon, no spec references, actionable recommendations.
    """
    insights: list[str] = []

    # --- Satellite link topology ---

    if plane_count > 1 and not has_cross_plane:
        insights.append(
            f"Each satellite has {isl_terminal_count} ISL terminal(s), which are all "
            f"used for links within the same orbital plane. With {plane_count} planes, "
            f"there are no connections between planes — each plane is an isolated ring. "
            f"Traffic between planes can only route through ground stations. "
            f"To connect planes directly, choose a satellite type with more ISL terminals."
        )
    elif plane_count > 1 and max_cross_per_sat == 1:
        insights.append(
            "Each satellite connects to both neighbors in its own plane plus one "
            "neighboring plane. With only one cross-plane link per satellite, there is "
            "no redundancy between planes — if that single link drops, the plane is "
            "isolated until it reforms. A satellite type with 4+ ISL terminals would "
            "provide connections to both adjacent planes."
        )

    # --- ISL link intermittency (from physics scan) ---

    if failure_reasons:
        total = (
            failure_reasons.range_exceeded
            + failure_reasons.tracking_exceeded
            + failure_reasons.los_blocked
            + failure_reasons.polar_seam
            + failure_reasons.field_of_regard
        )
        if total > 0:
            parts: list[str] = []
            if failure_reasons.polar_seam > 0:
                parts.append(
                    "cross-plane links drop at polar latitudes where a hard cutoff "
                    'disables them — this is the "polar seam" effect typical of '
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
                    "cannot see each other — this is normal geometry"
                )
            if failure_reasons.field_of_regard > 0:
                parts.append(
                    "some neighbors are outside the terminal's pointing cone. "
                    "A wider field of regard would help"
                )

            if parts:
                intro = "Some links are intermittent during the orbital period: "
                insights.append(intro + "; ".join(parts) + ".")

    # --- Ground station connectivity ---

    if sim_min == 0:
        if max_gap > 600:
            insights.append(
                f"There are gaps of up to {max_gap / 60:.0f} minutes with no ground "
                f"station connectivity at all. During these windows, all traffic stays "
                f"in orbit with no path to the ground. Adding more ground stations at "
                f"different longitudes would reduce or eliminate these gaps."
            )
        else:
            insights.append(
                "There are brief periods with no ground station connectivity. "
                "Routing protocols will reconverge when links restore. Adding "
                "ground stations at different longitudes would help."
            )
    elif max_gap > 300:
        insights.append(
            f"The longest gap at any single ground station is "
            f"{max_gap / 60:.0f} minutes. Overall constellation coverage is good, "
            f"but individual stations experience intermittent connectivity."
        )

    # --- Per-station diagnostics ---
    for name, stats in per_station.items():
        if stats.reason:
            insights.append(f"{name}: {stats.reason}")

    return insights


def _resolve_constellation_path(source: str) -> str:
    """Resolve a constellation source string to a file path.

    Tries in order: direct path, configs/constellations/{source}.yaml.
    """
    if Path(source).exists():
        return source
    candidate = Path("configs/constellations") / f"{source}.yaml"
    if candidate.exists():
        return str(candidate)
    # Try as a preset name
    preset_path = Path("configs/presets/constellations") / f"{source}.yaml"
    if preset_path.exists():
        data = yaml.safe_load(preset_path.read_text())
        return data.get("constellation", source)
    raise FileNotFoundError(f"Cannot resolve constellation: {source}")


def _resolve_gs_path(source: str) -> str:
    """Resolve a ground station source string to a file path or set name."""
    if Path(source).exists():
        return source
    # Try as a set name
    candidate = Path("configs/ground-stations/sets") / f"{source}.yaml"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(f"Cannot resolve ground stations: {source}")
