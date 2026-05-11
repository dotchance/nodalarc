"""Test visibility computation — LOS, range, elevation, scheduling."""

from nodalarc.geo import compute_range_km
from ome.propagator import (
    GeoPosition,
    Vec3,
    elements_from_params,
    geodetic_to_ecef,
    propagate_eci,
    propagate_keplerian,
)
from ome.visibility import (
    ScheduledLink,
    check_ground_visibility,
    check_isl_visibility,
    compute_angular_velocity,
    compute_elevation_angle,
    enforce_symmetric_scheduling,
    has_line_of_sight,
    schedule_isl_terminals,
)

EPOCH = 1735689600.0


class TestLineOfSight:
    def test_same_plane_sats_have_los(self):
        """Two satellites in the same orbital plane, nearby, should have LOS."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 36.0)  # 36° apart
        pos1, _, _ = propagate_keplerian(e1, EPOCH, 0.0)
        pos2, _, _ = propagate_keplerian(e2, EPOCH, 0.0)
        assert has_line_of_sight(pos1, pos2)

    def test_opposite_side_sats_no_los(self):
        """Two satellites on opposite sides of Earth should NOT have LOS."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 180.0)
        pos1, _, _ = propagate_keplerian(e1, EPOCH, 0.0)
        pos2, _, _ = propagate_keplerian(e2, EPOCH, 0.0)
        assert not has_line_of_sight(pos1, pos2)

    def test_gs_and_overhead_sat_have_los(self):
        """GS and a satellite directly overhead should have LOS."""
        gs = geodetic_to_ecef(GeoPosition(0.0, 0.0, 0.0))
        sat = geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0))
        assert has_line_of_sight(gs, sat)

    def test_gs_and_horizon_sat(self):
        """GS and satellite just above the horizon should have LOS."""
        gs = geodetic_to_ecef(GeoPosition(0.0, 0.0, 0.0))
        # Satellite at ~20° elevation
        sat = geodetic_to_ecef(GeoPosition(10.0, 0.0, 550.0))
        assert has_line_of_sight(gs, sat)


class TestRange:
    def test_same_plane_range(self):
        """Two sats 36° apart at 550km should be ~4300 km apart."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 36.0)
        pos1, _, _ = propagate_keplerian(e1, EPOCH, 0.0)
        pos2, _, _ = propagate_keplerian(e2, EPOCH, 0.0)
        r = compute_range_km(pos1, pos2)
        # 2 * (R+h) * sin(θ/2) ≈ 2 * 6921 * sin(18°) ≈ 4278 km
        assert 4000.0 < r < 4600.0

    def test_range_within_limit(self):
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 36.0)
        pos1, _, _ = propagate_keplerian(e1, EPOCH, 0.0)
        pos2, _, _ = propagate_keplerian(e2, EPOCH, 0.0)
        r = compute_range_km(pos1, pos2)
        assert r < 5016.0  # starlink-early-44 max range


class TestElevationAngle:
    def test_overhead_satellite(self):
        """Satellite directly overhead → ~90° elevation."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = geodetic_to_ecef(gs_geo)
        sat_ecef = geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0))
        elev = compute_elevation_angle(gs_ecef, gs_geo, sat_ecef)
        assert 85.0 < elev <= 90.0

    def test_horizon_satellite(self):
        """Satellite moderately far → positive but low elevation angle."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = geodetic_to_ecef(gs_geo)
        # Satellite at 10° lat, 550km — should be above horizon but not high
        sat_ecef = geodetic_to_ecef(GeoPosition(10.0, 0.0, 550.0))
        elev = compute_elevation_angle(gs_ecef, gs_geo, sat_ecef)
        # Should be positive but moderate
        assert 0.0 < elev < 45.0

    def test_below_horizon(self):
        """Satellite behind the curve → negative elevation."""
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = geodetic_to_ecef(gs_geo)
        sat_ecef = geodetic_to_ecef(GeoPosition(60.0, 0.0, 550.0))
        elev = compute_elevation_angle(gs_ecef, gs_geo, sat_ecef)
        assert elev < 0.0


class TestGroundVisibility:
    def test_overhead_visible(self):
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = geodetic_to_ecef(gs_geo)
        sat_ecef = geodetic_to_ecef(GeoPosition(0.0, 0.0, 550.0))
        result = check_ground_visibility(gs_ecef, gs_geo, sat_ecef, min_elevation_deg=25.0)
        assert result.visible

    def test_below_min_elevation(self):
        gs_geo = GeoPosition(0.0, 0.0, 0.0)
        gs_ecef = geodetic_to_ecef(gs_geo)
        sat_ecef = geodetic_to_ecef(GeoPosition(25.0, 0.0, 550.0))
        result = check_ground_visibility(gs_ecef, gs_geo, sat_ecef, min_elevation_deg=25.0)
        # Far satellite should be below 25° elevation
        assert not result.visible


class TestIslVisibility:
    def test_nearby_sats_visible(self):
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 36.0)
        pos1, vel1 = propagate_eci(e1, 0.0)
        pos2, vel2 = propagate_eci(e2, 0.0)
        # Use ECI positions as approximate ECEF (t=0 acceptable for test)
        result = check_isl_visibility(pos1, vel1, pos2, vel2, max_range_km=5016.0)
        assert result.visible
        assert result.reason == "ok"

    def test_range_exceeded(self):
        """Two nearby sats with LOS but beyond a tight range limit."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 36.0)  # ~4300 km apart, have LOS
        pos1, vel1 = propagate_eci(e1, 0.0)
        pos2, vel2 = propagate_eci(e2, 0.0)
        # Range is ~4300 km, limit to 1000 km
        result = check_isl_visibility(pos1, vel1, pos2, vel2, max_range_km=1000.0)
        assert not result.visible
        assert result.reason == "range_exceeded"

    def test_polar_seam_cutoff(self):
        """Cross-plane ISL at polar latitude should be blocked by polar seam config."""
        # Simulate two satellites at high latitude
        geo_a = GeoPosition(80.0, 0.0, 550.0)
        geo_b = GeoPosition(80.0, 10.0, 550.0)
        pos_a = geodetic_to_ecef(geo_a)
        pos_b = geodetic_to_ecef(geo_b)
        result = check_isl_visibility(
            pos_a,
            Vec3(0, 0, 0),
            pos_b,
            Vec3(0, 0, 0),
            max_range_km=5016.0,
            polar_seam_enabled=True,
            latitude_threshold_deg=75.0,
            geo_a=geo_a,
            geo_b=geo_b,
        )
        assert not result.visible
        assert result.reason == "polar_seam"


class TestAngularVelocity:
    def test_co_rotating_same_plane_near_zero(self):
        """Two satellites in the same plane, co-rotating → near-zero angular velocity."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 36.0)
        pos1, vel1 = propagate_eci(e1, 0.0)
        pos2, vel2 = propagate_eci(e2, 0.0)
        ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
        # Same orbital plane, same altitude → relative angular velocity should be very small
        # (it's not exactly zero because of the angular separation, but should be < 0.1 deg/s)
        assert ang_vel < 0.5

    def test_cross_plane_moderate_angular_velocity(self):
        """Cross-plane satellites have moderate angular velocity."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 30.0, 0.0)  # Different RAAN
        pos1, vel1 = propagate_eci(e1, 0.0)
        pos2, vel2 = propagate_eci(e2, 0.0)
        ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
        # Cross-plane → some angular velocity
        assert ang_vel > 0.0

    def test_counter_rotating_high_angular_velocity(self):
        """Counter-rotating satellites passing each other → high angular velocity.

        Construct positions/velocities directly: two satellites 200 km apart
        along X axis, with opposite Y velocities (perpendicular to LOS).
        This simulates a counter-rotating polar seam encounter.
        """
        v = 7.59  # km/s (typical LEO velocity)
        # Separated along X, velocities along Y → perpendicular to LOS
        pos1 = Vec3(6921.0, 0.0, 0.0)
        vel1 = Vec3(0.0, v, 0.0)
        pos2 = Vec3(7121.0, 0.0, 0.0)  # 200 km apart in X
        vel2 = Vec3(0.0, -v, 0.0)  # Counter-rotating in Y

        ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
        # Relative velocity = (0, 2v, 0) entirely perpendicular to LOS (along X)
        # ω = 2v / 200 ≈ 15.18/200 ≈ 0.076 rad/s ≈ 4.35 deg/s
        assert ang_vel > 3.0


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
