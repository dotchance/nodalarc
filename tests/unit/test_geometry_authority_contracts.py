# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Numeric proof tests for geometry and OME/Scheduler authority contracts."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from nodalarc.constants import SPEED_OF_LIGHT_KM_S, WGS84_A, WGS84_B
from nodalarc.frames import EcefVec3, GeoPosition, Vec3
from nodalarc.geo import (
    compute_latency_ms,
    compute_range_km,
    geodetic_to_ecef,
)
from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)
from nodalarc.propagator import geodetic_to_ecef as reexported_geodetic_to_ecef
from ome.event_stream import build_link_state_snapshot
from ome.propagation_engine import PropagatedState
from scheduler.dispatcher import Dispatcher
from scheduler.pod_locator import PodLocationMap

SIM = datetime(2026, 1, 1, tzinfo=UTC)
RANGE_TOL_KM = 1e-6
LATENCY_TOL_MS = 1e-9


def _propagated_state(node_id: str, lat: float, lon: float, alt_km: float) -> PropagatedState:
    geo = GeoPosition(lat, lon, alt_km)
    return PropagatedState(
        node_id=node_id,
        sim_time_unix=SIM.timestamp(),
        position_ecef_km=geodetic_to_ecef(geo),
        velocity_ecef_km_s=EcefVec3(Vec3(0.0, 0.0, 0.0)),
        geodetic=geo,
        propagator_id="test-authority",
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
        wiring_generation="sha256:" + "a" * 64,
        max_latency_age_s=1.0,
        gs_terminal_capacities={},
        sat_ground_terminal_capacities={},
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
        x, y, z = geodetic_to_ecef(GeoPosition(0.0, 0.0, 0.0))
        assert math.isclose(x, WGS84_A, abs_tol=RANGE_TOL_KM)
        assert math.isclose(y, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(z, 0.0, abs_tol=RANGE_TOL_KM)

        x, y, z = geodetic_to_ecef(GeoPosition(90.0, 0.0, 0.0))
        assert math.isclose(x, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(y, 0.0, abs_tol=RANGE_TOL_KM)
        assert math.isclose(z, WGS84_B, abs_tol=RANGE_TOL_KM)

    def test_propagator_reexports_single_geodetic_to_ecef_implementation(self):
        assert reexported_geodetic_to_ecef is geodetic_to_ecef

        geo = GeoPosition(lat_deg=33.9175, lon_deg=-118.328111, alt_km=0.04)
        direct_ecef = geodetic_to_ecef(geo)
        reexported_ecef = reexported_geodetic_to_ecef(geo)

        assert math.isclose(direct_ecef.x, reexported_ecef.x, abs_tol=RANGE_TOL_KM)
        assert math.isclose(direct_ecef.y, reexported_ecef.y, abs_tol=RANGE_TOL_KM)
        assert math.isclose(direct_ecef.z, reexported_ecef.z, abs_tol=RANGE_TOL_KM)


class TestOmeSnapshotGeometry:
    def test_snapshot_range_and_latency_match_authoritative_geometry_formula(self):
        pair = ("sat-a", "sat-b")
        propagated_states = {
            "sat-a": _propagated_state("sat-a", 0.0, 0.0, 550.0),
            "sat-b": _propagated_state("sat-b", 0.0, 5.0, 550.0),
        }
        snapshot = build_link_state_snapshot(
            isl_state={pair: (True, True)},
            gs_state={},
            interface_map={pair: ("isl0", "isl1")},
            bandwidth_map={pair: 1000.0},
            sim_time=SIM,
            seq=1,
            interval_s=1.0,
            propagated_states=propagated_states,
            epoch_id=0,
        )
        link = snapshot.links[0]
        expected_range = compute_range_km(
            geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0)),
            geodetic_to_ecef(GeoPosition(0.0, 5.0, 550.0)),
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
                isl_state={pair: (True, True)},
                gs_state={},
                interface_map={pair: ("isl0", "isl1")},
                bandwidth_map={pair: 1000.0},
                sim_time=SIM,
                seq=1,
                interval_s=1.0,
                propagated_states={
                    "sat-a": _propagated_state("sat-a", 0.0, 0.0, 550.0),
                },
                epoch_id=0,
            )

    def test_active_snapshot_link_missing_bandwidth_fails_loudly(self):
        pair = ("sat-a", "sat-b")

        with pytest.raises(ValueError, match="missing config-derived bandwidth"):
            build_link_state_snapshot(
                isl_state={pair: (True, True)},
                gs_state={},
                interface_map={pair: ("isl0", "isl1")},
                bandwidth_map={},
                sim_time=SIM,
                seq=1,
                interval_s=1.0,
                propagated_states={
                    "sat-a": _propagated_state("sat-a", 0.0, 0.0, 550.0),
                    "sat-b": _propagated_state("sat-b", 0.0, 5.0, 550.0),
                },
                epoch_id=0,
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
