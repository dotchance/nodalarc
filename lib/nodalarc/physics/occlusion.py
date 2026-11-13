# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
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

A body is its reference ellipsoid, and its terrain is not modelled. A site's altitude is its height
above that ellipsoid, and a real site can be below it: Gale crater's floor is 4.9 km below the
Mars ellipsoid, and a sea-level site where the geoid is low is below Earth's. Against the
reference ellipsoid such a site is inside its own body and every path from it is blocked. So the
occluder for a path is the smaller of the body's reference ellipsoid and the similar ellipsoid
through any end of the path that lies inside it: a site below the reference surface sees its own
body as a surface at its own level, and every other path is tested exactly as before. One rule
holds for every body; no body, site or end kind is named.

This is an approximation, not terrain. A real site's horizon is its terrain: a crater floor sees
the sky only above the rim, which stands about 2 km above Gale's floor. The only horizon NodalArc
models is the site's elevation mask, so a site below its reference surface sees down to its own
level and its mask must stand for the terrain around it. That is more permissive than the real
sky wherever the terrain rises above the mask. Rejected alternative: keep the reference ellipsoid,
which blinds every site below it. The fix that removes this approximation is a horizon per site:
an azimuth-dependent elevation profile from the body's terrain model (MOLA, LOLA, an Earth DEM).

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
        radius = SUN_RADIUS_KM if body_id == SUN_ID else shapes[body_id].equatorial_radius_km
        # Coarse pass with the body held at the emission epoch: the path cannot touch the body
        # unless its closest approach comes within the body's radius plus the distance the body
        # moves over the light time (plus 1 km for its acceleration and rounding, far above
        # either). Only paths inside that reach are evaluated at their crossing epochs.
        at_emission, body_velocity = motion.states(body_id, transmitters.tdb_seconds)
        start = transmitters.position_km - at_emission
        end = receivers.position_km - at_emission
        nearest = start + _closest_fraction(start, end)[:, None] * (end - start)
        reach = (
            radius
            + np.linalg.norm(body_velocity, axis=1)
            * (receivers.tdb_seconds - transmitters.tdb_seconds)
            + 1.0
        )
        rows = np.nonzero(np.einsum("ni,ni->n", nearest, nearest) < reach * reach)[0]
        if len(rows) == 0:
            continue
        tx = PathEnds(
            transmitters.body_ids[rows],
            transmitters.relative_km[rows],
            transmitters.position_km[rows],
            transmitters.tdb_seconds[rows],
        )
        rx = PathEnds(
            receivers.body_ids[rows],
            receivers.relative_km[rows],
            receivers.position_km[rows],
            receivers.tdb_seconds[rows],
        )
        at_arrival, _ = motion.states(body_id, rx.tdb_seconds)
        fraction = _closest_fraction(
            tx.position_km - at_emission[rows], rx.position_km - at_arrival
        )
        crossing = tx.tdb_seconds + fraction * (rx.tdb_seconds - tx.tdb_seconds)
        centre, velocity = motion.states(body_id, crossing)
        start = _relative_to(body_id, tx, centre, velocity, crossing)
        end = _relative_to(body_id, rx, centre, velocity, crossing)
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
        # The occluder's level in the unit-sphere frame: 1 for the reference ellipsoid, or the
        # squared scaled radius of an end below it (a site under its reference surface; see the
        # module docstring). A satellite is never inside its body and never lowers it.
        level = np.minimum(
            1.0, np.minimum(np.einsum("ni,ni->n", start, start), np.einsum("ni,ni->n", end, end))
        )
        blocked[rows, column] = np.einsum("ni,ni->n", nearest, nearest) < level - SURFACE_TOLERANCE
    return occulters, blocked
