# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Explicit body-frame fixtures for physics tests.

These helpers are test-only. Runtime code receives body facts through
ResolvedSession; tests that use the shipped catalog can load those facts here
and pass them explicitly to math APIs.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from nodalarc.body_frames import BodyFrame, body_runtime_support_for
from nodalarc.ephemeris_runtime import CommonBodyState
from nodalarc.frames import GeoPosition, Vec3
from nodalarc.geo import geodetic_to_ecef
from nodalarc.models.events import EphemerisBodyFrame
from nodalarc.orbital import OrbitalElements, elements_from_params
from nodalarc.propagator import (
    ecef_to_geodetic,
    eci_to_ecef_velocity,
    orbital_period,
    orbital_velocity,
    propagate_eci,
    propagate_j2_mean_elements,
    propagate_keplerian,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def body_frame_from_catalog(body_id: str) -> BodyFrame:
    path = _PROJECT_ROOT / "catalog" / "nodalarc" / "bodies" / f"{body_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    body = data["body"]
    if body["id"] != body_id:
        raise AssertionError(f"catalog body fixture {path} has id={body['id']!r}")
    support = body_runtime_support_for(body_id)
    return BodyFrame(
        name=body["id"],
        mean_radius_km=float(body["mean_radius_km"]),
        equatorial_radius_km=float(body["equatorial_radius_km"]),
        polar_radius_km=float(body["polar_radius_km"]),
        rotation_rate_rad_s=support.rotation_rate_rad_s,
        gravitational_parameter_km3_s2=float(body["gravitational_parameter_km3_s2"]),
        j2=support.j2,
    )


EARTH_TEST_BODY_FRAME = body_frame_from_catalog("earth")
LUNA_TEST_BODY_FRAME = body_frame_from_catalog("luna")
EARTH_TEST_BODY_FRAMES = {"earth": EARTH_TEST_BODY_FRAME}
LUNA_TEST_BODY_FRAMES = {"luna": LUNA_TEST_BODY_FRAME}


def ephemeris_body_frame_from_body_frame(frame: BodyFrame) -> EphemerisBodyFrame:
    return EphemerisBodyFrame(
        body_id=frame.name,
        mean_radius_km=frame.mean_radius_km,
        equatorial_radius_km=frame.equatorial_radius_km,
        polar_radius_km=frame.polar_radius_km,
        gravitational_parameter_km3_s2=frame.gravitational_parameter_km3_s2,
        rotation_rate_rad_s=frame.rotation_rate_rad_s,
        j2=frame.j2,
        origin_x_km=0.0,
        origin_y_km=0.0,
        origin_z_km=0.0,
        vel_x_km_s=0.0,
        vel_y_km_s=0.0,
        vel_z_km_s=0.0,
        provider="test_catalog_fixture",
        kernel_id="test_catalog_fixture",
        quality_tier="test",
        frame="test_common",
    )


EARTH_TEST_EPHEMERIS_BODY_FRAME = ephemeris_body_frame_from_body_frame(EARTH_TEST_BODY_FRAME)
LUNA_TEST_EPHEMERIS_BODY_FRAME = ephemeris_body_frame_from_body_frame(LUNA_TEST_BODY_FRAME)
EARTH_TEST_EPHEMERIS_BODY_FRAMES = {
    "earth": EARTH_TEST_EPHEMERIS_BODY_FRAME,
}
EARTH_LUNA_TEST_EPHEMERIS_BODY_FRAMES = {
    "earth": EARTH_TEST_EPHEMERIS_BODY_FRAME,
    "luna": LUNA_TEST_EPHEMERIS_BODY_FRAME,
}


EARTH_ORIGIN_BODY_STATE = CommonBodyState(
    body_id="earth",
    position_km=Vec3(0.0, 0.0, 0.0),
    velocity_km_s=Vec3(0.0, 0.0, 0.0),
    provider="test-fixture",
    kernel_id="earth-origin",
    quality_tier="analytic",
    frame="gcrs-earth-origin",
)
EARTH_ORIGIN_BODY_STATES = {"earth": EARTH_ORIGIN_BODY_STATE}


def earth_elements_from_params(
    altitude_km: float,
    inclination_deg: float,
    raan_deg: float,
    true_anomaly_deg: float,
) -> OrbitalElements:
    return elements_from_params(
        altitude_km,
        inclination_deg,
        raan_deg,
        true_anomaly_deg,
        reference_radius_km=EARTH_TEST_BODY_FRAME.mean_radius_km,
    )


def earth_orbital_period(altitude_km: float) -> float:
    return orbital_period(altitude_km, body_frame=EARTH_TEST_BODY_FRAME)


def earth_orbital_velocity(altitude_km: float) -> float:
    return orbital_velocity(altitude_km, body_frame=EARTH_TEST_BODY_FRAME)


def earth_propagate_eci(elements: OrbitalElements, dt: float):
    return propagate_eci(elements, dt, body_frame=EARTH_TEST_BODY_FRAME)


def earth_propagate_keplerian(elements: OrbitalElements, epoch: float, dt: float):
    return propagate_keplerian(elements, epoch, dt, body_frame=EARTH_TEST_BODY_FRAME)


def earth_propagate_j2_mean_elements(elements: OrbitalElements, epoch: float, dt: float):
    return propagate_j2_mean_elements(elements, epoch, dt, body_frame=EARTH_TEST_BODY_FRAME)


def earth_geodetic_to_ecef(geo: GeoPosition):
    return geodetic_to_ecef(geo, EARTH_TEST_BODY_FRAME)


def earth_ecef_to_geodetic(ecef):
    return ecef_to_geodetic(ecef, body_frame=EARTH_TEST_BODY_FRAME)


def earth_eci_to_ecef_velocity(pos_eci, vel_eci, unix_timestamp: float):
    return eci_to_ecef_velocity(
        pos_eci,
        vel_eci,
        unix_timestamp,
        body_frame=EARTH_TEST_BODY_FRAME,
    )
