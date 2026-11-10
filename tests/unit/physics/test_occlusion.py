# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
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

import numpy as np
import pytest
import spiceypy
from anise.time import Epoch
from nodalarc.frames import GeoPosition
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import PhysicsContext, body_fixed_from_geodetic
from nodalarc.physics.context import tdb_seconds_of
from nodalarc.physics.ephemeris_table import SUN_ID, SUN_RADIUS_KM
from nodalarc.physics.lighttime import (
    Endpoint,
    OrbitEndpoint,
    SiteEndpoint,
    signals_received_at,
    signals_sent_at,
)
from nodalarc.physics.occlusion import PathEnds, obstructions
from ome.direction_checks import (
    DirectionEnds,
    GroundLimits,
    GroundLimitTable,
    GroundPairVerdict,
    ground_directions,
    ground_pair_verdicts,
)

from tests.physics_fixtures import (
    EARTH_TEST_BODY_FRAME,
    LUNA_TEST_BODY_FRAME,
    earth_elements_from_params,
)
from tests.spice_endpoints import write_endpoint_spk

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


@pytest.fixture(scope="module")
def occult_reference(
    spice_reference: None,
    physics_context: PhysicsContext,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    path = tmp_path_factory.mktemp("occult") / "endpoints.bsp"
    write_endpoint_spk(
        path,
        physics_context,
        start_unix=ORBIT_START,
        end_unix=ORBIT_START + 27 * 3600.0,
        sites={GOLDSTONE_ID: (399, "ITRF93", GOLDSTONE_FIXED)},
        orbits={ORBITER_ID: (301, ORBITER)},
    )
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


# Line of sight at the geometries of OME's link-constraint tests, from real endpoints: each signal
# leaves its transmitter at SENT and reaches its receiver one light time later. A satellite held at
# a fixed point above a body is a site at that altitude.

SENT = _utc(2026, 11, 3, 12)
LUNAR_POLE_SITE = GeoPosition(-89.4, 30.0, 0.0)


def _site(body: str, geo: GeoPosition) -> SiteEndpoint:
    fixed = body_fixed_from_geodetic(geo, SHAPES[body])
    return SiteEndpoint(body, (fixed.x, fixed.y, fixed.z))


def _leo(true_anomaly_deg: float) -> OrbitEndpoint:
    """A satellite 550 km up on a 53-degree orbit, at the given anomaly at SENT."""
    elements = earth_elements_from_params(550.0, 53.0, 0.0, true_anomaly_deg)
    return OrbitEndpoint("earth", EARTH_TEST_BODY_FRAME, "two-body", elements, SENT)


def _blocked_both_ways(
    physics: PhysicsContext, a: Endpoint, b: Endpoint, body: str, sent: float = SENT
) -> list[bool]:
    """Whether ``body`` blocks the signal a sends to b, and the one b sends to a, at ``sent``."""
    signals = signals_sent_at(physics, SHAPES, [a, b], [b, a], sent)
    return [bool(blocked) for blocked in signals.occluded[:, signals.occulters.index(body)]]


def _ground_verdict(
    physics: PhysicsContext,
    station: GeoPosition,
    body: str,
    satellite: Endpoint,
    min_elevation_deg: float,
    sent: float = SENT,
) -> GroundPairVerdict:
    """OME's checks on a ground candidate with no terminal limits: the station's signal to the
    satellite (uplink) and the satellite's to the station (downlink), both sent at ``sent``."""
    station_end = _site(body, station)
    signals = signals_sent_at(
        physics, SHAPES, [station_end, satellite], [satellite, station_end], sent
    )
    ends = DirectionEnds.from_signals(signals)
    table = GroundLimitTable.from_limits(
        [GroundLimits(station.lat_deg, station.lon_deg, min_elevation_deg)]
    )
    up, down = np.array([0]), np.array([1])
    uplink = ground_directions(
        station=ends.transmitter.take(up),
        satellite=ends.receiver.take(up),
        path_km=ends.path_km[up],
        blocked=ends.blocked[up],
        table=table,
    )
    downlink = ground_directions(
        station=ends.receiver.take(down),
        satellite=ends.transmitter.take(down),
        path_km=ends.path_km[down],
        blocked=ends.blocked[down],
        table=table,
    )
    (verdict,) = ground_pair_verdicts(uplink, downlink)
    return verdict


def test_satellites_36_degrees_apart_in_one_plane_see_each_other(
    physics_context: PhysicsContext,
) -> None:
    assert _blocked_both_ways(physics_context, _leo(0.0), _leo(36.0), "earth") == [False, False]


def test_earth_blocks_satellites_on_opposite_sides_of_it(physics_context: PhysicsContext) -> None:
    assert _blocked_both_ways(physics_context, _leo(0.0), _leo(180.0), "earth") == [True, True]


def test_a_station_sees_a_satellite_near_its_horizon(physics_context: PhysicsContext) -> None:
    """A satellite 550 km up over latitude 10 degrees, about 20 degrees above the horizon of a
    station on the equator."""
    station = _site("earth", GeoPosition(0.0, 0.0, 0.0))
    satellite = _site("earth", GeoPosition(10.0, 0.0, 550.0))
    assert _blocked_both_ways(physics_context, station, satellite, "earth") == [False, False]


def test_a_satellite_behind_earth_fails_line_of_sight_before_the_elevation_mask(
    physics_context: PhysicsContext,
) -> None:
    """A satellite over the station's antipode: the ground decision is a line-of-sight
    rejection, with the -90 degree elevation sentinel, although the elevation is also below the
    mask."""
    verdict = _ground_verdict(
        physics_context,
        GeoPosition(0.0, 0.0, 0.0),
        "earth",
        _site("earth", GeoPosition(0.0, 180.0, 550.0)),
        min_elevation_deg=25.0,
    )
    assert not verdict.visible
    assert verdict.reason == "los_blocked"
    assert verdict.elevation_deg == -90.0


@pytest.mark.parametrize("body", ["earth", "luna"])
def test_the_analytic_tangent_point_is_clear_of_the_body(
    physics_context: PhysicsContext, body: str
) -> None:
    """A target 550 km up on the horizon plane of an observer standing on the equator: the path
    touches the ellipsoid only at the observer. The claim is about one instant's geometry, so
    both ends are taken at one epoch; over a signal's light time the body turns under the path."""
    radius = SHAPES[body].equatorial_radius_km
    orbit_radius = radius + 550.0
    angle = math.acos(radius / orbit_radius)
    observer = np.array([radius, 0.0, 0.0])
    target = np.array([orbit_radius * math.cos(angle), orbit_radius * math.sin(angle), 0.0])
    epoch = np.array([tdb_seconds_of(SENT)])
    rotations, _ = physics_context.motion.orientations(body, epoch)
    centre, _ = physics_context.motion.states(body, epoch)

    def end(fixed_km: np.ndarray) -> PathEnds:
        relative = rotations[0].T @ fixed_km
        return PathEnds(np.array([body], dtype=object), relative[None, :], centre + relative, epoch)

    occulters, blocked = obstructions(physics_context, SHAPES, end(observer), end(target))
    assert not blocked[0, occulters.index(body)]


def test_a_lunar_polar_site_at_zero_altitude_sees_its_zenith(
    physics_context: PhysicsContext,
) -> None:
    """A point on the surface is outside its body, so a satellite 100 km overhead is not
    blocked. The site at latitude -89.4 converts from geodetic coordinates to a point one
    rounding step inside the lunar ellipsoid (normalized radius squared 1 - 1.1e-16); turned
    by the Moon's orientation, it lands inside or outside by rounding depending on the epoch,
    and inside at some of these hours."""
    site = _site("luna", LUNAR_POLE_SITE)
    zenith = _site("luna", GeoPosition(-89.4, 30.0, 100.0))
    for hour in range(0, 24, 3):
        sent = _utc(2026, 11, 3, hour)
        assert _blocked_both_ways(physics_context, site, zenith, "luna", sent) == [False, False]


def test_a_lunar_polar_site_at_zero_altitude_accepts_a_zenith_pass(
    physics_context: PhysicsContext,
) -> None:
    zenith = _site("luna", GeoPosition(-89.4, 30.0, 100.0))
    for hour in range(0, 24, 3):
        verdict = _ground_verdict(
            physics_context,
            LUNAR_POLE_SITE,
            "luna",
            zenith,
            min_elevation_deg=10.0,
            sent=_utc(2026, 11, 3, hour),
        )
        assert verdict.visible, f"zenith pass rejected at hour {hour}: {verdict.reason}"
        assert verdict.elevation_deg > 80.0


def test_the_moon_blocks_a_path_from_its_south_polar_site_to_its_north_pole(
    physics_context: PhysicsContext,
) -> None:
    """The surface tolerance does not disable occlusion: the path to a satellite over the
    opposite pole crosses the Moon."""
    site = _site("luna", LUNAR_POLE_SITE)
    far_side = _site("luna", GeoPosition(89.4, 30.0, 100.0))
    assert _blocked_both_ways(physics_context, site, far_side, "luna") == [True, True]
