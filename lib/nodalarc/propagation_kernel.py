# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Vectorized structure-of-arrays orbital kernel (J2 mean elements).

One kernel evaluates (satellites x ticks) batches of the SAME models the
scalar propagator implements — execution changes, models do not. The
construction rules make determinism structural rather than tested-for:

- ELEMENTWISE UFUNCS ONLY. No floating-point reductions anywhere in the
  hot path: every output element is a pure function of its own inputs,
  so results are bit-exact across chunk sizes, N=1 versus batch, and
  any execution arrangement — replay and future shard reassignment
  reproduce identical streams by construction.
- MASKED FIXED-ITERATION KEPLER replicating the scalar solver's exact
  semantics (fmod reduction, e<0.8 seed, post-step 1e-14 break,
  twelve-iteration cap, raw mean anomaly returned for e == 0).
- THE SAME OPERATION ORDER as lib/nodalarc/propagator.py, term by term.
  numpy's sin/cos/sqrt are bit-identical to libm's on this stack, so
  ECI/body-fixed positions and velocities match the scalar path
  bit-for-bit; the equivalence suite enforces this, not prose.

Measured fact, recorded without machinery: numpy's SIMD dispatch
varies by host microarchitecture, so absolute float values differ in
the last bits ACROSS hosts while kernel and scalar paths stay
bit-identical to each other ON any one host. Downstream snapshot
authority is replace-not-merge, so a cross-host replay difference
self-corrects in one tick — no guard is wired, deliberately.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from nodalarc.body_frames import BodyFrame
from nodalarc.orbital import OrbitalElements
from nodalarc.propagator import J2000_UNIX

_TWO_PI = 2.0 * math.pi
_KEPLER_MAX_ITERATIONS = 12
_KEPLER_TOLERANCE = 1e-14


@dataclass(frozen=True)
class ElementsBatch:
    """Structure-of-arrays mean elements for N satellites.

    All arrays are float64 of identical shape (N,). Built once per epoch
    from the resolved session's elements; the kernel never mutates it.
    """

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
class EciStateBatch:
    """Positions and velocities, each a (N, T) float64 array per axis."""

    px: np.ndarray
    py: np.ndarray
    pz: np.ndarray
    vx: np.ndarray
    vy: np.ndarray
    vz: np.ndarray


def j2_mean_element_secular_rates_batch(
    batch: ElementsBatch, *, body_frame: BodyFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(RAAN, argp, mean-anomaly) secular rates, rad/s, shape (N,).

    Mirrors j2_mean_element_secular_rates term by term, including the
    e == 0 zero argp rate (circular orbits preserve the circular J2
    contract) and the positive semi-latus-rectum validation.
    """
    a = batch.semi_major_axis_km
    e = batch.eccentricity
    p = a * (1.0 - e * e)
    if np.any(p <= 0.0):
        raise ValueError("semi-latus rectum must be positive")
    # Multiply-only spelling, matching the scalar path bit for bit —
    # ** disagrees between libm pow and numpy fast paths at the ulp level.
    n = np.sqrt(body_frame.gravitational_parameter_km3_s2 / (a * a * a))
    cos_i = np.cos(batch.inclination_rad)
    radius_ratio = body_frame.equatorial_radius_km / p
    j2_factor = body_frame.j2 * (radius_ratio * radius_ratio)
    raan_dot = -1.5 * j2_factor * n * cos_i
    argp_dot = np.where(e == 0.0, 0.0, 0.75 * j2_factor * n * (5.0 * (cos_i * cos_i) - 1.0))
    mean_anomaly_dot = n * (
        1.0 + 0.75 * j2_factor * np.sqrt(1.0 - e * e) * (3.0 * (cos_i * cos_i) - 1.0)
    )
    return raan_dot, argp_dot, mean_anomaly_dot


def mean_to_eccentric_anomaly_batch(mean_anomaly: np.ndarray, ecc: np.ndarray) -> np.ndarray:
    """Masked Newton solve of Kepler's equation, scalar-exact semantics.

    Element shapes must match (broadcast before calling). Each element
    follows the scalar solver verbatim: e == 0 returns the RAW mean
    anomaly; otherwise iterate from the reduced anomaly (or pi when
    e >= 0.8) up to twelve times, freezing an element after the step
    that lands below 1e-14 — masking reproduces the scalar early break
    without any cross-element coupling.
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


def propagate_eci_batch(
    batch: ElementsBatch,
    dt_s: np.ndarray,
    *,
    body_frame: BodyFrame,
) -> EciStateBatch:
    """Propagate N mean-element orbits across T offsets: arrays (N, T).

    First-order secular J2 on mean elements, then the Keplerian state —
    the scalar propagate_eci_j2_mean_elements_for_body evaluated as a
    batch with identical operation order, positions and velocities both.
    """
    dt = np.asarray(dt_s, dtype=np.float64)
    raan_dot, argp_dot, mean_anomaly_dot = j2_mean_element_secular_rates_batch(
        batch, body_frame=body_frame
    )

    a = batch.semi_major_axis_km[:, None]
    e = batch.eccentricity[:, None]
    raan = batch.raan_rad[:, None] + raan_dot[:, None] * dt[None, :]
    argp = batch.argument_of_perigee_rad[:, None] + argp_dot[:, None] * dt[None, :]
    mean_anomaly = batch.mean_anomaly_rad[:, None] + mean_anomaly_dot[:, None] * dt[None, :]

    ea = mean_to_eccentric_anomaly_batch(mean_anomaly, np.broadcast_to(e, mean_anomaly.shape))
    cos_e = np.cos(ea)
    sin_e = np.sin(ea)
    sqrt_one_minus_e2 = np.sqrt(1.0 - e * e)
    denom = 1.0 - e * cos_e
    if np.any(denom <= 0.0):
        raise ValueError("invalid elliptical state: radius denominator is non-positive")
    x_pf = a * (cos_e - e)
    y_pf = a * sqrt_one_minus_e2 * sin_e
    eccentric_anomaly_dot = mean_anomaly_dot[:, None] / denom
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

    # Velocity: base rotation of perifocal rates plus the rotation's own
    # secular drift — d_raan and d_argp matrices, exactly as the scalar.
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

    raan_dot_col = raan_dot[:, None]
    argp_dot_col = argp_dot[:, None]
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

    return EciStateBatch(px=px, py=py, pz=pz, vx=vx, vy=vy, vz=vz)


def gmst_batch(unix_timestamps: np.ndarray) -> np.ndarray:
    """Greenwich Mean Sidereal Time in radians, shape-preserving."""
    ts = np.asarray(unix_timestamps, dtype=np.float64)
    jd = 2440587.5 + ts / 86400.0
    t = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * (t * t)
        - (t * t * t) / 38710000.0
    )
    return np.radians(gmst_deg % 360.0)


def body_rotation_angle_batch(body_frame: BodyFrame, unix_timestamps: np.ndarray) -> np.ndarray:
    """Rotation angle of the body-fixed frame: GMST for Earth, uniform
    rotation from the J2000 epoch for every other body — the scalar
    _body_rotation_angle, shape-preserving."""
    ts = np.asarray(unix_timestamps, dtype=np.float64)
    if body_frame.name == "earth":
        return gmst_batch(ts)
    return (ts - J2000_UNIX) * body_frame.rotation_rate_rad_s


def eci_to_body_fixed_batch(
    px: np.ndarray,
    py: np.ndarray,
    pz: np.ndarray,
    theta: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rotate inertial positions into the body-fixed frame.

    theta broadcasts against the position arrays — pass shape (T,) for
    (N, T) positions. pz passes through untouched (the rotation is about
    the polar axis), so callers receive it at its input shape.
    """
    cos_t = np.cos(theta)
    sin_t = np.sin(theta)
    return (
        cos_t * px + sin_t * py,
        -sin_t * px + cos_t * py,
        pz,
    )


def eci_to_body_fixed_velocity_batch(
    vx_eci: np.ndarray,
    vy_eci: np.ndarray,
    vz_eci: np.ndarray,
    bx_fixed: np.ndarray,
    by_fixed: np.ndarray,
    theta: np.ndarray,
    *,
    rotation_rate_rad_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inertial velocity into the rotating body frame, scalar-exact.

    Mirrors eci_to_body_fixed_velocity term by term: rotate the inertial
    velocity by theta, then remove the frame's own rotation (the omega
    cross r term) using the already-computed body-fixed position.
    """
    cos_t = np.cos(theta)
    sin_t = np.sin(theta)
    vx = cos_t * vx_eci + sin_t * vy_eci
    vy = -sin_t * vx_eci + cos_t * vy_eci
    vz = vz_eci
    omega = rotation_rate_rad_s
    vx = vx - (-omega * by_fixed)
    vy = vy - (omega * bx_fixed)
    return vx, vy, vz
