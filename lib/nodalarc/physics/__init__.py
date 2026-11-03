# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The shared physics module: the one place NodalArc computes physical facts.

Body positions and orientation come from ANISE reading NAIF data; the Kepler
step of the analytic orbit models comes from SPICE ``conics``; the J2 secular
rates are NodalArc's declared model. Every consumer (OME, the resolver,
VS-API and the Builder) goes through this module, and no consumer keeps a
private computation of the same fact.
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
    SPICE_LOCK,
    OrbitStates,
    advance_mean_elements,
    j2_mean_element_secular_rates,
    j2_mean_states,
    two_body_states,
)

__all__ = [
    "SPICE_LOCK",
    "BodyOrientation",
    "BodyState",
    "EphemerisLocation",
    "OrbitStates",
    "PhysicsContext",
    "PhysicsDataError",
    "SelectedFile",
    "advance_mean_elements",
    "body_fixed_from_geodetic",
    "epoch_of",
    "geodetic_from_body_fixed",
    "j2_mean_element_secular_rates",
    "j2_mean_states",
    "production_location",
    "two_body_states",
]
