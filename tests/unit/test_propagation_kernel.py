# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Equivalence suite: the vectorized kernel against the scalar propagator.

The kernel's contract is BIT-EXACTNESS, not closeness: same models, same
operation order, numpy's sin/cos/sqrt bit-identical to libm on this
stack. Every comparison here is ``==`` on float64 — a single differing
bit fails. Arrangement invariance (batch vs column vs element) is the
structural property that makes replay and shard reassignment honest by
construction; it is enforced here, not assumed.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from nodalarc.body_frames import BodyFrame
from nodalarc.orbital import OrbitalElements
from nodalarc.propagation_kernel import (
    ElementsBatch,
    body_rotation_angle_batch,
    eci_to_body_fixed_batch,
    propagate_eci_batch,
)
from nodalarc.propagator import (
    eci_to_body_fixed,
    propagate_eci_j2_mean_elements_for_body,
)

EARTH = BodyFrame(
    name="earth",
    mean_radius_km=6371.0,
    equatorial_radius_km=6378.137,
    polar_radius_km=6356.752,
    rotation_rate_rad_s=7.2921159e-5,
    gravitational_parameter_km3_s2=398600.4418,
    j2=1.08262668e-3,
)
LUNA = BodyFrame(
    name="luna",
    mean_radius_km=1737.4,
    equatorial_radius_km=1738.1,
    polar_radius_km=1736.0,
    rotation_rate_rad_s=2.6616995e-6,
    gravitational_parameter_km3_s2=4902.800066,
    j2=0.0,
)

EPOCH = 1_780_000_000.0


def _elements_grid(n: int, seed: int = 7) -> list[OrbitalElements]:
    """Deterministic element set covering the solver's branch space:
    circular (e == 0 early return), moderately and highly eccentric
    (e >= 0.8 seeds at pi), negative mean anomalies (fmod reduction),
    LEO through GEO radii, full inclination range."""
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


@pytest.mark.parametrize("body", [EARTH, LUNA], ids=lambda b: b.name)
def test_kernel_positions_and_velocities_are_bit_identical_to_scalar(body):
    elements = _elements_grid(60)
    dts = np.array([0.0, 1.0, 10.0, 3600.0, 86400.0], dtype=np.float64)
    batch = ElementsBatch.from_elements(elements)
    state = propagate_eci_batch(batch, dts, body_frame=body)
    theta = body_rotation_angle_batch(body, EPOCH + dts)
    bx, by, bz = eci_to_body_fixed_batch(state.px, state.py, state.pz, theta)

    for i, el in enumerate(elements):
        for j, dt in enumerate(dts):
            pos, vel = propagate_eci_j2_mean_elements_for_body(el, float(dt), body_frame=body)
            assert pos.x == state.px[i, j], f"px sat={i} dt={dt}"
            assert pos.y == state.py[i, j], f"py sat={i} dt={dt}"
            assert pos.z == state.pz[i, j], f"pz sat={i} dt={dt}"
            assert vel.x == state.vx[i, j], f"vx sat={i} dt={dt}"
            assert vel.y == state.vy[i, j], f"vy sat={i} dt={dt}"
            assert vel.z == state.vz[i, j], f"vz sat={i} dt={dt}"
            fixed = eci_to_body_fixed(pos, EPOCH + float(dt), body)
            assert fixed.x == bx[i, j], f"bx sat={i} dt={dt}"
            assert fixed.y == by[i, j], f"by sat={i} dt={dt}"
            assert fixed.z == bz[i, j], f"bz sat={i} dt={dt}"


def test_kernel_is_arrangement_invariant():
    """Batch (N,T) == per-column (N,1) == per-element (1,1), bit-exact.
    No reductions anywhere means execution arrangement cannot change a
    single output bit — replay and shard reassignment depend on this."""
    elements = _elements_grid(24, seed=11)
    dts = np.array([5.0, 60.0, 600.0], dtype=np.float64)
    batch = ElementsBatch.from_elements(elements)
    full = propagate_eci_batch(batch, dts, body_frame=EARTH)

    for j in range(len(dts)):
        col = propagate_eci_batch(batch, dts[j : j + 1], body_frame=EARTH)
        assert np.array_equal(col.px[:, 0], full.px[:, j])
        assert np.array_equal(col.vy[:, 0], full.vy[:, j])

    for i in range(0, len(elements), 7):
        single = ElementsBatch.from_elements(elements[i : i + 1])
        cell = propagate_eci_batch(single, dts, body_frame=EARTH)
        assert np.array_equal(cell.pz[0, :], full.pz[i, :])
        assert np.array_equal(cell.vx[0, :], full.vx[i, :])


def test_kernel_validations_mirror_scalar():
    # OrbitalElements rejects e >= 1 at construction; the kernel's own
    # guard is defense-in-depth for raw arrays, so build one directly.
    bad = ElementsBatch(
        semi_major_axis_km=np.array([7000.0]),
        eccentricity=np.array([1.0]),  # p = a(1-e^2) = 0
        inclination_rad=np.array([0.5]),
        raan_rad=np.array([0.0]),
        argument_of_perigee_rad=np.array([0.0]),
        mean_anomaly_rad=np.array([0.0]),
    )
    with pytest.raises(ValueError, match="semi-latus rectum"):
        propagate_eci_batch(bad, np.array([1.0]), body_frame=EARTH)


def test_elements_batch_rejects_ragged_shapes():
    with pytest.raises(ValueError, match="shape"):
        ElementsBatch(
            semi_major_axis_km=np.zeros(3),
            eccentricity=np.zeros(2),
            inclination_rad=np.zeros(3),
            raan_rad=np.zeros(3),
            argument_of_perigee_rad=np.zeros(3),
            mean_anomaly_rad=np.zeros(3),
        )


def test_engine_batch_path_is_bit_identical_to_scalar_wrapper():
    """The propagation engine's kernel batch path against the scalar
    wrapper it replaced — every PropagatedState field, == on floats,
    Earth and Luna in one mixed population, original dict order kept."""
    from nodalarc.propagator import propagate_j2_mean_elements_for_body
    from ome.propagation_engine import propagate_satellites

    class _Sat:
        def __init__(self, name, elements, central_body):
            self.name = name
            self.elements = elements
            self.central_body = central_body
            self.tle_line_1 = None
            self.tle_line_2 = None

    class _Addressing:
        pass

    import ome.propagation_engine as engine_mod

    elements = _elements_grid(30, seed=23)
    sats = [_Sat(f"sat-{i:02d}", el, "earth" if i % 3 else "luna") for i, el in enumerate(elements)]

    # satellite_node_id needs an addressing scheme; bypass with the name.
    original = engine_mod.satellite_node_id
    engine_mod.satellite_node_id = lambda sat, addressing: sat.name
    try:
        from nodalarc.ephemeris_runtime import CommonBodyState
        from nodalarc.frames import Vec3

        def _zero(body_id: str) -> CommonBodyState:
            return CommonBodyState(
                body_id=body_id,
                position_km=Vec3(0.0, 0.0, 0.0),
                velocity_km_s=Vec3(0.0, 0.0, 0.0),
                provider="test",
                kernel_id="test",
                quality_tier="test",
                frame="common",
            )

        states = propagate_satellites(
            satellites=sats,
            addressing=None,
            epoch_unix=EPOCH,
            dt=3600.0,
            propagator_id="j2-mean-elements",
            body_frames={"earth": EARTH, "luna": LUNA},
            body_states={"earth": _zero("earth"), "luna": _zero("luna")},
        )
    finally:
        engine_mod.satellite_node_id = original

    assert list(states) == [s.name for s in sats]  # original order kept
    for sat in sats:
        body = EARTH if sat.central_body == "earth" else LUNA
        pos_f, vel_f, geo, pos_i, vel_i = propagate_j2_mean_elements_for_body(
            sat.elements, EPOCH, 3600.0, body_frame=body
        )
        got = states[sat.name]
        assert got.position_ecef_km.x == pos_f.x and got.position_ecef_km.y == pos_f.y
        assert got.position_ecef_km.z == pos_f.z
        assert got.velocity_ecef_km_s.x == vel_f.x and got.velocity_ecef_km_s.y == vel_f.y
        assert got.velocity_ecef_km_s.z == vel_f.z
        assert got.geodetic.lat_deg == geo.lat_deg and got.geodetic.lon_deg == geo.lon_deg
        assert got.geodetic.alt_km == geo.alt_km
        assert got.position_common_km.x == pos_i.x  # zero body offset
        assert got.velocity_common_km_s.z == vel_i.z
