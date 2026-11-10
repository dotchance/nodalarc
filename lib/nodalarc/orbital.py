# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Orbital element types and constructors.

Pure data types and math for Keplerian mean elements. No propagation, no I/O.
Resolved session facts are materialized into these elements; the shared physics
module adds time evolution and frame conversion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _validate_finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def true_anomaly_to_mean_anomaly(true_anomaly_rad: float, eccentricity: float) -> float:
    """Convert true anomaly to mean anomaly for an elliptical orbit."""
    if eccentricity == 0.0:
        return true_anomaly_rad
    sin_nu = math.sin(true_anomaly_rad)
    cos_nu = math.cos(true_anomaly_rad)
    eccentric_anomaly = math.atan2(
        math.sqrt(1.0 - eccentricity * eccentricity) * sin_nu,
        eccentricity + cos_nu,
    )
    return eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly)


@dataclass(frozen=True, init=False)
class OrbitalElements:
    """Keplerian mean elements for circular and elliptical orbits."""

    semi_major_axis_km: float  # a
    inclination_rad: float  # i
    raan_rad: float  # Right Ascension of Ascending Node
    mean_anomaly_rad: float  # M at epoch
    eccentricity: float = 0.0  # e, elliptical only (0 <= e < 1)
    argument_of_perigee_rad: float = 0.0  # omega

    def __init__(
        self,
        semi_major_axis_km: float,
        inclination_rad: float,
        raan_rad: float,
        true_anomaly_rad: float | None = None,
        *,
        eccentricity: float = 0.0,
        argument_of_perigee_rad: float = 0.0,
        mean_anomaly_rad: float | None = None,
    ) -> None:
        a = _validate_finite("semi_major_axis_km", semi_major_axis_km)
        if a <= 0.0:
            raise ValueError("semi_major_axis_km must be positive")
        e = _validate_finite("eccentricity", eccentricity)
        if e < 0.0 or e >= 1.0:
            raise ValueError("eccentricity must satisfy 0 <= e < 1")
        i = _validate_finite("inclination_rad", inclination_rad)
        raan = _validate_finite("raan_rad", raan_rad)
        argp = _validate_finite("argument_of_perigee_rad", argument_of_perigee_rad)
        if mean_anomaly_rad is None:
            if true_anomaly_rad is None:
                raise ValueError("mean_anomaly_rad or true_anomaly_rad is required")
            true_anomaly = _validate_finite("true_anomaly_rad", true_anomaly_rad)
            mean_anomaly = true_anomaly_to_mean_anomaly(true_anomaly, e)
        else:
            mean_anomaly = _validate_finite("mean_anomaly_rad", mean_anomaly_rad)

        object.__setattr__(self, "semi_major_axis_km", a)
        object.__setattr__(self, "inclination_rad", i)
        object.__setattr__(self, "raan_rad", raan)
        object.__setattr__(self, "mean_anomaly_rad", mean_anomaly)
        object.__setattr__(self, "eccentricity", e)
        object.__setattr__(self, "argument_of_perigee_rad", argp)
