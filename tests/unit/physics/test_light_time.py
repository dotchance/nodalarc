"""Light time in both time indexes against SPICE, with the platform's own data files.

Body-pair references come from SPICE's light-time corrections: XCN for a signal transmitted at
the epoch, CN for one received at it (spkcpo / spkcpt, a fixed site on Earth as one end). Moving
satellite endpoints have no SPICE object, so their reference iterates on SPICE barycentric body
states plus the same orbit model. Tolerance: 1 microsecond. Measured worst
differences: 0.1 ps for the body pairs, 4.8 ps for the moving endpoints, and 0.24 ps between the
two time indexes of one signal.
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
    reception_light_times,
    transmission_light_times,
)

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME, LUNA_TEST_BODY_FRAME

C = 299792.458
TOLERANCE_S = 1e-6
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


def _spice_barycentric(endpoint, et: float, physics: PhysicsContext, t0: float) -> np.ndarray:
    """An endpoint's barycentric position from SPICE body states plus the shared orbit model."""
    body = endpoint.body_id
    centre, _ = spiceypy.spkezr("EARTH" if body == "earth" else "MOON", et, "J2000", "NONE", "SSB")
    if isinstance(endpoint, SiteEndpoint):
        frame = "ITRF93" if body == "earth" else "MOON_ME_DE440_ME421"
        return np.array(centre[:3]) + np.array(spiceypy.pxform(frame, "J2000", et)) @ np.array(
            endpoint.fixed_km
        )
    # Satellite phase on master sim time (et seconds map to Unix seconds through ANISE).
    unix = t0 + (et - _et(t0))
    states = orbit_states(
        ElementsBatch.from_elements([endpoint.elements]),
        np.array([[unix - endpoint.elements_epoch_unix]]),
        model="two-body",
        body_frame=endpoint.body_frame,
    )
    in_basis = np.array([states.px[0, 0], states.py[0, 0], states.pz[0, 0]])
    return np.array(centre[:3]) + in_basis @ physics.orbit_basis(body)


def _reference_transmission(a, b, t: float, physics) -> float:
    et = _et(t)
    origin = _spice_barycentric(a, et, physics, t)
    tau = np.linalg.norm(_spice_barycentric(b, et, physics, t) - origin) / C
    for _ in range(10):
        tau = np.linalg.norm(_spice_barycentric(b, et + tau, physics, t) - origin) / C
    return float(tau)


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

    up = transmission_light_times(physics_context, [GOLDSTONE_SITE], [MOON_CENTRE], t)[0]
    down = reception_light_times(physics_context, [MOON_CENTRE], [GOLDSTONE_SITE], t)[0]
    moon_tx = transmission_light_times(physics_context, [MOON_CENTRE], [GOLDSTONE_SITE], t)[0]

    assert abs(up - up_ref) < TOLERANCE_S
    assert abs(down - down_ref) < TOLERANCE_S
    assert abs(moon_tx - moon_tx_ref) < TOLERANCE_S


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
    produced = transmission_light_times(
        physics_context, [a for a, _ in pairs], [b for _, b in pairs], t
    )
    for (a, b), tau in zip(pairs, produced, strict=True):
        assert abs(tau - _reference_transmission(a, b, t, physics_context)) < TOLERANCE_S


@pytest.mark.parametrize("t", INSTANTS)
def test_the_two_time_indexes_describe_one_signal(
    physics_context: PhysicsContext, t: float
) -> None:
    """A signal sent at t and received at t + tau has reception-time light time tau."""
    sender, receiver = GOLDSTONE_SITE, _orbit(LUNAR, "luna", t - 3600.0)
    sent = transmission_light_times(physics_context, [sender], [receiver], t)[0]
    received = reception_light_times(physics_context, [sender], [receiver], t + sent)[0]
    assert abs(received - sent) < 2e-9


def test_light_time_outside_the_data_coverage_is_refused(physics_context: PhysicsContext) -> None:
    beyond = datetime(2200, 1, 1, tzinfo=UTC).timestamp()
    with pytest.raises(PhysicsDataError, match="does not cover"):
        transmission_light_times(physics_context, [GOLDSTONE_SITE], [MOON_CENTRE], beyond)


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
