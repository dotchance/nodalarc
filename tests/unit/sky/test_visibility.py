"""Test link constraints (range, elevation, ground and satellite field of regard, tracking rate,
polar seam) and ISL terminal scheduling.

Each constraint case states both ends at one instant and runs OME's per-direction checks on that
geometry (tests/direction_inputs.py). Line of sight through a body is tested against real
endpoints in tests/unit/physics/test_occlusion.py.
"""

import math

from nodalarc.frames import GeoPosition, Vec3
from nodalarc.geo import compute_range_km
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from ome.visibility import (
    ScheduledLink,
    enforce_symmetric_scheduling,
    schedule_isl_terminals,
)

from tests.direction_inputs import ground_verdict, isl_reason
from tests.physics_fixtures import (
    earth_elements_from_params,
    earth_geodetic_to_ecef,
    earth_propagate_eci,
    earth_propagate_keplerian,
)
from tests.seam_reference import DeclaredShell, encounter, state, verdict

EPOCH = 1735689600.0


class TestRange:
    def test_same_plane_range(self):
        """Two sats 36° apart at 550km should be ~4300 km apart."""
        e1 = earth_elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = earth_elements_from_params(550.0, 53.0, 0.0, 36.0)
        pos1, _, _ = earth_propagate_keplerian(e1, EPOCH, 0.0)
        pos2, _, _ = earth_propagate_keplerian(e2, EPOCH, 0.0)
        r = compute_range_km(pos1, pos2)
        # 2 * (R+h) * sin(θ/2) ≈ 2 * 6921 * sin(18°) ≈ 4278 km
        assert 4000.0 < r < 4600.0


def _elevation_deg(gs_ecef, gs_geo, sat_ecef) -> float:
    """The station's elevation of the satellite, with no elevation mask."""
    return ground_verdict(gs_ecef, gs_geo, sat_ecef, min_elevation_deg=-90.0).elevation_deg


class TestElevationAngle:
    def test_overhead_satellite(self):
        """Satellite directly overhead → ~90° elevation."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0))
        elev = _elevation_deg(gs_ecef, gs_geo, sat_ecef)
        assert 85.0 < elev <= 90.0

    def test_horizon_satellite(self):
        """Satellite moderately far → positive but low elevation angle."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        # Satellite at 10° lat, 550km — should be above horizon but not high
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(10.0, 0.0, 550.0))
        elev = _elevation_deg(gs_ecef, gs_geo, sat_ecef)
        # Should be positive but moderate
        assert 0.0 < elev < 45.0

    def test_below_horizon(self):
        """Satellite behind the curve → negative elevation."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(60.0, 0.0, 550.0))
        elev = _elevation_deg(gs_ecef, gs_geo, sat_ecef)
        assert elev < 0.0

    def test_analytic_horizon_is_near_zero_elevation(self):
        """At the geometric tangent point, elevation is zero in the local frame."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        radius_km = gs_ecef.x
        orbit_radius_km = radius_km + 550.0
        central_angle = math.acos(radius_km / orbit_radius_km)
        sat_ecef = Vec3(
            orbit_radius_km * math.cos(central_angle),
            orbit_radius_km * math.sin(central_angle),
            0.0,
        )
        elev = _elevation_deg(gs_ecef, gs_geo, sat_ecef)
        assert abs(elev) < 1e-9


class TestGroundVisibility:
    def test_overhead_visible(self):
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0))
        result = ground_verdict(gs_ecef, gs_geo, sat_ecef, min_elevation_deg=25.0)
        assert result.visible
        assert result.reason == "ok"

    def test_below_min_elevation(self):
        """LEO sat at 550km, 10° latitude offset from a ground station
        at the equator: elevation is ~20° — below the 25° mask. Must
        reject with `elevation_below_min`. (The line of sight of this
        pair is clear; test_occlusion.py tests it from real endpoints.)"""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(10.0, 0.0, 550.0))
        result = ground_verdict(gs_ecef, gs_geo, sat_ecef, min_elevation_deg=25.0)
        assert not result.visible
        assert result.reason == "elevation_below_min"
        # The elevation value carries the actual computed angle, not a
        # sentinel. This distinguishes elevation_below_min from los_blocked.
        assert 0.0 < result.elevation_deg < 25.0

    def test_range_limit_rejects_visible_overhead_satellite(self):
        """Both terminals declare a 100 km range, so the rejection names both endpoints."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0))
        assert compute_range_km(gs_ecef, sat_ecef) > 500.0
        result = ground_verdict(
            gs_ecef,
            gs_geo,
            sat_ecef,
            min_elevation_deg=25.0,
            gs_max_range_km=100.0,
            sat_max_range_km=100.0,
        )
        assert not result.visible
        assert result.reason == "range_exceeded"
        assert result.rejecting_endpoint == "both"

    def test_ground_field_of_regard_rejects_off_boresight_satellite(self):
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(10.0, 0.0, 550.0))
        result = ground_verdict(
            gs_ecef,
            gs_geo,
            sat_ecef,
            min_elevation_deg=0.0,
            gs_boresight=TerminalBoresight(mode="local_vertical"),
            gs_field_of_regard_deg=10.0,
        )
        assert not result.visible
        assert result.reason == "field_of_regard"
        assert result.rejecting_endpoint == "ground"

    def test_satellite_nadir_field_of_regard_rejects_off_axis_ground_station(self):
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(10.0, 0.0, 550.0))
        result = ground_verdict(
            gs_ecef,
            gs_geo,
            sat_ecef,
            min_elevation_deg=0.0,
            sat_boresight=SatGroundTerminalBoresight(
                target_body="earth",
                mode="nadir",
            ),
            sat_field_of_regard_deg=10.0,
        )
        assert not result.visible
        assert result.reason == "field_of_regard"
        assert result.rejecting_endpoint == "satellite"

    def test_tracking_rate_limit_rejects_fast_overhead_satellite(self):
        """Both terminals declare 0.1 deg/s, so the rejection names both endpoints."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = earth_geodetic_to_ecef(gs_geo)
        sat_ecef = earth_geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0))
        result = ground_verdict(
            gs_ecef,
            gs_geo,
            sat_ecef,
            min_elevation_deg=25.0,
            gs_max_tracking_rate_deg_s=0.1,
            sat_max_tracking_rate_deg_s=0.1,
            sat_velocity_km_s=Vec3(0.0, 10.0, 0.0),
        )
        assert not result.visible
        assert result.reason == "tracking_exceeded"
        assert result.rejecting_endpoint == "both"


class TestIslVisibility:
    def test_nearby_sats_visible(self):
        e1 = earth_elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = earth_elements_from_params(550.0, 53.0, 0.0, 36.0)
        pos1, vel1 = earth_propagate_eci(e1, 0.0)
        pos2, vel2 = earth_propagate_eci(e2, 0.0)
        # Use ECI positions as approximate ECEF (t=0 acceptable for test)
        assert isl_reason(pos1, vel1, pos2, vel2, max_range_km=5016.0) == "ok"

    def test_range_exceeded(self):
        """Two nearby sats with LOS but beyond a tight range limit."""
        e1 = earth_elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = earth_elements_from_params(550.0, 53.0, 0.0, 36.0)  # ~4300 km apart, have LOS
        pos1, vel1 = earth_propagate_eci(e1, 0.0)
        pos2, vel2 = earth_propagate_eci(e2, 0.0)
        # Range is ~4300 km, limit to 1000 km
        assert isl_reason(pos1, vel1, pos2, vel2, max_range_km=1000.0) == "range_exceeded"

    def test_range_exceedance_and_recovery_follow_the_seam_reference(self):
        """The shipped polar shell's counter-rotating seam pair, at times the
        declared-parameter reference model puts just inside, just outside and
        back inside the terminal's 6000 km range; the reference computes the
        states, the production check decides. The production path length of
        this pair is compared with the reference in
        tests/unit/physics/test_line_of_sight_rate.py."""
        shell = DeclaredShell(780.0, 86.4, 31.6, 6, 10.0)
        expected = [(700, "ok"), (1500, "range_exceeded"), (3720, "ok")]
        for t_s, reason in expected:
            pos_a, vel_a = state(shell, 5, 0, t_s)
            pos_b, vel_b = state(shell, 0, 0, t_s)
            reference_range, reference_rate, clear = encounter(shell, (5, 0), (0, 0), t_s)
            assert clear
            assert reference_rate < 2.0
            assert (
                verdict(
                    reference_range, reference_rate, clear, max_range_km=6000.0, max_rate_deg_s=2.0
                )
                == reason
            )
            result = isl_reason(
                pos_a,
                vel_a,
                pos_b,
                vel_b,
                max_range_km=6000.0,
                max_tracking_rate_deg_s=2.0,
            )
            assert result == reason, (t_s, result)

    def test_tracking_exceedance_and_recovery_with_range_and_line_of_sight_permitting(self):
        """The seam-crossing shell's close pass: line of sight clear and range
        two orders of magnitude inside the limit at every sample, so the
        tracking rate is the one binding constraint, exceeded then recovered.
        The production rate and path length of this pass are compared with
        the reference in tests/unit/physics/test_line_of_sight_rate.py."""
        shell = DeclaredShell(780.0, 86.4, 31.6, 6, 15.0)
        expected = [(2870, "ok", 1.41), (2890, "tracking_exceeded", 3.64), (2910, "ok", 1.38)]
        for t_s, reason, reference_rate in expected:
            pos_a, vel_a = state(shell, 5, 0, t_s)
            pos_b, vel_b = state(shell, 0, 2, t_s)
            range_km, rate, clear = encounter(shell, (5, 0), (0, 2), t_s)
            assert clear and range_km < 400.0, (t_s, range_km)
            assert abs(rate - reference_rate) < 0.02, (t_s, rate)
            result = isl_reason(
                pos_a,
                vel_a,
                pos_b,
                vel_b,
                max_range_km=6000.0,
                max_tracking_rate_deg_s=2.0,
            )
            assert result == reason, (t_s, result)

    def test_seam_closest_approach_of_the_range_experiment_stays_feasible(self):
        """The range experiment's closest approach (1954 km, 0.43 deg/s) is feasible:
        the shipped phasing never lets tracking bind, which is why that session
        is range coverage only. The production path length and rate at this
        approach are checked in tests/unit/physics/test_line_of_sight_rate.py."""
        shell = DeclaredShell(780.0, 86.4, 31.6, 6, 10.0)
        pos_a, vel_a = state(shell, 5, 0, 1090)
        pos_b, vel_b = state(shell, 0, 0, 1090)
        result = isl_reason(
            pos_a,
            vel_a,
            pos_b,
            vel_b,
            max_range_km=6000.0,
            max_tracking_rate_deg_s=2.0,
        )
        assert result == "ok"

    def test_polar_seam_cutoff(self):
        """Cross-plane ISL at polar latitude should be blocked by polar seam config."""
        # Simulate two satellites at high latitude
        geo_a = GeoPosition(80.0, 0.0, 550.0)
        geo_b = GeoPosition(80.0, 10.0, 550.0)
        pos_a = earth_geodetic_to_ecef(geo_a)
        pos_b = earth_geodetic_to_ecef(geo_b)
        result = isl_reason(
            pos_a,
            Vec3(0, 0, 0),
            pos_b,
            Vec3(0, 0, 0),
            max_range_km=5016.0,
            polar_seam_latitudes_deg=(geo_a.lat_deg, geo_b.lat_deg),
            latitude_threshold_deg=75.0,
        )
        assert result == "polar_seam"


class TestIslTerminalScheduling:
    def test_priority_ordering(self):
        feasible = [
            ("peer-C", 3, 1000.0),  # cross-left
            ("peer-A", 0, 500.0),  # intra-fwd
            ("peer-B", 1, 600.0),  # intra-aft
            ("peer-D", 2, 800.0),  # cross-right
        ]
        results = schedule_isl_terminals("sat-test", feasible, terminal_count=2)
        assert len(results) == 4
        # Top 2 by priority should be scheduled
        assert results[0].node_b == "peer-A"
        assert results[0].scheduled is True
        assert results[1].node_b == "peer-B"
        assert results[1].scheduled is True
        assert results[2].scheduled is False
        assert results[3].scheduled is False

    def test_all_terminals_available(self):
        feasible = [
            ("peer-A", 0, 500.0),
            ("peer-B", 1, 600.0),
        ]
        results = schedule_isl_terminals("sat-test", feasible, terminal_count=4)
        assert all(r.scheduled for r in results)

    def test_terminal_exhaustion(self):
        """More feasible ISLs than terminals → some unscheduled."""
        feasible = [
            ("peer-A", 0, 500.0),
            ("peer-B", 1, 600.0),
            ("peer-C", 2, 700.0),
            ("peer-D", 3, 800.0),
            ("peer-E", 4, 900.0),
            ("peer-F", 5, 1000.0),
        ]
        results = schedule_isl_terminals("sat-test", feasible, terminal_count=2)
        scheduled = [r for r in results if r.scheduled]
        unscheduled = [r for r in results if not r.scheduled]
        assert len(scheduled) == 2
        assert len(unscheduled) == 4


class TestSymmetricScheduling:
    def test_symmetric_pair_preserved(self):
        schedules = {
            "A": [ScheduledLink("A", "B", True, 500.0)],
            "B": [ScheduledLink("B", "A", True, 500.0)],
        }
        result = enforce_symmetric_scheduling(schedules)
        assert result["A"][0].scheduled is True
        assert result["B"][0].scheduled is True

    def test_asymmetric_pair_unscheduled(self):
        schedules = {
            "A": [ScheduledLink("A", "B", True, 500.0)],
            "B": [ScheduledLink("B", "A", False, 500.0)],  # B doesn't schedule A
        }
        result = enforce_symmetric_scheduling(schedules)
        assert result["A"][0].scheduled is False  # A→B unscheduled because B didn't schedule A

    def test_mixed_symmetric_and_asymmetric(self):
        schedules = {
            "A": [
                ScheduledLink("A", "B", True, 500.0),
                ScheduledLink("A", "C", True, 600.0),
            ],
            "B": [ScheduledLink("B", "A", True, 500.0)],
            "C": [ScheduledLink("C", "A", False, 600.0)],
        }
        result = enforce_symmetric_scheduling(schedules)
        # A↔B: symmetric, both stay scheduled
        assert result["A"][0].scheduled is True
        assert result["B"][0].scheduled is True
        # A→C: asymmetric (C doesn't schedule A), A→C gets unscheduled
        assert result["A"][1].scheduled is False
