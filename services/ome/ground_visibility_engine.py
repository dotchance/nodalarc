# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME ground visibility evaluation engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from nodalarc.constellation_loader import SatelliteNode
from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.models.addressing import AddressingScheme

from ome.propagation_engine import PropagatedState, propagate_satellites
from ome.types import GroundVisibilityDecision, GroundVisibilityDecisionMap
from ome.visibility import GroundVisibility, check_ground_visibility


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
            visible = check_ground_visibility(
                gs_ecef,
                gs_geo,
                state.position_ecef_km,
                gs_min_elevations[gs_id],
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
) -> GroundVisibilityEvaluation:
    """Evaluate geometric GS/satellite visibility for one tick.

    Missing propagated satellite state is fatal. Missing per-GS
    `tenant_id` or `reference_body` is fatal — Direction 2 and
    Direction 3 require every decision to carry both. The ground
    allocator must not receive a candidate set that silently omits a
    satellite because the propagation boundary failed upstream, and
    consumers must not receive decisions whose tenant or body context
    is unknown.

    `applied_*` constraint fields are populated as `None` in this
    sub-phase (Phase 1.2.b) — the typed decision substrate is in
    place, but the actual range/FoR/tracking constraint enforcement
    (Phase 2 of the foundational trust plan) has not landed yet. The
    `physical_v1` fidelity gate will populate them when it ships.
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

            gv = check_ground_visibility(
                gs_ecef,
                gs_geo,
                state.position_ecef_km,
                min_elev,
            )
            pair = (min(gs_id, sat_id), max(gs_id, sat_id))
            decisions[pair] = GroundVisibilityDecision(
                pair=pair,
                tenant_id=tenant_id,
                reference_body=reference_body,
                visible=gv.visible,
                range_km=gv.range_km,
                elevation_deg=gv.elevation_deg,
                # Azimuth is computed in Phase 2 when the boresight
                # model lands. Phase 1.2 publishes None to keep the
                # contract honest: we do not have the data yet.
                azimuth_deg=None,
                observer_frame="body_local",
                reject_reason=gv.reject_reason,
                applied_min_elevation_deg=min_elev,
                # Phase 2 populates these. Until then the decision
                # honestly says "this constraint was not applied" —
                # no fallback, no silent permissive default.
                applied_max_range_km=None,
                applied_field_of_regard_deg=None,
                applied_max_tracking_rate_deg_s=None,
                applied_boresight_mode=None,
                applied_gs_terminal_profile=None,
                applied_sat_terminal_profile=None,
            )
            if gv.visible:
                if gs_id in longest_pass_station_ids:
                    visible_candidates_requiring_dwell.add((gs_id, sat_id))
                visible_sats.append(
                    GroundVisibility(sat_id, gv.visible, gv.elevation_deg, gv.range_km),
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
                    gv.sat_id,
                    gv.visible,
                    gv.elevation_deg,
                    gv.range_km,
                    remaining_by_pair[(gs_id, gv.sat_id)],
                )
                for gv in visible_sats
            ]

    return GroundVisibilityEvaluation(
        decisions=decisions,
        visible_per_station=visible_per_station,
    )
