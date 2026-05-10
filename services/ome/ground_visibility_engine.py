# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""OME ground visibility evaluation engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from nodalarc.frames import EcefVec3, GeoPosition

from ome.propagation_engine import PropagatedState
from ome.visibility import GroundVisibility, check_ground_visibility

GroundVisibilityDetails = dict[tuple[str, str], tuple[bool, float, float | None]]


@dataclass(frozen=True)
class GroundVisibilityEvaluation:
    """Ground visibility output for one OME tick."""

    details: GroundVisibilityDetails
    visible_per_station: dict[str, list[GroundVisibility]]


def evaluate_ground_visibility(
    *,
    satellite_ids: Iterable[str],
    sat_states: Mapping[str, PropagatedState],
    gs_positions: Mapping[str, tuple[EcefVec3, GeoPosition]],
    gs_min_elevations: Mapping[str, float],
) -> GroundVisibilityEvaluation:
    """Evaluate geometric GS/satellite visibility for one tick.

    Missing propagated satellite state is fatal. The ground allocator must not
    receive a candidate set that silently omits a satellite because the
    propagation boundary failed upstream.
    """
    ordered_satellite_ids = tuple(satellite_ids)
    details: GroundVisibilityDetails = {}
    visible_per_station: dict[str, list[GroundVisibility]] = {}

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
                visible_sats.append(
                    GroundVisibility(sat_id, gv.visible, gv.elevation_deg, gv.range_km),
                )
        visible_per_station[gs_id] = visible_sats

    return GroundVisibilityEvaluation(
        details=details,
        visible_per_station=visible_per_station,
    )
