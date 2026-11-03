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

The flagship session's signals for one tick (light time and both ends' pointing, 1,174
directions) measured 5.0 ms through this table.

Grid points are read when first needed and kept, up to MAX_CACHED_POINTS (oldest dropped first).
Reading a missing point holds a lock, so threads that share a context (VS-API request threads)
never evict and insert at once; a hit takes no lock.
A grid point outside the data's coverage raises, so an epoch within one spacing of the coverage
edge needs the next point covered as well.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
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
        self._cells: dict[tuple[str, int], np.ndarray] = {}
        self._turns: dict[tuple[str, int], _Turn] = {}
        self._lock = threading.Lock()

    def _state(self, body_id: str, k: int) -> tuple[np.ndarray, np.ndarray]:
        key = (body_id, k)
        state = self._states.get(key)
        if state is None:
            with self._lock:
                state = self._states.get(key)
                if state is None:
                    state = self._physics.barycentric_state(body_id, k * GRID_SPACING_S)
                    _keep(self._states, key, state)
        return state

    def _rotation(self, body_id: str, k: int) -> np.ndarray:
        key = (body_id, k)
        rotation = self._rotations.get(key)
        if rotation is None:
            with self._lock:
                rotation = self._rotations.get(key)
                if rotation is None:
                    rotation = self._physics.rotation_at_tdb(body_id, k * GRID_SPACING_S)
                    _keep(self._rotations, key, rotation)
        return rotation

    def _cell_states(self, body_id: str, k: int) -> np.ndarray:
        """Hermite coefficients of cell k: rows p0, h v0, p1, h v1."""
        key = (body_id, k)
        cell = self._cells.get(key)
        if cell is None:
            p0, v0 = self._state(body_id, k)
            p1, v1 = self._state(body_id, k + 1)
            cell = np.stack([p0, GRID_SPACING_S * v0, p1, GRID_SPACING_S * v1])
            with self._lock:
                _keep(self._cells, key, cell)
        return cell

    def _cell_turn(self, body_id: str, k: int) -> _Turn:
        """The constant-rate turn from grid point k to k + 1."""
        key = (body_id, k)
        turn = self._turns.get(key)
        if turn is None:
            r0 = self._rotation(body_id, k)
            step = self._rotation(body_id, k + 1) @ r0.T
            axis = (
                np.array(
                    [step[2, 1] - step[1, 2], step[0, 2] - step[2, 0], step[1, 0] - step[0, 1]]
                )
                / 2
            )
            sine = float(np.linalg.norm(axis))
            angle = math.atan2(sine, (np.trace(step) - 1) / 2)
            unit = axis / sine if sine > 0.0 else np.zeros(3)
            kx = np.array([[0, -unit[2], unit[1]], [unit[2], 0, -unit[0]], [-unit[1], unit[0], 0]])
            turn = _Turn(r0, unit, angle, kx, kx @ kx)
            with self._lock:
                _keep(self._turns, key, turn)
        return turn

    def states(self, body_id: str, tdb_seconds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Barycentric positions (N, 3) and velocities (N, 3) at TDB epochs (N,)."""
        h = GRID_SPACING_S
        positions = np.empty((len(tdb_seconds), 3))
        velocities = np.empty((len(tdb_seconds), 3))
        for k, rows in _cells_of(tdb_seconds):
            coefficients = self._cell_states(body_id, k)
            s = (tdb_seconds[rows] - k * h) / h
            s2 = s * s
            s3 = s2 * s
            basis = np.stack([2 * s3 - 3 * s2 + 1, s3 - 2 * s2 + s, 3 * s2 - 2 * s3, s3 - s2], 1)
            slope = np.stack(
                [6 * s2 - 6 * s, 3 * s2 - 4 * s + 1, 6 * s - 6 * s2, 3 * s2 - 2 * s], 1
            )
            positions[rows] = basis @ coefficients
            velocities[rows] = (slope / h) @ coefficients
        return positions, velocities

    def orientations(self, body_id: str, tdb_seconds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """ICRF-to-body-fixed rotations (N, 3, 3) and the body's angular velocity on ICRF axes
        (N, 3), rad/s, at TDB epochs (N,).

        Within a cell the rotation is R(t) = exp(theta(t) [u]x) R0 with u the unit axis of
        R1 R0^T, so the angular velocity on ICRF axes is -(angle / spacing) R(t)^T u.
        """
        h = GRID_SPACING_S
        rotations = np.empty((len(tdb_seconds), 3, 3))
        rates = np.empty((len(tdb_seconds), 3))
        for k, rows in _cells_of(tdb_seconds):
            turn = self._cell_turn(body_id, k)
            theta = (tdb_seconds[rows] - k * h) / h * turn.angle
            turns = (
                np.eye(3)[None]
                + np.sin(theta)[:, None, None] * turn.kx[None]
                + (1 - np.cos(theta))[:, None, None] * turn.kx_squared[None]
            )
            turned = turns @ turn.r0
            rotations[rows] = turned
            rates[rows] = -(turn.angle / h) * np.einsum("nji,j->ni", turned, turn.unit)
        return rotations, rates


@dataclass(frozen=True)
class _Turn:
    r0: np.ndarray
    unit: np.ndarray
    angle: float
    kx: np.ndarray
    kx_squared: np.ndarray


def _cells_of(tdb_seconds: np.ndarray) -> list[tuple[int, slice | np.ndarray]]:
    """The grid cells the epochs fall in, with each cell's rows. A tick's epochs usually share
    one cell; then every row is taken without a mask."""
    if len(tdb_seconds) == 0:
        return []
    cells = np.floor(tdb_seconds / GRID_SPACING_S)
    first, last = cells.min(), cells.max()
    if first == last:
        return [(int(first), slice(None))]
    return [(int(k), cells == k) for k in np.unique(cells)]
