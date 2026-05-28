# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME ground visibility evaluation engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nodalarc.body_frames import body_frame_for
from nodalarc.constellation_loader import SatelliteNode
from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.ground_terminals import TerminalPhysicsProfile
from nodalarc.models.addressing import AddressingScheme

from ome.propagation_engine import PropagatedState, propagate_satellites
from ome.types import GroundVisibilityDecision, GroundVisibilityDecisionMap
from ome.visibility import GroundVisibility, check_ground_visibility

TerminalPhysicsProfileSet = TerminalPhysicsProfile | Sequence[TerminalPhysicsProfile]


@dataclass(frozen=True)
class GroundVisibilityEvaluation:
    """Ground visibility output for one OME tick.

    `decisions` carries the typed per-pair decision (visibility,
    range, elevation, applied constraints, reject reason). Replaces
    the legacy positional `details` tuple alias in Phase 1.2.b.
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
    propagator_id: str
    simulation_fidelity: Literal["geometry_only", "physical_v1"] = "physical_v1"
    gs_reference_bodies: Mapping[str, str] | None = None
    gs_terminal_profiles: Mapping[str, TerminalPhysicsProfile] | None = None
    sat_ground_terminal_profiles: Mapping[str, TerminalPhysicsProfileSet] | None = None


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
        raise ValueError(f"physical_v1 ground visibility has incomplete {label} for {node_id}")
    return profile


def _physical_profile(
    profiles: Mapping[str, TerminalPhysicsProfile] | None,
    node_id: str,
    *,
    label: str,
    fidelity: Literal["geometry_only", "physical_v1"],
) -> TerminalPhysicsProfile | None:
    if fidelity == "geometry_only":
        return None
    if profiles is None or node_id not in profiles:
        raise ValueError(f"physical_v1 ground visibility is missing {label} for {node_id}")
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
    fidelity: Literal["geometry_only", "physical_v1"],
) -> TerminalPhysicsProfile | None:
    if fidelity == "geometry_only":
        return None
    if profiles is None or sat_id not in profiles:
        raise ValueError(
            f"physical_v1 ground visibility is missing satellite ground terminal profile for {sat_id}"
        )
    options = _profile_options(profiles[sat_id])
    if not options:
        raise ValueError(
            f"physical_v1 ground visibility is missing satellite ground terminal profile for {sat_id}"
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


def _estimate_remaining_visible_seconds(
    *,
    candidates: set[tuple[str, str]],
    gs_positions: Mapping[str, tuple[EcefVec3, GeoPosition]],
    gs_min_elevations: Mapping[str, float],
    lookahead: GroundPassLookahead,
) -> dict[tuple[str, str], float]:
    """Estimate sampled remaining dwell time for visible GS/satellite pairs.

    The result is a sampled lower bound at OME tick resolution. A pair visible
    now and not visible at the next sample has 0 seconds of guaranteed sampled
    dwell remaining. A pair still visible at the end of the horizon receives
    the horizon duration; callers should treat that as "at least horizon".
    """
    if lookahead.horizon_ticks <= 0:
        raise ValueError("longest-remaining-pass requires lookahead_horizon_ticks > 0")
    if lookahead.step_seconds <= 0:
        raise ValueError("Ground pass lookahead requires step_seconds > 0")

    remaining = dict.fromkeys(candidates, lookahead.horizon_ticks * lookahead.step_seconds)
    open_pairs = set(candidates)
    if not open_pairs:
        return remaining

    for tick_offset in range(1, lookahead.horizon_ticks + 1):
        future_dt = (lookahead.step + tick_offset) * lookahead.step_seconds
        future_states = propagate_satellites(
            satellites=list(lookahead.satellites),
            addressing=lookahead.addressing,
            epoch_unix=lookahead.epoch_unix,
            dt=future_dt,
            propagator_id=lookahead.propagator_id,
        )

        for gs_id, sat_id in tuple(open_pairs):
            gs_ecef, gs_geo = gs_positions[gs_id]
            state = future_states.get(sat_id)
            if state is None:
                raise ValueError(
                    f"Missing propagated satellite state for {sat_id}; "
                    "ground pass lookahead cannot be evaluated authoritatively"
                )
            reference_body = (lookahead.gs_reference_bodies or {}).get(gs_id, "earth")
            body_frame = body_frame_for(reference_body)
            gs_profile = _physical_profile(
                lookahead.gs_terminal_profiles,
                gs_id,
                label="ground terminal profile",
                fidelity=lookahead.simulation_fidelity,
            )
            sat_profile = _sat_physical_profile(
                lookahead.sat_ground_terminal_profiles,
                sat_id,
                reference_body=reference_body,
                fidelity=lookahead.simulation_fidelity,
            )
            kwargs = {}
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
                    "body_frame": body_frame,
                }
            visible = check_ground_visibility(
                gs_ecef,
                gs_geo,
                state.position_ecef_km,
                gs_min_elevations[gs_id],
                **kwargs,
            ).visible
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
    gs_policies: Mapping[str, str] | None = None,
    pass_lookahead: GroundPassLookahead | None = None,
    simulation_fidelity: Literal["geometry_only", "physical_v1"] = "physical_v1",
    gs_terminal_profiles: Mapping[str, TerminalPhysicsProfile] | None = None,
    sat_ground_terminal_profiles: Mapping[str, TerminalPhysicsProfile] | None = None,
) -> GroundVisibilityEvaluation:
    """Evaluate geometric GS/satellite visibility for one tick.

    Missing propagated satellite state is fatal. Missing per-GS
    `tenant_id` or `reference_body` is fatal — Direction 2 and
    Direction 3 require every decision to carry both. The ground
    allocator must not receive a candidate set that silently omits a
    satellite because the propagation boundary failed upstream, and
    consumers must not receive decisions whose tenant or body context
    is unknown.

    In `physical_v1`, both endpoint terminal profiles are required and
    range, field-of-regard, and topocentric tracking-rate constraints are
    applied before a pair is allowed into the allocator. In
    `geometry_only`, those fields are deliberately absent and the caller
    must have passed the explicit session-level acknowledgement gate.
    """
    ordered_satellite_ids = tuple(satellite_ids)
    decisions: dict[tuple[str, str], GroundVisibilityDecision] = {}
    visible_per_station: dict[str, list[GroundVisibility]] = {}

    policies = gs_policies or {}
    if gs_policies is not None:
        missing_policies = sorted(set(gs_positions) - set(gs_policies))
        if missing_policies:
            raise ValueError(
                f"Ground visibility is missing scheduling policy for {', '.join(missing_policies)}"
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
    if simulation_fidelity == "physical_v1" and gs_positions:
        missing_gs_profiles = sorted(set(gs_positions) - set(gs_terminal_profiles or {}))
        if missing_gs_profiles:
            raise ValueError(
                "physical_v1 ground visibility is missing ground terminal profiles for "
                f"{', '.join(missing_gs_profiles)}"
            )
        missing_sat_profiles = sorted(
            set(ordered_satellite_ids) - set(sat_ground_terminal_profiles or {})
        )
        if missing_sat_profiles:
            raise ValueError(
                "physical_v1 ground visibility is missing satellite ground terminal profiles for "
                f"{', '.join(missing_sat_profiles)}"
            )

    longest_pass_station_ids = {
        gs_id for gs_id in gs_positions if policies.get(gs_id) == "longest-remaining-pass"
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
        for sat_id in ordered_satellite_ids:
            state = sat_states.get(sat_id)
            if state is None:
                raise ValueError(
                    f"Missing propagated satellite state for {sat_id}; "
                    "ground visibility cannot be evaluated authoritatively"
                )

            body_frame = body_frame_for(reference_body)
            gs_profile = _physical_profile(
                gs_terminal_profiles,
                gs_id,
                label="ground terminal profile",
                fidelity=simulation_fidelity,
            )
            sat_profile = _sat_physical_profile(
                sat_ground_terminal_profiles,
                sat_id,
                reference_body=reference_body,
                fidelity=simulation_fidelity,
            )
            gs_max_range_km = None
            sat_max_range_km = None
            max_range_km = None
            gs_field_of_regard_deg = None
            sat_field_of_regard_deg = None
            field_of_regard_deg = None
            gs_max_tracking_rate_deg_s = None
            sat_max_tracking_rate_deg_s = None
            max_tracking_rate_deg_s = None
            gs_boresight_mode = None
            sat_boresight_mode = None
            kwargs = {}
            if gs_profile is not None and sat_profile is not None:
                gs_max_range_km = gs_profile.max_range_km
                sat_max_range_km = sat_profile.max_range_km
                max_range_km = min(gs_max_range_km, sat_max_range_km)
                gs_field_of_regard_deg = gs_profile.field_of_regard_deg
                sat_field_of_regard_deg = sat_profile.field_of_regard_deg
                field_of_regard_deg = min(gs_field_of_regard_deg, sat_field_of_regard_deg)
                gs_max_tracking_rate_deg_s = gs_profile.max_tracking_rate_deg_s
                sat_max_tracking_rate_deg_s = sat_profile.max_tracking_rate_deg_s
                max_tracking_rate_deg_s = min(
                    gs_max_tracking_rate_deg_s,
                    sat_max_tracking_rate_deg_s,
                )
                gs_boresight_mode = getattr(gs_profile.boresight, "mode", None)
                sat_boresight_mode = getattr(sat_profile.boresight, "mode", None)
                kwargs = {
                    "gs_max_range_km": gs_max_range_km,
                    "sat_max_range_km": sat_max_range_km,
                    "gs_boresight": gs_profile.boresight,
                    "sat_boresight": sat_profile.boresight,
                    "gs_field_of_regard_deg": gs_field_of_regard_deg,
                    "sat_field_of_regard_deg": sat_field_of_regard_deg,
                    "gs_max_tracking_rate_deg_s": gs_max_tracking_rate_deg_s,
                    "sat_max_tracking_rate_deg_s": sat_max_tracking_rate_deg_s,
                    "sat_velocity_ecef_km_s": state.velocity_ecef_km_s,
                    "body_frame": body_frame,
                }

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
                observer_frame="body_local",
                reject_reason=gv.reject_reason,
                rejecting_endpoint=gv.rejecting_endpoint,
                applied_min_elevation_deg=min_elev,
                applied_max_range_km=max_range_km,
                applied_gs_max_range_km=gs_max_range_km,
                applied_sat_max_range_km=sat_max_range_km,
                applied_field_of_regard_deg=field_of_regard_deg,
                applied_gs_field_of_regard_deg=gs_field_of_regard_deg,
                applied_sat_field_of_regard_deg=sat_field_of_regard_deg,
                applied_max_tracking_rate_deg_s=max_tracking_rate_deg_s,
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
                    ),
                )
        visible_per_station[gs_id] = visible_sats

    remaining_by_pair: dict[tuple[str, str], float] = {}
    if visible_candidates_requiring_dwell:
        if pass_lookahead is None:
            raise ValueError(
                "Ground scheduling policy 'longest-remaining-pass' requires pass lookahead config"
            )
        remaining_by_pair = _estimate_remaining_visible_seconds(
            candidates=visible_candidates_requiring_dwell,
            gs_positions=gs_positions,
            gs_min_elevations=gs_min_elevations,
            lookahead=pass_lookahead,
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
                )
                for gv in visible_sats
            ]

    return GroundVisibilityEvaluation(
        decisions=decisions,
        visible_per_station=visible_per_station,
    )
