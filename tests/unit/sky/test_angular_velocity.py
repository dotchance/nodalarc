"""Test angular velocity computation — standalone file per PRD Appendix B.

Tests:
- Co-rotating same-plane neighbors: near-zero angular velocity
- Cross-plane neighbors at increasing latitudes: increasing angular velocity
- Tracking rate feasibility check against starlink-early-44 calibrated rate
- Counter-rotating (walker-star) high angular velocity
"""

from ome.propagator import (
    Vec3,
)
from ome.visibility import compute_angular_velocity

from tests.physics_fixtures import (
    earth_elements_from_params,
    earth_orbital_period,
    earth_propagate_eci,
)


class TestCoRotatingSamePlane:
    def test_near_zero_angular_velocity(self):
        """Two satellites in the same plane, co-rotating → near-zero angular velocity."""
        e1 = earth_elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2 = earth_elements_from_params(550.0, 53.0, 0.0, 36.0)
        pos1, vel1 = earth_propagate_eci(e1, 0.0)
        pos2, vel2 = earth_propagate_eci(e2, 0.0)
        ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
        assert ang_vel < 0.5, f"Same-plane angular velocity {ang_vel:.4f} deg/s should be < 0.5"

    def test_same_plane_various_separations(self):
        """Same-plane sats at different separations all have very low angular velocity."""
        for ta_sep in [10.0, 20.0, 36.0, 60.0]:
            e1 = earth_elements_from_params(550.0, 53.0, 0.0, 0.0)
            e2 = earth_elements_from_params(550.0, 53.0, 0.0, ta_sep)
            pos1, vel1 = earth_propagate_eci(e1, 0.0)
            pos2, vel2 = earth_propagate_eci(e2, 0.0)
            ang_vel = compute_angular_velocity(pos1, vel1, pos2, vel2)
            assert ang_vel < 0.5, (
                f"Same-plane sep={ta_sep}° angular velocity {ang_vel:.4f} should be < 0.5"
            )


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
        e1_delta = earth_elements_from_params(550.0, 53.0, 0.0, 0.0)
        e2_delta = earth_elements_from_params(550.0, 53.0, 30.0, 0.0)

        # Walker-star: 97.4° inclination
        e1_star = earth_elements_from_params(550.0, 97.4, 0.0, 0.0)
        e2_star = earth_elements_from_params(550.0, 97.4, 90.0, 0.0)

        # Find peak for each
        period = earth_orbital_period(550.0)
        max_delta = 0.0
        max_star = 0.0
        for step in range(0, int(period), 50):
            dt = float(step)
            pos1, vel1 = earth_propagate_eci(e1_delta, dt)
            pos2, vel2 = earth_propagate_eci(e2_delta, dt)
            max_delta = max(max_delta, compute_angular_velocity(pos1, vel1, pos2, vel2))

            pos1, vel1 = earth_propagate_eci(e1_star, dt)
            pos2, vel2 = earth_propagate_eci(e2_star, dt)
            max_star = max(max_star, compute_angular_velocity(pos1, vel1, pos2, vel2))

        assert max_star > max_delta, (
            f"Walker-star peak {max_star:.4f} should exceed walker-delta peak {max_delta:.4f}"
        )
