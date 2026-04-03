# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
"""Orbital element types and constructors.

Pure data types and math for Keplerian circular orbits. No propagation,
no I/O. Used by constellation_loader (to build satellite nodes from config)
and by ome/propagator.py (which adds propagation, ECEF conversion, etc.).
"""

from __future__ import annotations

import math
from typing import NamedTuple

from nodalarc.constants import EARTH_RADIUS_KM


class OrbitalElements(NamedTuple):
    """Keplerian orbital elements for circular orbits."""

    semi_major_axis_km: float  # a = altitude + Earth radius
    inclination_rad: float  # i
    raan_rad: float  # Right Ascension of Ascending Node
    true_anomaly_rad: float  # at epoch


def elements_from_params(
    altitude_km: float,
    inclination_deg: float,
    raan_deg: float,
    true_anomaly_deg: float,
) -> OrbitalElements:
    """Create OrbitalElements from human-readable parameters."""
    return OrbitalElements(
        semi_major_axis_km=EARTH_RADIUS_KM + altitude_km,
        inclination_rad=math.radians(inclination_deg),
        raan_rad=math.radians(raan_deg),
        true_anomaly_rad=math.radians(true_anomaly_deg),
    )
