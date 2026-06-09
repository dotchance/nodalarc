# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Orbital element types and constructors.

Pure data types and math for Keplerian mean elements. No propagation, no I/O.
Used by constellation_loader (to build satellite nodes from config) and by
ome/propagator.py (which adds propagation, ECEF conversion, etc.).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _validate_finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def mean_anomaly_to_eccentric_anomaly(mean_anomaly_rad: float, eccentricity: float) -> float:
    """Solve Kepler's equation M = E - e sin(E)."""
    if eccentricity == 0.0:
        return mean_anomaly_rad
    m = math.fmod(mean_anomaly_rad, 2.0 * math.pi)
    if m < 0.0:
        m += 2.0 * math.pi
    estimate = m if eccentricity < 0.8 else math.pi
    for _ in range(12):
        f = estimate - eccentricity * math.sin(estimate) - m
        fp = 1.0 - eccentricity * math.cos(estimate)
        step = f / fp
        estimate -= step
        if abs(step) < 1e-14:
            break
    return estimate


def eccentric_anomaly_to_true_anomaly(
    eccentric_anomaly_rad: float,
    eccentricity: float,
) -> float:
    """Convert eccentric anomaly to true anomaly for an elliptical orbit."""
    if eccentricity == 0.0:
        return eccentric_anomaly_rad
    return math.atan2(
        math.sqrt(1.0 - eccentricity * eccentricity) * math.sin(eccentric_anomaly_rad),
        math.cos(eccentric_anomaly_rad) - eccentricity,
    )


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

    @property
    def true_anomaly_rad(self) -> float:
        """True anomaly derived from epoch mean anomaly."""
        eccentric_anomaly = mean_anomaly_to_eccentric_anomaly(
            self.mean_anomaly_rad,
            self.eccentricity,
        )
        return eccentric_anomaly_to_true_anomaly(eccentric_anomaly, self.eccentricity)


def elements_from_params(
    altitude_km: float,
    inclination_deg: float,
    raan_deg: float,
    true_anomaly_deg: float,
    *,
    reference_radius_km: float,
) -> OrbitalElements:
    """Create OrbitalElements from human-readable parameters."""
    return OrbitalElements(
        semi_major_axis_km=reference_radius_km + altitude_km,
        inclination_rad=math.radians(inclination_deg),
        raan_rad=math.radians(raan_deg),
        mean_anomaly_rad=math.radians(true_anomaly_deg),
    )


def elements_from_params_for_radius(
    altitude_km: float,
    inclination_deg: float,
    raan_deg: float,
    true_anomaly_deg: float,
    radius_km: float,
) -> OrbitalElements:
    """Create circular elements around a body with the supplied reference radius."""
    return elements_from_params(
        altitude_km,
        inclination_deg,
        raan_deg,
        true_anomaly_deg,
        reference_radius_km=radius_km,
    )
