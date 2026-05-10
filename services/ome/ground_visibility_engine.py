# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""OME ground visibility evaluation engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from nodalarc.constellation_loader import SatelliteNode
from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.models.addressing import AddressingScheme

from ome.propagation_engine import PropagatedState, propagate_satellites
from ome.visibility import GroundVisibility, check_ground_visibility

GroundVisibilityDetails = dict[tuple[str, str], tuple[bool, float, float | None]]


@dataclass(frozen=True)
class GroundVisibilityEvaluation:
    """Ground visibility output for one OME tick."""

    details: GroundVisibilityDetails
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

    remaining = {pair: lookahead.horizon_ticks * lookahead.step_seconds for pair in candidates}
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
                gs_min_elevations.get(gs_id, 25.0),
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
    gs_policies: Mapping[str, str] | None = None,
    pass_lookahead: GroundPassLookahead | None = None,
) -> GroundVisibilityEvaluation:
    """Evaluate geometric GS/satellite visibility for one tick.

    Missing propagated satellite state is fatal. The ground allocator must not
    receive a candidate set that silently omits a satellite because the
    propagation boundary failed upstream.
    """
    ordered_satellite_ids = tuple(satellite_ids)
    details: GroundVisibilityDetails = {}
    visible_per_station: dict[str, list[GroundVisibility]] = {}

    policies = gs_policies or {}
    longest_pass_station_ids = {
        gs_id for gs_id in gs_positions if policies.get(gs_id) == "longest-remaining-pass"
    }
    if longest_pass_station_ids and pass_lookahead is None:
        raise ValueError(
            "Ground scheduling policy 'longest-remaining-pass' requires pass lookahead config"
        )

    visible_candidates_requiring_dwell: set[tuple[str, str]] = set()
    for gs_id, (gs_ecef, gs_geo) in gs_positions.items():
        min_elev = gs_min_elevations.get(gs_id, 25.0)
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
            details[pair] = (gv.visible, gv.range_km, gv.elevation_deg)
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
        details=details,
        visible_per_station=visible_per_station,
    )
