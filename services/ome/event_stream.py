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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodalarc.constellation_loader import SatelliteNode, isl_terminal_for_interface
from nodalarc.ground_terminals import (
    ground_terminal_type,
    station_ground_terminal_capacity,
    station_ground_terminal_type,
)
from nodalarc.models.addressing import AddressingScheme, NeighborAssignment, neighbors_by_node
from nodalarc.models.events import (
    ClockTick,
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    EphemerisNodeTLE,
    NodePosition,
    SessionEphemeris,
)
from nodalarc.models.ground_station import GroundStationFile

from ome.event_diff import diff_ground_visibility_events, diff_isl_visibility_events
from ome.ground_allocator import (
    GroundAllocationResult,
    allocate_ground_links,
)
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

    from nodalarc.constants import EARTH_RADIUS_KM

    nodes: dict[str, EphemerisNodeKeplerian | EphemerisNodeTLE | EphemerisNodeFixed] = {}

    for sat in ctx.satellites:
        node_id = ctx.addressing.sat_id(sat.plane, sat.slot)
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
            )
        else:
            nodes[node_id] = EphemerisNodeKeplerian(
                propagator=ctx.propagator_id,
                altitude_km=sat.elements.semi_major_axis_km - EARTH_RADIUS_KM,
                inclination_deg=math.degrees(sat.elements.inclination_rad),
                raan_deg=math.degrees(sat.elements.raan_rad),
                true_anomaly_deg=math.degrees(sat.elements.true_anomaly_rad),
                plane=sat.plane,
                slot=sat.slot,
            )

    for gs_id, (_ecef, geo) in ctx.gs_positions.items():
        nodes[gs_id] = EphemerisNodeFixed(
            lat_deg=geo.lat_deg,
            lon_deg=geo.lon_deg,
            alt_km=geo.alt_km,
        )

    return SessionEphemeris(
        epoch_id=epoch_id,
        sim_time=datetime.fromtimestamp(epoch_unix, tz=UTC),
        epoch_unix=epoch_unix,
        nodes=nodes,
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

from dataclasses import dataclass

from nodalarc.models.ground_station import HysteresisParameters


@dataclass(frozen=True)
class StepContext:
    """Session-constant arguments for compute_step(). Built once, reused every step."""

    satellites: list[SatelliteNode]
    addressing: AddressingScheme
    gs_positions: dict[str, tuple[EcefVec3, GeoPosition]]
    gs_min_elevations: dict[str, float]
    gs_terminal_counts: dict[str, int]
    gs_policies: dict[str, str]
    gs_hysteresis: dict[str, HysteresisParameters]
    gs_service_priorities: dict[str, int]
    # Per-GS tenant scope (Direction 2 — multi-tenant from day one) and
    # reference body (Direction 3 — multi-body from day one). Both
    # already exist on GroundStationConfig; the StepContext carries them
    # so every visibility decision is tenant- and body-attributable.
    gs_tenant_ids: dict[str, str]
    gs_reference_bodies: dict[str, str]
    ground_pair_terminal_types: dict[tuple[str, str], str]
    by_node: dict[str, list[NeighborAssignment]]
    sat_isl_terminals: dict[str, int]
    sat_isl_terminal_constraints: dict[str, dict[str, IslTerminalConstraints]]
    sat_ground_terminals: dict[str, int]  # satellite ground terminal capacity
    propagator_id: PropagatorId
    polar_seam_enabled: bool
    latitude_threshold_deg: float
    mbb_overlap_ticks: int = 3
    mbb_reserve: int = 0
    ground_policy_lookahead_horizon_ticks: int = 0


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


def build_step_context(
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    gs_file: GroundStationFile | None,
    neighbors: frozenset[tuple[str, NeighborAssignment]],
    propagator_id: PropagatorId,
    mbb_overlap_ticks: int = 3,
    mbb_reserve: int = 0,
    ground_policy_lookahead_horizon_ticks: int = 0,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    default_ground_policy: str | None = None,
) -> StepContext:
    """Build the per-session-constant context for compute_step()."""
    by_node = neighbors_by_node(neighbors)

    sat_isl_terminals: dict[str, int] = {}
    sat_isl_terminal_constraints: dict[str, dict[str, IslTerminalConstraints]] = {}
    sat_ground_terminals: dict[str, int] = {}
    sat_ground_terminal_types: dict[str, str] = {}
    for sat in satellites:
        nid = addressing.sat_id(sat.plane, sat.slot)
        sat_isl_terminals[nid] = sat.isl_terminal_count
        sat_ground_terminals[nid] = sat.ground_terminal_count
        sat_ground_terminal_types[nid] = ground_terminal_type(sat.ground_terminals)
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
    gs_policies: dict[str, str] = {}
    gs_hysteresis: dict[str, HysteresisParameters] = {}
    gs_service_priorities: dict[str, int] = {}
    gs_tenant_ids: dict[str, str] = {}
    gs_reference_bodies: dict[str, str] = {}
    gs_terminal_types: dict[str, str] = {}
    ground_pair_terminal_types: dict[tuple[str, str], str] = {}
    if gs_file:
        default_gs_policy = (
            default_ground_policy
            if default_ground_policy is not None
            else gs_file.default_scheduling_policy
        )
        for _i, station in enumerate(gs_file.stations):
            node_id = addressing.gs_id(station.name)
            geo = GeoPosition(station.lat_deg, station.lon_deg, (station.alt_m or 0) / 1000.0)
            ecef = geodetic_to_ecef(geo)
            gs_positions[node_id] = (ecef, geo)
            gs_min_elevations[node_id] = (
                station.min_elevation_deg
                if station.min_elevation_deg is not None
                else gs_file.default_min_elevation_deg
            )
            gs_terminal_counts[node_id] = station_ground_terminal_capacity(gs_file, station)
            gs_terminal_types[node_id] = station_ground_terminal_type(gs_file, station)
            gs_policies[node_id] = (
                station.scheduling_policy
                if station.scheduling_policy is not None
                else default_gs_policy
            )
            gs_hysteresis[node_id] = station.hysteresis
            gs_service_priorities[node_id] = station.service_priority
            gs_tenant_ids[node_id] = station.tenant_id
            gs_reference_bodies[node_id] = station.reference_body
        for gs_id, gs_type in gs_terminal_types.items():
            for sat_id, sat_type in sat_ground_terminal_types.items():
                if gs_type != sat_type:
                    raise ValueError(
                        f"Ground terminal type mismatch for {gs_id}<->{sat_id}: "
                        f"ground station uses {gs_type!r}, satellite uses {sat_type!r}. "
                        "Mixed terminal types require an explicit compatibility model."
                    )
                pair = (min(gs_id, sat_id), max(gs_id, sat_id))
                ground_pair_terminal_types[pair] = gs_type

    return StepContext(
        satellites=satellites,
        addressing=addressing,
        gs_positions=gs_positions,
        gs_min_elevations=gs_min_elevations,
        gs_terminal_counts=gs_terminal_counts,
        gs_policies=gs_policies,
        gs_hysteresis=gs_hysteresis,
        gs_service_priorities=gs_service_priorities,
        gs_tenant_ids=gs_tenant_ids,
        gs_reference_bodies=gs_reference_bodies,
        ground_pair_terminal_types=ground_pair_terminal_types,
        by_node=by_node,
        sat_isl_terminals=sat_isl_terminals,
        sat_isl_terminal_constraints=sat_isl_terminal_constraints,
        sat_ground_terminals=sat_ground_terminals,
        propagator_id=propagator_id,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        mbb_overlap_ticks=mbb_overlap_ticks,
        mbb_reserve=mbb_reserve,
        ground_policy_lookahead_horizon_ticks=ground_policy_lookahead_horizon_ticks,
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
    sat_states = propagate_satellites(
        satellites=ctx.satellites,
        addressing=ctx.addressing,
        epoch_unix=epoch_unix,
        dt=dt,
        propagator_id=ctx.propagator_id,
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
    node_order = [ctx.addressing.sat_id(sat.plane, sat.slot) for sat in ctx.satellites]
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
        gs_policies=ctx.gs_policies,
        pass_lookahead=(
            GroundPassLookahead(
                satellites=tuple(ctx.satellites),
                addressing=ctx.addressing,
                epoch_unix=epoch_unix,
                step=step,
                step_seconds=step_seconds,
                horizon_ticks=ctx.ground_policy_lookahead_horizon_ticks,
                propagator_id=ctx.propagator_id,
            )
            if "longest-remaining-pass" in set(ctx.gs_policies.values())
            else None
        ),
    )

    # 7. Scored, hysteresis-aware ground link allocation.
    ground_allocation = allocate_ground_links(
        step=step,
        visible_per_station=ground_visibility.visible_per_station,
        ground_station_ids=set(ctx.gs_positions),
        current_associations=current_associations,
        pending_teardowns=mbb_pending_teardowns,
        gs_terminal_counts=ctx.gs_terminal_counts,
        gs_policies=ctx.gs_policies,
        gs_min_elevations=ctx.gs_min_elevations,
        gs_hysteresis=ctx.gs_hysteresis,
        gs_service_priorities=ctx.gs_service_priorities,
        gs_tenant_ids=ctx.gs_tenant_ids,
        gs_reference_bodies=ctx.gs_reference_bodies,
        sat_ground_terminals=ctx.sat_ground_terminals,
        mbb_overlap_ticks=ctx.mbb_overlap_ticks,
        mbb_reserve=ctx.mbb_reserve,
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

    return StepResult(
        events=events,
        positions=positions,
        isl_scheduled=isl_scheduled,
        isl_feasibility=isl_feasibility,
        isl_links=isl_links,
        ground_allocation=ground_allocation,
        ground_decisions=ground_visibility.decisions,
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
    mbb_overlap_ticks: int = 3,
    mbb_reserve: int = 0,
    ground_policy_lookahead_horizon_ticks: int = 0,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    default_ground_policy: str | None = None,
    initial_isl_state: dict[tuple[str, str], tuple[bool, bool]] | None = None,
    initial_gs_state: dict[tuple[str, str], tuple[bool, bool, str]] | None = None,
    initial_associations: dict[tuple[str, str], tuple[int, int]] | None = None,
    initial_pending_teardowns: MbbTeardownState | None = None,
    timestamp_offset: float = 0.0,
    predictive: bool = False,
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
        mbb_overlap_ticks=mbb_overlap_ticks,
        mbb_reserve=mbb_reserve,
        ground_policy_lookahead_horizon_ticks=ground_policy_lookahead_horizon_ticks,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_ground_policy=default_ground_policy,
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
    mbb_overlap_ticks: int = 3,
    mbb_reserve: int = 0,
    ground_policy_lookahead_horizon_ticks: int = 0,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    default_ground_policy: str | None = None,
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
        mbb_overlap_ticks=mbb_overlap_ticks,
        mbb_reserve=mbb_reserve,
        ground_policy_lookahead_horizon_ticks=ground_policy_lookahead_horizon_ticks,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_ground_policy=default_ground_policy,
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
