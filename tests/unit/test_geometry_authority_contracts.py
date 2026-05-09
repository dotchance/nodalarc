# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Numeric proof tests for geometry and OME/Scheduler authority contracts."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from unittest.mock import MagicMock

from nodalarc.constants import SPEED_OF_LIGHT_KM_S, WGS84_A, WGS84_B
from nodalarc.geo import (
    compute_latency_ms,
    compute_range_km,
)
from nodalarc.geo import (
    geodetic_to_ecef as tuple_geodetic_to_ecef,
)
from nodalarc.models.events import NodePosition, VisibilityEvent
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)
from nodalarc.propagator import GeoPosition
from nodalarc.propagator import geodetic_to_ecef as typed_geodetic_to_ecef
from ome.event_stream import build_link_state_snapshot
from scheduler.dispatcher import Dispatcher
from scheduler.pod_locator import PodLocationMap

SIM = datetime(2026, 1, 1, tzinfo=UTC)
RANGE_TOL_KM = 1e-6
LATENCY_TOL_MS = 1e-9


def _node_position(lat: float, lon: float, alt_km: float) -> NodePosition:
    return NodePosition(
        lat_deg=lat,
        lon_deg=lon,
        alt_km=alt_km,
        vel_x_km_s=0.0,
        vel_y_km_s=0.0,
        vel_z_km_s=0.0,
    )


def _dispatcher() -> Dispatcher:
    pair = ("sat-P00S00", "sat-P00S01")
    loc = PodLocationMap()
    loc._node_of["sat-P00S00"] = "node-a"
    loc._node_of["sat-P00S01"] = "node-a"
    loc._agent_addrs["node-a"] = "agent-a"

    pool = MagicMock()
    return Dispatcher(
        interface_map={pair: ("isl0", "isl1")},
        bandwidth_map={pair: 1234.0},
        pod_locator=loc,
        agent_pool=pool,
        session_id="test-session",
        gs_terminal_capacities={},
        sat_ground_terminal_capacities={},
    )


class TestAnalyticGeometry:
    def test_ecef_range_matches_euclidean_distance(self):
        assert compute_range_km((0.0, 0.0, 0.0), (3.0, 4.0, 12.0)) == 13.0

    def test_speed_of_light_latency_formula(self):
        assert compute_latency_ms(SPEED_OF_LIGHT_KM_S) == 1000.0
        assert math.isclose(
            compute_latency_ms(1234.5),
            1234.5 / SPEED_OF_LIGHT_KM_S * 1000.0,
            abs_tol=LATENCY_TOL_MS,
        )

    def test_wgs84_axis_fixtures(self):
        x, y, z = tuple_geodetic_to_ecef(0.0, 0.0, 0.0)
        assert math.isclose(x, WGS84_A, abs_tol=RANGE_TOL_KM)
        assert math.isclose(y, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(z, 0.0, abs_tol=RANGE_TOL_KM)

        x, y, z = tuple_geodetic_to_ecef(90.0, 0.0, 0.0)
        assert math.isclose(x, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(y, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(z, WGS84_B, abs_tol=RANGE_TOL_KM)

    def test_duplicate_geodetic_to_ecef_paths_stay_identical_until_consolidated(self):
        geo = GeoPosition(lat_deg=33.9175, lon_deg=-118.328111, alt_km=0.04)
        tuple_ecef = tuple_geodetic_to_ecef(geo.lat_deg, geo.lon_deg, geo.alt_km)
        typed_ecef = typed_geodetic_to_ecef(geo)

        assert math.isclose(tuple_ecef[0], typed_ecef.x, abs_tol=RANGE_TOL_KM)
        assert math.isclose(tuple_ecef[1], typed_ecef.y, abs_tol=RANGE_TOL_KM)
        assert math.isclose(tuple_ecef[2], typed_ecef.z, abs_tol=RANGE_TOL_KM)


class TestOmeSnapshotGeometry:
    def test_snapshot_range_and_latency_match_authoritative_geometry_formula(self):
        pair = ("sat-a", "sat-b")
        positions = {
            "sat-a": _node_position(0.0, 0.0, 550.0),
            "sat-b": _node_position(0.0, 5.0, 550.0),
        }
        snapshot = build_link_state_snapshot(
            isl_state={pair: (True, True)},
            gs_state={},
            interface_map={pair: ("isl0", "isl1")},
            sim_time=SIM,
            seq=1,
            interval_s=1.0,
            positions=positions,
            epoch_id=0,
        )
        link = snapshot.links[0]
        expected_range = compute_range_km(
            tuple_geodetic_to_ecef(0.0, 0.0, 550.0),
            tuple_geodetic_to_ecef(0.0, 5.0, 550.0),
        )

        assert link.range_km is not None
        assert link.latency_ms is not None
        assert math.isclose(link.range_km, expected_range, abs_tol=RANGE_TOL_KM)
        assert math.isclose(
            link.latency_ms,
            compute_latency_ms(expected_range),
            abs_tol=LATENCY_TOL_MS,
        )


class TestSchedulerAuthorityPreservation:
    def test_visibility_event_range_and_latency_are_preserved_exactly(self):
        d = _dispatcher()
        event = VisibilityEvent(
            sim_time=SIM,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            visible=True,
            scheduled=True,
            range_km=3210.987654321,
            latency_ms=10.710123456789,
            elevation_deg=None,
            terminal_type="optical",
            link_type="isl",
        )

        desired = d._apply_events_to_desired([event])
        info = desired[("sat-P00S00", "sat-P00S01")]

        assert info.range_km == event.range_km
        assert info.latency_ms == event.latency_ms

    def test_snapshot_range_and_latency_are_preserved_exactly(self):
        d = _dispatcher()
        link = LinkState(
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            interface_a="isl0",
            interface_b="isl1",
            admin=AdminState.UP,
            carrier=CarrierState.UP,
            routing=RoutingState.UNKNOWN,
            range_km=4321.123456789,
            latency_ms=14.413123456789,
            bandwidth_mbps=1234.0,
            link_type="isl",
            sim_time=SIM,
        )
        snapshot = LinkStateSnapshot(
            sim_time=SIM,
            snapshot_seq=1,
            links=(link,),
            interval_s=1.0,
        )

        desired = d._build_desired_from_snapshot(snapshot)
        assert desired is not None
        info = desired[("sat-P00S00", "sat-P00S01")]

        assert info.range_km == link.range_km
        assert info.latency_ms == link.latency_ms
