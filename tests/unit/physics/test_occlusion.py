"""Link-path obstruction against SPICE's occult on reference cases.

SPICE's occult tests a point target behind an ellipsoid occulter as seen from an observer, with
light-time corrected positions (CN), the same geometry as a signal received at the observer. The
radii given to SPICE are the catalog radii (and the IAU nominal solar radius), so a difference is a
difference in computation. The Goldstone station and a lunar orbiter are written to a temporary
SPK: the station as a fixed point in ITRF93, the orbiter from the shared orbit model.

Each case scans a window, finds every change of state by bisection to 1e-4 s on both sides, and
compares the times within 1 ms. Measured worst differences: 229 us at Goldstone moonrise and
moonset (2 mm at the horizon's tangent point, 113 km from the station), 57 us for the lunar
eclipse, and none at the bisection's resolution for the lunar orbiter (6 changes), the solar
eclipse and Mercury behind the Sun.

The path is tested in each body's frame: an Earth-frame path to the Moon differs in direction
from the barycentric one by Earth's speed over c (4.2e-5 rad at Goldstone's moonrise), which
moves the moonrise by 0.35 s; SPICE's occult accounts for that motion as the module does.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import spiceypy
from anise.time import Epoch
from nodalarc.frames import GeoPosition
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import PhysicsContext, body_fixed_from_geodetic
from nodalarc.physics.context import tdb_seconds_of
from nodalarc.physics.ephemeris_table import SUN_ID, SUN_RADIUS_KM
from nodalarc.physics.kepler import ElementsBatch, orbit_states
from nodalarc.physics.lighttime import OrbitEndpoint, SiteEndpoint, signals_received_at
from nodalarc.physics.occlusion import PathEnds, obstructions

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME, LUNA_TEST_BODY_FRAME

C = 299792.458
SHAPES = {"earth": EARTH_TEST_BODY_FRAME, "luna": LUNA_TEST_BODY_FRAME}
TRANSITION_TOLERANCE_S = 1e-3
GOLDSTONE_ID = -399001
ORBITER_ID = -301001
GOLDSTONE_FIXED = tuple(
    float(v)
    for v in body_fixed_from_geodetic(GeoPosition(35.426, -116.89, 1.0), EARTH_TEST_BODY_FRAME)
)
GOLDSTONE = SiteEndpoint("earth", GOLDSTONE_FIXED)  # type: ignore[arg-type]
MOON_CENTRE = SiteEndpoint("luna", (0.0, 0.0, 0.0))
ORBIT_START = datetime(2026, 11, 3, 16, tzinfo=UTC).timestamp()
LOW_LUNAR_ORBIT = OrbitalElements(
    semi_major_axis_km=1737.4 + 100.0,
    inclination_rad=math.radians(90.0),
    raan_rad=math.radians(40.0),
    mean_anomaly_rad=0.0,
    eccentricity=0.0,
    argument_of_perigee_rad=0.0,
)
ORBITER = OrbitEndpoint("luna", LUNA_TEST_BODY_FRAME, "two-body", LOW_LUNAR_ORBIT, ORBIT_START)

# SPICE ellipsoids: catalog radii; the Sun's orientation only names a body-fixed frame for a sphere.
POOL = {
    "BODY399_RADII": [
        EARTH_TEST_BODY_FRAME.equatorial_radius_km,
        EARTH_TEST_BODY_FRAME.equatorial_radius_km,
        EARTH_TEST_BODY_FRAME.polar_radius_km,
    ],
    "BODY301_RADII": [
        LUNA_TEST_BODY_FRAME.equatorial_radius_km,
        LUNA_TEST_BODY_FRAME.equatorial_radius_km,
        LUNA_TEST_BODY_FRAME.polar_radius_km,
    ],
    "BODY10_RADII": [SUN_RADIUS_KM] * 3,
    "BODY10_POLE_RA": [286.13, 0.0, 0.0],
    "BODY10_POLE_DEC": [63.87, 0.0, 0.0],
    "BODY10_PM": [84.176, 14.1844, 0.0],
}


def _et(unix: float) -> float:
    return float(Epoch.init_from_unix_seconds(unix).to_et_seconds())


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


def _write_spk(path: Path, physics: PhysicsContext) -> None:
    first, last = _et(ORBIT_START), _et(ORBIT_START + 27 * 3600.0)
    handle = spiceypy.spkopn(str(path), "occultation reference", 0)
    station = [*GOLDSTONE_FIXED, 0.0, 0.0, 0.0]
    spiceypy.spkw08(
        handle,
        GOLDSTONE_ID,
        399,
        "ITRF93",
        first,
        last,
        "GOLDSTONE",
        1,
        2,
        [station, station],
        first,
        last - first,
    )
    epochs = np.arange(first, last + 10.0, 10.0)
    phases = np.array(
        [Epoch.init_from_et_seconds(float(e)).to_unix_seconds() - ORBIT_START for e in epochs]
    )
    states = orbit_states(
        ElementsBatch.from_elements([LOW_LUNAR_ORBIT]),
        phases[None, :],
        model="two-body",
        body_frame=LUNA_TEST_BODY_FRAME,
    )
    basis = physics.orbit_basis("luna")
    positions = np.stack([states.px[0], states.py[0], states.pz[0]], axis=1) @ basis
    velocities = np.stack([states.vx[0], states.vy[0], states.vz[0]], axis=1) @ basis
    spiceypy.spkw13(
        handle,
        ORBITER_ID,
        301,
        "J2000",
        float(epochs[0]),
        float(epochs[-1]),
        "ORBITER",
        7,
        len(epochs),
        np.hstack([positions, velocities]).tolist(),
        epochs.tolist(),
    )
    spiceypy.spkcls(handle)


@pytest.fixture(scope="module")
def occult_reference(
    spice_reference: None,
    physics_context: PhysicsContext,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    path = tmp_path_factory.mktemp("occult") / "endpoints.bsp"
    _write_spk(path, physics_context)
    spiceypy.furnsh(str(path))
    for name, values in POOL.items():
        spiceypy.pdpool(name, values)
    yield
    for name in POOL:
        spiceypy.dvpool(name)
    spiceypy.unload(str(path))


def _transitions(
    state: Callable[[float], bool], start: float, end: float, step: float
) -> list[float]:
    """Times where ``state`` changes in [start, end], found on a grid and bisected to 1e-4 s."""
    times = np.arange(start, end, step)
    values = [state(float(t)) for t in times]
    found = []
    for k in range(len(times) - 1):
        if values[k] != values[k + 1]:
            low, high = float(times[k]), float(times[k + 1])
            while high - low > 1e-4:
                middle = (low + high) / 2
                if state(middle) == values[k]:
                    low = middle
                else:
                    high = middle
            found.append((low + high) / 2)
    return found


def _compare(
    produced: Callable[[float], bool],
    reference: Callable[[float], bool],
    start: float,
    end: float,
    step: float,
) -> None:
    ours = _transitions(produced, start, end, step)
    theirs = _transitions(reference, start, end, step)
    assert len(theirs) >= 2, "the reference window holds no occultation"
    assert len(ours) == len(theirs)
    for a, b in zip(ours, theirs, strict=True):
        assert abs(a - b) < TRANSITION_TOLERANCE_S


def _spice_occulted(
    target: str, occulter: str, frame: str, observer: str
) -> Callable[[float], bool]:
    def occulted(unix: float) -> bool:
        code = spiceypy.occult(
            target, "POINT", " ", occulter, "ELLIPSOID", frame, "CN", observer, _et(unix)
        )
        return code < 0

    return occulted


@pytest.mark.usefixtures("occult_reference")
def test_earth_blocks_the_moon_below_goldstones_horizon(physics_context: PhysicsContext) -> None:
    def produced(unix: float) -> bool:
        signals = signals_received_at(physics_context, SHAPES, [MOON_CENTRE], [GOLDSTONE], unix)
        return bool(signals.occluded[0, signals.occulters.index("earth")])

    reference = _spice_occulted("MOON", "EARTH", "ITRF93", str(GOLDSTONE_ID))
    _compare(produced, reference, ORBIT_START, ORBIT_START + 26 * 3600.0, 120.0)


@pytest.mark.usefixtures("occult_reference")
def test_the_moon_blocks_an_orbiter_on_its_far_side(physics_context: PhysicsContext) -> None:
    def produced(unix: float) -> bool:
        signals = signals_received_at(physics_context, SHAPES, [ORBITER], [GOLDSTONE], unix)
        return bool(signals.occluded[0, signals.occulters.index("luna")])

    reference = _spice_occulted(str(ORBITER_ID), "MOON", "MOON_ME_DE440_ME421", str(GOLDSTONE_ID))
    _compare(produced, reference, ORBIT_START + 600.0, ORBIT_START + 6 * 3600.0, 30.0)


def _received_from(
    physics: PhysicsContext,
    far: Callable[[float], np.ndarray],
    far_body: str,
    near_body: str,
    unix: float,
) -> tuple[tuple[str, ...], np.ndarray]:
    """Obstruction of the path from a far point to a body's center, received at ``unix``."""
    received = tdb_seconds_of(unix)
    near, _ = physics.motion.states(near_body, np.array([received]))
    tau = 0.0
    for _ in range(10):
        tau = float(np.linalg.norm(near[0] - far(received - tau)) / C)
    origin = far(received - tau)
    return obstructions(
        physics,
        SHAPES,
        PathEnds(
            np.array([far_body], dtype=object),
            np.zeros((1, 3)),
            origin[None, :],
            np.array([received - tau]),
        ),
        PathEnds(np.array([near_body], dtype=object), np.zeros((1, 3)), near, np.array([received])),
    )


def _sun(physics: PhysicsContext) -> Callable[[float], np.ndarray]:
    return lambda tdb: physics.motion.states(SUN_ID, np.array([tdb]))[0][0]


def _column(physics: PhysicsContext, far, far_body: str, near_body: str, body: str):
    def produced(unix: float) -> bool:
        occulters, blocked = _received_from(physics, far, far_body, near_body, unix)
        return bool(blocked[0, occulters.index(body)])

    return produced


@pytest.mark.usefixtures("occult_reference")
def test_earth_blocks_the_sun_seen_from_the_moon_in_a_lunar_eclipse(
    physics_context: PhysicsContext,
) -> None:
    produced = _column(physics_context, _sun(physics_context), SUN_ID, "luna", "earth")
    reference = _spice_occulted("SUN", "EARTH", "ITRF93", "MOON")
    _compare(produced, reference, _utc(2026, 3, 3, 9), _utc(2026, 3, 3, 14), 60.0)


@pytest.mark.usefixtures("occult_reference")
def test_the_moon_blocks_the_sun_seen_from_earths_center_in_a_solar_eclipse(
    physics_context: PhysicsContext,
) -> None:
    produced = _column(physics_context, _sun(physics_context), SUN_ID, "earth", "luna")
    reference = _spice_occulted("SUN", "MOON", "MOON_ME_DE440_ME421", "EARTH")
    _compare(produced, reference, _utc(2027, 8, 2, 8), _utc(2027, 8, 2, 12), 30.0)


@pytest.mark.usefixtures("occult_reference")
def test_the_solar_disk_blocks_mercury_seen_from_earth(physics_context: PhysicsContext) -> None:
    def mercury(tdb: float) -> np.ndarray:
        et = float(Epoch.init_from_tdb_seconds(tdb).to_et_seconds())
        return np.array(spiceypy.spkezr("MERCURY BARYCENTER", et, "J2000", "NONE", "SSB")[0][:3])

    produced = _column(physics_context, mercury, "mercury", "earth", SUN_ID)
    reference = _spice_occulted("MERCURY BARYCENTER", "SUN", "IAU_SUN", "EARTH")
    _compare(produced, reference, _utc(2026, 5, 14, 6), _utc(2026, 5, 14, 22), 300.0)


def test_a_site_at_zero_altitude_sees_its_whole_sky(physics_context: PhysicsContext) -> None:
    """A site on the ellipsoid is outside its own body: a target overhead is not blocked."""
    site = SiteEndpoint(
        "earth",
        tuple(
            float(v)
            for v in body_fixed_from_geodetic(GeoPosition(0.0, 0.0, 0.0), EARTH_TEST_BODY_FRAME)
        ),  # type: ignore[arg-type]
    )
    overhead = SiteEndpoint("earth", (42164.0, 0.0, 0.0))
    for hour in range(0, 24, 3):
        signals = signals_received_at(
            physics_context, SHAPES, [overhead], [site], _utc(2026, 11, 3, hour)
        )
        assert not signals.occluded[0, signals.occulters.index("earth")]
