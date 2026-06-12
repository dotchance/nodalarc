# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME ground visibility evaluation engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nodalarc.body_frames import BodyFrame
from nodalarc.constellation_loader import SatelliteNode, satellite_node_id
from nodalarc.ephemeris_runtime import SkyfieldBspEphemeris, body_states_at
from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.ground_terminals import TerminalPhysicsProfile
from nodalarc.models.addressing import AddressingScheme

from ome.propagation_engine import PropagatedState, propagate_satellites
from ome.telemetry import SEG_DWELL, StepTimings
from ome.types import GroundVisibilityDecision, GroundVisibilityDecisionMap
from ome.visibility import GroundVisibility, check_ground_visibility

TerminalPhysicsProfileSet = TerminalPhysicsProfile | Sequence[TerminalPhysicsProfile]


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
    the engine raises if the horizon is not positive.
    """

    satellites: tuple[SatelliteNode, ...]
    addressing: AddressingScheme
    epoch_unix: float
    step: int
    step_seconds: int
    horizon_ticks: int
    horizon_ticks_by_gs: Mapping[str, int]
    gs_reference_bodies: Mapping[str, str]
    body_frames: Mapping[str, BodyFrame]
    propagator_id: str
    ground_link_model: Literal["geometry_only", "terminal_physics"] = "terminal_physics"
    gs_terminal_profiles: Mapping[str, TerminalPhysicsProfile] | None = None
    sat_ground_terminal_profiles: Mapping[str, TerminalPhysicsProfileSet] | None = None
    body_ephemeris: SkyfieldBspEphemeris | None = None
    active_bodies: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.active_bodies:
            raise ValueError("GroundPassLookahead requires active_bodies from StepContext")


def _require_complete_profile(
    profile: TerminalPhysicsProfile,
    *,
    node_id: str,
    label: str,
) -> TerminalPhysicsProfile:
    if (
        profile.max_range_km is None
        or profile.field_of_regard_deg is None
        or profile.max_tracking_rate_deg_s is None
        or profile.boresight is None
        or profile.profile_id is None
    ):
        raise ValueError(f"terminal_physics ground visibility has incomplete {label} for {node_id}")
    return profile


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
    return _require_complete_profile(profiles[node_id], node_id=node_id, label=label)


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
    return _require_complete_profile(
        matches[0],
        node_id=sat_id,
        label="satellite ground terminal profile",
    )


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


def _pair_visible_at(
    *,
    lookahead: GroundPassLookahead,
    gs_positions: Mapping[str, tuple[EcefVec3, GeoPosition]],
    gs_min_elevations: Mapping[str, float],
    gs_id: str,
    sat_id: str,
    state: PropagatedState,
) -> bool:
    """One pair's visibility check against a propagated future state.

    Single source of the per-pair physics for BOTH the production frontier
    walker and the exhaustive oracle — the two may differ only in which
    ticks they sample, never in what a sample computes.
    """
    gs_ecef, gs_geo = gs_positions[gs_id]
    reference_body = lookahead.gs_reference_bodies[gs_id]
    try:
        body_frame = lookahead.body_frames[reference_body]
    except KeyError as exc:
        raise ValueError(
            f"Ground pass lookahead is missing resolved body primitive facts "
            f"for reference_body={reference_body!r}"
        ) from exc
    gs_profile = _physical_profile(
        lookahead.gs_terminal_profiles,
        gs_id,
        label="ground terminal profile",
        ground_link_model=lookahead.ground_link_model,
    )
    sat_profile = _sat_physical_profile(
        lookahead.sat_ground_terminal_profiles,
        sat_id,
        reference_body=reference_body,
        ground_link_model=lookahead.ground_link_model,
    )
    kwargs = {"body_frame": body_frame}
    if gs_profile is not None and sat_profile is not None:
        kwargs = {
            "gs_max_range_km": gs_profile.max_range_km,
            "sat_max_range_km": sat_profile.max_range_km,
            "gs_boresight": gs_profile.boresight,
            "sat_boresight": sat_profile.boresight,
            "gs_field_of_regard_deg": gs_profile.field_of_regard_deg,
            "sat_field_of_regard_deg": sat_profile.field_of_regard_deg,
            "gs_max_tracking_rate_deg_s": gs_profile.max_tracking_rate_deg_s,
            "sat_max_tracking_rate_deg_s": sat_profile.max_tracking_rate_deg_s,
            "sat_velocity_ecef_km_s": state.velocity_ecef_km_s,
        }
    return check_ground_visibility(
        gs_ecef,
        gs_geo,
        state.position_ecef_km,
        gs_min_elevations[gs_id],
        **kwargs,
    ).visible


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
    missing_bodies = sorted(candidate_gs_ids - set(lookahead.gs_reference_bodies))
    if missing_bodies:
        raise ValueError(
            f"Ground pass lookahead is missing reference_body for {', '.join(missing_bodies)}"
        )


def _propagate_for_tick(
    lookahead: GroundPassLookahead,
    satellites: list,
    t_abs: int,
) -> Mapping[str, PropagatedState]:
    """Propagate the given satellites to absolute tick t_abs.

    future_dt reproduces the original walk's arithmetic exactly:
    (step + tick_offset) * step_seconds == t_abs * step_seconds.
    """
    future_dt = t_abs * lookahead.step_seconds
    return propagate_satellites(
        satellites=satellites,
        addressing=lookahead.addressing,
        epoch_unix=lookahead.epoch_unix,
        dt=future_dt,
        propagator_id=lookahead.propagator_id,
        body_frames=lookahead.body_frames,
        body_states=body_states_at(
            lookahead.body_ephemeris,
            set(lookahead.active_bodies),
            lookahead.epoch_unix + future_dt,
        ),
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

    sat_nodes_by_id: dict[str, object] = {}
    for sat in lookahead.satellites:
        sat_nodes_by_id[satellite_node_id(sat, lookahead.addressing)] = sat

    open_caps = {pair: now + horizon_by_gs[pair[0]] for pair in walk_start}
    t_abs = min(walk_start.values())
    t_last = max(open_caps.values())
    while t_abs <= t_last and open_caps:
        # Pairs whose cap fell before this tick are verified through it.
        for pair in [p for p, cap in open_caps.items() if cap < t_abs]:
            dwell_state[pair] = DwellPassState(
                verified_visible_through=open_caps.pop(pair), first_invisible=None
            )
            del walk_start[pair]
        active = [p for p, start in walk_start.items() if start <= t_abs and p in open_caps]
        if active:
            needed_ids = sorted({sat_id for _gs, sat_id in active})
            try:
                nodes = [sat_nodes_by_id[sat_id] for sat_id in needed_ids]
            except KeyError as exc:
                raise ValueError(
                    f"Missing propagated satellite state for {exc.args[0]}; "
                    "ground pass lookahead cannot be evaluated authoritatively"
                ) from exc
            states = _propagate_for_tick(lookahead, nodes, t_abs)
            for pair in active:
                gs_id, sat_id = pair
                state = states.get(sat_id)
                if state is None:
                    raise ValueError(
                        f"Missing propagated satellite state for {sat_id}; "
                        "ground pass lookahead cannot be evaluated authoritatively"
                    )
                visible = _pair_visible_at(
                    lookahead=lookahead,
                    gs_positions=gs_positions,
                    gs_min_elevations=gs_min_elevations,
                    gs_id=gs_id,
                    sat_id=sat_id,
                    state=state,
                )
                if not visible:
                    remaining[pair] = (t_abs - now - 1) * step_s
                    dwell_state[pair] = DwellPassState(
                        verified_visible_through=t_abs - 1, first_invisible=t_abs
                    )
                    del open_caps[pair]
                    del walk_start[pair]
        t_abs += 1

    # Survivors are verified visible through their caps.
    for pair, cap in open_caps.items():
        dwell_state[pair] = DwellPassState(verified_visible_through=cap, first_invisible=None)
    return remaining


def _estimate_remaining_visible_seconds_exhaustive(
    *,
    candidates: set[tuple[str, str]],
    gs_positions: Mapping[str, tuple[EcefVec3, GeoPosition]],
    gs_min_elevations: Mapping[str, float],
    lookahead: GroundPassLookahead,
) -> dict[tuple[str, str], float]:
    """The original full-horizon walk, retained as the equivalence oracle.

    Re-samples every future tick for every open pair on every call,
    propagating the full constellation per sampled tick. Production uses
    the frontier walker above; tests prove the two return identical
    results. Shares _pair_visible_at so the physics cannot drift.
    """
    _validate_dwell_inputs(candidates, lookahead)
    remaining = {
        pair: lookahead.horizon_ticks_by_gs[pair[0]] * lookahead.step_seconds for pair in candidates
    }
    open_pairs = set(candidates)
    if not open_pairs:
        return remaining

    for tick_offset in range(1, lookahead.horizon_ticks + 1):
        future_states = _propagate_for_tick(
            lookahead, list(lookahead.satellites), lookahead.step + tick_offset
        )
        for gs_id, sat_id in tuple(open_pairs):
            if tick_offset > lookahead.horizon_ticks_by_gs[gs_id]:
                open_pairs.remove((gs_id, sat_id))
                continue
            state = future_states.get(sat_id)
            if state is None:
                raise ValueError(
                    f"Missing propagated satellite state for {sat_id}; "
                    "ground pass lookahead cannot be evaluated authoritatively"
                )
            visible = _pair_visible_at(
                lookahead=lookahead,
                gs_positions=gs_positions,
                gs_min_elevations=gs_min_elevations,
                gs_id=gs_id,
                sat_id=sat_id,
                state=state,
            )
            if not visible:
                remaining[(gs_id, sat_id)] = (tick_offset - 1) * lookahead.step_seconds
                open_pairs.remove((gs_id, sat_id))

        if not open_pairs:
            break

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

    visible_candidates_requiring_dwell: set[tuple[str, str]] = set()
    for gs_id, (gs_ecef, gs_geo) in gs_positions.items():
        min_elev = gs_min_elevations[gs_id]
        tenant_id = gs_tenant_ids[gs_id]
        reference_body = gs_reference_bodies[gs_id]
        visible_sats: list[GroundVisibility] = []
        for sat_id in candidates_by_gs[gs_id]:
            state = sat_states.get(sat_id)
            if state is None:
                raise ValueError(
                    f"Missing propagated satellite state for {sat_id}; "
                    "ground visibility cannot be evaluated authoritatively"
                )
            if state.central_body != reference_body:
                # The resolver rejects cross-body access candidates; if one
                # reaches this engine anyway, evaluating it would mix two
                # body-fixed frames and emit plausible-looking garbage.
                raise ValueError(
                    f"cross-body ground visibility pair {gs_id}<->{sat_id}: "
                    f"GS reference_body={reference_body!r} vs satellite "
                    f"central_body={state.central_body!r}; access visibility is body-local"
                )

            try:
                body_frame = body_frames[reference_body]
            except KeyError as exc:
                raise ValueError(
                    f"Ground visibility is missing resolved body primitive facts "
                    f"for reference_body={reference_body!r}"
                ) from exc
            gs_profile = _physical_profile(
                gs_terminal_profiles,
                gs_id,
                label="ground terminal profile",
                ground_link_model=ground_link_model,
            )
            sat_profile = _sat_physical_profile(
                sat_ground_terminal_profiles,
                sat_id,
                reference_body=reference_body,
                ground_link_model=ground_link_model,
            )
            gs_max_range_km = None
            sat_max_range_km = None
            gs_field_of_regard_deg = None
            sat_field_of_regard_deg = None
            gs_max_tracking_rate_deg_s = None
            sat_max_tracking_rate_deg_s = None
            gs_boresight_mode = None
            sat_boresight_mode = None
            kwargs = {"body_frame": body_frame}
            if gs_profile is not None and sat_profile is not None:
                gs_max_range_km = gs_profile.max_range_km
                sat_max_range_km = sat_profile.max_range_km
                gs_field_of_regard_deg = gs_profile.field_of_regard_deg
                sat_field_of_regard_deg = sat_profile.field_of_regard_deg
                gs_max_tracking_rate_deg_s = gs_profile.max_tracking_rate_deg_s
                sat_max_tracking_rate_deg_s = sat_profile.max_tracking_rate_deg_s
                gs_boresight_mode = getattr(gs_profile.boresight, "mode", None)
                sat_boresight_mode = getattr(sat_profile.boresight, "mode", None)
                kwargs.update(
                    {
                        "gs_max_range_km": gs_max_range_km,
                        "sat_max_range_km": sat_max_range_km,
                        "gs_boresight": gs_profile.boresight,
                        "sat_boresight": sat_profile.boresight,
                        "gs_field_of_regard_deg": gs_field_of_regard_deg,
                        "sat_field_of_regard_deg": sat_field_of_regard_deg,
                        "gs_max_tracking_rate_deg_s": gs_max_tracking_rate_deg_s,
                        "sat_max_tracking_rate_deg_s": sat_max_tracking_rate_deg_s,
                        "sat_velocity_ecef_km_s": state.velocity_ecef_km_s,
                    }
                )

            gv = check_ground_visibility(
                gs_ecef,
                gs_geo,
                state.position_ecef_km,
                min_elev,
                **kwargs,
            )
            pair = (min(gs_id, sat_id), max(gs_id, sat_id))
            decisions[pair] = GroundVisibilityDecision(
                pair=pair,
                tenant_id=tenant_id,
                reference_body=reference_body,
                visible=gv.visible,
                range_km=gv.range_km,
                elevation_deg=gv.elevation_deg,
                azimuth_deg=gv.azimuth_deg,
                sat_off_nadir_deg=gv.sat_off_nadir_deg,
                observer_frame="body_local",
                reject_reason=gv.reject_reason,
                rejecting_endpoint=gv.rejecting_endpoint,
                applied_min_elevation_deg=min_elev,
                applied_gs_max_range_km=gs_max_range_km,
                applied_sat_max_range_km=sat_max_range_km,
                applied_gs_field_of_regard_deg=gs_field_of_regard_deg,
                applied_sat_field_of_regard_deg=sat_field_of_regard_deg,
                applied_gs_max_tracking_rate_deg_s=gs_max_tracking_rate_deg_s,
                applied_sat_max_tracking_rate_deg_s=sat_max_tracking_rate_deg_s,
                applied_gs_boresight_mode=gs_boresight_mode,
                applied_sat_boresight_mode=sat_boresight_mode,
                applied_gs_terminal_profile=gs_profile.profile_id if gs_profile else None,
                applied_sat_terminal_profile=sat_profile.profile_id if sat_profile else None,
            )
            if gv.visible:
                if gs_id in longest_pass_station_ids:
                    visible_candidates_requiring_dwell.add((gs_id, sat_id))
                visible_sats.append(
                    GroundVisibility(
                        sat_id=sat_id,
                        visible=gv.visible,
                        elevation_deg=gv.elevation_deg,
                        range_km=gv.range_km,
                        remaining_visible_s=None,
                        reject_reason=gv.reject_reason,
                        azimuth_deg=gv.azimuth_deg,
                        sat_off_nadir_deg=gv.sat_off_nadir_deg,
                    ),
                )
        visible_per_station[gs_id] = visible_sats

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
