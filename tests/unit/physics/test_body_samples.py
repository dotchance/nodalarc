"""Body-sample windows: the grid, values against SPICE, and the interpolation
error at the sample spacing for every supported body."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
import spiceypy
from nodalarc.models.events import BodySamples
from nodalarc.physics import PhysicsContext, PhysicsDataError, epoch_of
from nodalarc.physics.ephemeris_table import BODIES
from nodalarc.physics.samples import (
    SAMPLE_INTERVAL_S,
    WINDOW_AFTER_S,
    WINDOW_BEFORE_S,
    body_sample_window,
    next_window_due_unix,
)
from pydantic import ValidationError

SPICE_FRAME = {"earth": "ITRF93", "luna": "MOON_ME_DE440_ME421"}
PRODUCED = datetime(2026, 11, 3, 0, 7, 13, tzinfo=UTC).timestamp()


def _et(t: float) -> float:
    return epoch_of(t).to_et_seconds()


def _vee(skew: np.ndarray) -> np.ndarray:
    return np.array([skew[2, 1], skew[0, 2], skew[1, 0]])


def _rotation_from_vector(v: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(v))
    if angle == 0.0:
        return np.eye(3)
    k = v / angle
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + np.sin(angle) * kx + (1.0 - np.cos(angle)) * kx @ kx


def _rotation_angle(a: np.ndarray, b: np.ndarray) -> float:
    # atan2 of sine and cosine: arccos of the cosine alone cannot resolve an
    # angle below sqrt(2 * 2.2e-16) = 2.1e-8 rad.
    m = a.T @ b
    sine = np.linalg.norm(_vee(m - m.T)) / 2.0
    cosine = (np.trace(m) - 1.0) / 2.0
    return float(np.arctan2(sine, cosine))


def _hermite(p0, v0, p1, v1, h: float, s: float) -> np.ndarray:
    h00 = 2 * s**3 - 3 * s**2 + 1
    h10 = s**3 - 2 * s**2 + s
    h01 = -2 * s**3 + 3 * s**2
    h11 = s**3 - s**2
    return h00 * p0 + h10 * h * v0 + h01 * p1 + h11 * h * v1


def test_window_spans_the_production_instant_on_the_aligned_grid(
    physics_context: PhysicsContext,
) -> None:
    window = body_sample_window(physics_context, epoch_id=4, produced_at_unix=PRODUCED)
    times = [sample.sim_time_unix for sample in window.samples]

    assert window.epoch_id == 4
    assert window.interval_s == SAMPLE_INTERVAL_S
    assert times[0] % SAMPLE_INTERVAL_S == 0.0
    assert times[0] <= PRODUCED - WINDOW_BEFORE_S < times[0] + SAMPLE_INTERVAL_S
    assert times[-2] < PRODUCED + WINDOW_AFTER_S <= times[-1]
    assert all(set(sample.bodies) == {"earth", "luna"} for sample in window.samples)
    assert next_window_due_unix(PRODUCED) == PRODUCED + WINDOW_AFTER_S / 2


def test_windows_carry_identical_values_at_shared_instants(
    physics_context: PhysicsContext,
) -> None:
    first = body_sample_window(physics_context, epoch_id=0, produced_at_unix=PRODUCED)
    second = body_sample_window(
        physics_context, epoch_id=0, produced_at_unix=next_window_due_unix(PRODUCED)
    )
    by_time = {sample.sim_time_unix: sample for sample in first.samples}
    shared = [sample for sample in second.samples if sample.sim_time_unix in by_time]

    assert len(shared) >= 20
    for sample in shared:
        assert sample == by_time[sample.sim_time_unix]


@pytest.mark.usefixtures("spice_reference")
def test_sample_values_match_spice(physics_context: PhysicsContext) -> None:
    window = body_sample_window(physics_context, epoch_id=0, produced_at_unix=PRODUCED)
    # A GEO-radius state, to check the body-fixed velocity relation.
    r = np.array([30000.0, 25000.0, 9000.0])
    v = np.array([-1.8, 2.2, 0.4])
    for sample in window.samples[::7]:
        et = _et(sample.sim_time_unix)
        moon, _ = spiceypy.spkezr("MOON", et, "J2000", "NONE", "EARTH")
        luna = sample.bodies["luna"]
        assert np.linalg.norm(np.array(luna.position_km) - moon[:3]) < 1e-3
        assert np.linalg.norm(np.array(luna.velocity_km_s) - moon[3:]) < 1e-6
        sun, _ = spiceypy.spkezr("SUN", et, "J2000", "NONE", "EARTH")
        assert np.linalg.norm(np.array(sample.sun.position_km) - sun[:3]) < 1e-3
        assert np.linalg.norm(np.array(sample.sun.velocity_km_s) - sun[3:]) < 1e-6
        assert sample.bodies["earth"].position_km == (0.0, 0.0, 0.0)

        for body_id, body in sample.bodies.items():
            ref = np.array(spiceypy.sxform("J2000", SPICE_FRAME[body_id], et))
            rotation = np.array(body.rotation)
            omega = np.array(body.angular_velocity_rad_s)
            # Measured: Earth 1.6e-14 rad, Luna 6.0e-13 rad.
            assert _rotation_angle(rotation, ref[:3, :3]) < 1e-11
            assert np.linalg.norm(omega - _vee(-ref[:3, :3].T @ ref[3:, :3])) < 1e-13
            fixed_velocity = rotation @ (v - np.cross(omega, r))
            assert np.linalg.norm(fixed_velocity - (ref @ np.concatenate([r, v]))[3:]) < 1e-9


@pytest.mark.parametrize("body_id", sorted(BODIES))
def test_interpolation_error_at_the_sample_spacing(
    physics_context: PhysicsContext, body_id: str
) -> None:
    """Cubic Hermite positions and constant-rate rotation between samples.

    Bounds: 1 mm for body positions; 1e-10 rad for orientation (4.2 mm at GEO
    radius). Measured for Earth and Luna: 0.1 mm and 9.5e-12 rad. The Sun's
    bound is 10 mm: its error measured 3.0 mm at sample spacings from 150 s to
    1200 s alike, so it is the data's evaluation noise at the Sun's distance
    (2e-14 of it), not interpolation error.
    """
    h = SAMPLE_INTERVAL_S
    worst_position = 0.0
    worst_sun = 0.0
    worst_angle = 0.0
    for k in range(48):
        ta = PRODUCED + k * 1800.0
        tb = ta + h
        state_a = physics_context.body_state(body_id, ta)
        state_b = physics_context.body_state(body_id, tb)
        sun_a = physics_context.sun_state(ta)
        sun_b = physics_context.sun_state(tb)
        rotation_a = physics_context.orientation(body_id, ta).rotation
        rotation_b = physics_context.orientation(body_id, tb).rotation
        step = rotation_b @ rotation_a.T
        step_vector = _vee(step - step.T) / 2.0
        step_vector *= np.arcsin(min(1.0, np.linalg.norm(step_vector))) / max(
            np.linalg.norm(step_vector), 1e-300
        )
        for s in (0.25, 0.5, 0.75):
            t = ta + s * h
            body_error = _hermite_error(
                state_a, state_b, physics_context.body_state(body_id, t), h, s
            )
            sun_error = _hermite_error(sun_a, sun_b, physics_context.sun_state(t), h, s)
            worst_position = max(worst_position, body_error)
            worst_sun = max(worst_sun, sun_error)
            estimate_rotation = _rotation_from_vector(s * step_vector) @ rotation_a
            truth_rotation = physics_context.orientation(body_id, t).rotation
            worst_angle = max(worst_angle, _rotation_angle(estimate_rotation, truth_rotation))

    assert worst_position < 1e-6
    assert worst_sun < 1e-5
    assert worst_angle < 1e-10


def _hermite_error(a, b, truth, h: float, s: float) -> float:
    estimate = _hermite(
        np.array(a.position_km),
        np.array(a.velocity_km_s),
        np.array(b.position_km),
        np.array(b.velocity_km_s),
        h,
        s,
    )
    return float(np.linalg.norm(estimate - np.array(truth.position_km)))


def test_window_outside_the_data_coverage_is_refused(physics_context: PhysicsContext) -> None:
    beyond = datetime(2200, 1, 1, tzinfo=UTC).timestamp()
    with pytest.raises(PhysicsDataError, match="does not cover"):
        body_sample_window(physics_context, epoch_id=0, produced_at_unix=beyond)


def test_samples_off_the_grid_are_refused(physics_context: PhysicsContext) -> None:
    window = body_sample_window(physics_context, epoch_id=0, produced_at_unix=PRODUCED)
    shifted = list(window.samples)
    shifted[3] = shifted[3].model_copy(update={"sim_time_unix": shifted[3].sim_time_unix + 1.0})
    with pytest.raises(ValidationError, match="the grid places it"):
        BodySamples(epoch_id=0, interval_s=window.interval_s, samples=tuple(shifted))
