"""The production orbit kernel: determinism across batch arrangements, input
refusals, and agreement with SPICE over a grid covering the solver's branches."""

from __future__ import annotations

import math

import numpy as np
import pytest
import spiceypy
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import ElementsBatch, orbit_states

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME as EARTH


def _elements_grid(n: int, seed: int = 7) -> list[OrbitalElements]:
    """Deterministic element sets covering the solver's branches: circular
    (e == 0), moderately and highly eccentric (e >= 0.8 starts at pi),
    negative mean anomalies, LEO through GEO radii, all inclinations."""
    rng = np.random.default_rng(seed)
    a = rng.uniform(6700.0, 42164.0, n)
    e = np.where(rng.random(n) < 0.4, 0.0, rng.uniform(0.0, 0.85, n))
    inc = rng.uniform(0.0, math.pi, n)
    raan = rng.uniform(0.0, 2 * math.pi, n)
    argp = rng.uniform(0.0, 2 * math.pi, n)
    m0 = rng.uniform(-2 * math.pi, 4 * math.pi, n)
    return [
        OrbitalElements(
            float(a[i]),
            float(inc[i]),
            float(raan[i]),
            eccentricity=float(e[i]),
            argument_of_perigee_rad=float(argp[i]),
            mean_anomaly_rad=float(m0[i]),
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("model", ["two-body", "j2-mean-elements"])
def test_kernel_is_arrangement_invariant(model) -> None:
    """Batch (N,T) == per-column (N,1) == per-element (1,T), bit for bit.

    The kernel has no reductions, so the arrangement of a computation cannot
    change an output bit; replay depends on this.
    """
    elements = _elements_grid(24, seed=11)
    dts = np.array([5.0, 60.0, 600.0], dtype=np.float64)
    batch = ElementsBatch.from_elements(elements)
    full = orbit_states(batch, dts, model=model, body_frame=EARTH)

    for j in range(len(dts)):
        col = orbit_states(batch, dts[j : j + 1], model=model, body_frame=EARTH)
        assert np.array_equal(col.px[:, 0], full.px[:, j])
        assert np.array_equal(col.vy[:, 0], full.vy[:, j])

    for i in range(0, len(elements), 7):
        single = ElementsBatch.from_elements(elements[i : i + 1])
        cell = orbit_states(single, dts, model=model, body_frame=EARTH)
        assert np.array_equal(cell.pz[0, :], full.pz[i, :])
        assert np.array_equal(cell.vx[0, :], full.vx[i, :])


def test_two_body_grid_matches_spice_prop2b() -> None:
    mu = EARTH.gravitational_parameter_km3_s2
    elements = _elements_grid(60, seed=3)
    dts = np.array([0.0, 1.0, 3600.0, 86400.0, 864000.0])
    states = orbit_states(
        ElementsBatch.from_elements(elements), dts, model="two-body", body_frame=EARTH
    )
    for i, el in enumerate(elements):
        start = np.array(
            spiceypy.conics(
                [
                    el.semi_major_axis_km * (1.0 - el.eccentricity),
                    el.eccentricity,
                    el.inclination_rad,
                    el.raan_rad,
                    el.argument_of_perigee_rad,
                    el.mean_anomaly_rad,
                    0.0,
                    mu,
                ],
                0.0,
            )
        )
        for j, dt in enumerate(dts):
            ref = np.array(spiceypy.prop2b(mu, start, float(dt)))
            position = np.array([states.px[i, j], states.py[i, j], states.pz[i, j]])
            velocity = np.array([states.vx[i, j], states.vy[i, j], states.vz[i, j]])
            assert np.linalg.norm(position - ref[:3]) < 1e-5, (i, dt)
            assert np.linalg.norm(velocity - ref[3:]) < 1e-6, (i, dt)


def test_kernel_refuses_a_degenerate_ellipse() -> None:
    # OrbitalElements refuses e >= 1 at construction; the kernel guards raw
    # arrays too, so build one directly.
    bad = ElementsBatch(
        semi_major_axis_km=np.array([7000.0]),
        eccentricity=np.array([1.0]),
        inclination_rad=np.array([0.5]),
        raan_rad=np.array([0.0]),
        argument_of_perigee_rad=np.array([0.0]),
        mean_anomaly_rad=np.array([0.0]),
    )
    with pytest.raises(ValueError, match="semi-latus rectum"):
        orbit_states(bad, np.array([1.0]), model="j2-mean-elements", body_frame=EARTH)


def test_elements_batch_rejects_ragged_shapes() -> None:
    with pytest.raises(ValueError, match="shape"):
        ElementsBatch(
            semi_major_axis_km=np.zeros(3),
            eccentricity=np.zeros(2),
            inclination_rad=np.zeros(3),
            raan_rad=np.zeros(3),
            argument_of_perigee_rad=np.zeros(3),
            mean_anomaly_rad=np.zeros(3),
        )
