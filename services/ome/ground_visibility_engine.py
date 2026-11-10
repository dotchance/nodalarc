# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""OME ground visibility evaluation engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from nodalarc.body_frames import BodyFrame
from nodalarc.frames import CommonVec3, EcefVec3, GeoPosition
from nodalarc.geo import compute_range_km
from nodalarc.ground_terminals import TerminalPhysicsProfile
from nodalarc.models.terminal_physics import GroundBoresightMode, SatGroundBoresightMode
from nodalarc.ome_runtime import PASS_SAMPLE_CHUNK_TICKS
from nodalarc.physics import PhysicsContext
from nodalarc.physics.lighttime import Endpoint, Signals, SiteEndpoint

from ome.direction_checks import (
    DirectionEnds,
    GroundLimits,
    GroundLimitTable,
    GroundPairVerdict,
    PairSignals,
    ground_directions,
    ground_pair_verdicts,
)
from ome.propagation_engine import PropagatedState
from ome.telemetry import SEG_DWELL, StepTimings
from ome.types import GroundVisibilityDecision, GroundVisibilityDecisionMap
from ome.visibility import GroundVisibility

type TerminalPhysicsProfileSet = TerminalPhysicsProfile | Sequence[TerminalPhysicsProfile]


@dataclass(frozen=True)
class GroundVisibilityEvaluation:
    """Ground visibility output for one OME tick.

    `decisions` carries the typed per-pair decision (visibility,
    range, elevation, applied constraints, reject reason). Replaces
    the legacy positional `details` tuple alias.
    """

    decisions: GroundVisibilityDecisionMap
    visible_per_station: dict[str, list[GroundVisibility]]


@dataclass(frozen=True)
class GroundPassLookahead:
    """Inputs required for ground pass-duration scoring.

    The horizon is a user-selected policy parameter. OME never invents it:
    policies that need future dwell prediction must provide this object, and
    the engine raises if the horizon is not positive. Future samples use the
    tick's ground geometry and signal endpoints (satellites and stations), so a
    sample computes what the tick computes.
    """

    epoch_unix: float
    step: int
    step_seconds: float
    horizon_ticks: int
    horizon_ticks_by_gs: Mapping[str, int]
    geometry: GroundGeometry
    endpoints: Mapping[str, Endpoint]

    def signals_at(
        self, pairs: Sequence[tuple[str, str]], sent_unix: float | np.ndarray
    ) -> PairSignals:
        return PairSignals.sent_at(
            self.geometry.physics, self.geometry.body_frames, self.endpoints, pairs, sent_unix
        )


def _physical_profile(
    profiles: Mapping[str, TerminalPhysicsProfile] | None,
    node_id: str,
    *,
    label: str,
    ground_link_model: Literal["geometry_only", "terminal_physics"],
) -> TerminalPhysicsProfile | None:
    if ground_link_model == "geometry_only":
        return None
    if profiles is None or node_id not in profiles:
        raise ValueError(f"terminal_physics ground visibility is missing {label} for {node_id}")
    return profiles[node_id]


def _profile_options(value: TerminalPhysicsProfileSet) -> tuple[TerminalPhysicsProfile, ...]:
    if isinstance(value, TerminalPhysicsProfile):
        return (value,)
    return tuple(value)


def _sat_physical_profile(
    profiles: Mapping[str, TerminalPhysicsProfileSet] | None,
    sat_id: str,
    *,
    reference_body: str,
    ground_link_model: Literal["geometry_only", "terminal_physics"],
) -> TerminalPhysicsProfile | None:
    if ground_link_model == "geometry_only":
        return None
    if profiles is None or sat_id not in profiles:
        raise ValueError(
            f"terminal_physics ground visibility is missing satellite ground terminal profile for {sat_id}"
        )
    options = _profile_options(profiles[sat_id])
    if not options:
        raise ValueError(
            f"terminal_physics ground visibility is missing satellite ground terminal profile for {sat_id}"
        )
    matches = [profile for profile in options if profile.target_body == reference_body]
    if len(matches) != 1:
        available = sorted(str(profile.target_body) for profile in options)
        raise ValueError(
            f"Satellite ground terminal profiles for {sat_id} do not contain exactly one "
            f"profile for reference_body={reference_body!r}; available target bodies: {available}"
        )
    return matches[0]


@dataclass
class DwellPassState:
    """Rolling pass-frontier memo for one (gs, sat) candidate pair.

    Pair visibility at a future tick is a pure function of the session and
    the absolute tick, so samples never expire on their own — entries are
    discarded only when they cannot answer for the current tick (the pass
    ended, or candidacy lapsed and left a coverage gap). Carried through
    compute_step exactly like isl_state/gs_state: mutated in place by the
    estimator, owned by the caller, reset wherever those reset (seek /
    epoch change / fresh session).
    """

    verified_visible_through: int  # absolute tick, inclusive
    first_invisible: int | None  # absolute tick; None while the pass end is unknown


class GroundGeometry:
    """Every ground candidate's per-direction evaluation, shared by the tick, the pass lookahead
    and the Builder preview so they cannot differ in what a sample computes. Built once per
    session context: each candidate's terminal profiles and limits are resolved and validated
    here, and a tick indexes into the table."""

    def __init__(
        self,
        *,
        physics: PhysicsContext,
        body_frames: Mapping[str, BodyFrame],
        gs_positions: Mapping[str, tuple[EcefVec3, GeoPosition]],
        gs_min_elevations: Mapping[str, float],
        gs_reference_bodies: Mapping[str, str],
        ground_link_model: Literal["geometry_only", "terminal_physics"],
        gs_terminal_profiles: Mapping[str, TerminalPhysicsProfile] | None,
        sat_ground_terminal_profiles: Mapping[str, TerminalPhysicsProfileSet] | None,
        pairs: Iterable[tuple[str, str]],
    ) -> None:
        self.physics = physics
        self.body_frames = body_frames
        self.gs_positions = gs_positions
        self.gs_reference_bodies = gs_reference_bodies
        self._rows: dict[tuple[str, str], int] = {}
        self._profiles: list[
            tuple[TerminalPhysicsProfile | None, TerminalPhysicsProfile | None]
        ] = []
        self.station_endpoints: dict[str, Endpoint] = {}
        limits: list[GroundLimits] = []
        for gs_id, sat_id in pairs:
            if (gs_id, sat_id) in self._rows:
                continue
            gs_ecef, gs_geo = gs_positions[gs_id]
            gs_profile = _physical_profile(
                gs_terminal_profiles,
                gs_id,
                label="ground terminal profile",
                ground_link_model=ground_link_model,
            )
            sat_profile = _sat_physical_profile(
                sat_ground_terminal_profiles,
                sat_id,
                reference_body=gs_reference_bodies[gs_id],
                ground_link_model=ground_link_model,
            )
            self._rows[(gs_id, sat_id)] = len(limits)
            self._profiles.append((gs_profile, sat_profile))
            self.station_endpoints.setdefault(
                gs_id, SiteEndpoint(gs_reference_bodies[gs_id], (gs_ecef.x, gs_ecef.y, gs_ecef.z))
            )
            limits.append(_limits(gs_geo, gs_min_elevations[gs_id], gs_profile, sat_profile))
        self._table = GroundLimitTable.from_limits(limits)

    def station_positions_common(self, sim_time_unix: float) -> dict[str, CommonVec3]:
        """Each station's position in the common frame (Earth's centre, ICRF axes) at an instant:
        its body's centre plus its body-fixed position turned onto ICRF axes. A station and a
        satellite of another body are then measured in one frame."""
        positions: dict[str, CommonVec3] = {}
        by_body: dict[str, list[tuple[str, Endpoint]]] = {}
        for gs_id, endpoint in self.station_endpoints.items():
            by_body.setdefault(endpoint.body_id, []).append((gs_id, endpoint))
        for body_id, stations in by_body.items():
            centre = self.physics.body_state(body_id, sim_time_unix).position_km
            to_icrf = self.physics.orientation(body_id, sim_time_unix).rotation.T
            for gs_id, endpoint in stations:
                assert isinstance(endpoint, SiteEndpoint)
                x, y, z = to_icrf @ np.asarray(endpoint.fixed_km, dtype=np.float64)
                positions[gs_id] = CommonVec3(centre.x + x, centre.y + y, centre.z + z)
        return positions

    def profiles(
        self, gs_id: str, sat_id: str
    ) -> tuple[TerminalPhysicsProfile | None, TerminalPhysicsProfile | None]:
        return self._profiles[self._row(gs_id, sat_id)]

    def _row(self, gs_id: str, sat_id: str) -> int:
        try:
            return self._rows[(gs_id, sat_id)]
        except KeyError as exc:
            raise ValueError(
                f"ground pair {gs_id}<->{sat_id} is not a declared access candidate of this "
                "session's ground geometry"
            ) from exc

    def verdicts(
        self, pairs: Sequence[tuple[str, str]], uplink_signals: Signals, downlink_signals: Signals
    ) -> list[GroundPairVerdict]:
        """Each (gs, sat) candidate's verdict from its two signals, row-aligned with ``pairs``:
        the station transmits the uplink, the satellite the downlink."""
        if not pairs:
            return []
        rows = np.array([self._row(gs_id, sat_id) for gs_id, sat_id in pairs])
        up = DirectionEnds.from_signals(uplink_signals)
        down = DirectionEnds.from_signals(downlink_signals)
        table = self._table.take(rows)
        uplink = ground_directions(
            station=up.transmitter,
            satellite=up.receiver,
            path_km=up.path_km,
            blocked=up.blocked,
            table=table,
        )
        downlink = ground_directions(
            station=down.receiver,
            satellite=down.transmitter,
            path_km=down.path_km,
            blocked=down.blocked,
            table=table,
        )
        return ground_pair_verdicts(uplink, downlink)


def _limits(
    gs_geo: GeoPosition,
    min_elevation_deg: float,
    gs_profile: TerminalPhysicsProfile | None,
    sat_profile: TerminalPhysicsProfile | None,
) -> GroundLimits:
    if gs_profile is None or sat_profile is None:
        return GroundLimits(gs_geo.lat_deg, gs_geo.lon_deg, min_elevation_deg)
    return GroundLimits(
        gs_lat_deg=gs_geo.lat_deg,
        gs_lon_deg=gs_geo.lon_deg,
        min_elevation_deg=min_elevation_deg,
        gs_max_range_km=gs_profile.max_range_km,
        sat_max_range_km=sat_profile.max_range_km,
        gs_boresight=gs_profile.boresight,
        gs_field_of_regard_deg=gs_profile.field_of_regard_deg,
        sat_boresight=sat_profile.boresight,
        sat_field_of_regard_deg=sat_profile.field_of_regard_deg,
        gs_max_tracking_rate_deg_s=gs_profile.max_tracking_rate_deg_s,
        sat_max_tracking_rate_deg_s=sat_profile.max_tracking_rate_deg_s,
    )


@dataclass(frozen=True)
class _Applied:
    """A decision's applied terminal constraints; None where not in effect (geometry-only)."""

    gs_max_range_km: float | None = None
    sat_max_range_km: float | None = None
    gs_field_of_regard_deg: float | None = None
    sat_field_of_regard_deg: float | None = None
    gs_max_tracking_rate_deg_s: float | None = None
    sat_max_tracking_rate_deg_s: float | None = None
    gs_boresight_mode: GroundBoresightMode | None = None
    sat_boresight_mode: SatGroundBoresightMode | None = None


def _applied_constraints(
    gs_profile: TerminalPhysicsProfile | None, sat_profile: TerminalPhysicsProfile | None
) -> _Applied:
    if gs_profile is None or sat_profile is None:
        return _Applied()
    return _Applied(
        gs_max_range_km=gs_profile.max_range_km,
        sat_max_range_km=sat_profile.max_range_km,
        gs_field_of_regard_deg=gs_profile.field_of_regard_deg,
        sat_field_of_regard_deg=sat_profile.field_of_regard_deg,
        gs_max_tracking_rate_deg_s=gs_profile.max_tracking_rate_deg_s,
        sat_max_tracking_rate_deg_s=sat_profile.max_tracking_rate_deg_s,
        gs_boresight_mode=getattr(gs_profile.boresight, "mode", None),
        sat_boresight_mode=getattr(sat_profile.boresight, "mode", None),
    )


def _validate_dwell_inputs(
    candidates: set[tuple[str, str]],
    lookahead: GroundPassLookahead,
) -> None:
    if lookahead.horizon_ticks <= 0:
        raise ValueError("longest-remaining-pass requires lookahead_horizon_ticks > 0")
    if lookahead.step_seconds <= 0:
        raise ValueError("Ground pass lookahead requires step_seconds > 0")
    candidate_gs_ids = {gs_id for gs_id, _sat_id in candidates}
    missing_horizons = sorted(candidate_gs_ids - set(lookahead.horizon_ticks_by_gs))
    if missing_horizons:
        raise ValueError(
            f"Ground pass lookahead is missing per-GS horizon for {', '.join(missing_horizons)}"
        )
    missing_bodies = sorted(candidate_gs_ids - set(lookahead.geometry.gs_reference_bodies))
    if missing_bodies:
        raise ValueError(
            f"Ground pass lookahead is missing reference_body for {', '.join(missing_bodies)}"
        )


def _estimate_remaining_visible_seconds(
    *,
    candidates: set[tuple[str, str]],
    gs_positions: Mapping[str, tuple[EcefVec3, GeoPosition]],
    gs_min_elevations: Mapping[str, float],
    lookahead: GroundPassLookahead,
    dwell_state: dict[tuple[str, str], DwellPassState] | None = None,
) -> dict[tuple[str, str], float]:
    """Estimate sampled remaining dwell time for visible GS/satellite pairs.

    The result is a sampled lower bound at OME tick resolution. A pair visible
    now and not visible at the next sample has 0 seconds of guaranteed sampled
    dwell remaining. A pair still visible at the end of the horizon receives
    the horizon duration; callers should treat that as "at least horizon".

    Identical results to the exhaustive oracle below, computed incrementally:
    each pair's pass frontier is carried in dwell_state across ticks, so a
    steady-state tick samples at most ONE new future tick per pair (frontier
    extension) and zero for pairs whose pass end is already known — instead
    of re-sampling the full horizon for every pair on every tick. Callers
    that do not thread dwell_state get correct results at exhaustive cost.
    """
    _validate_dwell_inputs(candidates, lookahead)
    if dwell_state is None:
        dwell_state = {}
    step_s = lookahead.step_seconds
    now = lookahead.step
    horizon_by_gs = lookahead.horizon_ticks_by_gs

    # Entries that cannot answer for the current tick are dead: the pass
    # ended (first_invisible <= now), or candidacy lapsed long enough that
    # the frontier fell behind (coverage gap; also prunes pairs that set
    # and never rose again, bounding the dict by live candidacy).
    dead = [
        pair
        for pair, entry in dwell_state.items()
        if (entry.first_invisible is not None and entry.first_invisible <= now)
        or entry.verified_visible_through < now
    ]
    for pair in dead:
        del dwell_state[pair]

    remaining: dict[tuple[str, str], float] = {}
    walk_start: dict[tuple[str, str], int] = {}  # pair -> first tick to sample
    for pair in candidates:
        h = horizon_by_gs[pair[0]]
        cap = now + h
        entry = dwell_state.get(pair)
        if entry is not None and entry.first_invisible is not None:
            # Pass end known (and > now, by invalidation above). Beyond the
            # per-GS cap the answer saturates at the horizon, exactly as a
            # fresh walk would cap.
            if entry.first_invisible > cap:
                remaining[pair] = h * step_s
            else:
                remaining[pair] = (entry.first_invisible - now - 1) * step_s
        elif entry is not None and entry.verified_visible_through >= cap:
            remaining[pair] = h * step_s
        else:
            walk_start[pair] = entry.verified_visible_through + 1 if entry else now + 1
            remaining[pair] = h * step_s  # provisional: survives-to-horizon

    if not walk_start:
        return remaining

    # Each unresolved pair samples PASS_SAMPLE_CHUNK_TICKS consecutive future ticks per signals
    # call, and the memo keeps what was verified, including ticks past the horizon cap: a pass
    # that stays visible costs one call per chunk instead of one per tick (one call per tick
    # measured 0.75 ms on the GEO dwell session, a quarter of its step).
    pending = dict(walk_start)
    while pending:
        sampled_pairs = [pair for pair in pending for _ in range(PASS_SAMPLE_CHUNK_TICKS)]
        ticks = np.array(
            [start + k for start in pending.values() for k in range(PASS_SAMPLE_CHUNK_TICKS)]
        )
        uplink, downlink = lookahead.signals_at(
            sampled_pairs, lookahead.epoch_unix + ticks * step_s
        ).in_order()
        verdicts = lookahead.geometry.verdicts(sampled_pairs, uplink, downlink)
        unresolved: dict[tuple[str, str], int] = {}
        for index, (pair, start) in enumerate(pending.items()):
            chunk = verdicts[
                index * PASS_SAMPLE_CHUNK_TICKS : (index + 1) * PASS_SAMPLE_CHUNK_TICKS
            ]
            h = horizon_by_gs[pair[0]]
            cap = now + h
            dark = next((k for k, verdict in enumerate(chunk) if not verdict.visible), None)
            if dark is not None:
                first_invisible = start + dark
                dwell_state[pair] = DwellPassState(
                    verified_visible_through=first_invisible - 1, first_invisible=first_invisible
                )
                if first_invisible <= cap:
                    remaining[pair] = (first_invisible - now - 1) * step_s
                continue
            verified = start + PASS_SAMPLE_CHUNK_TICKS - 1
            dwell_state[pair] = DwellPassState(
                verified_visible_through=verified, first_invisible=None
            )
            if verified < cap:
                unresolved[pair] = verified + 1
        pending = unresolved
    return remaining


def _estimate_remaining_visible_seconds_exhaustive(
    *,
    candidates: set[tuple[str, str]],
    gs_positions: Mapping[str, tuple[EcefVec3, GeoPosition]],
    gs_min_elevations: Mapping[str, float],
    lookahead: GroundPassLookahead,
) -> dict[tuple[str, str], float]:
    """The full-horizon walk, retained as the equivalence oracle.

    Samples every future tick of every pair's horizon on every call, with no
    memo, in one signals call (each sample is independent of the others).
    Production uses the frontier walker above; tests prove the two return
    identical results. Shares GroundGeometry.verdicts so the physics cannot
    drift.
    """
    _validate_dwell_inputs(candidates, lookahead)
    step_s = lookahead.step_seconds
    remaining = {pair: lookahead.horizon_ticks_by_gs[pair[0]] * step_s for pair in candidates}
    ordered = sorted(candidates)
    if not ordered:
        return remaining
    sampled = [
        (pair, offset)
        for pair in ordered
        for offset in range(1, lookahead.horizon_ticks_by_gs[pair[0]] + 1)
    ]
    pairs = [pair for pair, _offset in sampled]
    ticks = np.array([lookahead.step + offset for _pair, offset in sampled])
    uplink, downlink = lookahead.signals_at(pairs, lookahead.epoch_unix + ticks * step_s).in_order()
    verdicts = lookahead.geometry.verdicts(pairs, uplink, downlink)
    closed: set[tuple[str, str]] = set()
    for (pair, offset), verdict in zip(sampled, verdicts, strict=True):
        if pair in closed or verdict.visible:
            continue
        remaining[pair] = (offset - 1) * step_s
        closed.add(pair)
    return remaining


def evaluate_ground_visibility(
    *,
    satellite_ids: Iterable[str],
    sat_states: Mapping[str, PropagatedState],
    gs_positions: Mapping[str, tuple[EcefVec3, GeoPosition]],
    gs_min_elevations: Mapping[str, float],
    gs_tenant_ids: Mapping[str, str],
    gs_reference_bodies: Mapping[str, str],
    body_frames: Mapping[str, BodyFrame],
    gs_selection_policy_names: Mapping[str, str] | None = None,
    pass_lookahead: GroundPassLookahead | None = None,
    ground_link_model: Literal["geometry_only", "terminal_physics"] = "terminal_physics",
    gs_terminal_profiles: Mapping[str, TerminalPhysicsProfile] | None = None,
    sat_ground_terminal_profiles: Mapping[str, TerminalPhysicsProfileSet] | None = None,
    candidate_satellite_ids_by_gs: Mapping[str, Iterable[str]] | None = None,
    timings: StepTimings | None = None,
    dwell_state: dict[tuple[str, str], DwellPassState] | None = None,
    geometry: GroundGeometry,
    signals: PairSignals,
    station_positions_common: Mapping[str, CommonVec3],
) -> GroundVisibilityEvaluation:
    """Evaluate geometric GS/satellite visibility for one tick.

    Missing propagated satellite state is fatal. Missing per-GS
    `tenant_id` or `reference_body` is fatal — Direction 2 and
    Direction 3 require every decision to carry both. The ground
    allocator must not receive a candidate set that silently omits a
    satellite because the propagation boundary failed upstream, and
    consumers must not receive decisions whose tenant or body context
    is unknown.

    In `terminal_physics`, both endpoint terminal profiles are required and
    range, field-of-regard, and topocentric tracking-rate constraints are
    applied before a pair is allowed into the allocator. In
    `geometry_only`, those fields are deliberately absent and the caller
    must have passed the explicit session-level acknowledgement gate.
    """
    ordered_satellite_ids = tuple(satellite_ids)
    all_satellite_ids = set(ordered_satellite_ids)
    if candidate_satellite_ids_by_gs is None and gs_positions:
        raise ValueError(
            "Ground visibility requires declared access candidates for every ground station"
        )
    candidates_by_gs = {}
    candidate_map = candidate_satellite_ids_by_gs or {}
    missing_candidates = sorted(set(gs_positions) - set(candidate_map))
    if missing_candidates:
        raise ValueError(
            "Ground visibility is missing declared access candidates for "
            f"{', '.join(missing_candidates)}"
        )
    for gs_id in sorted(gs_positions):
        candidates = tuple(candidate_map[gs_id])
        unknown = sorted(set(candidates) - all_satellite_ids)
        if unknown:
            raise ValueError(
                f"Ground visibility candidates for {gs_id} reference unknown "
                f"satellite id(s): {', '.join(unknown)}"
            )
        candidates_by_gs[gs_id] = candidates
    declared_satellite_ids = sorted({sat_id for ids in candidates_by_gs.values() for sat_id in ids})
    decisions: dict[tuple[str, str], GroundVisibilityDecision] = {}
    visible_per_station: dict[str, list[GroundVisibility]] = {}

    selection_policy_names = gs_selection_policy_names or {}
    if gs_selection_policy_names is not None:
        missing_policies = sorted(set(gs_positions) - set(gs_selection_policy_names))
        if missing_policies:
            raise ValueError(
                f"Ground visibility is missing selection policy name for {', '.join(missing_policies)}"
            )
    missing_min_elev = sorted(set(gs_positions) - set(gs_min_elevations))
    if missing_min_elev:
        raise ValueError(
            "Ground visibility is missing minimum elevation config for "
            f"{', '.join(missing_min_elev)}"
        )
    missing_tenant = sorted(set(gs_positions) - set(gs_tenant_ids))
    if missing_tenant:
        raise ValueError(
            "Ground visibility is missing tenant_id for "
            f"{', '.join(missing_tenant)} — Direction 2 requires every decision "
            "to carry tenant scope from day one"
        )
    missing_body = sorted(set(gs_positions) - set(gs_reference_bodies))
    if missing_body:
        raise ValueError(
            "Ground visibility is missing reference_body for "
            f"{', '.join(missing_body)} — Direction 3 requires every decision "
            "to be anchored to a specific body"
        )
    if ground_link_model == "terminal_physics" and gs_positions:
        missing_gs_profiles = sorted(set(gs_positions) - set(gs_terminal_profiles or {}))
        if missing_gs_profiles:
            raise ValueError(
                "terminal_physics ground visibility is missing ground terminal profiles for "
                f"{', '.join(missing_gs_profiles)}"
            )
        missing_sat_profiles = sorted(
            set(declared_satellite_ids) - set(sat_ground_terminal_profiles or {})
        )
        if missing_sat_profiles:
            raise ValueError(
                "terminal_physics ground visibility is missing satellite ground terminal profiles for "
                f"{', '.join(missing_sat_profiles)}"
            )

    longest_pass_station_ids = {
        gs_id
        for gs_id in gs_positions
        if selection_policy_names.get(gs_id) == "longest-remaining-pass"
    }
    if longest_pass_station_ids and pass_lookahead is None:
        raise ValueError(
            "Ground scheduling policy 'longest-remaining-pass' requires pass lookahead config"
        )

    for reference_body in sorted({gs_reference_bodies[gs_id] for gs_id in gs_positions}):
        if reference_body not in body_frames:
            raise ValueError(
                f"Ground visibility is missing resolved body primitive facts "
                f"for reference_body={reference_body!r}"
            )
    pairs: list[tuple[str, str]] = []
    for gs_id in gs_positions:
        reference_body = gs_reference_bodies[gs_id]
        for sat_id in candidates_by_gs[gs_id]:
            state = sat_states.get(sat_id)
            if state is None:
                raise ValueError(
                    f"Missing propagated satellite state for {sat_id}; "
                    "ground visibility cannot be evaluated authoritatively"
                )
            pairs.append((gs_id, sat_id))

    uplink, downlink = signals.directions(pairs)
    verdicts = geometry.verdicts(pairs, uplink, downlink)

    visible_candidates_requiring_dwell: set[tuple[str, str]] = set()
    for gs_id in gs_positions:
        visible_per_station[gs_id] = []
    for row, ((gs_id, sat_id), verdict) in enumerate(zip(pairs, verdicts, strict=True)):
        gs_profile, sat_profile = geometry.profiles(gs_id, sat_id)
        applied = _applied_constraints(gs_profile, sat_profile)
        pair = (min(gs_id, sat_id), max(gs_id, sat_id))
        # Each direction's one-way light time of a signal sent at this tick, in pair order.
        up_ms = float(uplink.light_time_s[row]) * 1000.0
        down_ms = float(downlink.light_time_s[row]) * 1000.0
        decisions[pair] = GroundVisibilityDecision(
            pair=pair,
            tenant_id=gs_tenant_ids[gs_id],
            reference_body=gs_reference_bodies[gs_id],
            visible=verdict.visible,
            # Same-instant distance in the common frame, for a station and a satellite of one
            # body or of two.
            range_km=compute_range_km(
                station_positions_common[gs_id], sat_states[sat_id].position_common_km
            ),
            latency_a_to_b_ms=up_ms if pair[0] == gs_id else down_ms,
            latency_b_to_a_ms=down_ms if pair[0] == gs_id else up_ms,
            elevation_deg=verdict.elevation_deg,
            azimuth_deg=verdict.azimuth_deg,
            sat_off_nadir_deg=verdict.sat_off_nadir_deg,
            observer_frame="body_local",
            reject_reason=verdict.reason,
            rejecting_endpoint=verdict.rejecting_endpoint,
            applied_min_elevation_deg=gs_min_elevations[gs_id],
            applied_gs_max_range_km=applied.gs_max_range_km,
            applied_sat_max_range_km=applied.sat_max_range_km,
            applied_gs_field_of_regard_deg=applied.gs_field_of_regard_deg,
            applied_sat_field_of_regard_deg=applied.sat_field_of_regard_deg,
            applied_gs_max_tracking_rate_deg_s=applied.gs_max_tracking_rate_deg_s,
            applied_sat_max_tracking_rate_deg_s=applied.sat_max_tracking_rate_deg_s,
            applied_gs_boresight_mode=applied.gs_boresight_mode,
            applied_sat_boresight_mode=applied.sat_boresight_mode,
            applied_gs_terminal_profile=gs_profile.profile_id if gs_profile else None,
            applied_sat_terminal_profile=sat_profile.profile_id if sat_profile else None,
        )
        if verdict.visible:
            if gs_id in longest_pass_station_ids:
                visible_candidates_requiring_dwell.add((gs_id, sat_id))
            visible_per_station[gs_id].append(
                GroundVisibility(
                    sat_id=sat_id,
                    visible=True,
                    elevation_deg=verdict.elevation_deg,
                    range_km=decisions[pair].range_km,
                    remaining_visible_s=None,
                    reject_reason=verdict.reason,
                    azimuth_deg=verdict.azimuth_deg,
                    sat_off_nadir_deg=verdict.sat_off_nadir_deg,
                ),
            )

    remaining_by_pair: dict[tuple[str, str], float] = {}
    if visible_candidates_requiring_dwell:
        if pass_lookahead is None:
            raise ValueError(
                "Ground scheduling policy 'longest-remaining-pass' requires pass lookahead config"
            )
        with (timings or StepTimings()).measure(SEG_DWELL):
            remaining_by_pair = _estimate_remaining_visible_seconds(
                candidates=visible_candidates_requiring_dwell,
                gs_positions=gs_positions,
                gs_min_elevations=gs_min_elevations,
                lookahead=pass_lookahead,
                dwell_state=dwell_state,
            )
        for gs_id, visible_sats in list(visible_per_station.items()):
            if gs_id not in longest_pass_station_ids:
                continue
            visible_per_station[gs_id] = [
                GroundVisibility(
                    sat_id=gv.sat_id,
                    visible=gv.visible,
                    elevation_deg=gv.elevation_deg,
                    range_km=gv.range_km,
                    remaining_visible_s=remaining_by_pair[(gs_id, gv.sat_id)],
                    reject_reason=gv.reject_reason,
                    azimuth_deg=gv.azimuth_deg,
                    sat_off_nadir_deg=gv.sat_off_nadir_deg,
                )
                for gv in visible_sats
            ]

    return GroundVisibilityEvaluation(
        decisions=decisions,
        visible_per_station=visible_per_station,
    )
