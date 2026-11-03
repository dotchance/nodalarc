# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Analytic satellite states relative to their central body, in two frames.

OME, VS-API and the Scheduler's diagnostic latency helper all turn published
orbital elements into states with this one function, so a satellite's
position cannot differ between them.

Order of operations: the orbit kernel gives each state in the central body's
orbit-element basis; the basis rotates it onto ICRF axes; the body's measured
orientation turns it into the body-fixed frame, and its time derivative gives
the body-fixed velocity (no rotation-rate constant is involved); ``geodesy``
gives the geodetic coordinates on the catalog ellipsoid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from nodalarc.body_frames import BodyFrame
from nodalarc.frames import GeoPosition
from nodalarc.orbital import OrbitalElements
from nodalarc.physics.context import PhysicsContext
from nodalarc.physics.geodesy import geodetic_from_body_fixed_batch
from nodalarc.physics.kepler import ElementsBatch, OrbitModel, orbit_states


@dataclass(frozen=True)
class SatelliteStates:
    """States of N satellites of one central body at one instant.

    Positions in km and velocities in km/s, each an (N, 3) array, relative to
    the central body's center. ``*_icrf`` are on ICRF axes; ``*_fixed`` are in
    the body-fixed frame.
    """

    position_icrf_km: np.ndarray
    velocity_icrf_km_s: np.ndarray
    position_fixed_km: np.ndarray
    velocity_fixed_km_s: np.ndarray
    geodetic: tuple[GeoPosition, ...]


def orbit_model_for(propagator_id: str) -> OrbitModel:
    """The kernel model behind a propagator identifier."""
    if propagator_id in ("two-body", "keplerian-circular"):
        return "two-body"
    if propagator_id == "j2-mean-elements":
        return "j2-mean-elements"
    raise ValueError(f"propagator {propagator_id!r} has no analytic orbit model")


def analytic_satellite_states(
    physics: PhysicsContext,
    *,
    body_frame: BodyFrame,
    model: OrbitModel,
    elements: Sequence[OrbitalElements],
    phases_s: Sequence[float],
    master_sim_time_unix: float,
) -> SatelliteStates:
    """States of satellites that share a central body and an orbit model.

    ``phases_s`` holds, per satellite, the seconds from its elements' validity
    epoch to ``master_sim_time_unix``.
    """
    if len(elements) != len(phases_s):
        raise ValueError(f"{len(elements)} element sets but {len(phases_s)} phase times")
    batch = ElementsBatch.from_elements(elements)
    phases = np.asarray(phases_s, dtype=np.float64)[:, None]
    states = orbit_states(batch, phases, model=model, body_frame=body_frame)
    position_basis = np.stack([states.px[:, 0], states.py[:, 0], states.pz[:, 0]], axis=1)
    velocity_basis = np.stack([states.vx[:, 0], states.vy[:, 0], states.vz[:, 0]], axis=1)

    basis = physics.orbit_basis(body_frame.name)
    position_icrf = position_basis @ basis
    velocity_icrf = velocity_basis @ basis

    orientation = physics.orientation(body_frame.name, master_sim_time_unix)
    position_fixed = position_icrf @ orientation.rotation.T
    velocity_fixed = (
        velocity_icrf @ orientation.rotation.T + position_icrf @ orientation.rotation_rate.T
    )
    lat, lon, alt = geodetic_from_body_fixed_batch(position_fixed, body_frame)
    geodetic = tuple(
        GeoPosition(lat_deg=float(la), lon_deg=float(lo), alt_km=float(al))
        for la, lo, al in zip(lat, lon, alt, strict=True)
    )
    return SatelliteStates(
        position_icrf_km=position_icrf,
        velocity_icrf_km_s=velocity_icrf,
        position_fixed_km=position_fixed,
        velocity_fixed_km_s=velocity_fixed,
        geodetic=geodetic,
    )
