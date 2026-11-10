# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Signals in both time indexes against SPICE, with the platform's own data files: light time,
and the apparent direction each end points along.

Body-pair references come from SPICE's corrections: XCN (+S) for a signal transmitted at the
epoch, CN (+S) for one received at it (spkcpo / spkcpt, a fixed site on Earth as one end). Moving
satellite endpoints have no SPICE object, so their reference iterates on SPICE barycentric body
states and frames plus the same orbit model, and applies SPICE's stlabx and stelab.

Tolerances: 1 microsecond for light time, 1e-9 rad for pointing (stellar aberration itself is
up to about 1e-4 rad). Measured worst differences:
- light time: 0.1 ps for the body pairs, 4.8 ps for the moving endpoints at transmission and
  5.1 ps at reception, 0.24 ps between the two time indexes of one signal;
- pointing: 2.7e-13 rad for the body pairs, 4.6e-13 rad for the lunar orbiter and Goldstone,
  7.0e-13 rad between the two time indexes, and 5.5e-10 rad for the LEO pair at transmission and
  4.5e-10 rad at reception (1.1 mm at 2,016 km). Both the produced value and the reference hold
  master sim time as float64 Unix seconds, in steps of 2.4e-7 s, which is 1.8 mm of LEO motion.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest
import spiceypy
from nodalarc.frames import GeoPosition
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import PhysicsContext, PhysicsDataError, body_fixed_from_geodetic, epoch_of
from nodalarc.physics.context import tdb_seconds_of, unix_seconds_of_tdb
from nodalarc.physics.kepler import ElementsBatch, orbit_states
from nodalarc.physics.lighttime import (
    OrbitEndpoint,
    SiteEndpoint,
    _unix_seconds,
    signals_received_at,
    signals_sent_at,
)

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME, LUNA_TEST_BODY_FRAME

C = 299792.458
TOLERANCE_S = 1e-6
POINTING_TOLERANCE_RAD = 1e-9
SPICE_NAME = {"earth": "EARTH", "luna": "MOON"}
SHAPES = {"earth": EARTH_TEST_BODY_FRAME, "luna": LUNA_TEST_BODY_FRAME}
SPICE_FRAME = {"earth": "ITRF93", "luna": "MOON_ME_DE440_ME421"}
INSTANTS = [
    datetime(2026, 11, 3, 12, tzinfo=UTC).timestamp(),
    datetime(2027, 3, 21, 4, 30, tzinfo=UTC).timestamp(),
    datetime(2029, 8, 9, 22, 15, tzinfo=UTC).timestamp(),
]
GOLDSTONE = tuple(
    float(v)
    for v in body_fixed_from_geodetic(GeoPosition(35.426, -116.89, 1.0), EARTH_TEST_BODY_FRAME)
)
MOON_CENTRE = SiteEndpoint("luna", (0.0, 0.0, 0.0))
GOLDSTONE_SITE = SiteEndpoint("earth", GOLDSTONE)  # type: ignore[arg-type]

LEO_A = OrbitalElements(
    semi_major_axis_km=6928.0,
    inclination_rad=math.radians(53),
    raan_rad=0.0,
    mean_anomaly_rad=0.0,
    eccentricity=0.0,
    argument_of_perigee_rad=0.0,
)
LEO_B = OrbitalElements(
    semi_major_axis_km=6928.0,
    inclination_rad=math.radians(53),
    raan_rad=math.radians(15),
    mean_anomaly_rad=math.radians(5),
    eccentricity=0.0,
    argument_of_perigee_rad=0.0,
)
LUNAR = OrbitalElements(
    semi_major_axis_km=1737.4 + (673.0 + 7332.0) / 2,
    inclination_rad=math.radians(46.8),
    raan_rad=math.radians(252.0),
    mean_anomaly_rad=math.radians(30.0),
    eccentricity=(7332.0 - 673.0) / (7332.0 + 673.0 + 2 * 1737.4),
    argument_of_perigee_rad=math.radians(86.2),
)


def _et(t: float) -> float:
    return epoch_of(t).to_et_seconds()


def _orbit(elements: OrbitalElements, body: str, t0: float) -> OrbitEndpoint:
    frame = EARTH_TEST_BODY_FRAME if body == "earth" else LUNA_TEST_BODY_FRAME
    return OrbitEndpoint(body, frame, "two-body", elements, t0)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def _angle(a, b) -> float:
    a, b = _unit(a), _unit(b)
    return float(np.arctan2(np.linalg.norm(np.cross(a, b)), a @ b))


def _spice_state(endpoint, et: float, physics: PhysicsContext, t0: float):
    """An endpoint's barycentric position and velocity from SPICE body states and frames plus
    the shared orbit model."""
    body = endpoint.body_id
    centre = np.array(spiceypy.spkezr(SPICE_NAME[body], et, "J2000", "NONE", "SSB")[0])
    if isinstance(endpoint, SiteEndpoint):
        transform = np.array(spiceypy.sxform(SPICE_FRAME[body], "J2000", et))
        site = transform @ np.concatenate([endpoint.fixed_km, np.zeros(3)])
        return centre[:3] + site[:3], centre[3:] + site[3:]
    # Satellite phase on master sim time (et seconds map to Unix seconds through ANISE).
    unix = t0 + (et - _et(t0))
    states = orbit_states(
        ElementsBatch.from_elements([endpoint.elements]),
        np.array([[unix - endpoint.elements_epoch_unix]]),
        model="two-body",
        body_frame=endpoint.body_frame,
    )
    basis = physics.orbit_basis(body)
    position = np.array([states.px[0, 0], states.py[0, 0], states.pz[0, 0]]) @ basis
    velocity = np.array([states.vx[0, 0], states.vy[0, 0], states.vz[0, 0]]) @ basis
    return centre[:3] + position, centre[3:] + velocity


def _reference_sent(a, b, t: float, physics):
    """Light time and both pointing directions for a signal a sends to b at t: SPICE states,
    SPICE stlabx at the transmitter and stelab at the receiver."""
    et = _et(t)
    origin, origin_velocity = _spice_state(a, et, physics, t)
    tau = np.linalg.norm(_spice_state(b, et, physics, t)[0] - origin) / C
    for _ in range(10):
        tau = np.linalg.norm(_spice_state(b, et + tau, physics, t)[0] - origin) / C
    arrival, arrival_velocity = _spice_state(b, et + tau, physics, t)
    path = arrival - origin
    return (
        float(tau),
        spiceypy.stlabx(path, origin_velocity),
        spiceypy.stelab(-path, arrival_velocity),
    )


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("t", INSTANTS)
def test_body_pair_light_time_matches_spice(physics_context: PhysicsContext, t: float) -> None:
    et = _et(t)
    obspos = list(GOLDSTONE)
    # Goldstone transmits at t (XCN); Goldstone receives at t (CN); the Moon transmits at t (XCN).
    _, up_ref = spiceypy.spkcpo("MOON", et, "J2000", "OBSERVER", "XCN", obspos, "EARTH", "ITRF93")
    _, down_ref = spiceypy.spkcpo("MOON", et, "J2000", "OBSERVER", "CN", obspos, "EARTH", "ITRF93")
    _, moon_tx_ref = spiceypy.spkcpt(
        obspos, "EARTH", "ITRF93", et, "J2000", "TARGET", "XCN", "MOON"
    )

    up = signals_sent_at(physics_context, SHAPES, [GOLDSTONE_SITE], [MOON_CENTRE], t).light_time_s[
        0
    ]
    down = signals_received_at(
        physics_context, SHAPES, [MOON_CENTRE], [GOLDSTONE_SITE], t
    ).light_time_s[0]
    moon_tx = signals_sent_at(
        physics_context, SHAPES, [MOON_CENTRE], [GOLDSTONE_SITE], t
    ).light_time_s[0]

    assert abs(up - up_ref) < TOLERANCE_S
    assert abs(down - down_ref) < TOLERANCE_S
    assert abs(moon_tx - moon_tx_ref) < TOLERANCE_S


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("t", INSTANTS)
def test_body_pair_pointing_matches_spice(physics_context: PhysicsContext, t: float) -> None:
    """Each end of each signal against SPICE's XCN+S (transmitting) or CN+S (receiving)
    apparent position, at that end's own epoch."""
    et = _et(t)
    obspos = list(GOLDSTONE)

    def from_goldstone(epoch: float, correction: str) -> np.ndarray:
        return spiceypy.spkcpo(
            "MOON", epoch, "J2000", "OBSERVER", correction, obspos, "EARTH", "ITRF93"
        )[0][:3]

    def from_moon(epoch: float, correction: str) -> np.ndarray:
        return spiceypy.spkcpt(
            obspos, "EARTH", "ITRF93", epoch, "J2000", "TARGET", correction, "MOON"
        )[0][:3]

    # Stellar aberration from Earth's orbital speed is about 1e-4 rad; the bound is far below it.
    assert (
        _angle(from_goldstone(et, "XCN+S"), from_goldstone(et, "XCN"))
        > 1e3 * POINTING_TOLERANCE_RAD
    )

    up = signals_sent_at(physics_context, SHAPES, [GOLDSTONE_SITE], [MOON_CENTRE], t)
    down = signals_received_at(physics_context, SHAPES, [MOON_CENTRE], [GOLDSTONE_SITE], t)
    moon_up = signals_sent_at(physics_context, SHAPES, [MOON_CENTRE], [GOLDSTONE_SITE], t)
    moon_down = signals_received_at(physics_context, SHAPES, [GOLDSTONE_SITE], [MOON_CENTRE], t)
    cases = [
        (up.transmitter.pointing[0], from_goldstone(et, "XCN+S")),
        (up.receiver.pointing[0], from_moon(et + up.light_time_s[0], "CN+S")),
        (down.receiver.pointing[0], from_goldstone(et, "CN+S")),
        (down.transmitter.pointing[0], from_moon(et - down.light_time_s[0], "XCN+S")),
        (moon_up.transmitter.pointing[0], from_moon(et, "XCN+S")),
        (moon_down.receiver.pointing[0], from_moon(et, "CN+S")),
    ]
    for produced, reference in cases:
        assert _angle(produced, reference) < POINTING_TOLERANCE_RAD


def _reference_received(a, b, t: float, physics):
    """Light time and both pointing directions for a signal b receives from a at t: SPICE states,
    SPICE stlabx at the transmitter and stelab at the receiver."""
    et = _et(t)
    arrival, arrival_velocity = _spice_state(b, et, physics, t)
    tau = np.linalg.norm(arrival - _spice_state(a, et, physics, t)[0]) / C
    for _ in range(10):
        tau = np.linalg.norm(arrival - _spice_state(a, et - tau, physics, t)[0]) / C
    origin, origin_velocity = _spice_state(a, et - tau, physics, t)
    path = arrival - origin
    return (
        float(tau),
        spiceypy.stlabx(path, origin_velocity),
        spiceypy.stelab(-path, arrival_velocity),
    )


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("t", INSTANTS)
def test_moving_endpoints_match_a_spice_state_reference(
    physics_context: PhysicsContext, t: float
) -> None:
    pairs = [
        (_orbit(LEO_A, "earth", t - 600.0), _orbit(LEO_B, "earth", t - 600.0)),
        (_orbit(LUNAR, "luna", t - 3600.0), GOLDSTONE_SITE),
        (GOLDSTONE_SITE, _orbit(LUNAR, "luna", t - 3600.0)),
    ]
    produced = signals_sent_at(
        physics_context, SHAPES, [a for a, _ in pairs], [b for _, b in pairs], t
    )
    for row, (a, b) in enumerate(pairs):
        tau, transmitter_pointing, receiver_pointing = _reference_sent(a, b, t, physics_context)
        assert abs(produced.light_time_s[row] - tau) < TOLERANCE_S
        assert (
            _angle(produced.transmitter.pointing[row], transmitter_pointing)
            < POINTING_TOLERANCE_RAD
        )
        assert _angle(produced.receiver.pointing[row], receiver_pointing) < POINTING_TOLERANCE_RAD


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("t", INSTANTS)
def test_moving_endpoints_received_match_a_spice_state_reference(
    physics_context: PhysicsContext, t: float
) -> None:
    """Reception time: the receiver at t, the transmitter when the signal left it."""
    pairs = [
        (_orbit(LEO_A, "earth", t - 600.0), _orbit(LEO_B, "earth", t - 600.0)),
        (_orbit(LUNAR, "luna", t - 3600.0), GOLDSTONE_SITE),
        (GOLDSTONE_SITE, _orbit(LUNAR, "luna", t - 3600.0)),
    ]
    produced = signals_received_at(
        physics_context, SHAPES, [a for a, _ in pairs], [b for _, b in pairs], t
    )
    for row, (a, b) in enumerate(pairs):
        tau, transmitter_pointing, receiver_pointing = _reference_received(a, b, t, physics_context)
        assert abs(produced.light_time_s[row] - tau) < TOLERANCE_S
        assert (
            _angle(produced.transmitter.pointing[row], transmitter_pointing)
            < POINTING_TOLERANCE_RAD
        )
        assert _angle(produced.receiver.pointing[row], receiver_pointing) < POINTING_TOLERANCE_RAD


@pytest.mark.parametrize("t", INSTANTS)
def test_the_two_time_indexes_describe_one_signal(
    physics_context: PhysicsContext, t: float
) -> None:
    """A signal sent at t and received at t + tau has reception-time light time tau, and both
    ends point the same way in either index."""
    sender, receiver = GOLDSTONE_SITE, _orbit(LUNAR, "luna", t - 3600.0)
    sent = signals_sent_at(physics_context, SHAPES, [sender], [receiver], t)
    received = signals_received_at(
        physics_context, SHAPES, [sender], [receiver], t + sent.light_time_s[0]
    )
    assert abs(received.light_time_s[0] - sent.light_time_s[0]) < 2e-9
    assert _angle(received.transmitter.pointing[0], sent.transmitter.pointing[0]) < 1e-9
    assert _angle(received.receiver.pointing[0], sent.receiver.pointing[0]) < 1e-9


def test_light_time_outside_the_data_coverage_is_refused(physics_context: PhysicsContext) -> None:
    beyond = datetime(2200, 1, 1, tzinfo=UTC).timestamp()
    with pytest.raises(PhysicsDataError, match="does not cover"):
        signals_sent_at(physics_context, SHAPES, [GOLDSTONE_SITE], [MOON_CENTRE], beyond)


@pytest.mark.parametrize(
    "start",
    [
        datetime(2026, 11, 3, 12, tzinfo=UTC).timestamp(),
        # The window holds the leap second at the end of 2016-12-31.
        datetime(2016, 12, 31, 23, 50, tzinfo=UTC).timestamp(),
    ],
)
def test_receiver_epochs_convert_to_master_sim_time_exactly(start: float) -> None:
    """Every epoch of a 1,400 s window (the longest Mars light time) converts as it would alone,
    to within two float64 steps of Unix seconds (2.4e-7 s each at 2026 dates)."""
    epochs = tdb_seconds_of(start) + np.linspace(0.0, 1400.0, 701)
    exact = np.array([unix_seconds_of_tdb(float(epoch)) for epoch in epochs])
    assert np.all(np.abs(_unix_seconds(epochs) - exact) <= 2 * np.spacing(exact))


@pytest.mark.usefixtures("spice_reference")
@pytest.mark.parametrize("t", INSTANTS)
def test_each_end_in_its_body_frame_matches_spice(
    physics_context: PhysicsContext, t: float
) -> None:
    """Goldstone sends to the Moon's center: each end's body-fixed position and pointing at its
    own epoch, against SPICE's apparent positions turned into ITRF93 and the lunar frame."""
    signals = signals_sent_at(physics_context, SHAPES, [GOLDSTONE_SITE], [MOON_CENTRE], t)
    sent, end = signals.transmitter, signals.receiver
    et = _et(t)
    arrival = et + signals.light_time_s[0]
    obspos = list(GOLDSTONE)
    up = spiceypy.spkcpo("MOON", et, "J2000", "OBSERVER", "XCN+S", obspos, "EARTH", "ITRF93")[0]
    down = spiceypy.spkcpt(obspos, "EARTH", "ITRF93", arrival, "J2000", "TARGET", "CN+S", "MOON")[0]
    assert np.linalg.norm(sent.fixed_km[0] - np.array(GOLDSTONE)) < 1e-6
    assert np.linalg.norm(end.fixed_km[0]) == 0.0
    to_itrf = np.array(spiceypy.pxform("J2000", "ITRF93", et))
    to_moon = np.array(spiceypy.pxform("J2000", SPICE_FRAME["luna"], arrival))
    assert _angle(sent.pointing_fixed[0], to_itrf @ np.array(up[:3])) < POINTING_TOLERANCE_RAD
    assert _angle(end.pointing_fixed[0], to_moon @ np.array(down[:3])) < POINTING_TOLERANCE_RAD


def _sight_direction(physics: PhysicsContext, a, b, t: float, frame_body: str | None) -> np.ndarray:
    """Unit line of sight from a (at t) to b (at arrival), on ICRF axes or in a body's frame."""
    signals = signals_sent_at(physics, SHAPES, [a], [b], t)
    sight = signals.receiver.position_km[0] - signals.transmitter.position_km[0]
    if frame_body is not None:
        rotations, _ = physics.motion.orientations(frame_body, signals.transmitter.epoch_tdb[:1])
        sight = rotations[0] @ sight
    return _unit(sight)


@pytest.mark.parametrize("t", INSTANTS)
def test_line_of_sight_rate_is_its_time_derivative(
    physics_context: PhysicsContext, t: float
) -> None:
    """The rate equals the central difference of the line of sight over 0.5 s, in the frame the
    transmitting end tracks in: a LEO pair in Earth's rotating frame, Goldstone tracking a lunar
    orbiter in Earth's rotating frame (a station turns with its body whatever it tracks), and the
    lunar orbiter tracking a LEO satellite of another body on inertial axes."""
    cases = [
        (_orbit(LEO_A, "earth", t - 600.0), _orbit(LEO_B, "earth", t - 600.0), "earth"),
        (GOLDSTONE_SITE, _orbit(LUNAR, "luna", t - 3600.0), "earth"),
        (_orbit(LUNAR, "luna", t - 3600.0), _orbit(LEO_A, "earth", t - 600.0), None),
    ]
    h = 0.25
    for a, b, frame_body in cases:
        rate = signals_sent_at(physics_context, SHAPES, [a], [b], t).transmitter.rate_rad_s[0]
        before = _sight_direction(physics_context, a, b, t - h, frame_body)
        after = _sight_direction(physics_context, a, b, t + h, frame_body)
        numerical = np.linalg.norm(after - before) / (2 * h)
        assert abs(rate - numerical) < 1e-4 * numerical


def test_ends_at_one_position_have_no_direction(physics_context: PhysicsContext) -> None:
    """Two identical orbits put two satellites at one position: the signal between them takes no
    time, and pointing and line-of-sight rate are zero, never NaN."""
    t = INSTANTS[0]
    first = _orbit(LEO_A, "earth", t - 600.0)
    twin = _orbit(LEO_A, "earth", t - 600.0)
    with np.errstate(all="raise"):
        signals = signals_sent_at(physics_context, SHAPES, [first, twin], [twin, first], t)
    assert np.all(signals.light_time_s == 0.0)
    for end in (signals.transmitter, signals.receiver):
        assert np.all(end.pointing == 0.0)
        assert np.all(end.pointing_fixed == 0.0)
        assert np.all(end.rate_rad_s == 0.0)
