# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Unit tests for OME ground visibility evaluation."""

from __future__ import annotations

import pytest
from nodalarc.frames import EcefVec3, GeoPosition, Vec3
from nodalarc.geo import geodetic_to_ecef
from ome.ground_visibility_engine import evaluate_ground_visibility
from ome.propagation_engine import PropagatedState


def _state(node_id: str, geo: GeoPosition) -> PropagatedState:
    return PropagatedState(
        node_id=node_id,
        sim_time_unix=0.0,
        position_ecef_km=geodetic_to_ecef(geo),
        velocity_ecef_km_s=EcefVec3(Vec3(0.0, 0.0, 0.0)),
        geodetic=geo,
        propagator_id="test-fixture",
    )


def test_ground_visibility_evaluates_all_station_satellite_pairs():
    gs_geo = GeoPosition(0.0, 0.0, 0.0)
    sat_geo = GeoPosition(0.0, 0.0, 550.0)

    result = evaluate_ground_visibility(
        satellite_ids=("sat-a",),
        sat_states={"sat-a": _state("sat-a", sat_geo)},
        gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
        gs_min_elevations={"gs-equator": 25.0},
    )

    pair = ("gs-equator", "sat-a")
    assert pair in result.details
    visible, range_km, elevation_deg = result.details[pair]
    assert visible is True
    assert range_km > 500.0
    assert elevation_deg is not None
    assert result.visible_per_station["gs-equator"][0].sat_id == "sat-a"


def test_ground_visibility_missing_propagated_state_fails_loudly():
    gs_geo = GeoPosition(0.0, 0.0, 0.0)

    with pytest.raises(ValueError, match="Missing propagated satellite state"):
        evaluate_ground_visibility(
            satellite_ids=("sat-missing",),
            sat_states={},
            gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
            gs_min_elevations={"gs-equator": 25.0},
        )
