# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Body motion at many epochs at once: barycentric positions and orientations.

Light time evaluates every receiver of a tick at its own epoch, about a thousand epochs per tick
for the flagship session, three times over while the iteration converges. One ANISE lookup per
epoch measured 22.3 ms per tick for that session (1,174 link directions), four times the whole
step. ANISE has no batch call, so this table reads each body from ANISE once per grid point
(600 s apart in TDB) and interpolates every epoch in one vectorized pass:

- Positions: cubic Hermite from position and velocity at the two grid points around the epoch.
  Measured against SPICE over two days: 3.6 mm worst for Earth and the Moon relative to the solar
  system barycenter. Direct ANISE lookups differ from SPICE by 3 mm at 60 s and 600 s spacing
  alike, so this is the data's evaluation noise at that distance, not interpolation error; 3.6 mm
  is 12 ps of light time.
- Orientations: rotation at a constant rate about one axis between the two grid points. Measured
  against SPICE: 1.6e-11 rad worst for Earth and 7.4e-11 rad for the Moon (0.1 mm at either
  surface).

The flagship session's light time for one tick measured 4.0 ms through this table.

Grid points are read when first needed and kept, up to MAX_CACHED_POINTS (oldest dropped first).
A grid point outside the data's coverage raises, so an epoch within one spacing of the coverage
edge needs the next point covered as well.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from nodalarc.physics.context import PhysicsContext

GRID_SPACING_S = 600.0
# Grid points kept per table: a tick's epochs span a few cells, so the oldest are dropped first.
MAX_CACHED_POINTS = 512


def _keep[K, V](cache: dict[K, V], key: K, value: V) -> None:
    if len(cache) >= MAX_CACHED_POINTS:
        del cache[next(iter(cache))]
    cache[key] = value


class BodyMotionTable:
    """Interpolated barycentric positions and orientations for one physics context."""

    def __init__(self, physics: PhysicsContext) -> None:
        self._physics = physics
        self._states: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
        self._rotations: dict[tuple[str, int], np.ndarray] = {}

    def _state(self, body_id: str, k: int) -> tuple[np.ndarray, np.ndarray]:
        key = (body_id, k)
        state = self._states.get(key)
        if state is None:
            state = self._physics.barycentric_state(body_id, k * GRID_SPACING_S)
            _keep(self._states, key, state)
        return state

    def _rotation(self, body_id: str, k: int) -> np.ndarray:
        key = (body_id, k)
        rotation = self._rotations.get(key)
        if rotation is None:
            rotation = self._physics.rotation_at_tdb(body_id, k * GRID_SPACING_S)
            _keep(self._rotations, key, rotation)
        return rotation

    def positions(self, body_id: str, tdb_seconds: np.ndarray) -> np.ndarray:
        """Barycentric positions (N, 3) at TDB epochs (N,)."""
        h = GRID_SPACING_S
        cells = np.floor(tdb_seconds / h).astype(np.int64)
        out = np.empty((len(tdb_seconds), 3))
        for k in np.unique(cells):
            rows = cells == k
            p0, v0 = self._state(body_id, int(k))
            p1, v1 = self._state(body_id, int(k) + 1)
            s = (tdb_seconds[rows] - k * h) / h
            s2, s3 = s * s, s * s * s
            out[rows] = (
                (2 * s3 - 3 * s2 + 1)[:, None] * p0
                + (s3 - 2 * s2 + s)[:, None] * (h * v0)
                + (-2 * s3 + 3 * s2)[:, None] * p1
                + (s3 - s2)[:, None] * (h * v1)
            )
        return out

    def rotations(self, body_id: str, tdb_seconds: np.ndarray) -> np.ndarray:
        """ICRF-to-body-fixed rotations (N, 3, 3) at TDB epochs (N,)."""
        h = GRID_SPACING_S
        cells = np.floor(tdb_seconds / h).astype(np.int64)
        out = np.empty((len(tdb_seconds), 3, 3))
        for k in np.unique(cells):
            rows = cells == k
            r0 = self._rotation(body_id, int(k))
            r1 = self._rotation(body_id, int(k) + 1)
            step = r1 @ r0.T
            axis = (
                np.array(
                    [step[2, 1] - step[1, 2], step[0, 2] - step[2, 0], step[1, 0] - step[0, 1]]
                )
                / 2
            )
            sine = float(np.linalg.norm(axis))
            angle = math.atan2(sine, (np.trace(step) - 1) / 2)
            if sine == 0.0:
                out[rows] = r0
                continue
            unit = axis / sine
            kx = np.array([[0, -unit[2], unit[1]], [unit[2], 0, -unit[0]], [-unit[1], unit[0], 0]])
            theta = (tdb_seconds[rows] - k * h) / h * angle
            turns = (
                np.eye(3)[None]
                + np.sin(theta)[:, None, None] * kx[None]
                + (1 - np.cos(theta))[:, None, None] * (kx @ kx)[None]
            )
            out[rows] = turns @ r0
        return out
