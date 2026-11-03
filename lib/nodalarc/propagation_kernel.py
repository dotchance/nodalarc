# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Vectorized body rotation helpers that OME still uses.

The orbit kernel moved to ``nodalarc.physics.kepler``. These rotation helpers
(GMST for Earth, a uniform spin for other bodies) are replaced by measured body
orientation from the shared physics module.
"""

from __future__ import annotations

import numpy as np

from nodalarc.body_frames import BodyFrame
from nodalarc.propagator import J2000_UNIX


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
