# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Numeric proof tests for geometry and OME/Scheduler authority contracts."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from nodalarc.frames import CommonVec3, EcefVec3, GeoPosition, Vec3
from nodalarc.geo import compute_range_km
from nodalarc.models.events import VisibilityEvent
from nodalarc.ome_inputs import build_ome_inputs_from_resolved
from nodalarc.physics.lighttime import signals_sent_at
from nodalarc.physics.session import session_node_positions
from ome.event_stream import build_link_state_snapshot, build_step_context, compute_step
from ome.propagation_engine import PropagatedState, satellite_endpoints
from ome.snapshot_builder import LinkSnapshotSource

from tests.conftest import load_runtime_ome_test_inputs
from tests.physics_fixtures import EARTH_TEST_BODY_FRAME, earth_geodetic_to_ecef

SIM = datetime(2026, 1, 1, tzinfo=UTC)
RANGE_TOL_KM = 1e-6
# Two evaluations of one signal in different batches agree within this (ms). The light-time
# iteration stops once a step moves by under 1 ns, and each step shrinks the error by the
# receiver's barycentric speed over c (about 1.3e-4), so evaluations agree within about
# 3e-13 s. The two directions of a link differ by 10.6 ns or more on the tick tested here.
SAME_SIGNAL_MS = 1e-9
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
    ranges_km: dict[tuple[str, str], float],
    latencies_ms: dict[tuple[str, str], tuple[float, float]],
) -> LinkSnapshotSource:
    return LinkSnapshotSource(
        isl_state=isl_state or {},
        ground_state=ground_state or {},
        associations={},
        pending_teardowns={},
        ranges_km=ranges_km,
        latencies_ms=latencies_ms,
    )


class TestAnalyticGeometry:
    def test_ecef_range_matches_euclidean_distance(self):
        assert compute_range_km(Vec3(0.0, 0.0, 0.0), Vec3(3.0, 4.0, 12.0)) == 13.0

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
    def test_snapshot_shows_the_range_and_delays_the_tick_committed(self):
        """The snapshot carries each up link's range and each direction's delay as the tick
        committed them, in the pair's order; it computes neither."""
        pair = ("sat-a", "sat-b")
        committed = (2.08291, 2.08297)
        snapshot = build_link_state_snapshot(
            _snapshot_source(
                isl_state={pair: (True, True)},
                ranges_km={pair: 624.4},
                latencies_ms={pair: committed},
            ),
            interface_map={pair: ("isl0", "isl1")},
            sim_time=SIM,
            seq=1,
            interval_s=1.0,
            epoch_id=0,
        )
        link = snapshot.links[0]

        assert link.range_km == 624.4
        assert (link.latency_a_to_b_ms, link.latency_b_to_a_ms) == committed

    def test_every_delay_a_tick_shows_is_the_light_time_of_that_directions_signal(self):
        """One OME tick of a session with ISLs and a ground station: every VisibilityEvent and
        every up link of the LinkStateSnapshot (built as OME's main loop builds it) carries, for
        node_a to node_b and node_b to node_a, the light time of the signal that end sends at
        the tick, computed directly by the signal layer. The two directions differ on every
        link (by 10.6 ns at least on this tick), so a swap or a merge of directions anywhere
        fails."""
        session, resolved, gs_file, satellites, addressing, neighbors, candidates = (
            load_runtime_ome_test_inputs(origin="test.geometry_authority")
        )
        ctx = build_step_context(
            satellites=satellites,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=candidates,
            ground_link_model=session.ground_link_model,
            body_frames=session.body_frames,
            physics=session.physics,
        )
        result = compute_step(
            ctx,
            1704067200.0,
            step=0,
            step_seconds=session.time.step_seconds,
            timestamp_offset=0.0,
            isl_state={},
            gs_state={},
            current_associations={},
        )
        snapshot = build_link_state_snapshot(
            result.link_snapshot_source,
            interface_map=build_ome_inputs_from_resolved(resolved).interface_map,
            sim_time=result.sim_time,
            seq=1,
            interval_s=5.0,
            fixed_positions=ctx.gs_positions,
            epoch_id=0,
            mbb_overlap_ticks_by_gs=ctx.gs_mbb_overlap_ticks,
            current_step=result.step,
        )
        events = [
            timeline.data for timeline in result.events if timeline.event_type == "VisibilityEvent"
        ]
        assert all(isinstance(event, VisibilityEvent) for event in events)
        up_links = [link for link in snapshot.links if link.carrier == "UP"]
        shown = {
            (event.node_a, event.node_b): (event.latency_a_to_b_ms, event.latency_b_to_a_ms)
            for event in events
        }
        assert {link.link_type for link in up_links} == {"isl", "ground"}
        assert {event.link_type for event in events} == {"isl", "ground"}

        endpoints = {
            **satellite_endpoints(
                satellites=satellites,
                addressing=addressing,
                propagator_id=session.orbit.propagator,
                body_frames=session.body_frames,
            ),
            **ctx.ground_geometry.station_endpoints,
        }
        pairs = sorted(set(shown) | {(link.node_a, link.node_b) for link in up_links})
        signals = signals_sent_at(
            session.physics,
            session.body_frames,
            [endpoints[a] for a, _b in pairs] + [endpoints[b] for _a, b in pairs],
            [endpoints[b] for _a, b in pairs] + [endpoints[a] for a, _b in pairs],
            result.sim_time_unix,
        )
        light_ms = {
            pair: (
                float(signals.light_time_s[row]) * 1000.0,
                float(signals.light_time_s[row + len(pairs)]) * 1000.0,
            )
            for row, pair in enumerate(pairs)
        }

        # The tick and this check evaluate each signal in different batches. They agree within
        # SAME_SIGNAL_MS; a pair's two directions differ by far more, so a swap still fails.
        assert all(
            abs(a_to_b - b_to_a) > 100 * SAME_SIGNAL_MS for a_to_b, b_to_a in light_ms.values()
        )
        for pair, delays in shown.items():
            assert delays == pytest.approx(light_ms[pair], rel=0.0, abs=SAME_SIGNAL_MS), pair
        for link in up_links:
            pair = (link.node_a, link.node_b)
            delays = (link.latency_a_to_b_ms, link.latency_b_to_a_ms)
            assert delays == pytest.approx(light_ms[pair], rel=0.0, abs=SAME_SIGNAL_MS), pair

        # Every up link's range is the same-instant distance between its ends, against the
        # shared physics module's own positions of every node at the tick (satellites from their
        # orbits, stations from their bodies' orientation), a separate path from OME's.
        positions = session_node_positions(resolved, session.physics, result.sim_time_unix)
        for link in up_links:
            assert link.range_km == pytest.approx(
                compute_range_km(positions[link.node_a], positions[link.node_b]),
                rel=0.0,
                abs=RANGE_TOL_KM,
            ), (link.node_a, link.node_b)

    def test_active_snapshot_link_missing_authority_fails_loudly(self):
        pair = ("sat-a", "sat-b")

        with pytest.raises(ValueError, match="committed no range"):
            build_link_state_snapshot(
                _snapshot_source(
                    isl_state={pair: (True, True)},
                    ranges_km={},
                    latencies_ms={pair: (2.08291, 2.08297)},
                ),
                interface_map={pair: ("isl0", "isl1")},
                sim_time=SIM,
                seq=1,
                interval_s=1.0,
                epoch_id=0,
            )

    def test_active_snapshot_link_without_committed_delays_fails_loudly(self):
        """An up link the tick committed no light time for is refused, never published with an
        invented delay."""
        pair = ("sat-a", "sat-b")

        with pytest.raises(ValueError, match="committed no light time"):
            build_link_state_snapshot(
                _snapshot_source(
                    isl_state={pair: (True, True)},
                    ranges_km={pair: 624.4},
                    latencies_ms={},
                ),
                interface_map={pair: ("isl0", "isl1")},
                sim_time=SIM,
                seq=1,
                interval_s=1.0,
                epoch_id=0,
            )
