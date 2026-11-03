# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Obstruction of link paths by every session body and by the solar disk.

A signal's path runs from the transmitter at emission to the receiver at arrival. Each body is
tested where the signal passes it: the body's state and orientation are taken at the epoch the
signal reaches its closest approach to the body's center, and the body moves linearly about that
epoch. Its deviation from that line is half its acceleration times the square of the light time
between that epoch and an end: 7 mm for the Moon at an end 1.3 s away (8.6e-6 km/s^2), and less
where the path passes the body. The path and the body's ellipsoid (catalog equatorial and polar
radii) are expressed in the body-fixed frame and scaled to the unit sphere; the path is blocked
when its closest point lies inside. The Sun is a sphere of the IAU nominal solar radius.

An end whose central body is the tested body keeps its body-relative position. Differencing
barycentric positions instead rounds it by up to 3 cm (float64 resolution of kilometres at 1 AU),
which would put a site at zero altitude inside its own body. As in OME's line-of-sight test, a
point on the surface is outside: the surface tolerance is 1e-12 of the radius (6 um for Earth).

Every path and body is tested in one vectorized pass per body.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from nodalarc.body_frames import BodyFrame
from nodalarc.physics.context import PhysicsContext
from nodalarc.physics.ephemeris_table import SUN_ID, SUN_RADIUS_KM

SURFACE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class PathEnds:
    """One end of every path: central body ids (N,), position relative to that body and
    barycentric position (N, 3) on ICRF axes in km, and TDB epochs (N,)."""

    body_ids: np.ndarray
    relative_km: np.ndarray
    position_km: np.ndarray
    tdb_seconds: np.ndarray


def _closest_fraction(start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """Fraction along each segment of its closest point to the origin, in [0, 1]."""
    step = end - start
    length_sq = np.einsum("ni,ni->n", step, step)
    along = -np.einsum("ni,ni->n", start, step)
    fraction = np.divide(along, length_sq, out=np.zeros_like(along), where=length_sq > 0.0)
    return np.clip(fraction, 0.0, 1.0)


def _relative_to(
    body_id: str,
    ends: PathEnds,
    centre_km: np.ndarray,
    velocity_km_s: np.ndarray,
    crossing_tdb: np.ndarray,
) -> np.ndarray:
    relative = (
        ends.position_km - centre_km - velocity_km_s * (ends.tdb_seconds - crossing_tdb)[:, None]
    )
    own = ends.body_ids == body_id
    relative[own] = ends.relative_km[own]
    return relative


def obstructions(
    physics: PhysicsContext,
    shapes: Mapping[str, BodyFrame],
    transmitters: PathEnds,
    receivers: PathEnds,
) -> tuple[tuple[str, ...], np.ndarray]:
    """The bodies tested (every session body, then the Sun) and, per path and body, whether the
    body blocks the path: a (N, B) boolean array."""
    occulters = (*physics.body_ids, SUN_ID)
    missing = [body for body in physics.body_ids if body not in shapes]
    if missing:
        raise ValueError(f"no shape for session bodies {missing}")
    count = len(transmitters.tdb_seconds)
    blocked: np.ndarray = np.zeros((count, len(occulters)), dtype=bool)
    if count == 0:
        return occulters, blocked
    motion = physics.motion
    for column, body_id in enumerate(occulters):
        at_emission, _ = motion.states(body_id, transmitters.tdb_seconds)
        at_arrival, _ = motion.states(body_id, receivers.tdb_seconds)
        fraction = _closest_fraction(
            transmitters.position_km - at_emission, receivers.position_km - at_arrival
        )
        crossing = transmitters.tdb_seconds + fraction * (
            receivers.tdb_seconds - transmitters.tdb_seconds
        )
        centre, velocity = motion.states(body_id, crossing)
        start = _relative_to(body_id, transmitters, centre, velocity, crossing)
        end = _relative_to(body_id, receivers, centre, velocity, crossing)
        if body_id == SUN_ID:
            scale = np.full(3, 1.0 / SUN_RADIUS_KM)
        else:
            rotations, _ = motion.orientations(body_id, crossing)
            start = np.einsum("nij,nj->ni", rotations, start)
            end = np.einsum("nij,nj->ni", rotations, end)
            shape = shapes[body_id]
            scale = 1.0 / np.array(
                [shape.equatorial_radius_km, shape.equatorial_radius_km, shape.polar_radius_km]
            )
        start, end = start * scale, end * scale
        nearest = start + _closest_fraction(start, end)[:, None] * (end - start)
        blocked[:, column] = np.einsum("ni,ni->n", nearest, nearest) < 1.0 - SURFACE_TOLERANCE
    return occulters, blocked
