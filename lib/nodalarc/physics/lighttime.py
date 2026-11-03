# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Signals between link endpoints, in each direction: light time and the apparent direction each
end points along.

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

Pointing follows the physically correct direction: the transmitter points at the receiver's
position when the signal will arrive, the receiver at the transmitter's position when the signal
left it, and both include stellar aberration from that end's barycentric velocity (SPICE's
XCN+S and CN+S corrections, by SPICE's own construction in stlabx and stelab). Aberration changes
direction by up to about 1e-4 rad and never changes the light time.

Each path is tested for obstruction by every session body and the solar disk (``occlusion``).

Inside one call every endpoint is evaluated in batches: one orbit-kernel call per body and model,
and body positions and orientations from the context's motion table (``physics.motion``), which
interpolates ANISE grid points 600 s apart. A caller passes all of a tick's links at once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from nodalarc.body_frames import BodyFrame
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.orbital import OrbitalElements
from nodalarc.physics.context import PhysicsContext, tdb_seconds_of, unix_seconds_of_tdb
from nodalarc.physics.kepler import ElementsBatch, OrbitModel, orbit_states
from nodalarc.physics.occlusion import PathEnds, obstructions
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
    body_ids: np.ndarray
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
        body_ids=np.array([endpoint.body_id for endpoint in endpoints], dtype=object),
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


@dataclass(frozen=True)
class _EndStates:
    """Endpoint states on ICRF axes: relative to the central body, and the central body's
    barycentric state, each position (km) and velocity (km/s)."""

    relative_km: np.ndarray
    relative_km_s: np.ndarray
    body_km: np.ndarray
    body_km_s: np.ndarray

    @property
    def position_km(self) -> np.ndarray:
        return self.relative_km + self.body_km

    @property
    def velocity_km_s(self) -> np.ndarray:
        return self.relative_km_s + self.body_km_s

    def take(self, rows: np.ndarray) -> _EndStates:
        return _EndStates(
            self.relative_km[rows],
            self.relative_km_s[rows],
            self.body_km[rows],
            self.body_km_s[rows],
        )


def _states(
    physics: PhysicsContext, prepared: _PreparedEndpoints, tdb_seconds: np.ndarray
) -> _EndStates:
    """One row per endpoint at its own TDB epoch."""
    relative = np.empty((prepared.count, 3))
    relative_velocity = np.empty((prepared.count, 3))
    body = np.empty((prepared.count, 3))
    body_velocity = np.empty((prepared.count, 3))
    if prepared.orbits or prepared.tles:
        unix = _unix_seconds(tdb_seconds)
    for group in prepared.orbits:
        phases = (unix[group.rows] - group.elements_epoch_unix)[:, None]
        states = orbit_states(group.batch, phases, model=group.model, body_frame=group.body_frame)
        basis = physics.orbit_basis(group.body_id)
        relative[group.rows] = (
            np.stack([states.px[:, 0], states.py[:, 0], states.pz[:, 0]], 1) @ basis
        )
        relative_velocity[group.rows] = (
            np.stack([states.vx[:, 0], states.vy[:, 0], states.vz[:, 0]], 1) @ basis
        )
    for row, tle in prepared.tles:
        # SGP4 runs on master sim time; the TDB-to-UTC rate difference over a light time is
        # below 1e-9 of it, a millimetre of satellite motion.
        relative[row], relative_velocity[row] = propagate_sgp4_tle_gcrs(
            tle.line_1, tle.line_2, float(unix[row])
        )
    motion = physics.motion
    for site in prepared.sites:
        rotations, rates = motion.orientations(site.body_id, tdb_seconds[site.rows])
        turned = np.einsum("nji,nj->ni", rotations, site.fixed_km)
        relative[site.rows] = turned
        relative_velocity[site.rows] = _cross(rates, turned)
    for body_id, rows in prepared.bodies:
        body[rows], body_velocity[rows] = motion.states(body_id, tdb_seconds[rows])
    return _EndStates(relative, relative_velocity, body, body_velocity)


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cross product of (N, 3) arrays; numpy's general cross costs 27 us per call in
    axis handling, most of a small tick's signal time."""
    return np.stack(
        [
            a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1],
            a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2],
            a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0],
        ],
        axis=1,
    )


def _aberrated(direction_km: np.ndarray, velocity_km_s: np.ndarray) -> np.ndarray:
    """Unit apparent directions: each direction turned toward the observer's barycentric velocity
    by phi, sin(phi) = |u x v / c| (SPICE stelab; pass the negated velocity for stlabx)."""
    unit = direction_km / np.linalg.norm(direction_km, axis=1, keepdims=True)
    h = _cross(unit, velocity_km_s / SPEED_OF_LIGHT_KM_S)
    sine = np.linalg.norm(h, axis=1, keepdims=True)
    if np.any(sine >= 1.0):
        raise ValueError("an endpoint's barycentric speed is not below the speed of light")
    cosine = np.sqrt(1.0 - sine * sine)
    # Rotating u about h / |h| by phi: h is perpendicular to u, so the turn is
    # u cos(phi) + (h / |h|) x u sin(phi) = u cos(phi) + h x u (|h| = sin(phi)).
    return unit * cosine + _cross(h, unit)


@dataclass(frozen=True)
class SignalEnd:
    """One end of every signal, at that end's own epoch (TDB seconds past J2000): the
    transmitter at emission or the receiver at arrival.

    ``position_km`` is barycentric on ICRF axes. ``fixed_km`` is the position relative to the
    end's central body in that body's fixed frame. ``pointing`` (ICRF) and ``pointing_fixed``
    (the body's fixed frame) are the unit apparent direction the end points along.
    ``rate_rad_s`` is the angular rate of the line of sight to the other end: in the body's
    rotating frame when both ends share a central body, inertial when they do not.
    """

    body_ids: np.ndarray
    epoch_tdb: np.ndarray
    position_km: np.ndarray
    fixed_km: np.ndarray
    pointing: np.ndarray
    pointing_fixed: np.ndarray
    rate_rad_s: np.ndarray

    def take(self, rows: np.ndarray) -> SignalEnd:
        return SignalEnd(*(getattr(self, name)[rows] for name in self.__dataclass_fields__))


@dataclass(frozen=True)
class Signals:
    """One signal per (transmitter, receiver) pair.

    The end found by iteration (the receiver of a signal sent at an instant, the transmitter of
    one received at an instant) is evaluated at the last iteration's epoch, within 1 ns of its
    reported epoch. ``occluded`` (N, B) states, per path, whether each body in ``occulters``
    (every session body, then the Sun) blocks it.
    """

    light_time_s: np.ndarray
    transmitter: SignalEnd
    receiver: SignalEnd
    occulters: tuple[str, ...]
    occluded: np.ndarray

    def take(self, rows: np.ndarray) -> Signals:
        return Signals(
            light_time_s=self.light_time_s[rows],
            transmitter=self.transmitter.take(rows),
            receiver=self.receiver.take(rows),
            occulters=self.occulters,
            occluded=self.occluded[rows],
        )


def _signal_end(
    physics: PhysicsContext,
    body_ids: np.ndarray,
    other_body_ids: np.ndarray,
    epochs: np.ndarray,
    this: _EndStates,
    that: _EndStates,
    pointing: np.ndarray,
) -> SignalEnd:
    count = len(body_ids)
    rotations = np.empty((count, 3, 3))
    spin = np.empty((count, 3))
    for body_id in set(body_ids):
        rows = body_ids == body_id
        rotations[rows], spin[rows] = physics.motion.orientations(body_id, epochs[rows])
    sight = that.position_km - this.position_km
    relative_velocity = that.velocity_km_s - this.velocity_km_s
    same_body = body_ids == other_body_ids
    relative_velocity[same_body] -= _cross(spin[same_body], sight[same_body])
    distance = np.linalg.norm(sight, axis=1)
    along = np.einsum("ni,ni->n", relative_velocity, sight) / distance
    across = relative_velocity - (along / distance)[:, None] * sight
    return SignalEnd(
        body_ids=body_ids,
        epoch_tdb=epochs,
        position_km=this.position_km,
        fixed_km=np.einsum("nij,nj->ni", rotations, this.relative_km),
        pointing=pointing,
        pointing_fixed=np.einsum("nij,nj->ni", rotations, pointing),
        rate_rad_s=np.linalg.norm(across, axis=1) / distance,
    )


def _signals(
    physics: PhysicsContext,
    shapes: Mapping[str, BodyFrame],
    body_ids: tuple[np.ndarray, np.ndarray],
    epochs: tuple[np.ndarray, np.ndarray],
    states: tuple[_EndStates, _EndStates],
    light_time_s: np.ndarray,
) -> Signals:
    (sending, receiving), (emitted, received), (transmitter, receiver) = body_ids, epochs, states
    origin, arrival = transmitter.position_km, receiver.position_km
    path = arrival - origin
    occulters, occluded = obstructions(
        physics,
        shapes,
        PathEnds(sending, transmitter.relative_km, origin, emitted),
        PathEnds(receiving, receiver.relative_km, arrival, received),
    )
    return Signals(
        light_time_s=light_time_s,
        transmitter=_signal_end(
            physics,
            sending,
            receiving,
            emitted,
            transmitter,
            receiver,
            _aberrated(path, -transmitter.velocity_km_s),
        ),
        receiver=_signal_end(
            physics,
            receiving,
            sending,
            received,
            receiver,
            transmitter,
            _aberrated(-path, receiver.velocity_km_s),
        ),
        occulters=occulters,
        occluded=occluded,
    )


def _check_pairs(transmitters: Sequence[Endpoint], receivers: Sequence[Endpoint]) -> None:
    if len(transmitters) != len(receivers):
        raise ValueError(f"{len(transmitters)} transmitters but {len(receivers)} receivers")


def _not_converged() -> RuntimeError:
    return RuntimeError(
        f"light time did not converge to {CONVERGENCE_S} s in {MAX_ITERATIONS} rounds"
    )


def _distinct(
    transmitters: Sequence[Endpoint], receivers: Sequence[Endpoint], instants: np.ndarray
) -> tuple[list[Endpoint], np.ndarray, np.ndarray, np.ndarray]:
    """The distinct (endpoint, instant) rows of both lists, their instants, and each list's rows
    into them. Callers pass the same endpoint object for a node in both directions of its
    links, so a node is evaluated once per instant however many links it has."""
    rows: dict[tuple[int, float], int] = {}
    distinct: list[Endpoint] = []
    distinct_instants: list[float] = []

    def index(endpoints: Sequence[Endpoint]) -> np.ndarray:
        found = []
        for endpoint, instant in zip(endpoints, instants.tolist(), strict=True):
            key = (id(endpoint), instant)
            if key not in rows:
                rows[key] = len(distinct)
                distinct.append(endpoint)
                distinct_instants.append(instant)
            found.append(rows[key])
        return np.array(found, dtype=np.int64)

    tx_rows, rx_rows = index(transmitters), index(receivers)
    return distinct, np.array(distinct_instants), tx_rows, rx_rows


def _first_guess(separation_km: np.ndarray, velocity_km_s: np.ndarray) -> np.ndarray:
    """Light time with the iterated end moving straight at its velocity at the instant: the
    positive root of |r + v tau| = c tau. Its error is half the end's acceleration times tau
    squared over c, so a LEO link starts within 1e-12 s and converges in one iteration."""
    c = SPEED_OF_LIGHT_KM_S
    along = np.einsum("ni,ni->n", separation_km, velocity_km_s)
    a = c * c - np.einsum("ni,ni->n", velocity_km_s, velocity_km_s)
    r_sq = np.einsum("ni,ni->n", separation_km, separation_km)
    return (along + np.sqrt(along * along + a * r_sq)) / a


def _tdb_seconds(instants_unix: np.ndarray) -> np.ndarray:
    distinct, index = np.unique(instants_unix, return_inverse=True)
    return np.array([tdb_seconds_of(float(instant)) for instant in distinct])[index]


def _signals_at(
    physics: PhysicsContext,
    shapes: Mapping[str, BodyFrame],
    transmitters: Sequence[Endpoint],
    receivers: Sequence[Endpoint],
    instants_unix: float | Sequence[float] | np.ndarray,
    *,
    sent: bool,
) -> Signals:
    _check_pairs(transmitters, receivers)
    at = _tdb_seconds(
        np.broadcast_to(np.asarray(instants_unix, dtype=np.float64), (len(transmitters),))
    )
    distinct, distinct_at, tx_rows, rx_rows = _distinct(transmitters, receivers, at)
    prepared = _prepare(distinct)
    at_instant = _states(physics, prepared, distinct_at)
    transmitter, receiver = at_instant.take(tx_rows), at_instant.take(rx_rows)
    tx_bodies, rx_bodies = prepared.body_ids[tx_rows], prepared.body_ids[rx_rows]
    moving = _prepare(receivers if sent else transmitters)
    separation = receiver.position_km - transmitter.position_km
    tau = _first_guess(separation, receiver.velocity_km_s if sent else transmitter.velocity_km_s)
    for _ in range(MAX_ITERATIONS):
        if sent:
            receiver = _states(physics, moving, at + tau)
        else:
            transmitter = _states(physics, moving, at - tau)
        new = (
            np.linalg.norm(receiver.position_km - transmitter.position_km, axis=1)
            / SPEED_OF_LIGHT_KM_S
        )
        if np.max(np.abs(new - tau), initial=0.0) < CONVERGENCE_S:
            epochs = (at, at + new) if sent else (at - new, at)
            return _signals(
                physics, shapes, (tx_bodies, rx_bodies), epochs, (transmitter, receiver), new
            )
        tau = new
    raise _not_converged()


def signals_sent_at(
    physics: PhysicsContext,
    shapes: Mapping[str, BodyFrame],
    transmitters: Sequence[Endpoint],
    receivers: Sequence[Endpoint],
    sent_unix: float | Sequence[float] | np.ndarray,
) -> Signals:
    """The signal each transmitter sends at ``sent_unix`` (master sim time; one instant, or one
    per row) to its receiver. ``shapes`` holds the catalog shape of every session body."""
    return _signals_at(physics, shapes, transmitters, receivers, sent_unix, sent=True)


def signals_received_at(
    physics: PhysicsContext,
    shapes: Mapping[str, BodyFrame],
    transmitters: Sequence[Endpoint],
    receivers: Sequence[Endpoint],
    received_unix: float | Sequence[float] | np.ndarray,
) -> Signals:
    """The signal each receiver takes in at ``received_unix`` (master sim time; one instant, or
    one per row) from its transmitter. ``shapes`` holds the catalog shape of every session
    body."""
    return _signals_at(physics, shapes, transmitters, receivers, received_unix, sent=False)
