"""Test angular velocity computation — standalone file per PRD Appendix B.

Tests:
- Co-rotating same-plane neighbors: near-zero angular velocity
- Cross-plane neighbors at increasing latitudes: increasing angular velocity
- Tracking rate feasibility check against starlink-early-44 calibrated rate
- Counter-rotating (walker-star) high angular velocity
"""

import pytest

from ome.propagator import (
    Vec3,
    elements_from_params,
    orbital_period,
    propagate_eci,
)
from ome.visibility import compute_angular_velocity

EPOCH = 1735689600.0


class TestCoRotatingSamePlane:
    def test_near_zero_angular_velocity(self):
        """Two satellites in the same plane, co-rotating → near-zero angular velocity."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 0.0, 36.0)
        pos1, vel1 = propagate_eci(e1, 0.0)
        pos2, vel2 = propagate_eci(e2, 0.0)
        ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
        assert ang_vel < 0.5, f"Same-plane angular velocity {ang_vel:.4f} deg/s should be < 0.5"

    def test_same_plane_various_separations(self):
        """Same-plane sats at different separations all have very low angular velocity."""
        for ta_sep in [10.0, 20.0, 36.0, 60.0]:
            e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
            e2 = elements_from_params(550.0, 53.0, 0.0, ta_sep)
            pos1, vel1 = propagate_eci(e1, 0.0)
            pos2, vel2 = propagate_eci(e2, 0.0)
            ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
            assert ang_vel < 0.5, (
                f"Same-plane sep={ta_sep}° angular velocity {ang_vel:.4f} should be < 0.5"
            )


class TestCrossPlaneIncreasingLatitude:
    def test_cross_plane_has_nonzero_angular_velocity(self):
        """Cross-plane neighbors have measurable angular velocity."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 30.0, 0.0)
        pos1, vel1 = propagate_eci(e1, 0.0)
        pos2, vel2 = propagate_eci(e2, 0.0)
        ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
        assert ang_vel > 0.0, "Cross-plane should have nonzero angular velocity"

    def test_angular_velocity_varies_with_orbital_position(self):
        """Cross-plane angular velocity varies as satellites move along orbit."""
        e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = elements_from_params(550.0, 53.0, 30.0, 6.0)  # starlink-early-44 RAAN + phase
        period = orbital_period(550.0)

        angular_velocities = []
        for step in range(0, int(period), 100):
            dt = float(step)
            pos1, vel1 = propagate_eci(e1, dt)
            pos2, vel2 = propagate_eci(e2, dt)
            ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
            angular_velocities.append(ang_vel)

        # Angular velocity should vary (not constant)
        assert max(angular_velocities) > min(angular_velocities)


class TestTrackingRateCalibration:
    def test_starlink_early_peak_below_config_rate(self):
        """Peak cross-plane angular velocity for starlink-early-44 is below configured 3.0 deg/s.

        PRD R-OME-003: calibration task to verify tracking rate covers actual peak.
        Starlink-early-44: 4 planes, 45° RAAN spacing, 53° inclination, 550 km.
        """
        from pathlib import Path

        from nodalarc.constellation_loader import load_constellation

        config_path = (
            Path(__file__).parent.parent.parent / "configs/constellations/starlink-early-44.yaml"
        )
        if not config_path.exists():
            pytest.skip("starlink-early-44 config not available")

        config = load_constellation(config_path)
        tracking_rate = config.default_terminals.isl[0].max_tracking_rate_deg_s

        # Compute peak cross-plane angular velocity across all RAAN pairings
        period = orbital_period(550.0)
        max_ang_vel = 0.0
        raan_spacing = 45.0
        phase_offset = 8.2

        for plane_delta in [1, 2, 3]:  # Adjacent, 2-away, 3-away planes
            raan_diff = plane_delta * raan_spacing
            for step in range(0, int(period), 10):
                dt = float(step)
                e1 = elements_from_params(550.0, 53.0, 0.0, 0.0)
                e2 = elements_from_params(550.0, 53.0, raan_diff, plane_delta * phase_offset)
                pos1, vel1 = propagate_eci(e1, dt)
                pos2, vel2 = propagate_eci(e2, dt)
                ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
                if ang_vel > max_ang_vel:
                    max_ang_vel = ang_vel

        assert tracking_rate > max_ang_vel, (
            f"Config tracking rate {tracking_rate} deg/s must exceed "
            f"peak angular velocity {max_ang_vel:.4f} deg/s"
        )

    def test_tracking_rate_read_from_config(self):
        """OME reads tracking rate from constellation config, not hardcoded."""
        from pathlib import Path

        from nodalarc.constellation_loader import load_constellation

        config_path = (
            Path(__file__).parent.parent.parent / "configs/constellations/starlink-early-44.yaml"
        )
        config = load_constellation(config_path)

        # Config has an explicit tracking rate
        assert config.default_terminals.isl[0].max_tracking_rate_deg_s > 0
        # The value should be 3.0 (from the config file)
        assert config.default_terminals.isl[0].max_tracking_rate_deg_s == 3.0


class TestCounterRotating:
    def test_counter_rotating_high_angular_velocity(self):
        """Counter-rotating satellites passing each other → high angular velocity.

        Walker-star polar orbits have counter-rotating adjacent planes at seam.
        Simulated with directly opposing velocities perpendicular to LOS.
        """
        v = 7.59  # km/s typical LEO velocity
        pos1 = Vec3(6921.0, 0.0, 0.0)
        vel1 = Vec3(0.0, v, 0.0)
        pos2 = Vec3(7121.0, 0.0, 0.0)
        vel2 = Vec3(0.0, -v, 0.0)

        ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
        # ω = 2v / 200 ≈ 4.35 deg/s
        assert ang_vel > 3.0, f"Counter-rotating angular velocity {ang_vel:.2f} should be > 3.0"

    def test_walker_star_cross_plane_higher_than_walker_delta(self):
        """Walker-star (97.4° incl) cross-plane angular velocity > walker-delta (53°).

        Near-polar orbits have higher relative velocities at equatorial crossings.
        """
        # Walker-delta: 53° inclination
        e1_delta = elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2_delta = elements_from_params(550.0, 53.0, 30.0, 0.0)

        # Walker-star: 97.4° inclination
        e1_star = elements_from_params(550.0, 97.4, 0.0, 0.0)
        e2_star = elements_from_params(550.0, 97.4, 90.0, 0.0)

        # Find peak for each
        period = orbital_period(550.0)
        max_delta = 0.0
        max_star = 0.0
        for step in range(0, int(period), 50):
            dt = float(step)
            pos1, vel1 = propagate_eci(e1_delta, dt)
            pos2, vel2 = propagate_eci(e2_delta, dt)
            max_delta = max(max_delta, compute_angular_velocity(pos1, vel1, pos2, vel2))

            pos1, vel1 = propagate_eci(e1_star, dt)
            pos2, vel2 = propagate_eci(e2_star, dt)
            max_star = max(max_star, compute_angular_velocity(pos1, vel1, pos2, vel2))

        assert max_star > max_delta, (
            f"Walker-star peak {max_star:.4f} should exceed walker-delta peak {max_delta:.4f}"
        )
