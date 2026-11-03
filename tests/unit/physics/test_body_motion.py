"""The body motion table against SPICE: barycentric positions and orientations at arbitrary
epochs, interpolated from grid points 600 s apart.

Bounds: 1 cm for positions (measured 3.6 mm worst for Earth and the Moon, the data's evaluation
noise at barycentric distance; 1 cm is 33 ps of light time) and 1e-10 rad for orientations
(measured 1.6e-11 rad worst for Earth and 7.4e-11 rad for the Moon, 0.1 mm at either surface).
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
import spiceypy
from anise.time import Epoch
from nodalarc.physics import PhysicsContext, PhysicsDataError
from nodalarc.physics.context import tdb_seconds_of
from nodalarc.physics.motion import GRID_SPACING_S

SPICE_NAME = {"earth": "EARTH", "luna": "MOON"}
SPICE_FRAME = {"earth": "ITRF93", "luna": "MOON_ME_DE440_ME421"}
STARTS = [
    datetime(2026, 11, 3, 12, tzinfo=UTC).timestamp(),
    datetime(2029, 8, 9, 22, 15, tzinfo=UTC).timestamp(),
]
# Two days of epochs at an odd spacing, so every phase within a grid cell is sampled.
OFFSETS_S = np.arange(0.0, 2 * 86400.0, 977.3)


def _et(tdb_seconds: float) -> float:
    """SPICE ephemeris time of a TDB epoch: hifitime's TDB and ET (SPICE's realization of TDB)
    differ by microseconds (6.7 us of Earth's orbital motion is 200 mm)."""
    return float(Epoch.init_from_tdb_seconds(float(tdb_seconds)).to_et_seconds())


def _rotation_angle(a: np.ndarray, b: np.ndarray) -> float:
    # atan2 of sine and cosine: arccos alone cannot resolve angles below 2.1e-8 rad.
    m = a.T @ b
    skew = m - m.T
    sine = np.linalg.norm(np.array([skew[2, 1], skew[0, 2], skew[1, 0]])) / 2.0
    return float(np.arctan2(sine, (np.trace(m) - 1.0) / 2.0))


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("body_id", ["earth", "luna"])
@pytest.mark.parametrize("start", STARTS)
def test_positions_match_spice(physics_context: PhysicsContext, body_id: str, start: float) -> None:
    epochs = tdb_seconds_of(start) + OFFSETS_S
    produced = physics_context.motion.positions(body_id, epochs)
    for row, tdb in enumerate(epochs):
        reference, _ = spiceypy.spkezr(SPICE_NAME[body_id], _et(tdb), "J2000", "NONE", "SSB")
        assert np.linalg.norm(produced[row] - np.array(reference[:3])) < 1e-5


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("body_id", ["earth", "luna"])
@pytest.mark.parametrize("start", STARTS)
def test_orientations_match_spice(
    physics_context: PhysicsContext, body_id: str, start: float
) -> None:
    epochs = tdb_seconds_of(start) + OFFSETS_S
    produced = physics_context.motion.rotations(body_id, epochs)
    for row, tdb in enumerate(epochs):
        reference = np.array(spiceypy.pxform("J2000", SPICE_FRAME[body_id], _et(tdb)))
        assert _rotation_angle(produced[row], reference) < 1e-10


def test_an_epoch_whose_next_grid_point_is_uncovered_is_refused(
    physics_context: PhysicsContext,
) -> None:
    beyond = datetime(2200, 1, 1, tzinfo=UTC).timestamp()
    epoch = np.array([tdb_seconds_of(beyond)])
    with pytest.raises(PhysicsDataError, match="does not cover"):
        physics_context.motion.positions("earth", epoch)
    with pytest.raises(PhysicsDataError, match="does not cover"):
        physics_context.motion.rotations("earth", epoch)


def test_grid_points_lie_on_the_tdb_grid(physics_context: PhysicsContext) -> None:
    """An epoch on a grid point returns that point's ANISE value unchanged."""
    k = int(tdb_seconds_of(STARTS[0]) // GRID_SPACING_S)
    on_grid = np.array([k * GRID_SPACING_S])
    position, _ = physics_context.barycentric_state("luna", k * GRID_SPACING_S)
    assert np.array_equal(physics_context.motion.positions("luna", on_grid)[0], position)
    rotation = physics_context.rotation_at_tdb("luna", k * GRID_SPACING_S)
    assert np.array_equal(physics_context.motion.rotations("luna", on_grid)[0], rotation)
