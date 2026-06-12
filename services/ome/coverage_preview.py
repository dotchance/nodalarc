# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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
from typing import Any

from nodalarc.catalog_paths import CatalogRoots
from nodalarc.constellation_loader import (
    isl_terminal_for_interface,
)
from nodalarc.models.addressing import (
    neighbors_by_node,
    topology_summary,
    unique_isl_pairs,
)
from nodalarc.models.coverage import (
    CoveragePreviewResult,
    GsPreview,
    GsStationPreview,
    IslFailureBreakdown,
    IslPreview,
)
from nodalarc.models.resolved_session import SourceContext
from nodalarc.ome_inputs import build_ome_inputs_from_resolved
from nodalarc.propagator import (
    propagate_j2_mean_elements_for_body,
    propagate_keplerian_for_body,
    propagate_sgp4_tle,
)
from nodalarc.resolve_session import resolve_session_with_assets

from ome.coverage_insights import (
    describe_gs_coverage,
    describe_sampled_gs_coverage,
    generate_insights,
)
from ome.event_stream import precompute_timeline_window
from ome.visibility import check_isl_visibility

log = logging.getLogger(__name__)

_PREVIEW_STEP_SECONDS = 10
_FAILURE_SCAN_SAMPLES = 10


def _preview_segment_session(
    *,
    constellation_source: str | dict,
    ground_stations_source: str | dict,
) -> dict:
    preview_ground = {
        "selection_policy": {"highest_elevation": {}},
        "handover_policy": {"hard_release": {}},
        "handover_mode": "bbm",
        "mbb_overlap_ticks": 0,
        "mbb_reserve": 0,
    }
    return {
        "session": {"name": "coverage-preview"},
        "segments": [
            {
                "id": "space",
                "source": constellation_source,
            },
            {
                "id": "ground",
                "placement": {"from_site_set": ground_stations_source},
                "apply": {"scheduling": preview_ground},
            },
        ],
        "link_rules": [
            {
                "id": "ground-access",
                "topology": {"mode": "visible_candidates"},
                "endpoints": [
                    {
                        "select": {"segment": "ground"},
                        "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                        "min_elevation_deg": 10,
                    },
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                    },
                ],
            },
            {
                "id": "space-isl",
                "topology": {"mode": "nearest_n", "n": 1},
                "endpoints": [
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
                    },
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
                    },
                ],
            },
        ],
        "addressing": {
            "loopbacks": [
                {
                    "id": "space-loopbacks-v4",
                    "applies_to": {"segment": "space"},
                    "ipv4_pool": "10.0.0.0/16",
                    "prefix_length": 32,
                    "allocation": "by_node_order",
                },
                {
                    "id": "space-loopbacks-v6",
                    "applies_to": {"segment": "space"},
                    "ipv6_pool": "fd00::/64",
                    "prefix_length": 128,
                    "allocation": "by_node_order",
                },
            ]
        },
        "simulation": {
            "candidate_limits": {
                "max_pairs_per_rule": 100000,
                "max_pairs_per_tick": 100000,
            },
        },
    }


def compute_coverage_preview(
    constellation_source: str | dict | None,
    satellite_type_override: str | None,
    ground_stations_source: str | list[str] | dict | None,
    *,
    catalog_roots: CatalogRoots | None = None,
) -> CoveragePreviewResult:
    """Compute coverage statistics through the segment-session resolver.

    The browser still supplies wizard-style preview selections, but this function
    immediately assembles a segment-session and resolves it through the same
    semantic path used by deploy/upload. Coverage preview must not maintain a
    second runtime view of constellation + ground station truth.
    """
    t0 = time.monotonic()

    if constellation_source is None:
        raise ValueError("constellation is required for coverage preview")
    if ground_stations_source is None:
        raise ValueError("ground_stations is required for coverage preview")
    if satellite_type_override is not None:
        # Same composition as session generation: the constellation's geometry
        # flown by the chosen node primitive, resolved through the same path.
        from nodalarc.session_generator import merge_constellation_with_satellite_type

        constellation_source = merge_constellation_with_satellite_type(
            constellation_source,
            satellite_type_override,
            catalog_roots,
        )
    if isinstance(ground_stations_source, list):
        raise ValueError(
            "coverage preview requires a site_set catalog reference or inline site_set"
        )

    session_dict = _preview_segment_session(
        constellation_source=constellation_source,
        ground_stations_source=ground_stations_source,
    )
    resolution = resolve_session_with_assets(
        session_dict,
        catalog_roots=catalog_roots,
        source_context=SourceContext(origin="coverage_preview"),
    )

    runtime = build_ome_inputs_from_resolved(resolution.resolved)
    gs_file = runtime.gs_file
    satellites = list(runtime.satellites)
    if not satellites:
        raise ValueError("No satellites in constellation")

    addressing = runtime.addressing
    neighbors = runtime.neighbors

    first_body = satellites[0].central_body
    try:
        first_body_frame = runtime.body_frames[first_body]
    except KeyError as exc:
        raise ValueError(
            f"coverage preview is missing resolved body primitive facts for {first_body!r}"
        ) from exc
    first_alt = satellites[0].elements.semi_major_axis_km - first_body_frame.equatorial_radius_km
    period = runtime.period

    vis_params = {
        "polar_seam_enabled": False,
        "latitude_threshold_deg": 70.0,
    }
    ground_scheduling = runtime.ground_scheduling

    window = precompute_timeline_window(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=0.0,
        duration_s=period,
        propagator_id=runtime.propagator_id,
        step_seconds=_PREVIEW_STEP_SECONDS,
        ground_scheduling=ground_scheduling,
        ground_link_model=runtime.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=runtime.ground_candidate_satellites_by_gs,
        body_frames=runtime.body_frames,
        body_ephemeris=runtime.body_ephemeris,
        active_bodies=runtime.active_bodies,
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
        runtime.propagator_id,
        runtime.body_frames,
        vis_params,
    )

    # Extract constellation geometry for insights
    inclination_deg = 0.0
    altitude_km = first_alt
    max_eccentricity = max((sat.elements.eccentricity for sat in satellites), default=0.0)
    plane_count = len({(sat.segment_id or "space", sat.local_plane) for sat in satellites}) or 1
    if satellites:
        import math

        inclination_deg = math.degrees(satellites[0].elements.inclination_rad)

    isl_terminal_count = satellites[0].isl_terminal_count if satellites else 4
    topo = topology_summary(neighbors)
    all_pairs = unique_isl_pairs(neighbors)

    # Analyze timeline events
    isl_stats, gs_stats = _count_events(events, neighbors, gs_file, addressing, period)

    # Compute per-GS diagnostics
    per_station = _build_gs_previews(
        gs_file,
        gs_stats,
        addressing,
        inclination_deg,
        altitude_km,
        first_body_frame.equatorial_radius_km,
        max_eccentricity,
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


def _count_events(events, neighbors, gs_file, addressing, period: float) -> tuple[dict, dict]:
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
            gs_coverage_steps[addressing.gs_id(station.name)] = 0

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


def _build_gs_previews(
    gs_file,
    gs_stats,
    addressing,
    inclination_deg,
    altitude_km,
    body_radius_km,
    max_eccentricity,
):
    """Build per-station preview with physics-based descriptions for every station."""
    per_station: dict[str, GsStationPreview] = {}
    if not gs_file:
        return per_station

    total_steps = gs_stats["total_steps"]
    coverage_steps = gs_stats["coverage_steps"]
    default_min_elev = gs_file.default_min_elevation_deg

    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        steps_connected = coverage_steps.get(gs_id, 0)
        coverage_pct = (steps_connected / total_steps) * 100.0 if total_steps > 0 else 0.0
        gap_steps = total_steps - steps_connected
        longest_gap_s = gap_steps * _PREVIEW_STEP_SECONDS if gap_steps > 0 else 0.0
        min_elev = (
            station.min_elevation_deg if station.min_elevation_deg is not None else default_min_elev
        )
        if max_eccentricity > 1e-6:
            reason = describe_sampled_gs_coverage(
                station_name=station.name,
                coverage_pct=coverage_pct,
                longest_gap_s=longest_gap_s,
                min_elevation_deg=min_elev,
                orbit_family="eccentric orbit",
            )
        else:
            reason = describe_gs_coverage(
                station_name=station.name,
                station_lat=station.lat_deg,
                inclination_deg=inclination_deg,
                altitude_km=altitude_km,
                body_radius_km=body_radius_km,
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
    propagator_id,
    body_frames,
    vis_params,
) -> tuple[IslFailureBreakdown, set[tuple[str, str]]]:
    """Sample timesteps and count WHY ISLs fail to form.

    Returns (failure_breakdown, pairs_ever_feasible).
    """
    by_node = neighbors_by_node(neighbors)
    reason_counts: dict[str, int] = defaultdict(int)
    pairs_ever_feasible: set[tuple[str, str]] = set()

    sat_map: dict[str, Any] = {}
    for sat in satellites:
        if sat.node_id is None:
            raise ValueError("coverage preview requires resolver-owned satellite node_id")
        sat_map[sat.node_id] = sat
    sample_times = [period * i / _FAILURE_SCAN_SAMPLES for i in range(_FAILURE_SCAN_SAMPLES)]

    seam_enabled = vis_params["polar_seam_enabled"]
    lat_threshold = vis_params["latitude_threshold_deg"]

    for dt in sample_times:
        positions = {}
        for nid, sat in sat_map.items():
            central_body = sat.central_body
            try:
                body_frame = body_frames[central_body]
            except KeyError as exc:
                raise ValueError(
                    f"coverage preview is missing resolved body primitive facts for "
                    f"central_body={central_body!r} while scanning {nid!r}"
                ) from exc
            sat_propagator_id = getattr(sat, "propagator_id", None) or propagator_id
            if sat_propagator_id == "mixed":
                raise ValueError(
                    f"coverage preview mixed propagation requires propagator_id on {nid!r}"
                )
            if sat_propagator_id in ("two-body", "keplerian-circular"):
                positions[nid] = propagate_keplerian_for_body(
                    sat.elements,
                    epoch_unix,
                    dt,
                    body_frame=body_frame,
                )[:3]
            elif sat_propagator_id == "j2-mean-elements":
                positions[nid] = propagate_j2_mean_elements_for_body(
                    sat.elements,
                    epoch_unix,
                    dt,
                    body_frame=body_frame,
                )[:3]
            elif sat_propagator_id == "sgp4-tle":
                if central_body != "earth":
                    raise ValueError(
                        f"coverage preview SGP4/TLE scan is Earth-only; {nid!r} uses "
                        f"central_body={central_body!r}"
                    )
                if sat.tle_line_1 is None or sat.tle_line_2 is None:
                    raise ValueError(
                        f"coverage preview SGP4/TLE scan requires TLE lines for {nid!r}"
                    )
                positions[nid] = propagate_sgp4_tle(
                    sat.tle_line_1,
                    sat.tle_line_2,
                    epoch_unix,
                    dt,
                    body_frame=body_frame,
                )
            else:
                raise ValueError(
                    f"coverage preview does not support propagator {sat_propagator_id!r} "
                    f"for {nid!r}"
                )

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

                reverse = next(
                    (
                        assignment
                        for assignment in by_node.get(na.peer_node_id, ())
                        if assignment.peer_node_id == nid
                    ),
                    None,
                )
                if reverse is None:
                    raise ValueError(
                        "Coverage preview cannot evaluate ISL feasibility for "
                        f"{pair}: missing reverse neighbor assignment"
                    )
                term_a = isl_terminal_for_interface(sat_map[nid].isl_terminals, na.interface)
                term_b = isl_terminal_for_interface(
                    sat_map[na.peer_node_id].isl_terminals,
                    reverse.interface,
                )
                if str(term_a.type) != str(term_b.type):
                    raise ValueError(
                        "Coverage preview cannot represent mixed terminal-type ISL "
                        f"{pair}: {term_a.type!r} vs {term_b.type!r}"
                    )
                max_range = min(float(term_a.max_range_km), float(term_b.max_range_km))
                max_tracking = min(
                    float(term_a.max_tracking_rate_deg_s),
                    float(term_b.max_tracking_rate_deg_s),
                )
                fov = min(float(term_a.field_of_regard_deg), float(term_b.field_of_regard_deg))
                is_cross = na.link_type == "cross_plane_isl"
                result = check_isl_visibility(
                    pos_a,
                    vel_a,
                    pos_b,
                    vel_b,
                    max_range_km=max_range,
                    max_tracking_rate_deg_s=max_tracking if is_cross else None,
                    field_of_regard_deg=fov,
                    body_frame=body_frames[sat_map[nid].central_body],
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
