# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Light time between link endpoints, in each direction.

A signal leaves its transmitter at one epoch and reaches its receiver at a later one, and both
endpoints move meanwhile. Two time indexes, each for one purpose:

- Transmission time (actuation): for a signal sent at t, tau solves
  |r_receiver(t + tau) - r_transmitter(t)| = c * tau.
- Reception time (observation): for a signal received at t, tau solves
  |r_receiver(t) - r_transmitter(t - tau)| = c * tau.

Positions are relative to the solar system barycenter on ICRF axes, at TDB epochs: a
satellite's position is its central-body-relative state from its orbit model plus its body's
barycentric position at the same epoch; a site's is its body-fixed position turned by its body's
orientation at that epoch plus the body's barycentric position. Earth-relative vectors are never
differenced across two epochs; doing so put an Earth-to-Mars delay 75.294 ms off.

tau is iterated to convergence within 1 ns. Measured on the shipped data: 3 iterations for a
Goldstone-to-Moon link (1248.4 ms) and 2 for a LEO pair; the two directions of the Goldstone-to-Moon
link differ by 235.058 us, and transmission-time light time differs from the same-instant
Earth-centred range / c by 117.070 us there and by 0.757 us on the LEO pair.

Inside one call every endpoint is evaluated in batches: one orbit-kernel call per body and model,
and body positions and orientations from the context's motion table (``physics.motion``), which
interpolates ANISE grid points 600 s apart. A caller passes all of a tick's links at once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from nodalarc.body_frames import BodyFrame
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.orbital import OrbitalElements
from nodalarc.physics.context import PhysicsContext, tdb_seconds_of, unix_seconds_of_tdb
from nodalarc.physics.kepler import ElementsBatch, OrbitModel, orbit_states
from nodalarc.propagator import propagate_sgp4_tle_gcrs

CONVERGENCE_S = 1e-9
MAX_ITERATIONS = 10


@dataclass(frozen=True)
class OrbitEndpoint:
    """A satellite on an analytic orbit; ``elements`` are valid at ``elements_epoch_unix``."""

    body_id: str
    body_frame: BodyFrame
    model: OrbitModel
    elements: OrbitalElements
    elements_epoch_unix: float


@dataclass(frozen=True)
class TleEndpoint:
    """An Earth satellite given by a TLE."""

    body_id: str
    line_1: str
    line_2: str


@dataclass(frozen=True)
class SiteEndpoint:
    """A point fixed on a body: body-fixed position in km (a body's centre is (0, 0, 0))."""

    body_id: str
    fixed_km: tuple[float, float, float]


Endpoint = OrbitEndpoint | TleEndpoint | SiteEndpoint


@dataclass(frozen=True)
class _OrbitGroup:
    rows: np.ndarray
    body_id: str
    body_frame: BodyFrame
    model: OrbitModel
    batch: ElementsBatch
    elements_epoch_unix: np.ndarray


@dataclass(frozen=True)
class _SiteGroup:
    rows: np.ndarray
    body_id: str
    fixed_km: np.ndarray


@dataclass(frozen=True)
class _PreparedEndpoints:
    """Endpoints grouped once per call; every pass of the iteration reuses the groups. Grouping
    the flagship's 1,174 endpoints measured 0.45 ms, and an iteration makes five passes."""

    count: int
    orbits: tuple[_OrbitGroup, ...]
    sites: tuple[_SiteGroup, ...]
    tles: tuple[tuple[int, TleEndpoint], ...]
    bodies: tuple[tuple[str, np.ndarray], ...]


def _prepare(endpoints: Sequence[Endpoint]) -> _PreparedEndpoints:
    by_body: dict[str, list[int]] = {}
    orbit_groups: dict[tuple[str, OrbitModel], list[tuple[int, OrbitEndpoint]]] = {}
    site_groups: dict[str, list[tuple[int, SiteEndpoint]]] = {}
    tles: list[tuple[int, TleEndpoint]] = []
    for row, endpoint in enumerate(endpoints):
        by_body.setdefault(endpoint.body_id, []).append(row)
        if isinstance(endpoint, OrbitEndpoint):
            orbit_groups.setdefault((endpoint.body_id, endpoint.model), []).append((row, endpoint))
        elif isinstance(endpoint, TleEndpoint):
            tles.append((row, endpoint))
        else:
            site_groups.setdefault(endpoint.body_id, []).append((row, endpoint))
    return _PreparedEndpoints(
        count=len(endpoints),
        orbits=tuple(
            _OrbitGroup(
                rows=np.array([row for row, _ in members]),
                body_id=body_id,
                body_frame=members[0][1].body_frame,
                model=model,
                batch=ElementsBatch.from_elements([member.elements for _, member in members]),
                elements_epoch_unix=np.array([member.elements_epoch_unix for _, member in members]),
            )
            for (body_id, model), members in orbit_groups.items()
        ),
        sites=tuple(
            _SiteGroup(
                rows=np.array([row for row, _ in members]),
                body_id=body_id,
                fixed_km=np.array([member.fixed_km for _, member in members], dtype=np.float64),
            )
            for body_id, members in site_groups.items()
        ),
        tles=tuple(tles),
        bodies=tuple((body_id, np.array(rows)) for body_id, rows in by_body.items()),
    )


# Below this, the difference between the TDB span and the Unix span of a window is TDB - TT drift
# (at most 3.3e-10 s per second); a leap second makes it 1 s.
_LEAP_SECOND_TEST_S = 1e-3


def _unix_seconds(tdb_seconds: np.ndarray) -> np.ndarray:
    """Master sim time of each TDB epoch.

    TDB - UTC is constant apart from the TDB - TT term (amplitude 1.7 ms, period one year) and
    leap seconds. Without a leap second between the window's ends, Unix time is linear in TDB to
    within the curvature of that term: 1.6e-11 s across a 1,400 s window (the longest Mars light
    time), below the float64 step of Unix seconds (2.4e-7 s at 2026 dates), which bounds the
    per-epoch conversion too. The conversion is exact at both ends and interpolated between them;
    converting each epoch measured 0.4 ms per pass for the flagship's 1,174 receivers against
    0.02 ms. A window with a leap second inside is converted epoch by epoch."""
    first, last = float(tdb_seconds.min()), float(tdb_seconds.max())
    unix_first = unix_seconds_of_tdb(first)
    if last == first:
        return np.full(len(tdb_seconds), unix_first)
    unix_last = unix_seconds_of_tdb(last)
    if abs((unix_last - unix_first) - (last - first)) < _LEAP_SECOND_TEST_S:
        return unix_first + (tdb_seconds - first) * ((unix_last - unix_first) / (last - first))
    distinct, index = np.unique(tdb_seconds, return_inverse=True)
    return np.array([unix_seconds_of_tdb(float(value)) for value in distinct])[index]


def _positions(
    physics: PhysicsContext, prepared: _PreparedEndpoints, tdb_seconds: np.ndarray
) -> np.ndarray:
    relative = np.empty((prepared.count, 3))
    if prepared.orbits or prepared.tles:
        unix = _unix_seconds(tdb_seconds)
    for group in prepared.orbits:
        phases = (unix[group.rows] - group.elements_epoch_unix)[:, None]
        states = orbit_states(group.batch, phases, model=group.model, body_frame=group.body_frame)
        in_basis = np.stack([states.px[:, 0], states.py[:, 0], states.pz[:, 0]], axis=1)
        relative[group.rows] = in_basis @ physics.orbit_basis(group.body_id)
    for row, tle in prepared.tles:
        # SGP4 runs on master sim time; the TDB-to-UTC rate difference over a light time is
        # below 1e-9 of it, a millimetre of satellite motion.
        position, _velocity = propagate_sgp4_tle_gcrs(tle.line_1, tle.line_2, float(unix[row]))
        relative[row] = position
    motion = physics.motion
    for site in prepared.sites:
        rotations = motion.rotations(site.body_id, tdb_seconds[site.rows])
        relative[site.rows] = np.einsum("nji,nj->ni", rotations, site.fixed_km)
    positions = np.empty((prepared.count, 3))
    for body_id, rows in prepared.bodies:
        positions[rows] = motion.positions(body_id, tdb_seconds[rows]) + relative[rows]
    return positions


def barycentric_positions(
    physics: PhysicsContext, endpoints: Sequence[Endpoint], tdb_seconds: np.ndarray
) -> np.ndarray:
    """Each endpoint's position relative to the solar system barycenter, ICRF axes, km, at its
    own TDB epoch: (N,) endpoints and epochs to (N, 3)."""
    if len(tdb_seconds) != len(endpoints):
        raise ValueError(f"{len(endpoints)} endpoints but {len(tdb_seconds)} epochs")
    return _positions(physics, _prepare(endpoints), tdb_seconds)


def _check_pairs(transmitters: Sequence[Endpoint], receivers: Sequence[Endpoint]) -> None:
    if len(transmitters) != len(receivers):
        raise ValueError(f"{len(transmitters)} transmitters but {len(receivers)} receivers")


def transmission_light_times(
    physics: PhysicsContext,
    transmitters: Sequence[Endpoint],
    receivers: Sequence[Endpoint],
    sent_unix: float,
) -> np.ndarray:
    """Seconds from transmission at ``sent_unix`` to reception, one per (transmitter, receiver)."""
    _check_pairs(transmitters, receivers)
    sending, receiving = _prepare(transmitters), _prepare(receivers)
    sent = np.full(len(transmitters), tdb_seconds_of(sent_unix))
    origin = _positions(physics, sending, sent)
    tau = np.linalg.norm(_positions(physics, receiving, sent) - origin, axis=1)
    tau /= SPEED_OF_LIGHT_KM_S
    for _ in range(MAX_ITERATIONS):
        arrival = _positions(physics, receiving, sent + tau)
        new = np.linalg.norm(arrival - origin, axis=1) / SPEED_OF_LIGHT_KM_S
        if np.max(np.abs(new - tau), initial=0.0) < CONVERGENCE_S:
            return new
        tau = new
    raise RuntimeError(
        f"light time did not converge to {CONVERGENCE_S} s in {MAX_ITERATIONS} rounds"
    )


def reception_light_times(
    physics: PhysicsContext,
    transmitters: Sequence[Endpoint],
    receivers: Sequence[Endpoint],
    received_unix: float,
) -> np.ndarray:
    """Seconds from transmission to reception at ``received_unix``, one per pair."""
    _check_pairs(transmitters, receivers)
    sending, receiving = _prepare(transmitters), _prepare(receivers)
    received = np.full(len(receivers), tdb_seconds_of(received_unix))
    destination = _positions(physics, receiving, received)
    tau = np.linalg.norm(destination - _positions(physics, sending, received), axis=1)
    tau /= SPEED_OF_LIGHT_KM_S
    for _ in range(MAX_ITERATIONS):
        departure = _positions(physics, sending, received - tau)
        new = np.linalg.norm(destination - departure, axis=1) / SPEED_OF_LIGHT_KM_S
        if np.max(np.abs(new - tau), initial=0.0) < CONVERGENCE_S:
            return new
        tau = new
    raise RuntimeError(
        f"light time did not converge to {CONVERGENCE_S} s in {MAX_ITERATIONS} rounds"
    )
