# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Orbit states for the two analytic orbit models: one vectorized Kepler kernel.

Explicit exception to the rule that physics comes from libraries. The Kepler
step (orbital elements to position and velocity) is NodalArc's NumPy kernel,
not a library call, because measured performance rules the library out:

- SPICE ``conics`` through SpiceyPy took 5.27 ms per tick for 1,000
  satellites and 51.8 ms for 10,000; this kernel took 0.19 ms and 1.10 ms.
  OME computes every step, so at 1 s steps the SPICE path alone would cap
  playback near 190 times speed at 1,000 satellites and 19 times at 10,000,
  and a 600-tick Builder preview of 10,000 satellites would take about 31 s
  instead of 3.2 s. SPICE is also not thread safe, so every call in a
  process would wait on one lock.
- ANISE's two-body propagation (``Orbit.at_epoch`` in 0.10.6) was
  11,480 km from SPICE one second after a lunar orbit started at periapsis
  and refuses exactly circular orbits (upstream nyx-space/anise issue #821).

SPICE remains the qualification reference: the tests compare this kernel's
two-body states with SPICE ``prop2b`` and its J2 states with SPICE ``conics``
under the same declared rates, over 10 days, for circular, equatorial,
apsis-start, highly eccentric and retrograde orbits, and check each velocity
against a numerical derivative of the positions.

Both models run through the same kernel. ``two-body`` explicitly omits the J2
rates: its node and perigee rates are zero and its mean-anomaly rate is the
two-body mean motion. ``j2-mean-elements`` applies NodalArc's declared
first-order secular J2 rates.

Construction rules, which make the output deterministic: elementwise
operations only, with no floating-point reductions, so every output element
is a function of its own inputs and results are identical across batch sizes;
a masked fixed-iteration Kepler solve (at most twelve Newton steps, freezing
an element once its step falls below 1e-14); powers spelled as
multiplication, because libm ``pow`` and NumPy's integer-power paths differ
in the last bit.

States are relative to the central body's center, on the axes of the body's
orbit-element basis (see ``PhysicsContext.orbit_basis``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from nodalarc.body_frames import BodyFrame
from nodalarc.orbital import OrbitalElements

type OrbitModel = Literal["two-body", "j2-mean-elements"]

_TWO_PI = 2.0 * math.pi
_KEPLER_MAX_ITERATIONS = 12
_KEPLER_TOLERANCE = 1e-14


@dataclass(frozen=True)
class ElementsBatch:
    """Structure-of-arrays elements for N satellites, each array shape (N,)."""

    semi_major_axis_km: np.ndarray
    eccentricity: np.ndarray
    inclination_rad: np.ndarray
    raan_rad: np.ndarray
    argument_of_perigee_rad: np.ndarray
    mean_anomaly_rad: np.ndarray

    def __post_init__(self) -> None:
        shape = self.semi_major_axis_km.shape
        for name in (
            "eccentricity",
            "inclination_rad",
            "raan_rad",
            "argument_of_perigee_rad",
            "mean_anomaly_rad",
        ):
            arr = getattr(self, name)
            if arr.shape != shape:
                raise ValueError(f"ElementsBatch field {name} shape {arr.shape} != {shape}")

    def __len__(self) -> int:
        return int(self.semi_major_axis_km.shape[0])

    @classmethod
    def from_elements(cls, elements: Sequence[OrbitalElements]) -> ElementsBatch:
        return cls(
            semi_major_axis_km=np.array([e.semi_major_axis_km for e in elements], dtype=np.float64),
            eccentricity=np.array([e.eccentricity for e in elements], dtype=np.float64),
            inclination_rad=np.array([e.inclination_rad for e in elements], dtype=np.float64),
            raan_rad=np.array([e.raan_rad for e in elements], dtype=np.float64),
            argument_of_perigee_rad=np.array(
                [e.argument_of_perigee_rad for e in elements], dtype=np.float64
            ),
            mean_anomaly_rad=np.array([e.mean_anomaly_rad for e in elements], dtype=np.float64),
        )


@dataclass(frozen=True)
class SecularRates:
    """Node, argument-of-perigee and mean-anomaly rates, rad/s, shape (N,)."""

    raan_dot: np.ndarray
    argument_of_perigee_dot: np.ndarray
    mean_anomaly_dot: np.ndarray


@dataclass(frozen=True)
class OrbitStateBatch:
    """Positions (km) and velocities (km/s), each axis an (N, T) array."""

    px: np.ndarray
    py: np.ndarray
    pz: np.ndarray
    vx: np.ndarray
    vy: np.ndarray
    vz: np.ndarray


def _mean_motion(batch: ElementsBatch, mu_km3_s2: float) -> np.ndarray:
    a = batch.semi_major_axis_km
    return np.sqrt(mu_km3_s2 / (a * a * a))


def two_body_rates(batch: ElementsBatch, *, body_frame: BodyFrame) -> SecularRates:
    """Two-body motion: no node or perigee motion, mean motion from GM alone."""
    zero: np.ndarray = np.zeros(len(batch), dtype=np.float64)
    return SecularRates(
        raan_dot=zero,
        argument_of_perigee_dot=zero,
        mean_anomaly_dot=_mean_motion(batch, body_frame.gravitational_parameter_km3_s2),
    )


def j2_secular_rates(batch: ElementsBatch, *, body_frame: BodyFrame) -> SecularRates:
    """NodalArc's declared first-order secular J2 rates.

    No qualified library with a matching model was found (searched: ANISE,
    Skyfield, SPICE, Orekit, Nyx), and a different model would move the
    satellites of every existing session that uses this one. For circular
    orbits the argument of perigee is undefined and its rate is zero, which
    keeps the circular J2 contract existing sessions rely on.
    """
    a = batch.semi_major_axis_km
    e = batch.eccentricity
    p = a * (1.0 - e * e)
    if np.any(p <= 0.0):
        raise ValueError("semi-latus rectum must be positive")
    n = _mean_motion(batch, body_frame.gravitational_parameter_km3_s2)
    cos_i = np.cos(batch.inclination_rad)
    radius_ratio = body_frame.equatorial_radius_km / p
    j2_factor = body_frame.j2 * (radius_ratio * radius_ratio)
    return SecularRates(
        raan_dot=-1.5 * j2_factor * n * cos_i,
        argument_of_perigee_dot=np.where(
            e == 0.0, 0.0, 0.75 * j2_factor * n * (5.0 * (cos_i * cos_i) - 1.0)
        ),
        mean_anomaly_dot=n
        * (1.0 + 0.75 * j2_factor * np.sqrt(1.0 - e * e) * (3.0 * (cos_i * cos_i) - 1.0)),
    )


def secular_rates(
    batch: ElementsBatch, *, model: OrbitModel, body_frame: BodyFrame
) -> SecularRates:
    if model == "two-body":
        return two_body_rates(batch, body_frame=body_frame)
    if model == "j2-mean-elements":
        return j2_secular_rates(batch, body_frame=body_frame)
    raise ValueError(f"unsupported orbit model {model!r}")


def mean_to_eccentric_anomaly_batch(mean_anomaly: np.ndarray, ecc: np.ndarray) -> np.ndarray:
    """Masked Newton solve of Kepler's equation, elementwise.

    Shapes must match (broadcast before calling). For e == 0 the mean anomaly
    is returned unchanged; otherwise the solve starts from the mean anomaly
    reduced to [0, 2 pi) (or pi when e >= 0.8) and takes at most twelve
    steps, freezing an element after the step that falls below 1e-14.
    """
    m_reduced = np.fmod(mean_anomaly, _TWO_PI)
    m_reduced = np.where(m_reduced < 0.0, m_reduced + _TWO_PI, m_reduced)
    estimate = np.where(ecc < 0.8, m_reduced, math.pi)
    active = ecc != 0.0
    estimate = np.where(active, estimate, mean_anomaly)
    live = active.copy()
    for _ in range(_KEPLER_MAX_ITERATIONS):
        if not live.any():
            break
        f = estimate - ecc * np.sin(estimate) - m_reduced
        fp = 1.0 - ecc * np.cos(estimate)
        step = f / fp
        estimate = np.where(live, estimate - step, estimate)
        live = live & (np.abs(step) >= _KEPLER_TOLERANCE)
    return np.where(active, estimate, mean_anomaly)


def orbit_states(
    batch: ElementsBatch,
    dt_s: np.ndarray,
    *,
    model: OrbitModel,
    body_frame: BodyFrame,
) -> OrbitStateBatch:
    """States of N element sets at T offsets: arrays of shape (N, T).

    ``dt_s`` is shape (T,), shared by every element set, or shape (N, T),
    one row per element set.

    The rates move the node, the argument of perigee and the mean anomaly;
    the moved elements give the Keplerian position. The velocity is the time
    derivative of that trajectory: the perifocal velocity at the model's
    mean-anomaly rate, plus the rotation of the orbit by the node and perigee
    rates. A two-body velocity from the moved elements alone would omit that
    rotation: 3.548 m/s for a circular 550 km orbit at 53 degrees after one
    hour.
    """
    rates = secular_rates(batch, model=model, body_frame=body_frame)
    dt = np.asarray(dt_s, dtype=np.float64)
    dt_grid = dt[None, :] if dt.ndim == 1 else dt

    a = batch.semi_major_axis_km[:, None]
    e = batch.eccentricity[:, None]
    raan = batch.raan_rad[:, None] + rates.raan_dot[:, None] * dt_grid
    argp = batch.argument_of_perigee_rad[:, None] + rates.argument_of_perigee_dot[:, None] * dt_grid
    mean_anomaly = batch.mean_anomaly_rad[:, None] + rates.mean_anomaly_dot[:, None] * dt_grid

    ea = mean_to_eccentric_anomaly_batch(mean_anomaly, np.broadcast_to(e, mean_anomaly.shape))
    cos_e = np.cos(ea)
    sin_e = np.sin(ea)
    sqrt_one_minus_e2 = np.sqrt(1.0 - e * e)
    denom = 1.0 - e * cos_e
    if np.any(denom <= 0.0):
        raise ValueError("invalid elliptical state: radius denominator is non-positive")
    x_pf = a * (cos_e - e)
    y_pf = a * sqrt_one_minus_e2 * sin_e
    eccentric_anomaly_dot = rates.mean_anomaly_dot[:, None] / denom
    vx_pf = -a * sin_e * eccentric_anomaly_dot
    vy_pf = a * sqrt_one_minus_e2 * cos_e * eccentric_anomaly_dot

    cos_raan = np.cos(raan)
    sin_raan = np.sin(raan)
    cos_i = np.cos(batch.inclination_rad)[:, None]
    sin_i = np.sin(batch.inclination_rad)[:, None]
    cos_argp = np.cos(argp)
    sin_argp = np.sin(argp)

    r00 = cos_raan * cos_argp - sin_raan * sin_argp * cos_i
    r01 = -cos_raan * sin_argp - sin_raan * cos_argp * cos_i
    r10 = sin_raan * cos_argp + cos_raan * sin_argp * cos_i
    r11 = -sin_raan * sin_argp + cos_raan * cos_argp * cos_i
    r20 = sin_argp * sin_i
    r21 = cos_argp * sin_i

    px = r00 * x_pf + r01 * y_pf
    py = r10 * x_pf + r11 * y_pf
    pz = r20 * x_pf + r21 * y_pf

    d_raan_00 = -sin_raan * cos_argp - cos_raan * sin_argp * cos_i
    d_raan_01 = sin_raan * sin_argp - cos_raan * cos_argp * cos_i
    d_raan_10 = cos_raan * cos_argp - sin_raan * sin_argp * cos_i
    d_raan_11 = -cos_raan * sin_argp - sin_raan * cos_argp * cos_i

    d_argp_00 = -cos_raan * sin_argp - sin_raan * cos_argp * cos_i
    d_argp_01 = -cos_raan * cos_argp + sin_raan * sin_argp * cos_i
    d_argp_10 = -sin_raan * sin_argp + cos_raan * cos_argp * cos_i
    d_argp_11 = -sin_raan * cos_argp - cos_raan * sin_argp * cos_i
    d_argp_20 = cos_argp * sin_i
    d_argp_21 = -sin_argp * sin_i

    raan_dot_col = rates.raan_dot[:, None]
    argp_dot_col = rates.argument_of_perigee_dot[:, None]
    vx = (
        (r00 * vx_pf + r01 * vy_pf)
        + raan_dot_col * (d_raan_00 * x_pf + d_raan_01 * y_pf)
        + argp_dot_col * (d_argp_00 * x_pf + d_argp_01 * y_pf)
    )
    vy = (
        (r10 * vx_pf + r11 * vy_pf)
        + raan_dot_col * (d_raan_10 * x_pf + d_raan_11 * y_pf)
        + argp_dot_col * (d_argp_10 * x_pf + d_argp_11 * y_pf)
    )
    vz = (
        (r20 * vx_pf + r21 * vy_pf)
        + raan_dot_col * 0.0
        + argp_dot_col * (d_argp_20 * x_pf + d_argp_21 * y_pf)
    )
    return OrbitStateBatch(px=px, py=py, pz=pz, vx=vx, vy=vy, vz=vz)


def orbital_period_s(elements: OrbitalElements, body_frame: BodyFrame) -> float:
    """The two-body period of an element set around its central body."""
    a = elements.semi_major_axis_km
    return math.tau * math.sqrt((a * a * a) / body_frame.gravitational_parameter_km3_s2)


def advance_mean_elements(
    elements: OrbitalElements,
    dt_s: float,
    *,
    body_frame: BodyFrame,
    model: OrbitModel,
) -> OrbitalElements:
    """The element set moved forward by ``dt_s`` seconds under its own model.

    Propagation takes elements valid at the pacing epoch. This moves an
    authored element set, valid at its orbit's declared epoch, to a new
    anchor with the same rates ``orbit_states`` uses, so advancing by ``a``
    and propagating ``b`` equals propagating ``a + b``. ``dt_s == 0.0``
    returns ``elements`` unchanged.
    """
    if dt_s == 0.0:
        return elements
    rates = secular_rates(
        ElementsBatch.from_elements([elements]), model=model, body_frame=body_frame
    )
    return OrbitalElements(
        semi_major_axis_km=elements.semi_major_axis_km,
        inclination_rad=elements.inclination_rad,
        raan_rad=(elements.raan_rad + float(rates.raan_dot[0]) * dt_s) % math.tau,
        eccentricity=elements.eccentricity,
        argument_of_perigee_rad=(
            elements.argument_of_perigee_rad + float(rates.argument_of_perigee_dot[0]) * dt_s
        )
        % math.tau,
        mean_anomaly_rad=(elements.mean_anomaly_rad + float(rates.mean_anomaly_dot[0]) * dt_s)
        % math.tau,
    )
