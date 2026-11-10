# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""A ground pair's tracking check judges each terminal by its own rate and its own limit.

A station turns with its body while it holds a peer; an orbiter of another body holds the station
with an inertial rate, so the two ends of an Earth-Mars pair slew at very different rates. Each
terminal declares its own limit, and each end is within it or not on its own account.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest
from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.ground_terminals import TerminalPhysicsProfile
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import PhysicsContext, body_fixed_from_geodetic
from nodalarc.physics.lighttime import OrbitEndpoint
from ome.direction_checks import (
    DirectionEnds,
    EndArrays,
    GroundLimits,
    GroundLimitTable,
    PairSignals,
    ground_directions,
)
from ome.ground_visibility_engine import GroundGeometry

from tests.physics_fixtures import EARTH_TEST_BODY_FRAME, body_frame_from_catalog, physics_for

MARS_TEST_BODY_FRAME = body_frame_from_catalog("mars")
SHAPES = {"earth": EARTH_TEST_BODY_FRAME, "mars": MARS_TEST_BODY_FRAME}
GOLDSTONE_GEO = GeoPosition(35.426, -116.89, 1.0)
GOLDSTONE_FIXED = body_fixed_from_geodetic(GOLDSTONE_GEO, EARTH_TEST_BODY_FRAME)
# 2027-02-19, the 2027 opposition: Mars is up at night over Goldstone.
START = datetime(2027, 2, 19, tzinfo=UTC).timestamp()
STATION_LIMIT_DEG_S = 1.0
ORBITER_LIMIT_DEG_S = 0.001
MARS_ORBITER = OrbitEndpoint(
    "mars",
    MARS_TEST_BODY_FRAME,
    "two-body",
    OrbitalElements(
        semi_major_axis_km=20427.69,
        inclination_rad=0.0,
        raan_rad=0.0,
        mean_anomaly_rad=0.0,
        eccentricity=0.0,
        argument_of_perigee_rad=0.0,
    ),
    START,
)


def _profiles() -> tuple[TerminalPhysicsProfile, TerminalPhysicsProfile]:
    """Terminals that reach across the Earth-Mars distance; only their tracking limits differ."""
    station = TerminalPhysicsProfile(
        profile_id="goldstone.deep_space",
        max_range_km=5.0e8,
        field_of_regard_deg=180.0,
        max_tracking_rate_deg_s=STATION_LIMIT_DEG_S,
        boresight=TerminalBoresight(mode="local_vertical"),
    )
    orbiter = TerminalPhysicsProfile(
        profile_id="mars-orbiter.earth_link",
        max_range_km=5.0e8,
        field_of_regard_deg=180.0,
        max_tracking_rate_deg_s=ORBITER_LIMIT_DEG_S,
        boresight=SatGroundTerminalBoresight(target_body="earth", mode="nadir"),
        target_body="earth",
    )
    return station, orbiter


@pytest.fixture(scope="module")
def earth_mars() -> PhysicsContext:
    return physics_for("earth", "mars")


def test_an_earth_station_and_a_mars_orbiter_each_within_its_own_limit_are_in_contact(
    earth_mars: PhysicsContext,
) -> None:
    """Goldstone and an orbiter of Mars over a day at the 2027 opposition, through the production
    ground geometry and signals. Wherever the station must slew faster than the orbiter's limit
    but within its own, and the orbiter within its own, the contact opens; holding the station to
    the orbiter's limit would reject every one of those samples."""
    station_profile, orbiter_profile = _profiles()
    geometry = GroundGeometry(
        physics=earth_mars,
        body_frames=SHAPES,
        gs_positions={"gs": (EcefVec3(GOLDSTONE_FIXED), GOLDSTONE_GEO)},
        gs_min_elevations={"gs": 10.0},
        gs_reference_bodies={"gs": "earth"},
        ground_link_model="terminal_physics",
        gs_terminal_profiles={"gs": station_profile},
        sat_ground_terminal_profiles={"orbiter": orbiter_profile},
        pairs=[("gs", "orbiter")],
    )
    endpoints = {"gs": geometry.station_endpoints["gs"], "orbiter": MARS_ORBITER}
    judged = 0
    reasons = set()
    for t in np.arange(START, START + 86_400.0, 600.0):
        signals = PairSignals.sent_at(earth_mars, SHAPES, endpoints, [("gs", "orbiter")], float(t))
        uplink, downlink = signals.directions([("gs", "orbiter")])
        (verdict,) = geometry.verdicts([("gs", "orbiter")], uplink, downlink)
        reasons.add(verdict.reason)
        up = DirectionEnds.from_signals(uplink)
        station_rate = math.degrees(float(up.transmitter.rate_rad_s[0]))
        orbiter_rate = math.degrees(float(up.receiver.rate_rad_s[0]))
        if verdict.reason in ("los_blocked", "elevation_below_min"):
            continue
        # The discriminating case: the station needs more than the orbiter's limit allows.
        assert ORBITER_LIMIT_DEG_S < station_rate < STATION_LIMIT_DEG_S, (t, station_rate)
        assert orbiter_rate < ORBITER_LIMIT_DEG_S, (t, orbiter_rate)
        assert verdict.visible, (t, verdict.reason, verdict.rejecting_endpoint)
        judged += 1
    assert judged > 0, f"no sample had Mars above the mask and the orbiter in view: {reasons}"


def _ends(rate_deg_s: float, pointing: tuple[float, float, float]) -> EndArrays:
    return EndArrays(
        fixed_km=np.array([[0.0, 0.0, 0.0]]),
        pointing_fixed=np.array([pointing]),
        rate_rad_s=np.array([math.radians(rate_deg_s)]),
        toward_other_body_fixed=np.array([pointing]),
    )


@pytest.mark.parametrize(
    ("station_rate", "orbiter_rate", "reason", "rejecting"),
    [
        (0.5, 0.0005, "ok", "none"),
        (1.5, 0.0005, "tracking_exceeded", "ground"),
        (0.5, 0.002, "tracking_exceeded", "satellite"),
        (1.5, 0.002, "tracking_exceeded", "both"),
    ],
)
def test_each_end_is_judged_against_its_own_tracking_limit(
    station_rate: float, orbiter_rate: float, reason: str, rejecting: str
) -> None:
    """A station limited to 1 deg/s and an orbiter limited to 0.001 deg/s, the station looking
    straight up at the orbiter overhead: the rejecting endpoint is the end over its own limit."""
    table = GroundLimitTable.from_limits(
        [
            GroundLimits(
                gs_lat_deg=0.0,
                gs_lon_deg=0.0,
                min_elevation_deg=10.0,
                gs_max_tracking_rate_deg_s=STATION_LIMIT_DEG_S,
                sat_max_tracking_rate_deg_s=ORBITER_LIMIT_DEG_S,
            )
        ]
    )
    up = (1.0, 0.0, 0.0)  # local vertical at latitude 0, longitude 0
    directions = ground_directions(
        station=_ends(station_rate, up),
        satellite=_ends(orbiter_rate, (-1.0, 0.0, 0.0)),
        path_km=np.array([1000.0]),
        blocked=np.array([False]),
        table=table,
    )
    assert (directions.reason[0], directions.rejecting_endpoint[0]) == (reason, rejecting)
