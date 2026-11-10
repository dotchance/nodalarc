# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""The shared physics module: the one place NodalArc computes physical facts.

Body positions, body orientation, orbit-element bases and geodetic conversion
come from ANISE reading NAIF data. Orbit states come from one vectorized
Kepler kernel, an explicit exception justified by measured performance (see
kepler.py), qualified against SPICE in the tests. OME, the resolver, VS-API
and the Builder use this module, and none keeps a private computation of the
same fact.
"""

from nodalarc.physics.context import (
    BodyOrientation,
    BodyState,
    PhysicsContext,
    SelectedFile,
    epoch_of,
)
from nodalarc.physics.datasets import EphemerisLocation, production_location
from nodalarc.physics.errors import PhysicsDataError
from nodalarc.physics.geodesy import body_fixed_from_geodetic, geodetic_from_body_fixed
from nodalarc.physics.kepler import (
    ElementsBatch,
    OrbitModel,
    OrbitStateBatch,
    SecularRates,
    advance_mean_elements,
    orbit_states,
    secular_rates,
)

__all__ = [
    "BodyOrientation",
    "BodyState",
    "ElementsBatch",
    "EphemerisLocation",
    "OrbitModel",
    "OrbitStateBatch",
    "PhysicsContext",
    "PhysicsDataError",
    "SecularRates",
    "SelectedFile",
    "advance_mean_elements",
    "body_fixed_from_geodetic",
    "epoch_of",
    "geodetic_from_body_fixed",
    "orbit_states",
    "production_location",
    "secular_rates",
]
