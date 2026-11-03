"""Line-of-sight angular rate of signals between two Earth satellites.

``SignalEnd.rate_rad_s`` for two ends on one central body is the rate in that body's rotating
frame; OME limits terminal tracking with it. Checked properties: neighbours moving together in
one plane turn the line of sight slowly, satellites passing in opposite directions turn it fast,
and a near-polar (walker-star) cross-plane pair peaks above an inclined (walker-delta) pair. The
polar seam shell's encounters are compared with the declared-parameter reference model
(tests/seam_reference.py) for rate and path length.

The reference values these thresholds came from were inertial. Earth's rotation changes a rate by
at most Earth's own rate, 0.0042 deg/s, and no threshold needed to change. Measured in the
rotating frame (inertial in brackets): same-plane neighbours 0.0603 deg/s at 10 to 60 degrees
apart (0.0628); the counter-rotating pair 4.322 deg/s (4.317); the walker-delta pair's peak
0.1002 deg/s (0.1044) and the walker-star pair's 0.4918 deg/s (0.4876). Seam encounters: rates
within 0.0040 deg/s of the reference and path lengths within 0.71 km of its range.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import PhysicsContext
from nodalarc.physics.kepler import OrbitModel
from nodalarc.physics.lighttime import OrbitEndpoint, signals_sent_at

from tests.physics_fixtures import (
    EARTH_TEST_BODY_FRAME,
    LUNA_TEST_BODY_FRAME,
    earth_elements_from_params,
    earth_orbital_period,
)
from tests.seam_reference import DeclaredShell, encounter

SHAPES = {"earth": EARTH_TEST_BODY_FRAME, "luna": LUNA_TEST_BODY_FRAME}
EPOCH = datetime(2026, 11, 3, 12, tzinfo=UTC).timestamp()


def _satellite(elements: OrbitalElements, model: OrbitModel = "two-body") -> OrbitEndpoint:
    return OrbitEndpoint("earth", EARTH_TEST_BODY_FRAME, model, elements, EPOCH)


def _rates_deg_s(
    physics: PhysicsContext, a: OrbitEndpoint, b: OrbitEndpoint, after_s: float = 0.0
) -> tuple[float, float]:
    """The line-of-sight rate at each end of the signal a sends to b ``after_s`` past the
    elements' epoch: the transmitter's, then the receiver's."""
    signals = signals_sent_at(physics, SHAPES, [a], [b], EPOCH + after_s)
    return (
        math.degrees(float(signals.transmitter.rate_rad_s[0])),
        math.degrees(float(signals.receiver.rate_rad_s[0])),
    )


class TestCoRotatingSamePlane:
    def test_near_zero_angular_velocity(self, physics_context: PhysicsContext) -> None:
        """Two satellites in the same plane, co-rotating → near-zero angular velocity."""
        a = _satellite(earth_elements_from_params(550.0, 53.0, 0.0, 0.0))
        b = _satellite(earth_elements_from_params(550.0, 53.0, 0.0, 36.0))
        for rate in _rates_deg_s(physics_context, a, b):
            assert rate < 0.5, f"Same-plane angular velocity {rate:.4f} deg/s should be < 0.5"

    def test_same_plane_various_separations(self, physics_context: PhysicsContext) -> None:
        """Same-plane sats at different separations all have very low angular velocity."""
        for ta_sep in [10.0, 20.0, 36.0, 60.0]:
            a = _satellite(earth_elements_from_params(550.0, 53.0, 0.0, 0.0))
            b = _satellite(earth_elements_from_params(550.0, 53.0, 0.0, ta_sep))
            for rate in _rates_deg_s(physics_context, a, b):
                assert rate < 0.5, (
                    f"Same-plane sep={ta_sep}° angular velocity {rate:.4f} should be < 0.5"
                )


class TestCounterRotating:
    def test_counter_rotating_high_angular_velocity(self, physics_context: PhysicsContext) -> None:
        """Counter-rotating satellites passing each other → high angular velocity.

        Walker-star polar orbits have counter-rotating adjacent planes at seam. Here a
        prograde equatorial orbit at 6921 km and a retrograde one at 7121 km, both on the
        x axis at the epoch: the line of sight is radial, 200 km long, and the satellites
        cross it in opposite directions at 7.59 and 7.48 km/s.
        """
        prograde = _satellite(
            OrbitalElements(
                semi_major_axis_km=6921.0, inclination_rad=0.0, raan_rad=0.0, mean_anomaly_rad=0.0
            )
        )
        retrograde = _satellite(
            OrbitalElements(
                semi_major_axis_km=7121.0,
                inclination_rad=math.pi,
                raan_rad=0.0,
                mean_anomaly_rad=0.0,
            )
        )
        # ω = (7.59 + 7.48) / 200 rad/s ≈ 4.3 deg/s
        for rate in _rates_deg_s(physics_context, prograde, retrograde):
            assert rate > 3.0, f"Counter-rotating angular velocity {rate:.2f} should be > 3.0"

    def test_walker_star_cross_plane_higher_than_walker_delta(
        self, physics_context: PhysicsContext
    ) -> None:
        """Walker-star (97.4° incl) cross-plane angular velocity > walker-delta (53°).

        Near-polar orbits have higher relative velocities at equatorial crossings.
        """
        # Walker-delta: 53° inclination
        delta = (
            _satellite(earth_elements_from_params(550.0, 53.0, 0.0, 0.0)),
            _satellite(earth_elements_from_params(550.0, 53.0, 30.0, 0.0)),
        )
        # Walker-star: 97.4° inclination
        star = (
            _satellite(earth_elements_from_params(550.0, 97.4, 0.0, 0.0)),
            _satellite(earth_elements_from_params(550.0, 97.4, 90.0, 0.0)),
        )

        # Find peak for each
        period = earth_orbital_period(550.0)
        max_delta = 0.0
        max_star = 0.0
        for step in range(0, int(period), 50):
            signals = signals_sent_at(
                physics_context, SHAPES, [delta[0], star[0]], [delta[1], star[1]], EPOCH + step
            )
            rates = np.degrees(
                np.maximum(signals.transmitter.rate_rad_s, signals.receiver.rate_rad_s)
            )
            max_delta = max(max_delta, float(rates[0]))
            max_star = max(max_star, float(rates[1]))

        assert max_star > max_delta, (
            f"Walker-star peak {max_star:.4f} should exceed walker-delta peak {max_delta:.4f}"
        )


def _seam_satellite(shell: DeclaredShell, plane: int, slot: int) -> OrbitEndpoint:
    """A seam shell satellite with the reference model's elements, on the declared J2 model."""
    semi_major_axis_km, inclination_rad, raan_rad, argument_of_latitude_rad = (
        shell.initial_elements(plane, slot)
    )
    elements = OrbitalElements(
        semi_major_axis_km=semi_major_axis_km,
        inclination_rad=inclination_rad,
        raan_rad=raan_rad,
        mean_anomaly_rad=argument_of_latitude_rad,
    )
    return _satellite(elements, "j2-mean-elements")


def _path_and_rate(
    physics: PhysicsContext, a: OrbitEndpoint, b: OrbitEndpoint, after_s: float
) -> tuple[float, float]:
    """Path length (km) and the larger end rate (deg/s) of the signal a sends to b."""
    signals = signals_sent_at(physics, SHAPES, [a], [b], EPOCH + after_s)
    path_km = float(signals.light_time_s[0]) * SPEED_OF_LIGHT_KM_S
    rate = max(float(signals.transmitter.rate_rad_s[0]), float(signals.receiver.rate_rad_s[0]))
    return path_km, math.degrees(rate)


class TestPolarSeamEncounters:
    """The shipped polar shell's seam pairs, with production propagation and signals, against
    the declared-parameter reference model."""

    def test_range_exceedance_and_recovery_path_follows_the_reference(
        self, physics_context: PhysicsContext
    ) -> None:
        """The counter-rotating seam pair just inside, just outside and back inside 6000 km."""
        shell = DeclaredShell(780.0, 86.4, 31.6, 6, 10.0)
        a, b = _seam_satellite(shell, 5, 0), _seam_satellite(shell, 0, 0)
        for t_s in (700, 1500, 3720):
            reference_range, _rate, _clear = encounter(shell, (5, 0), (0, 0), t_s)
            path_km, _ = _path_and_rate(physics_context, a, b, t_s)
            assert abs(path_km - reference_range) < 1.0, (t_s, path_km, reference_range)

    def test_close_pass_rate_follows_the_reference(self, physics_context: PhysicsContext) -> None:
        """The seam-crossing shell's close pass: under 400 km, with the rate rising above
        2 deg/s and falling back, as the reference model puts it."""
        shell = DeclaredShell(780.0, 86.4, 31.6, 6, 15.0)
        a, b = _seam_satellite(shell, 5, 0), _seam_satellite(shell, 0, 2)
        for t_s in (2870, 2890, 2910):
            _range, reference_rate, _clear = encounter(shell, (5, 0), (0, 2), t_s)
            path_km, rate = _path_and_rate(physics_context, a, b, t_s)
            assert abs(rate - reference_rate) < 0.02, (t_s, rate, reference_rate)
            assert path_km < 400.0, (t_s, path_km)

    def test_closest_approach_of_the_range_experiment(
        self, physics_context: PhysicsContext
    ) -> None:
        """The range experiment's closest approach: 1954 km at 0.43 deg/s."""
        shell = DeclaredShell(780.0, 86.4, 31.6, 6, 10.0)
        path_km, rate = _path_and_rate(
            physics_context, _seam_satellite(shell, 5, 0), _seam_satellite(shell, 0, 0), 1090
        )
        assert 1900.0 < path_km < 2000.0
        assert 0.40 < rate < 0.46
