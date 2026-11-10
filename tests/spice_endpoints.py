# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Temporary SPK files that let SPICE treat test endpoints as ephemeris objects: a site as a fixed
point in its body's frame, and an orbiting satellite from the shared orbit model. SPICE then
applies its own light-time and aberration corrections to them."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import spiceypy
from anise.time import Epoch
from nodalarc.physics import PhysicsContext
from nodalarc.physics.kepler import ElementsBatch, orbit_states
from nodalarc.physics.lighttime import OrbitEndpoint

# Segment spacing for an orbit, and the Lagrange degree SPICE interpolates it with.
ORBIT_SPACING_S = 10.0
ORBIT_DEGREE = 7


def et_of(unix: float) -> float:
    return float(Epoch.init_from_unix_seconds(unix).to_et_seconds())


def write_endpoint_spk(
    path: Path,
    physics: PhysicsContext,
    *,
    start_unix: float,
    end_unix: float,
    sites: Mapping[int, tuple[int, str, tuple[float, float, float]]],
    orbits: Mapping[int, tuple[int, OrbitEndpoint]],
) -> None:
    """Write ``sites`` (NAIF id -> center id, body-fixed frame name, fixed position in km) and
    ``orbits`` (NAIF id -> center id, endpoint) for [start_unix, end_unix]."""
    first, last = et_of(start_unix), et_of(end_unix)
    handle = spiceypy.spkopn(str(path), "endpoint reference", 0)
    for naif_id, (center, frame, fixed) in sites.items():
        state = [*fixed, 0.0, 0.0, 0.0]
        spiceypy.spkw08(
            handle, naif_id, center, frame, first, last, f"SITE {naif_id}", 1, 2,
            [state, state], first, last - first,
        )  # fmt: skip
    epochs = np.arange(first, last + ORBIT_SPACING_S, ORBIT_SPACING_S)
    unix = np.array([Epoch.init_from_et_seconds(float(e)).to_unix_seconds() for e in epochs])
    for naif_id, (center, endpoint) in orbits.items():
        states = orbit_states(
            ElementsBatch.from_elements([endpoint.elements]),
            (unix - endpoint.elements_epoch_unix)[None, :],
            model=endpoint.model,
            body_frame=endpoint.body_frame,
        )
        basis = physics.orbit_basis(endpoint.body_id)
        positions = np.stack([states.px[0], states.py[0], states.pz[0]], axis=1) @ basis
        velocities = np.stack([states.vx[0], states.vy[0], states.vz[0]], axis=1) @ basis
        spiceypy.spkw13(
            handle, naif_id, center, "J2000", float(epochs[0]), float(epochs[-1]),
            f"ORBIT {naif_id}", ORBIT_DEGREE, len(epochs),
            np.hstack([positions, velocities]).tolist(), epochs.tolist(),
        )  # fmt: skip
    spiceypy.spkcls(handle)
