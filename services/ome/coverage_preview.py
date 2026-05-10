# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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
from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.addressing import (
    AddressingScheme,
    assign_isl_neighbors,
    neighbors_by_node,
    topology_summary,
    unique_isl_pairs,
)
from nodalarc.models.constellation import ParametricConstellation
from nodalarc.models.coverage import (
    CoveragePreviewResult,
    GsPreview,
    GsStationPreview,
    IslFailureBreakdown,
    IslPreview,
)
from nodalarc.models.session import AddressingConfig
from nodalarc.propagator import orbital_period, propagate_keplerian
from nodalarc.session_generator import merge_constellation_with_satellite_type

from ome.coverage_insights import describe_gs_coverage, generate_insights
from ome.event_stream import precompute_timeline_window
from ome.visibility import check_isl_visibility

log = logging.getLogger(__name__)

_PREVIEW_STEP_SECONDS = 10
_FAILURE_SCAN_SAMPLES = 10


def compute_coverage_preview(
    constellation_source: str | dict | None,
    satellite_type_override: str | None,
    ground_stations_source: str | list[str] | dict | None,
) -> CoveragePreviewResult:
    """Compute coverage statistics for a constellation + GS combination.

    Returns CoveragePreviewResult with ISL/GS statistics and user-facing insights.
    """
    t0 = time.monotonic()

    if constellation_source is None:
        raise ValueError("constellation is required for coverage preview")
    if ground_stations_source is None:
        raise ValueError("ground_stations is required for coverage preview")

    # --- Resolve constellation (with optional satellite type override) ---
    constellation = _load_constellation(constellation_source, satellite_type_override)

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
    vis_params = _extract_visibility_params(constellation, gs_file)

    # Run timeline computation
    window = precompute_timeline_window(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=0.0,
        duration_s=period,
        step_seconds=_PREVIEW_STEP_SECONDS,
        **vis_params,
    )
    events = window.events

    elapsed = time.monotonic() - t0
    log.info(
        "Coverage preview: %d sats, %d GS, %.1fs period, %d events in %.1fs",
        len(satellites),
        len(gs_file.stations) if gs_file else 0,
        period,
        len(events),
        elapsed,
    )

    # Scan ISL failure reasons (fast: 10 sample timesteps)
    failure_reasons, pairs_ever_feasible = _scan_isl_failure_reasons(
        satellites,
        addressing,
        neighbors,
        0.0,
        period,
        vis_params,
    )

    # Extract constellation geometry for insights
    inclination_deg = 0.0
    altitude_km = first_alt
    plane_count = 1
    if isinstance(constellation, ParametricConstellation):
        inclination_deg = constellation.orbit.inclination_deg
        plane_count = constellation.planes.count

    isl_terminal_count = satellites[0].isl_terminal_count if satellites else 4
    topo = topology_summary(neighbors)
    all_pairs = unique_isl_pairs(neighbors)

    # Analyze timeline events
    isl_stats, gs_stats = _count_events(events, neighbors, gs_file, period)

    # Compute per-GS diagnostics
    per_station = _build_gs_previews(
        gs_file,
        gs_stats,
        inclination_deg,
        altitude_km,
    )

    # Compute ISL failure breakdown (merge scan + event data)
    feasible_never_scheduled = pairs_ever_feasible - isl_stats["ever_formed"]
    merged_reasons = IslFailureBreakdown(
        range_exceeded=failure_reasons.range_exceeded,
        tracking_exceeded=failure_reasons.tracking_exceeded,
        field_of_regard=failure_reasons.field_of_regard,
        los_blocked=failure_reasons.los_blocked,
        polar_seam=failure_reasons.polar_seam,
        terminal_exhausted=len(feasible_never_scheduled),
    )

    # Generate user-facing insights
    warnings = generate_insights(
        sim_min=gs_stats["sim_min"],
        max_gap=gs_stats["max_gap"],
        per_station=per_station,
        failure_reasons=merged_reasons,
        has_cross_plane=topo["has_cross_plane"],
        max_cross_per_sat=topo["max_cross_per_sat"],
        plane_count=plane_count,
        isl_terminal_count=isl_terminal_count,
    )

    total_pairs = len(all_pairs)
    formed = len(isl_stats["ever_formed"])
    return CoveragePreviewResult(
        orbital_period_s=round(period, 1),
        preview_step_s=_PREVIEW_STEP_SECONDS,
        isl=IslPreview(
            total_possible=total_pairs,
            formed_at_least_once=formed,
            never_formed=total_pairs - formed,
            feasibility_pct=round(formed / total_pairs * 100.0 if total_pairs > 0 else 100.0, 1),
            min_active=isl_stats["min_active"],
            max_active=isl_stats["max_active"],
            failure_reasons=merged_reasons,
        ),
        ground_stations=GsPreview(
            per_station=per_station,
            simultaneous_min=gs_stats["sim_min"],
            simultaneous_max=gs_stats["sim_max"],
            simultaneous_mean=round(gs_stats["sim_mean"], 2),
            max_gap_s=round(gs_stats["max_gap"], 1),
        ),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_constellation(source, satellite_type_override):
    """Load constellation with optional satellite type override."""
    if isinstance(source, dict):
        if satellite_type_override:
            source = dict(source)
            source["satellite_type"] = satellite_type_override
            source.pop("default_terminals", None)
        return load_constellation(source)

    if isinstance(source, str):
        path = _resolve_constellation_path(source)
        if satellite_type_override:
            merged = merge_constellation_with_satellite_type(path, satellite_type_override)
            return load_constellation(merged)
        return load_constellation(path)

    raise ValueError(f"Invalid constellation source type: {type(source)}")


def _extract_visibility_params(constellation, gs_file) -> dict:
    """Extract OME visibility parameters from resolved constellation."""
    params = {
        "max_range_km": 5016.0,
        "max_tracking_rate_deg_s": 3.0,
        "field_of_regard_deg": 360.0,
        "polar_seam_enabled": False,
        "latitude_threshold_deg": 70.0,
        "default_min_elevation_deg": 25.0,
    }
    if isinstance(constellation, ParametricConstellation):
        if constellation.default_terminals and constellation.default_terminals.isl:
            isl = constellation.default_terminals.isl[0]
            params["max_range_km"] = isl.max_range_km
            params["max_tracking_rate_deg_s"] = isl.max_tracking_rate_deg_s
            params["field_of_regard_deg"] = isl.field_of_regard_deg
        if constellation.polar_seam:
            params["polar_seam_enabled"] = constellation.polar_seam.enabled
            params["latitude_threshold_deg"] = constellation.polar_seam.latitude_threshold_deg
    if gs_file and gs_file.default_min_elevation_deg:
        params["default_min_elevation_deg"] = gs_file.default_min_elevation_deg
    return params


def _count_events(events, neighbors, gs_file, period: float) -> tuple[dict, dict]:
    """Count ISL and GS statistics from timeline events.

    Returns (isl_stats, gs_stats) dicts.
    """
    isl_active: dict[tuple[str, str], bool] = {}
    isl_ever_formed: set[tuple[str, str]] = set()
    isl_counts_per_step: list[int] = []

    gs_active: dict[str, set[str]] = defaultdict(set)
    gs_connected_per_step: list[int] = []
    gs_coverage_steps: dict[str, int] = defaultdict(int)

    if gs_file:
        for station in gs_file.stations:
            gs_coverage_steps[f"gs-{station.name}"] = 0

    total_steps = 0

    for event in events:
        if event.event_type == "VisibilityEvent":
            vis = event.data
            key = (vis.node_a, vis.node_b)
            is_gs = vis.link_type == "ground"

            if is_gs:
                gs_id, sat_id = vis.node_a, vis.node_b
                if vis.visible and vis.scheduled:
                    gs_active[gs_id].add(sat_id)
                else:
                    gs_active[gs_id].discard(sat_id)
            else:
                if vis.visible and vis.scheduled:
                    isl_active[key] = True
                    isl_ever_formed.add(key)
                else:
                    isl_active[key] = False

        elif event.event_type == "ClockTick":
            total_steps += 1
            isl_counts_per_step.append(sum(1 for v in isl_active.values() if v))
            connected_gs = sum(1 for sats in gs_active.values() if len(sats) > 0)
            gs_connected_per_step.append(connected_gs)
            for gs_id in gs_coverage_steps:
                if len(gs_active.get(gs_id, set())) > 0:
                    gs_coverage_steps[gs_id] += 1

    if total_steps == 0:
        total_steps = max(1, int(period / _PREVIEW_STEP_SECONDS))

    sim_min = min(gs_connected_per_step) if gs_connected_per_step else 0
    sim_max = max(gs_connected_per_step) if gs_connected_per_step else 0
    sim_mean = (
        sum(gs_connected_per_step) / len(gs_connected_per_step) if gs_connected_per_step else 0.0
    )

    isl_stats = {
        "ever_formed": isl_ever_formed,
        "min_active": min(isl_counts_per_step) if isl_counts_per_step else 0,
        "max_active": max(isl_counts_per_step) if isl_counts_per_step else 0,
    }
    gs_stats = {
        "sim_min": sim_min,
        "sim_max": sim_max,
        "sim_mean": sim_mean,
        "max_gap": 0.0,
        "coverage_steps": gs_coverage_steps,
        "total_steps": total_steps,
    }
    return isl_stats, gs_stats


def _build_gs_previews(gs_file, gs_stats, inclination_deg, altitude_km):
    """Build per-station preview with physics-based descriptions for every station."""
    per_station: dict[str, GsStationPreview] = {}
    if not gs_file:
        return per_station

    total_steps = gs_stats["total_steps"]
    coverage_steps = gs_stats["coverage_steps"]
    default_min_elev = gs_file.default_min_elevation_deg or 25.0

    for station in gs_file.stations:
        gs_id = f"gs-{station.name}"
        steps_connected = coverage_steps.get(gs_id, 0)
        coverage_pct = (steps_connected / total_steps) * 100.0 if total_steps > 0 else 0.0
        gap_steps = total_steps - steps_connected
        longest_gap_s = gap_steps * _PREVIEW_STEP_SECONDS if gap_steps > 0 else 0.0
        min_elev = (
            station.min_elevation_deg if station.min_elevation_deg is not None else default_min_elev
        )
        reason = describe_gs_coverage(
            station_name=station.name,
            station_lat=station.lat_deg,
            inclination_deg=inclination_deg,
            altitude_km=altitude_km,
            coverage_pct=coverage_pct,
            longest_gap_s=longest_gap_s,
            min_elevation_deg=min_elev,
        )
        per_station[station.name] = GsStationPreview(
            coverage_pct=round(coverage_pct, 1),
            longest_gap_s=round(longest_gap_s, 1),
            reason=reason,
        )

    gs_stats["max_gap"] = max((s.longest_gap_s for s in per_station.values()), default=0.0)
    return per_station


def _scan_isl_failure_reasons(
    satellites,
    addressing,
    neighbors,
    epoch_unix,
    period,
    vis_params,
) -> tuple[IslFailureBreakdown, set[tuple[str, str]]]:
    """Sample timesteps and count WHY ISLs fail to form.

    Returns (failure_breakdown, pairs_ever_feasible).
    """
    by_node = neighbors_by_node(neighbors)
    reason_counts: dict[str, int] = defaultdict(int)
    pairs_ever_feasible: set[tuple[str, str]] = set()

    sat_map = {addressing.sat_id(s.plane, s.slot): s for s in satellites}
    sample_times = [period * i / _FAILURE_SCAN_SAMPLES for i in range(_FAILURE_SCAN_SAMPLES)]

    max_range = vis_params["max_range_km"]
    max_tracking = vis_params["max_tracking_rate_deg_s"]
    fov = vis_params["field_of_regard_deg"]
    seam_enabled = vis_params["polar_seam_enabled"]
    lat_threshold = vis_params["latitude_threshold_deg"]

    for dt in sample_times:
        positions = {
            nid: propagate_keplerian(sat.elements, epoch_unix, dt) for nid, sat in sat_map.items()
        }

        seen: set[tuple[str, str]] = set()
        for nid, assignments in by_node.items():
            for na in assignments:
                pair = (min(nid, na.peer_node_id), max(nid, na.peer_node_id))
                if pair in seen:
                    continue
                seen.add(pair)

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
                    max_range_km=max_range,
                    max_tracking_rate_deg_s=max_tracking if is_cross else None,
                    field_of_regard_deg=fov,
                    polar_seam_enabled=seam_enabled and is_cross,
                    latitude_threshold_deg=lat_threshold,
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


def _resolve_constellation_path(source: str) -> str:
    """Resolve constellation source string to a file path."""
    if Path(source).exists():
        return source
    candidate = Path("configs/constellations") / f"{source}.yaml"
    if candidate.exists():
        return str(candidate)
    preset_path = Path("configs/presets/constellations") / f"{source}.yaml"
    if preset_path.exists():
        data = yaml.safe_load(preset_path.read_text())
        return data.get("constellation", source)
    raise FileNotFoundError(f"Cannot resolve constellation: {source}")


def _resolve_gs_path(source: str) -> str:
    """Resolve ground station source string to a file path."""
    if Path(source).exists():
        return source
    candidate = Path("configs/ground-stations/sets") / f"{source}.yaml"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(f"Cannot resolve ground stations: {source}")
