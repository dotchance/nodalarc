# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Numeric proof tests for geometry and OME/Scheduler authority contracts."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.frames import CommonVec3, EcefVec3, GeoPosition, Vec3
from nodalarc.geo import (
    compute_latency_ms,
    compute_range_km,
)
from ome.event_stream import build_link_state_snapshot
from ome.propagation_engine import PropagatedState
from ome.snapshot_builder import LinkSnapshotSource

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME, earth_geodetic_to_ecef

SIM = datetime(2026, 1, 1, tzinfo=UTC)
RANGE_TOL_KM = 1e-6
LATENCY_TOL_MS = 1e-9
WGS84_A = EARTH_TEST_BODY_FRAME.equatorial_radius_km
WGS84_B = EARTH_TEST_BODY_FRAME.polar_radius_km


def _propagated_state(node_id: str, lat: float, lon: float, alt_km: float) -> PropagatedState:
    geo = GeoPosition(lat, lon, alt_km)
    ecef = earth_geodetic_to_ecef(geo)
    return PropagatedState(
        node_id=node_id,
        sim_time_unix=SIM.timestamp(),
        position_ecef_km=ecef,
        velocity_ecef_km_s=EcefVec3(Vec3(0.0, 0.0, 0.0)),
        geodetic=geo,
        propagator_id="test-authority",
        central_body="earth",
        position_common_km=CommonVec3(*ecef),
        velocity_common_km_s=CommonVec3(0.0, 0.0, 0.0),
        body_origin_common_km=CommonVec3(0.0, 0.0, 0.0),
    )


def _snapshot_source(
    *,
    isl_state: dict[tuple[str, str], tuple[bool, bool]] | None = None,
    ground_state: dict[tuple[str, str], tuple[bool, bool, str]] | None = None,
    propagated_states: dict[str, PropagatedState] | None = None,
) -> LinkSnapshotSource:
    return LinkSnapshotSource(
        isl_state=isl_state or {},
        ground_state=ground_state or {},
        associations={},
        pending_teardowns={},
        propagated_states=propagated_states or {},
    )


class TestAnalyticGeometry:
    def test_ecef_range_matches_euclidean_distance(self):
        assert compute_range_km(Vec3(0.0, 0.0, 0.0), Vec3(3.0, 4.0, 12.0)) == 13.0

    def test_speed_of_light_latency_formula(self):
        assert compute_latency_ms(SPEED_OF_LIGHT_KM_S) == 1000.0
        assert math.isclose(
            compute_latency_ms(1234.5),
            1234.5 / SPEED_OF_LIGHT_KM_S * 1000.0,
            abs_tol=LATENCY_TOL_MS,
        )

    def test_wgs84_axis_fixtures(self):
        x, y, z = earth_geodetic_to_ecef(GeoPosition(0.0, 0.0, 0.0))
        assert math.isclose(x, WGS84_A, abs_tol=RANGE_TOL_KM)
        assert math.isclose(y, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(z, 0.0, abs_tol=RANGE_TOL_KM)

        x, y, z = earth_geodetic_to_ecef(GeoPosition(90.0, 0.0, 0.0))
        assert math.isclose(x, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(y, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(z, WGS84_B, abs_tol=RANGE_TOL_KM)


class TestOmeSnapshotGeometry:
    def test_snapshot_range_and_latency_match_authoritative_geometry_formula(self):
        pair = ("sat-a", "sat-b")
        propagated_states = {
            "sat-a": _propagated_state("sat-a", 0.0, 0.0, 550.0),
            "sat-b": _propagated_state("sat-b", 0.0, 5.0, 550.0),
        }
        snapshot = build_link_state_snapshot(
            _snapshot_source(isl_state={pair: (True, True)}, propagated_states=propagated_states),
            interface_map={pair: ("isl0", "isl1")},
            sim_time=SIM,
            seq=1,
            interval_s=1.0,
            epoch_id=0,
        )
        link = snapshot.links[0]
        expected_range = compute_range_km(
            earth_geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0)),
            earth_geodetic_to_ecef(GeoPosition(0.0, 5.0, 550.0)),
        )

        assert link.range_km is not None
        assert link.latency_ms is not None
        assert math.isclose(link.range_km, expected_range, abs_tol=RANGE_TOL_KM)
        assert math.isclose(
            link.latency_ms,
            compute_latency_ms(expected_range),
            abs_tol=LATENCY_TOL_MS,
        )

    def test_active_snapshot_link_missing_authority_fails_loudly(self):
        pair = ("sat-a", "sat-b")

        with pytest.raises(ValueError, match="missing same-tick ECEF state"):
            build_link_state_snapshot(
                _snapshot_source(
                    isl_state={pair: (True, True)},
                    propagated_states={
                        "sat-a": _propagated_state("sat-a", 0.0, 0.0, 550.0),
                    },
                ),
                interface_map={pair: ("isl0", "isl1")},
                sim_time=SIM,
                seq=1,
                interval_s=1.0,
                epoch_id=0,
            )
