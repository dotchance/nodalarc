# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Event stream — precompute timeline and write/publish events.

Propagates all satellites, computes visibility at each step,
emits ClockTick + TimelinePositionSnapshot every step,
emits VisibilityEvents on state changes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from nodalarc.body_frames import body_frame_for
from nodalarc.constellation_loader import (
    SatelliteNode,
    isl_terminal_for_interface,
    satellite_node_id,
)
from nodalarc.ephemeris_runtime import SkyfieldBspEphemeris, body_states_at
from nodalarc.ground_handover import resolve_station_ground_scheduling
from nodalarc.ground_terminals import (
    TerminalPhysicsProfile,
    ground_terminal_type,
    satellite_terminal_index_pools_by_target_body,
    station_ground_terminal_type,
    terminal_physics_profile,
    terminal_physics_profiles,
)
from nodalarc.models.addressing import AddressingScheme, NeighborAssignment, neighbors_by_node
from nodalarc.models.events import (
    ClockTick,
    EphemerisBodyFrame,
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    EphemerisNodeTLE,
    NodePosition,
    SessionEphemeris,
)
from nodalarc.models.ground_policy import HandoverPolicySpec, RankingComponent, SelectionPolicySpec
from nodalarc.models.ground_station import GroundStationFile, HysteresisParameters
from nodalarc.models.link_decisions import GroundPolicyAudit
from nodalarc.models.session import GroundSchedulingConfig

from ome.event_diff import diff_ground_visibility_events, diff_isl_visibility_events
from ome.ground_allocator import (
    GroundAllocationResult,
    allocate_ground_links,
)
from ome.ground_selection_policies import validate_selection_score_scale_compatibility
from ome.ground_visibility_engine import GroundPassLookahead, evaluate_ground_visibility
from ome.isl_engine import (
    IslFeasibilityResult,
    IslTerminalConstraints,
    ScheduledIsl,
    evaluate_isl_feasibility,
    schedule_isl_links,
)
from ome.propagation_engine import (
    PropagatedState,
    PropagatorId,
    build_node_positions,
    propagate_satellites,
)
from ome.propagator import (
    EcefVec3,
    GeoPosition,
    geodetic_to_ecef,
)
from ome.snapshot_builder import LinkSnapshotSource
from ome.snapshot_builder import build_link_state_snapshot as build_link_state_snapshot
from ome.types import GroundVisibilityDecisionMap, MbbTeardownState

logger = logging.getLogger(__name__)


def build_session_ephemeris(
    ctx: StepContext,
    epoch_unix: float,
    epoch_id: int,
) -> SessionEphemeris:
    """Build SessionEphemeris from session context.

    Maps satellites to the ephemeris node type matching the selected
    propagator and ground stations to EphemerisNodeFixed. Published once per
    epoch to NODALARC_SESSION.
    """
    import math

    from nodalarc.body_frames import body_frame_for

    nodes: dict[str, EphemerisNodeKeplerian | EphemerisNodeTLE | EphemerisNodeFixed] = {}

    def _meta(node_id: str) -> dict[str, object]:
        raw = ctx.node_metadata.get(node_id, {})
        return {
            key: raw[key]
            for key in (
                "segment_id",
                "local_node_id",
                "namespace",
                "tags",
                "reference_body",
                "frame_id",
            )
            if key in raw and raw[key] is not None
        }

    for sat in ctx.satellites:
        node_id = satellite_node_id(sat, ctx.addressing)
        sat_body = getattr(sat, "central_body", "earth")
        body_frame = body_frame_for(sat_body)
        if ctx.propagator_id == "sgp4-tle":
            if sat.tle_line_1 is None or sat.tle_line_2 is None:
                raise ValueError(
                    f"Satellite {node_id} has no TLE lines; cannot build SGP4 ephemeris"
                )
            nodes[node_id] = EphemerisNodeTLE(
                tle_line_1=sat.tle_line_1,
                tle_line_2=sat.tle_line_2,
                plane=sat.plane,
                slot=sat.slot,
                norad_id=sat.norad_id,
                **_meta(node_id),
            )
        else:
            nodes[node_id] = EphemerisNodeKeplerian(
                propagator=ctx.propagator_id,
                altitude_km=sat.elements.semi_major_axis_km - body_frame.equatorial_radius_km,
                inclination_deg=math.degrees(sat.elements.inclination_rad),
                raan_deg=math.degrees(sat.elements.raan_rad),
                true_anomaly_deg=math.degrees(sat.elements.true_anomaly_rad),
                plane=sat.plane,
                slot=sat.slot,
                **_meta(node_id),
            )

    for gs_id, (_ecef, geo) in ctx.gs_positions.items():
        nodes[gs_id] = EphemerisNodeFixed(
            lat_deg=geo.lat_deg,
            lon_deg=geo.lon_deg,
            alt_km=geo.alt_km,
            **_meta(gs_id),
        )

    epoch_body_states = body_states_at(ctx.body_ephemeris, set(ctx.active_bodies), epoch_unix)
    body_frames: dict[str, EphemerisBodyFrame] = {}
    for body_id, body_state in sorted(epoch_body_states.items()):
        body_frame = body_frame_for(body_id)
        body_frames[body_id] = EphemerisBodyFrame(
            body_id=body_id,
            radius_km=body_frame.equatorial_radius_km,
            origin_x_km=body_state.position_km.x,
            origin_y_km=body_state.position_km.y,
            origin_z_km=body_state.position_km.z,
            vel_x_km_s=body_state.velocity_km_s.x,
            vel_y_km_s=body_state.velocity_km_s.y,
            vel_z_km_s=body_state.velocity_km_s.z,
            provider=body_state.provider,
            kernel_id=body_state.kernel_id,
            quality_tier=body_state.quality_tier,
            frame=body_state.frame,
        )

    return SessionEphemeris(
        epoch_id=epoch_id,
        sim_time=datetime.fromtimestamp(epoch_unix, tz=UTC),
        epoch_unix=epoch_unix,
        nodes=nodes,
        body_frames=body_frames,
    )


class TimelineEvent:
    """A single event in the precomputed timeline."""

    __slots__ = ("timestamp_s", "event_type", "data")

    def __init__(self, timestamp_s: float, event_type: str, data: Any) -> None:
        self.timestamp_s = timestamp_s
        self.event_type = event_type
        self.data = data


# ---------------------------------------------------------------------------
# Per-step computation — extracted for real-time stepped emission
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StepContext:
    """Session-constant arguments for compute_step(). Built once, reused every step."""

    satellites: list[SatelliteNode]
    addressing: AddressingScheme
    gs_positions: dict[str, tuple[EcefVec3, GeoPosition]]
    gs_min_elevations: dict[str, float]
    gs_terminal_counts: dict[str, int]
    gs_selection_policies: dict[str, SelectionPolicySpec]
    gs_selection_policy_names: dict[str, str]
    gs_handover_policies: dict[str, HandoverPolicySpec]
    gs_service_priorities: dict[str, int]
    ground_ranking_order: tuple[RankingComponent, ...]
    gs_handover_modes: dict[str, Literal["bbm", "mbb"]]
    gs_mbb_overlap_ticks: dict[str, int]
    gs_mbb_reserve: dict[str, int]
    ground_mbb_preemption: str
    ground_successor_abort_policy: str
    ground_cross_tenant_displacement: str
    ground_bbm_acquire_timeout_ticks: int
    ignored_ground_capacity_fields: tuple[str, ...]
    ground_policy_audit: GroundPolicyAudit
    ground_link_model: Literal["geometry_only", "terminal_physics"]
    gs_terminal_profiles: dict[str, TerminalPhysicsProfile]
    sat_ground_terminal_profiles: dict[str, tuple[TerminalPhysicsProfile, ...]]
    sat_ground_terminal_indices_by_body: dict[str, dict[str, tuple[int, ...]]]
    # Per-GS tenant scope (Direction 2 — multi-tenant from day one) and
    # reference body (Direction 3 — multi-body from day one). Both
    # already exist on GroundStationConfig; the StepContext carries them
    # so every visibility decision is tenant- and body-attributable.
    gs_tenant_ids: dict[str, str]
    gs_reference_bodies: dict[str, str]
    ground_candidate_satellites_by_gs: dict[str, tuple[str, ...]]
    ground_pair_terminal_types: dict[tuple[str, str], str]
    node_metadata: dict[str, dict[str, object]]
    by_node: dict[str, list[NeighborAssignment]]
    sat_isl_terminals: dict[str, int]
    sat_isl_terminal_constraints: dict[str, dict[str, IslTerminalConstraints]]
    sat_ground_terminals: dict[str, int]  # satellite ground terminal capacity
    propagator_id: PropagatorId
    body_ephemeris: SkyfieldBspEphemeris | None = None
    active_bodies: frozenset[str] = field(default_factory=lambda: frozenset({"earth"}))
    polar_seam_enabled: bool = False
    latitude_threshold_deg: float = 70.0
    ground_policy_lookahead_horizon_ticks: int = 0
    ground_policy_lookahead_horizon_ticks_by_gs: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class StepResult:
    """Named result of one OME tick.

    This replaces the former tuple return so callers cannot accidentally mix
    physics output, allocation state, and MBB teardown state by position.

    ``ground_decisions`` carries the typed per-pair visibility decisions
    the OME produced this tick. The OME main loop uses them to build
    ``GroundLinkDecisionSnapshot`` at every snapshot interval — the diagnostic
    companion to ``LinkStateSnapshot``.
    """

    events: list[TimelineEvent]
    positions: dict[str, NodePosition]
    isl_scheduled: dict[tuple[str, str], bool]
    isl_feasibility: dict[tuple[str, str], IslFeasibilityResult]
    isl_links: dict[tuple[str, str], ScheduledIsl]
    ground_allocation: GroundAllocationResult
    ground_decisions: GroundVisibilityDecisionMap
    link_snapshot_source: LinkSnapshotSource
    propagated_states: dict[str, PropagatedState]
    sim_time: datetime
    sim_time_unix: float
    step: int

    @property
    def associations(self) -> dict[tuple[str, str], tuple[int, int]]:
        return self.ground_allocation.associations

    @property
    def pending_teardowns(self) -> MbbTeardownState:
        return self.ground_allocation.pending_teardowns


@dataclass(frozen=True)
class TimelineWindowResult:
    """Named result for precomputed OME windows.

    Lookahead windows are predictive. They may be useful for proactive
    scheduling, but they are not authoritative dispatch state and must not be
    replayed as if they came from the live pacing loop.
    """

    events: list[TimelineEvent]
    isl_state: dict[tuple[str, str], tuple[bool, bool]]
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]]
    associations: dict[tuple[str, str], tuple[int, int]]
    pending_teardowns: MbbTeardownState
    predictive: bool = False


def _future_capacity_field_paths(terminals: tuple[object, ...], *, base_path: str) -> list[str]:
    paths: list[str] = []
    for idx, terminal in enumerate(terminals):
        for field_name in ("gateway_beam_quota", "user_terminal_beam_quota"):
            if getattr(terminal, field_name, None) is not None:
                paths.append(f"{base_path}[{idx}].{field_name}")
    return paths


def _normalize_handover_policy(policy: HandoverPolicySpec) -> HandoverPolicySpec:
    if policy.name == "none":
        if policy.params:
            raise ValueError("handover_policy.name='none' requires empty params")
        return policy.model_copy(deep=True)
    if policy.name == "hysteresis":
        return HandoverPolicySpec(
            name="hysteresis",
            params=HysteresisParameters(**policy.params).model_dump(),
        )
    raise ValueError(f"Unknown handover_policy.name={policy.name!r}")


def _build_policy_audit(
    *,
    gs_selection_policies: dict[str, SelectionPolicySpec],
    gs_handover_policies: dict[str, HandoverPolicySpec],
    ground_scheduling: GroundSchedulingConfig,
    gs_handover_modes: dict[str, Literal["bbm", "mbb"]],
    gs_mbb_overlap_ticks: dict[str, int],
    gs_mbb_reserve: dict[str, int],
    ignored_capacity_fields: tuple[str, ...],
) -> GroundPolicyAudit:
    return GroundPolicyAudit(
        selection_policies={k: v.name for k, v in sorted(gs_selection_policies.items())},
        selection_policy_params={
            k: dict(v.params) for k, v in sorted(gs_selection_policies.items())
        },
        handover_policies={k: v.name for k, v in sorted(gs_handover_policies.items())},
        handover_policy_params={k: dict(v.params) for k, v in sorted(gs_handover_policies.items())},
        ranking_order=tuple(ground_scheduling.ranking_order),
        handover_mode=(
            next(iter(set(gs_handover_modes.values())))
            if len(set(gs_handover_modes.values())) == 1
            else "mixed"
        ),
        handover_modes=dict(sorted(gs_handover_modes.items())),
        mbb_preemption=ground_scheduling.mbb_preemption,
        successor_abort_policy=ground_scheduling.successor_abort_policy,
        cross_tenant_displacement=ground_scheduling.cross_tenant_displacement,
        mbb_overlap_ticks=max(gs_mbb_overlap_ticks.values(), default=0),
        mbb_overlap_ticks_by_gs=dict(sorted(gs_mbb_overlap_ticks.items())),
        mbb_reserve=max(gs_mbb_reserve.values(), default=0),
        mbb_reserve_by_gs=dict(sorted(gs_mbb_reserve.items())),
        bbm_acquire_timeout_ticks=ground_scheduling.bbm_acquire_timeout_ticks,
        ignored_capacity_fields=ignored_capacity_fields,
    )


def build_step_context(
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    gs_file: GroundStationFile | None,
    neighbors: frozenset[tuple[str, NeighborAssignment]],
    propagator_id: PropagatorId,
    ground_scheduling: GroundSchedulingConfig | None = None,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    ground_link_model: Literal["geometry_only", "terminal_physics"] = "terminal_physics",
    ground_defaults_applied: bool = False,
    ground_candidate_satellites_by_gs: Mapping[str, tuple[str, ...]] | None = None,
    node_metadata: Mapping[str, Mapping[str, object]] | None = None,
    body_ephemeris: SkyfieldBspEphemeris | None = None,
    active_bodies: frozenset[str] | None = None,
) -> StepContext:
    """Build the per-session-constant context for compute_step()."""
    by_node = neighbors_by_node(neighbors)
    node_metadata_map = {node_id: dict(value) for node_id, value in (node_metadata or {}).items()}
    has_ground_stations = gs_file is not None and bool(gs_file.stations)
    if has_ground_stations and ground_scheduling is None:
        raise ValueError(
            "build_step_context requires explicit ground_scheduling when ground stations "
            "exist; NodalArc does not silently choose selection or handover policy"
        )
    ground_scheduling = ground_scheduling or GroundSchedulingConfig()
    sat_isl_terminals: dict[str, int] = {}
    sat_isl_terminal_constraints: dict[str, dict[str, IslTerminalConstraints]] = {}
    sat_ground_terminals: dict[str, int] = {}
    sat_ground_terminal_types: dict[str, str] = {}
    sat_ground_terminal_profiles: dict[str, tuple[TerminalPhysicsProfile, ...]] = {}
    sat_ground_terminal_indices_by_body: dict[str, dict[str, tuple[int, ...]]] = {}
    ignored_capacity_fields: list[str] = []
    require_ground_physics = ground_link_model == "terminal_physics" and has_ground_stations
    terminal_pool_link_model: Literal["geometry_only", "terminal_physics"] = (
        "terminal_physics" if require_ground_physics else "geometry_only"
    )
    for sat in satellites:
        nid = satellite_node_id(sat, addressing)
        sat_isl_terminals[nid] = sat.isl_terminal_count
        sat_ground_terminals[nid] = sat.ground_terminal_count
        sat_ground_terminal_indices_by_body[nid] = satellite_terminal_index_pools_by_target_body(
            tuple(sat.ground_terminals),
            total_count=sat.ground_terminal_count,
            ground_link_model=terminal_pool_link_model,
        )
        if sat.ground_terminals:
            ignored_capacity_fields.extend(
                _future_capacity_field_paths(
                    tuple(sat.ground_terminals),
                    base_path=f"satellites.{nid}.ground_terminals",
                )
            )
            sat_ground_terminal_types[nid] = ground_terminal_type(sat.ground_terminals)
            sat_ground_terminal_profiles[nid] = terminal_physics_profiles(
                tuple(sat.ground_terminals),
                profile_id=f"{nid}.ground_terminals",
                endpoint="satellite",
                require_constraints=require_ground_physics,
            )
        constraints_by_iface: dict[str, IslTerminalConstraints] = {}
        for idx in range(sat.isl_terminal_count):
            iface = f"isl{idx}"
            term = isl_terminal_for_interface(sat.isl_terminals, iface)
            constraints_by_iface[iface] = IslTerminalConstraints(
                role=getattr(term, "role", None),
                max_range_km=float(term.max_range_km),
                max_tracking_rate_deg_s=float(term.max_tracking_rate_deg_s),
                field_of_regard_deg=float(term.field_of_regard_deg),
                terminal_type=str(term.type),
            )
        sat_isl_terminal_constraints[nid] = constraints_by_iface

    gs_positions: dict[str, tuple[EcefVec3, GeoPosition]] = {}
    gs_min_elevations: dict[str, float] = {}
    gs_terminal_counts: dict[str, int] = {}
    gs_selection_policies: dict[str, SelectionPolicySpec] = {}
    gs_selection_policy_names: dict[str, str] = {}
    gs_handover_policies: dict[str, HandoverPolicySpec] = {}
    gs_handover_modes: dict[str, Literal["bbm", "mbb"]] = {}
    gs_mbb_overlap_ticks: dict[str, int] = {}
    gs_mbb_reserve: dict[str, int] = {}
    gs_service_priorities: dict[str, int] = {}
    gs_tenant_ids: dict[str, str] = {}
    gs_reference_bodies: dict[str, str] = {}
    declared_ground_candidates: dict[str, tuple[str, ...]] = {}
    gs_terminal_types: dict[str, str] = {}
    gs_terminal_profiles: dict[str, TerminalPhysicsProfile] = {}
    ground_pair_terminal_types: dict[tuple[str, str], str] = {}
    if gs_file:
        ignored_capacity_fields.extend(
            _future_capacity_field_paths(
                tuple(gs_file.default_terminals),
                base_path="ground_stations.default_terminals",
            )
        )
        for _i, station in enumerate(gs_file.stations):
            node_id = addressing.gs_id(station.name)
            geo = GeoPosition(station.lat_deg, station.lon_deg, (station.alt_m or 0) / 1000.0)
            ecef = geodetic_to_ecef(geo, body_frame_for(station.reference_body))
            gs_positions[node_id] = (ecef, geo)
            gs_min_elevations[node_id] = (
                station.min_elevation_deg
                if station.min_elevation_deg is not None
                else gs_file.default_min_elevation_deg
            )
            station_resolution = resolve_station_ground_scheduling(
                ground_scheduling,
                gs_file,
                station,
                apply_ground_defaults=not ground_defaults_applied,
            )
            station_scheduling = station_resolution.scheduling
            gs_terminal_counts[node_id] = station_resolution.terminal_capacity
            gs_terminal_types[node_id] = station_ground_terminal_type(gs_file, station)
            effective_terminals = station.terminals or gs_file.default_terminals
            if station.terminals is not None:
                ignored_capacity_fields.extend(
                    _future_capacity_field_paths(
                        tuple(station.terminals),
                        base_path=f"ground_stations.stations.{station.name}.terminals",
                    )
                )
            gs_terminal_profiles[node_id] = terminal_physics_profile(
                tuple(effective_terminals),
                profile_id=f"{node_id}.terminals",
                endpoint="ground",
                require_constraints=require_ground_physics,
            )
            selection_policy = station_scheduling.selection_policy.model_copy(deep=True)
            handover_policy = _normalize_handover_policy(station_scheduling.handover_policy)
            gs_selection_policies[node_id] = selection_policy
            gs_selection_policy_names[node_id] = selection_policy.name
            gs_handover_policies[node_id] = handover_policy
            gs_handover_modes[node_id] = station_scheduling.handover_mode
            gs_mbb_overlap_ticks[node_id] = (
                station_scheduling.mbb_overlap_ticks
                if station_scheduling.handover_mode == "mbb"
                else 0
            )
            gs_mbb_reserve[node_id] = (
                station_scheduling.mbb_reserve if station_scheduling.handover_mode == "mbb" else 0
            )
            gs_service_priorities[node_id] = station.service_priority
            gs_tenant_ids[node_id] = station.tenant_id
            gs_reference_bodies[node_id] = station.reference_body

        if ground_candidate_satellites_by_gs is None:
            raise ValueError(
                "build_step_context requires a declared ground-link candidate map "
                "when ground stations exist"
            )
        missing = sorted(set(gs_terminal_types) - set(ground_candidate_satellites_by_gs))
        extra = sorted(set(ground_candidate_satellites_by_gs) - set(gs_terminal_types))
        if missing or extra:
            raise ValueError(
                "Ground candidate map does not match ground station universe: "
                f"missing={missing}, extra={extra}"
            )
        for gs_id in sorted(gs_terminal_types):
            candidates = tuple(ground_candidate_satellites_by_gs[gs_id])
            unknown = sorted(set(candidates) - set(sat_ground_terminal_types))
            if unknown:
                raise ValueError(
                    f"Ground station {gs_id} declares unknown access candidate(s): "
                    f"{', '.join(unknown)}"
                )
            declared_ground_candidates[gs_id] = tuple(sorted(candidates))
        for gs_id, gs_type in gs_terminal_types.items():
            for sat_id in declared_ground_candidates[gs_id]:
                sat_type = sat_ground_terminal_types[sat_id]
                if gs_type != sat_type:
                    raise ValueError(
                        f"Ground terminal type mismatch for {gs_id}<->{sat_id}: "
                        f"ground station uses {gs_type!r}, satellite uses {sat_type!r}. "
                        "Mixed terminal types require an explicit compatibility model."
                    )
                pair = (min(gs_id, sat_id), max(gs_id, sat_id))
                ground_pair_terminal_types[pair] = gs_type

    validate_selection_score_scale_compatibility(
        policies=gs_selection_policies,
        ranking_order=tuple(ground_scheduling.ranking_order),
    )

    lookahead_horizon_ticks_by_gs = {
        gs_id: int(policy.params["lookahead_horizon_ticks"])
        for gs_id, policy in gs_selection_policies.items()
        if policy.name == "longest-remaining-pass"
    }
    lookahead_horizon_ticks = (
        max(lookahead_horizon_ticks_by_gs.values()) if lookahead_horizon_ticks_by_gs else 0
    )

    ignored_capacity_tuple = tuple(sorted(set(ignored_capacity_fields)))
    policy_audit = _build_policy_audit(
        gs_selection_policies=gs_selection_policies,
        gs_handover_policies=gs_handover_policies,
        ground_scheduling=ground_scheduling,
        gs_handover_modes=gs_handover_modes,
        gs_mbb_overlap_ticks=gs_mbb_overlap_ticks,
        gs_mbb_reserve=gs_mbb_reserve,
        ignored_capacity_fields=ignored_capacity_tuple,
    )

    return StepContext(
        satellites=satellites,
        addressing=addressing,
        gs_positions=gs_positions,
        gs_min_elevations=gs_min_elevations,
        gs_terminal_counts=gs_terminal_counts,
        gs_selection_policies=gs_selection_policies,
        gs_selection_policy_names=gs_selection_policy_names,
        gs_handover_policies=gs_handover_policies,
        gs_service_priorities=gs_service_priorities,
        ground_ranking_order=tuple(ground_scheduling.ranking_order),
        gs_handover_modes=gs_handover_modes,
        gs_mbb_overlap_ticks=gs_mbb_overlap_ticks,
        gs_mbb_reserve=gs_mbb_reserve,
        ground_mbb_preemption=ground_scheduling.mbb_preemption,
        ground_successor_abort_policy=ground_scheduling.successor_abort_policy,
        ground_cross_tenant_displacement=ground_scheduling.cross_tenant_displacement,
        ground_bbm_acquire_timeout_ticks=ground_scheduling.bbm_acquire_timeout_ticks,
        ignored_ground_capacity_fields=ignored_capacity_tuple,
        ground_policy_audit=policy_audit,
        ground_link_model=ground_link_model,
        gs_terminal_profiles=gs_terminal_profiles,
        sat_ground_terminal_profiles=sat_ground_terminal_profiles,
        sat_ground_terminal_indices_by_body=sat_ground_terminal_indices_by_body,
        gs_tenant_ids=gs_tenant_ids,
        gs_reference_bodies=gs_reference_bodies,
        ground_candidate_satellites_by_gs=declared_ground_candidates,
        ground_pair_terminal_types=ground_pair_terminal_types,
        node_metadata=node_metadata_map,
        by_node=by_node,
        sat_isl_terminals=sat_isl_terminals,
        sat_isl_terminal_constraints=sat_isl_terminal_constraints,
        sat_ground_terminals=sat_ground_terminals,
        propagator_id=propagator_id,
        body_ephemeris=body_ephemeris,
        active_bodies=active_bodies or frozenset({"earth"}),
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        ground_policy_lookahead_horizon_ticks=lookahead_horizon_ticks,
        ground_policy_lookahead_horizon_ticks_by_gs=lookahead_horizon_ticks_by_gs,
    )


def _build_link_snapshot_source(
    *,
    isl_feasibility: Mapping[tuple[str, str], IslFeasibilityResult],
    isl_links: Mapping[tuple[str, str], ScheduledIsl],
    ground_decisions: GroundVisibilityDecisionMap,
    ground_allocation: GroundAllocationResult,
    propagated_states: Mapping[str, PropagatedState],
) -> LinkSnapshotSource:
    """Derive authoritative snapshot state from current tick facts.

    This does not inspect emitted VisibilityEvents or event-diff transition
    state. Snapshots are committed state; events are deltas after state.
    """
    isl_state: dict[tuple[str, str], tuple[bool, bool]] = {}
    for pair, feasibility in sorted(isl_feasibility.items()):
        visible = feasibility.feasible
        scheduled = isl_links[pair].scheduled if visible else False
        if not visible and not scheduled:
            continue
        isl_state[pair] = (visible, scheduled)

    ground_state: dict[tuple[str, str], tuple[bool, bool, str]] = {}
    forwarding_pairs = (
        set(ground_allocation.scheduled_pairs)
        | set(ground_allocation.pending_teardowns)
        | {unscheduled.pair for unscheduled in ground_allocation.unscheduled_pairs}
    )
    for pair in sorted(forwarding_pairs):
        decision = ground_decisions.get(pair)
        if decision is None:
            raise ValueError(
                "Cannot build authoritative LinkStateSnapshot source for ground pair "
                f"{pair}: allocator referenced a pair missing from ground visibility decisions"
            )
        visible = decision.visible
        scheduled = pair in ground_allocation.scheduled_pairs
        if not visible:
            raise ValueError(
                "Cannot build authoritative LinkStateSnapshot source for ground pair "
                f"{pair}: allocator referenced an invisible pair"
            )
        if scheduled and pair not in ground_allocation.associations:
            raise ValueError(
                "Cannot build authoritative LinkStateSnapshot source for scheduled "
                f"ground pair {pair}: missing terminal association"
            )
        sched_state = "teardown" if pair in ground_allocation.pending_teardowns else "active"
        ground_state[pair] = (visible, scheduled, sched_state)

    return LinkSnapshotSource(
        isl_state=isl_state,
        ground_state=ground_state,
        associations=dict(ground_allocation.associations),
        pending_teardowns=dict(ground_allocation.pending_teardowns),
        propagated_states=dict(propagated_states),
    )


def compute_step(
    ctx: StepContext,
    epoch_unix: float,
    step: int,
    step_seconds: int,
    timestamp_offset: float,
    isl_state: dict[tuple[str, str], tuple[bool, bool]],
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]],
    current_associations: dict[tuple[str, str], tuple[int, int]] | None = None,
    mbb_pending_teardowns: MbbTeardownState | None = None,
) -> StepResult:
    """Compute one step of the timeline. Mutates isl_state and gs_state in place.

    Returns a StepResult with named physics, scheduling, and allocation fields.

    Pure computation — no I/O, no wall-time awareness.
    This is the Physicist role (R-OME-008B Part 2).
    """
    if current_associations is None:
        current_associations = {}
    if mbb_pending_teardowns is None:
        mbb_pending_teardowns = {}
    dt = step * step_seconds
    timestamp_s = dt + timestamp_offset
    sim_time = datetime.fromtimestamp(epoch_unix + dt, tz=UTC)

    # 1. Propagate all satellite states using the session-selected engine.
    body_states = body_states_at(ctx.body_ephemeris, set(ctx.active_bodies), epoch_unix + dt)
    sat_states = propagate_satellites(
        satellites=ctx.satellites,
        addressing=ctx.addressing,
        epoch_unix=epoch_unix,
        dt=dt,
        propagator_id=ctx.propagator_id,
        body_states=body_states,
    )

    # 2. Build positions dict (for LinkStateSnapshot latency) and ClockTick
    positions = build_node_positions(sat_states, ctx.gs_positions)
    clock_tick = ClockTick(
        sim_time=sim_time,
        wall_time=sim_time,  # Placeholder — Pacemaker overrides with real wall_time at emission
        compression_ratio=1.0,  # Placeholder — Pacemaker overrides at emission
    )
    events: list[TimelineEvent] = [
        TimelineEvent(timestamp_s, "ClockTick", clock_tick),
    ]

    # 3-4. Evaluate ISL physics and allocate ISL terminals.
    node_order = [satellite_node_id(sat, ctx.addressing) for sat in ctx.satellites]
    isl_feasibility = evaluate_isl_feasibility(
        node_order=node_order,
        sat_states=sat_states,
        by_node=ctx.by_node,
        terminal_constraints=ctx.sat_isl_terminal_constraints,
        polar_seam_enabled=ctx.polar_seam_enabled,
        latitude_threshold_deg=ctx.latitude_threshold_deg,
    )
    isl_links = schedule_isl_links(
        feasibility=isl_feasibility,
        by_node=ctx.by_node,
        terminal_counts=ctx.sat_isl_terminals,
    )
    isl_scheduled = {pair: link.scheduled for pair, link in isl_links.items()}

    # 5. Emit ISL visibility events on state changes.
    isl_diff = diff_isl_visibility_events(
        sim_time=sim_time,
        feasibility=isl_feasibility,
        scheduled_links=isl_links,
        previous_state=isl_state,
    )
    isl_state.clear()
    isl_state.update(isl_diff.state)
    events.extend(TimelineEvent(timestamp_s, "VisibilityEvent", event) for event in isl_diff.events)

    # 6. Check ground station visibility and schedule
    ground_visibility = evaluate_ground_visibility(
        satellite_ids=node_order,
        sat_states=sat_states,
        gs_positions=ctx.gs_positions,
        gs_min_elevations=ctx.gs_min_elevations,
        gs_tenant_ids=ctx.gs_tenant_ids,
        gs_reference_bodies=ctx.gs_reference_bodies,
        gs_selection_policy_names=ctx.gs_selection_policy_names,
        pass_lookahead=(
            GroundPassLookahead(
                satellites=tuple(ctx.satellites),
                addressing=ctx.addressing,
                epoch_unix=epoch_unix,
                step=step,
                step_seconds=step_seconds,
                horizon_ticks=ctx.ground_policy_lookahead_horizon_ticks,
                horizon_ticks_by_gs=ctx.ground_policy_lookahead_horizon_ticks_by_gs,
                propagator_id=ctx.propagator_id,
                ground_link_model=ctx.ground_link_model,
                gs_reference_bodies=ctx.gs_reference_bodies,
                gs_terminal_profiles=ctx.gs_terminal_profiles,
                sat_ground_terminal_profiles=ctx.sat_ground_terminal_profiles,
                body_ephemeris=ctx.body_ephemeris,
                active_bodies=ctx.active_bodies,
            )
            if "longest-remaining-pass" in set(ctx.gs_selection_policy_names.values())
            else None
        ),
        ground_link_model=ctx.ground_link_model,
        gs_terminal_profiles=ctx.gs_terminal_profiles,
        sat_ground_terminal_profiles=ctx.sat_ground_terminal_profiles,
        candidate_satellite_ids_by_gs=ctx.ground_candidate_satellites_by_gs,
    )

    # 7. Scored, hysteresis-aware ground link allocation.
    ground_allocation = allocate_ground_links(
        step=step,
        visible_per_station=ground_visibility.visible_per_station,
        ground_station_ids=set(ctx.gs_positions),
        current_associations=current_associations,
        pending_teardowns=mbb_pending_teardowns,
        gs_terminal_counts=ctx.gs_terminal_counts,
        gs_selection_policies=ctx.gs_selection_policies,
        gs_min_elevations=ctx.gs_min_elevations,
        gs_handover_policies=ctx.gs_handover_policies,
        gs_service_priorities=ctx.gs_service_priorities,
        gs_tenant_ids=ctx.gs_tenant_ids,
        gs_reference_bodies=ctx.gs_reference_bodies,
        sat_ground_terminals=ctx.sat_ground_terminals,
        sat_ground_terminal_indices_by_body=ctx.sat_ground_terminal_indices_by_body,
        ranking_order=ctx.ground_ranking_order,
        gs_handover_modes=ctx.gs_handover_modes,
        gs_mbb_overlap_ticks=ctx.gs_mbb_overlap_ticks,
        gs_mbb_reserve=ctx.gs_mbb_reserve,
        mbb_preemption=ctx.ground_mbb_preemption,
        successor_abort_policy=ctx.ground_successor_abort_policy,
        cross_tenant_displacement=ctx.ground_cross_tenant_displacement,
        bbm_acquire_timeout_ticks=ctx.ground_bbm_acquire_timeout_ticks,
        ignored_capacity_fields=ctx.ignored_ground_capacity_fields,
    )
    # 8. Emit ground visibility events on state changes (triple state).
    ground_diff = diff_ground_visibility_events(
        sim_time=sim_time,
        visibility_decisions=ground_visibility.decisions,
        allocation=ground_allocation,
        previous_state=gs_state,
        terminal_types=ctx.ground_pair_terminal_types,
    )
    gs_state.clear()
    gs_state.update(ground_diff.state)
    events.extend(
        TimelineEvent(timestamp_s, "VisibilityEvent", event) for event in ground_diff.events
    )

    link_snapshot_source = _build_link_snapshot_source(
        isl_feasibility=isl_feasibility,
        isl_links=isl_links,
        ground_decisions=ground_visibility.decisions,
        ground_allocation=ground_allocation,
        propagated_states=sat_states,
    )

    return StepResult(
        events=events,
        positions=positions,
        isl_scheduled=isl_scheduled,
        isl_feasibility=isl_feasibility,
        isl_links=isl_links,
        ground_allocation=ground_allocation,
        ground_decisions=ground_visibility.decisions,
        link_snapshot_source=link_snapshot_source,
        propagated_states=sat_states,
        sim_time=sim_time,
        sim_time_unix=epoch_unix + dt,
        step=step,
    )


# ---------------------------------------------------------------------------
# Batch window precomputation — used by lookahead thread and offline tools
# ---------------------------------------------------------------------------


def precompute_timeline_window_from_context(
    ctx: StepContext,
    epoch_unix: float,
    duration_s: float,
    step_seconds: int = 1,
    initial_isl_state: dict[tuple[str, str], tuple[bool, bool]] | None = None,
    initial_gs_state: dict[tuple[str, str], tuple[bool, bool, str]] | None = None,
    initial_associations: dict[tuple[str, str], tuple[int, int]] | None = None,
    initial_pending_teardowns: MbbTeardownState | None = None,
    timestamp_offset: float = 0.0,
    predictive: bool = False,
) -> TimelineWindowResult:
    """Precompute a single window using an already-normalized StepContext.

    This is the path used by live lookahead. Passing the live StepContext makes
    propagation, visibility, allocation, hysteresis, and MBB parameters
    identical by construction instead of relying on a duplicate argument list.

    Returns named boundary state so callers can carry the result into the next
    window without tuple-position coupling.
    """
    isl_state: dict[tuple[str, str], tuple[bool, bool]] = (
        dict(initial_isl_state) if initial_isl_state else {}
    )
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]] = (
        dict(initial_gs_state) if initial_gs_state else {}
    )
    associations: dict[tuple[str, str], tuple[int, int]] = (
        dict(initial_associations) if initial_associations else {}
    )
    pending_teardowns: MbbTeardownState = (
        dict(initial_pending_teardowns) if initial_pending_teardowns else {}
    )

    events: list[TimelineEvent] = []
    steps = int(duration_s / step_seconds)
    for s in range(steps + 1):
        result = compute_step(
            ctx,
            epoch_unix,
            s,
            step_seconds,
            timestamp_offset,
            isl_state,
            gs_state,
            associations,
            pending_teardowns,
        )
        events.extend(result.events)
        associations = result.associations
        pending_teardowns = result.pending_teardowns

    return TimelineWindowResult(
        events=events,
        isl_state=isl_state,
        gs_state=gs_state,
        associations=associations,
        pending_teardowns=pending_teardowns,
        predictive=predictive,
    )


def precompute_timeline_window(
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    gs_file: GroundStationFile | None,
    neighbors: frozenset[tuple[str, NeighborAssignment]],
    epoch_unix: float,
    duration_s: float,
    propagator_id: PropagatorId,
    step_seconds: int = 1,
    ground_scheduling: GroundSchedulingConfig | None = None,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    ground_link_model: Literal["geometry_only", "terminal_physics"] = "terminal_physics",
    ground_defaults_applied: bool = False,
    ground_candidate_satellites_by_gs: Mapping[str, tuple[str, ...]] | None = None,
    initial_isl_state: dict[tuple[str, str], tuple[bool, bool]] | None = None,
    initial_gs_state: dict[tuple[str, str], tuple[bool, bool, str]] | None = None,
    initial_associations: dict[tuple[str, str], tuple[int, int]] | None = None,
    initial_pending_teardowns: MbbTeardownState | None = None,
    timestamp_offset: float = 0.0,
    predictive: bool = False,
    body_ephemeris: SkyfieldBspEphemeris | None = None,
    active_bodies: frozenset[str] | None = None,
) -> TimelineWindowResult:
    """Precompute a single window of the timeline (batch mode).

    Offline callers provide raw session inputs; this wrapper normalizes them
    into a StepContext once, then delegates to the same context-based engine
    used by live lookahead.
    """
    ctx = build_step_context(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        propagator_id=propagator_id,
        ground_scheduling=ground_scheduling,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        ground_link_model=ground_link_model,
        ground_defaults_applied=ground_defaults_applied,
        ground_candidate_satellites_by_gs=ground_candidate_satellites_by_gs,
        body_ephemeris=body_ephemeris,
        active_bodies=active_bodies,
    )
    return precompute_timeline_window_from_context(
        ctx,
        epoch_unix=epoch_unix,
        duration_s=duration_s,
        step_seconds=step_seconds,
        initial_isl_state=initial_isl_state,
        initial_gs_state=initial_gs_state,
        initial_associations=initial_associations,
        initial_pending_teardowns=initial_pending_teardowns,
        timestamp_offset=timestamp_offset,
        predictive=predictive,
    )


def precompute_timeline(
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    gs_file: GroundStationFile | None,
    neighbors: frozenset[tuple[str, NeighborAssignment]],
    epoch_unix: float,
    duration_s: float,
    propagator_id: PropagatorId,
    step_seconds: int = 1,
    ground_scheduling: GroundSchedulingConfig | None = None,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    ground_link_model: Literal["geometry_only", "terminal_physics"] = "terminal_physics",
    ground_defaults_applied: bool = False,
    ground_candidate_satellites_by_gs: Mapping[str, tuple[str, ...]] | None = None,
    body_ephemeris: SkyfieldBspEphemeris | None = None,
    active_bodies: frozenset[str] | None = None,
) -> list[TimelineEvent]:
    """Single-window convenience wrapper.

    Returns only events, discarding boundary state.
    """
    result = precompute_timeline_window(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=epoch_unix,
        duration_s=duration_s,
        propagator_id=propagator_id,
        step_seconds=step_seconds,
        ground_scheduling=ground_scheduling,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        ground_link_model=ground_link_model,
        ground_defaults_applied=ground_defaults_applied,
        ground_candidate_satellites_by_gs=ground_candidate_satellites_by_gs,
        body_ephemeris=body_ephemeris,
        active_bodies=active_bodies,
    )
    return result.events


def write_timeline_jsonl(events: list[TimelineEvent], output_path: Path) -> None:
    """Write timeline events to JSON Lines file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for event in events:
            record = {
                "timestamp_s": event.timestamp_s,
                "event_type": event.event_type,
                "data": event.data.model_dump(mode="json"),
            }
            f.write(json.dumps(record) + "\n")
    logger.info(f"Wrote {len(events)} events to {output_path}")


def append_timeline_jsonl(events: list[TimelineEvent], output_path: Path) -> None:
    """Append events to an existing JSONL file (or create it).

    Uses fsync to ensure the dispatcher's tail-reader sees complete lines.
    """
    import os

    with open(output_path, "a") as f:
        for event in events:
            record = {
                "timestamp_s": event.timestamp_s,
                "event_type": event.event_type,
                "data": event.data.model_dump(mode="json"),
            }
            f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())
    logger.info(f"Appended {len(events)} events to {output_path}")


def read_timeline_jsonl(path: Path) -> list[dict]:
    """Read timeline events from JSON Lines file."""
    events = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events
