# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Unit tests for OME ground visibility evaluation."""

from __future__ import annotations

import pytest
from nodalarc.frames import EcefVec3, GeoPosition, Vec3
from nodalarc.geo import geodetic_to_ecef
from ome.ground_visibility_engine import GroundPassLookahead, evaluate_ground_visibility
from ome.propagation_engine import PropagatedState
from ome.visibility import GroundVisibility


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


def test_longest_remaining_pass_populates_sampled_dwell(monkeypatch):
    gs_geo = GeoPosition(0.0, 0.0, 0.0)

    def fake_check_ground_visibility(_gs_ecef, _gs_geo, sat_ecef, _min_elev):
        # sat-short uses y=1 and drops at t=2; sat-long uses y=2 and drops at t=4.
        visible_until = 2.0 if sat_ecef.y == 1.0 else 4.0
        visible = sat_ecef.x < visible_until
        elevation = 70.0 if sat_ecef.y == 1.0 else 30.0
        return GroundVisibility("", visible, elevation if visible else -10.0, 1000.0)

    def fake_propagate_satellites(**kwargs):
        dt = float(kwargs["dt"])
        return {
            "sat-short": PropagatedState(
                "sat-short",
                dt,
                EcefVec3(Vec3(dt, 1.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test",
            ),
            "sat-long": PropagatedState(
                "sat-long",
                dt,
                EcefVec3(Vec3(dt, 2.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test",
            ),
        }

    monkeypatch.setattr(
        "ome.ground_visibility_engine.check_ground_visibility",
        fake_check_ground_visibility,
    )
    monkeypatch.setattr(
        "ome.ground_visibility_engine.propagate_satellites",
        fake_propagate_satellites,
    )

    result = evaluate_ground_visibility(
        satellite_ids=("sat-short", "sat-long"),
        sat_states={
            "sat-short": PropagatedState(
                "sat-short",
                0.0,
                EcefVec3(Vec3(0.0, 1.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test",
            ),
            "sat-long": PropagatedState(
                "sat-long",
                0.0,
                EcefVec3(Vec3(0.0, 2.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test",
            ),
        },
        gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
        gs_min_elevations={"gs-equator": 25.0},
        gs_policies={"gs-equator": "longest-remaining-pass"},
        pass_lookahead=GroundPassLookahead(
            satellites=(),
            addressing=object(),
            epoch_unix=0.0,
            step=0,
            step_seconds=1,
            horizon_ticks=5,
            propagator_id="test",
        ),
    )

    remaining = {
        gv.sat_id: gv.remaining_visible_s for gv in result.visible_per_station["gs-equator"]
    }
    assert remaining == {"sat-short": 1.0, "sat-long": 3.0}


def test_longest_remaining_pass_without_lookahead_fails_loudly():
    gs_geo = GeoPosition(0.0, 0.0, 0.0)

    with pytest.raises(ValueError, match="requires pass lookahead"):
        evaluate_ground_visibility(
            satellite_ids=("sat-a",),
            sat_states={"sat-a": _state("sat-a", GeoPosition(0.0, 0.0, 550.0))},
            gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
            gs_min_elevations={"gs-equator": 25.0},
            gs_policies={"gs-equator": "longest-remaining-pass"},
        )
