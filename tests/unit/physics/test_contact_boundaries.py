# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Contact opening and closing times of OME's link decisions against SPICE, with both endpoints
moving.

OME decides a link once per tick from two signals sent at the tick: each direction's transmitter
at the tick and its receiver when the signal arrives, both pointing along the apparent direction.
These tests run the decisions OME runs (the ground geometry shared by the tick, the pass lookahead
and the Builder preview, and the ISL checks) on a grid, find every change by bisection to 1e-4 s,
and compare the times with the same decision computed by SPICE: endpoints written to a temporary
SPK, light time and stellar aberration from SPICE's XCN+S and CN+S corrections, line of sight from
SPICE's occult with the catalog radii.

The acceptance tolerance for a contact boundary is one step; these compare within 1 ms. Measured:
OME and SPICE land in the same 0.1 ms bisection interval at every change for the ground pair (4)
and the Earth-Moon pair (22), and within 57 us for Goldstone and the lunar orbiter (5 changes). The tolerance tells the corrections apart: a reference with geometric
directions moves the ground changes by up to 45 ms, one with no correction by up to 6 ms, and
evaluating both ends of the Earth-Moon link at one instant moves its changes by up to 1.32 s.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import numpy as np
import pytest
import spiceypy
from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import PhysicsContext, body_fixed_from_geodetic
from nodalarc.physics.ephemeris_table import SUN_RADIUS_KM
from nodalarc.physics.lighttime import OrbitEndpoint
from ome.direction_checks import DirectionEnds, IslLimits, PairSignals, isl_reasons
from ome.ground_visibility_engine import GroundGeometry

from tests.physics_fixtures import (
    EARTH_TEST_BODY_FRAME,
    LUNA_TEST_BODY_FRAME,
    earth_elements_from_params,
)
from tests.spice_endpoints import et_of, write_endpoint_spk

SHAPES = {"earth": EARTH_TEST_BODY_FRAME, "luna": LUNA_TEST_BODY_FRAME}
TOLERANCE_S = 1e-3
START = datetime(2026, 11, 3, 16, tzinfo=UTC).timestamp()
HOURS = 12.0
GOLDSTONE_ID, LEO_ID, ORBITER_ID = -399001, -399101, -301001
GOLDSTONE_GEO = GeoPosition(35.426, -116.89, 1.0)
GOLDSTONE_FIXED = body_fixed_from_geodetic(GOLDSTONE_GEO, EARTH_TEST_BODY_FRAME)
MASK_DEG = 10.0
LEO = OrbitEndpoint(
    "earth",
    EARTH_TEST_BODY_FRAME,
    "two-body",
    earth_elements_from_params(550.0, 53.0, 240.0, 0.0),
    START,
)
ORBITER = OrbitEndpoint(
    "luna",
    LUNA_TEST_BODY_FRAME,
    "two-body",
    OrbitalElements(
        semi_major_axis_km=1737.4 + 100.0,
        inclination_rad=math.radians(90.0),
        raan_rad=math.radians(40.0),
        mean_anomaly_rad=0.0,
        eccentricity=0.0,
        argument_of_perigee_rad=0.0,
    ),
    START,
)
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
OCCULTERS = (("EARTH", "ITRF93"), ("MOON", "MOON_ME_DE440_ME421"), ("SUN", "IAU_SUN"))


@pytest.fixture(scope="module")
def endpoints_in_spice(
    spice_reference: None,
    physics_context: PhysicsContext,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    path = tmp_path_factory.mktemp("contacts") / "endpoints.bsp"
    write_endpoint_spk(
        path,
        physics_context,
        start_unix=START - 600.0,
        end_unix=START + HOURS * 3600.0 + 600.0,
        sites={
            GOLDSTONE_ID: (
                399,
                "ITRF93",
                (GOLDSTONE_FIXED.x, GOLDSTONE_FIXED.y, GOLDSTONE_FIXED.z),
            )
        },
        orbits={LEO_ID: (399, LEO), ORBITER_ID: (301, ORBITER)},
    )
    spiceypy.furnsh(str(path))
    for name, values in POOL.items():
        spiceypy.pdpool(name, values)
    yield
    for name in POOL:
        spiceypy.dvpool(name)
    spiceypy.unload(str(path))


def _changes(open_at: Callable[[float], bool], step_s: float) -> list[float]:
    """Times where ``open_at`` changes over the window, found on a grid and bisected to 1e-4 s."""
    times = np.arange(START, START + HOURS * 3600.0, step_s)
    values = [open_at(float(t)) for t in times]
    found = []
    for k in range(len(times) - 1):
        if values[k] != values[k + 1]:
            low, high = float(times[k]), float(times[k + 1])
            while high - low > 1e-4:
                middle = (low + high) / 2
                if open_at(middle) == values[k]:
                    low = middle
                else:
                    high = middle
            found.append((low + high) / 2)
    return found


def _assert_same_changes(produced: list[float], reference: list[float]) -> None:
    assert len(reference) >= 4, "the window holds fewer than two contacts"
    assert len(produced) == len(reference), (produced, reference)
    worst = max(abs(a - b) for a, b in zip(produced, reference, strict=True))
    assert worst < TOLERANCE_S, f"a contact boundary differs by {worst:.6f} s"


def _station_elevation_deg(direction_j2000: np.ndarray, et: float) -> float:
    """Elevation of a J2000 direction above Goldstone's geodetic horizon at ``et``."""
    fixed = np.array(spiceypy.pxform("J2000", "ITRF93", et)) @ direction_j2000
    up = np.array(
        spiceypy.latrec(
            1.0, math.radians(GOLDSTONE_GEO.lon_deg), math.radians(GOLDSTONE_GEO.lat_deg)
        )
    )
    return math.degrees(math.asin(float(fixed @ up / np.linalg.norm(fixed))))


@pytest.mark.usefixtures("endpoints_in_spice")
def test_a_ground_contact_opens_and_closes_when_spice_says(
    physics_context: PhysicsContext,
) -> None:
    """Goldstone and a satellite 550 km up: open while both directions clear the 10 degree mask,
    the uplink at the station when it transmits, the downlink at the station when it receives."""
    geometry = GroundGeometry(
        physics=physics_context,
        body_frames=SHAPES,
        gs_positions={"gs": (EcefVec3(GOLDSTONE_FIXED), GOLDSTONE_GEO)},
        gs_min_elevations={"gs": MASK_DEG},
        gs_reference_bodies={"gs": "earth"},
        ground_link_model="geometry_only",
        gs_terminal_profiles=None,
        sat_ground_terminal_profiles=None,
        pairs=[("gs", "sat")],
    )
    endpoints = {"gs": geometry.station_endpoints["gs"], "sat": LEO}

    def produced(t: float) -> bool:
        signals = PairSignals.sent_at(physics_context, SHAPES, endpoints, [("gs", "sat")], t)
        uplink, downlink = signals.directions([("gs", "sat")])
        return geometry.verdicts([("gs", "sat")], uplink, downlink)[0].visible

    def reference(t: float) -> bool:
        et = et_of(t)
        uplink, _ = spiceypy.spkpos(str(LEO_ID), et, "J2000", "XCN+S", str(GOLDSTONE_ID))
        _, tau = spiceypy.spkpos(str(GOLDSTONE_ID), et, "J2000", "XCN", str(LEO_ID))
        downlink, _ = spiceypy.spkpos(str(LEO_ID), et + tau, "J2000", "CN+S", str(GOLDSTONE_ID))
        return (
            min(
                _station_elevation_deg(np.array(uplink), et),
                _station_elevation_deg(np.array(downlink), et + tau),
            )
            >= MASK_DEG
        )

    _assert_same_changes(_changes(produced, 30.0), _changes(reference, 30.0))


@pytest.mark.usefixtures("endpoints_in_spice")
def test_an_earth_moon_link_opens_and_closes_when_spice_says(
    physics_context: PhysicsContext,
) -> None:
    """A satellite 550 km above Earth and one 100 km above the Moon: open while no body blocks
    either direction, each received at its arrival time."""
    endpoints = {"leo": LEO, "orbiter": ORBITER}
    unlimited = [IslLimits(math.inf, 360.0, None, polar_seam=False)]

    def produced(t: float) -> bool:
        signals = PairSignals.sent_at(physics_context, SHAPES, endpoints, [("leo", "orbiter")], t)
        for direction in signals.directions([("leo", "orbiter")]):
            reasons = isl_reasons(
                DirectionEnds.from_signals(direction),
                unlimited,
                transmitter_latitude_deg=np.zeros(1),
                receiver_latitude_deg=np.zeros(1),
                latitude_threshold_deg=90.0,
            )
            if reasons[0] != "ok":
                return False
        return True

    def reference(t: float) -> bool:
        et = et_of(t)
        for sender, receiver in ((LEO_ID, ORBITER_ID), (ORBITER_ID, LEO_ID)):
            _, tau = spiceypy.spkpos(str(receiver), et, "J2000", "XCN", str(sender))
            for body, frame in OCCULTERS:
                code = spiceypy.occult(
                    str(sender), "POINT", " ", body, "ELLIPSOID", frame, "CN", str(receiver),
                    et + tau,
                )  # fmt: skip
                if code < 0:
                    return False
        return True

    _assert_same_changes(_changes(produced, 30.0), _changes(reference, 30.0))


@pytest.mark.usefixtures("endpoints_in_spice")
def test_an_earth_station_contact_with_a_lunar_orbiter_opens_and_closes_when_spice_says(
    physics_context: PhysicsContext,
) -> None:
    """Goldstone and an orbiter 100 km above the Moon, a ground pair across two bodies: open while
    both directions clear Goldstone's 10 degree mask and no body blocks either direction. The
    window holds moonrise, moonset and the orbiter's passes behind the Moon."""
    geometry = GroundGeometry(
        physics=physics_context,
        body_frames=SHAPES,
        gs_positions={"gs": (EcefVec3(GOLDSTONE_FIXED), GOLDSTONE_GEO)},
        gs_min_elevations={"gs": MASK_DEG},
        gs_reference_bodies={"gs": "earth"},
        ground_link_model="geometry_only",
        gs_terminal_profiles=None,
        sat_ground_terminal_profiles=None,
        pairs=[("gs", "orbiter")],
    )
    endpoints = {"gs": geometry.station_endpoints["gs"], "orbiter": ORBITER}

    def produced(t: float) -> bool:
        signals = PairSignals.sent_at(physics_context, SHAPES, endpoints, [("gs", "orbiter")], t)
        uplink, downlink = signals.directions([("gs", "orbiter")])
        return geometry.verdicts([("gs", "orbiter")], uplink, downlink)[0].visible

    def clear(sender: int, receiver: int, received_et: float) -> bool:
        return all(
            spiceypy.occult(
                str(sender),
                "POINT",
                " ",
                body,
                "ELLIPSOID",
                frame,
                "CN",
                str(receiver),
                received_et,
            )  # fmt: skip
            >= 0
            for body, frame in OCCULTERS
        )

    def reference(t: float) -> bool:
        et = et_of(t)
        uplink, up_tau = spiceypy.spkpos(str(ORBITER_ID), et, "J2000", "XCN+S", str(GOLDSTONE_ID))
        _, down_tau = spiceypy.spkpos(str(GOLDSTONE_ID), et, "J2000", "XCN", str(ORBITER_ID))
        downlink, _ = spiceypy.spkpos(
            str(ORBITER_ID), et + down_tau, "J2000", "CN+S", str(GOLDSTONE_ID)
        )
        return (
            min(
                _station_elevation_deg(np.array(uplink), et),
                _station_elevation_deg(np.array(downlink), et + down_tau),
            )
            >= MASK_DEG
            and clear(GOLDSTONE_ID, ORBITER_ID, et + up_tau)
            and clear(ORBITER_ID, GOLDSTONE_ID, et + down_tau)
        )

    _assert_same_changes(_changes(produced, 30.0), _changes(reference, 30.0))
