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
    IslPreview,
)
from nodalarc.models.session import AddressingConfig
from ome.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from ome.event_stream import precompute_timeline_window
from ome.propagator import orbital_period

log = logging.getLogger(__name__)

_PREVIEW_STEP_SECONDS = 10


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

    # --- Analyze events ---
    return _analyze_events(
        events=events,
        neighbors=neighbors,
        gs_file=gs_file,
        addressing=addressing,
        period=period,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
    )


def _analyze_events(
    events,
    neighbors,
    gs_file,
    addressing,
    period: float,
    max_range_km: float,
    max_tracking_rate_deg_s: float,
) -> CoveragePreviewResult:
    """Analyze timeline events into coverage statistics.

    TimelineEvent objects have event_type (str) and data (Pydantic model).
    VisibilityEvent data has: node_a, node_b, visible, scheduled, range_km,
    elevation_deg, terminal_type.
    """
    # Count total possible ISL pairs from neighbor assignment
    total_isl_pairs = len(neighbors)

    # Track ISL state per timestep
    isl_active: dict[tuple[str, str], bool] = {}
    isl_ever_formed: set[tuple[str, str]] = set()
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
                if vis.visible and vis.scheduled:
                    isl_active[key] = True
                    isl_ever_formed.add(key)
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
            # Approximate longest gap from coverage percentage
            gap_steps = total_steps - steps_connected
            longest_gap_s = gap_steps * _PREVIEW_STEP_SECONDS if gap_steps > 0 else 0.0
            per_station[station.name] = GsStationPreview(
                coverage_pct=round(coverage_pct, 1),
                longest_gap_s=round(longest_gap_s, 1),
            )

    # ISL stats
    formed = len(isl_ever_formed)
    never = total_isl_pairs - formed
    feasibility_pct = (formed / total_isl_pairs * 100.0) if total_isl_pairs > 0 else 100.0

    min_isl = min(isl_counts_per_step) if isl_counts_per_step else 0
    max_isl = max(isl_counts_per_step) if isl_counts_per_step else 0

    # GS simultaneous stats
    sim_min = min(gs_connected_per_step) if gs_connected_per_step else 0
    sim_max = max(gs_connected_per_step) if gs_connected_per_step else 0
    sim_mean = (
        sum(gs_connected_per_step) / len(gs_connected_per_step) if gs_connected_per_step else 0.0
    )
    max_gap = max((s.longest_gap_s for s in per_station.values()), default=0.0)

    # Generate warnings
    warnings = _generate_warnings(
        feasibility_pct=feasibility_pct,
        never_formed=never,
        total_possible=total_isl_pairs,
        sim_min=sim_min,
        max_gap=max_gap,
        per_station=per_station,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
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


def _generate_warnings(
    feasibility_pct: float,
    never_formed: int,
    total_possible: int,
    sim_min: int,
    max_gap: float,
    per_station: dict[str, GsStationPreview],
    max_range_km: float,
    max_tracking_rate_deg_s: float,
) -> list[str]:
    """Generate plain-English warnings for anomalous combinations."""
    warnings: list[str] = []

    if feasibility_pct < 70:
        warnings.append(
            f"Only {feasibility_pct:.0f}% of geometrically possible ISLs are feasible "
            f"— the selected terminal range ({max_range_km:.0f}km) or tracking rate "
            f"({max_tracking_rate_deg_s} deg/s) may be insufficient for this orbital geometry"
        )

    if never_formed > total_possible * 0.5 and total_possible > 0:
        warnings.append(
            f"{never_formed} of {total_possible} possible ISLs never form "
            f"— consider increasing terminal range or tracking rate"
        )

    if sim_min == 0:
        warnings.append(
            "There are periods with zero ground station connectivity "
            "— satellites will have no ground path during these windows"
        )

    if max_gap > 300:
        warnings.append(
            f"Largest ground connectivity gap is {max_gap:.0f}s ({max_gap / 60:.0f} minutes) "
            f"— routing protocols will need to reconverge when connectivity resumes"
        )

    for name, stats in per_station.items():
        if stats.coverage_pct < 10:
            warnings.append(
                f"Ground station '{name}' has only {stats.coverage_pct:.0f}% coverage "
                f"— it may be too far from the orbital track"
            )

    return warnings


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
