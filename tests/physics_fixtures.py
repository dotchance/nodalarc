# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Explicit body-frame fixtures for physics tests.

These helpers are test-only. Runtime code receives body facts through
ResolvedSession; tests that use the shipped catalog can load those facts here
and pass them explicitly to math APIs.
"""

from __future__ import annotations

import functools
import math
from pathlib import Path

import numpy as np
import yaml
from nodalarc.body_frames import BodyFrame, body_runtime_support_for
from nodalarc.frames import EcefVec3, GeoPosition, Vec3
from nodalarc.models.events import EphemerisBodyFrame
from nodalarc.orbital import OrbitalElements
from nodalarc.physics import (
    BodyState,
    ElementsBatch,
    PhysicsContext,
    body_fixed_from_geodetic,
    geodetic_from_body_fixed,
    orbit_states,
)
from nodalarc.physics.kepler import OrbitModel, orbital_period_s
from nodalarc.physics.satellites import analytic_satellite_states

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
        gravitational_parameter_km3_s2=float(body["gravitational_parameter_km3_s2"]),
        j2=support.j2,
    )


EARTH_TEST_BODY_FRAME = body_frame_from_catalog("earth")
LUNA_TEST_BODY_FRAME = body_frame_from_catalog("luna")
EARTH_TEST_BODY_FRAMES = {"earth": EARTH_TEST_BODY_FRAME}
LUNA_TEST_BODY_FRAMES = {"luna": LUNA_TEST_BODY_FRAME}


# Earth's orbit-element basis is the identity by definition (elements are
# measured on ICRF axes); see PhysicsContext.orbit_basis.
EARTH_ORBIT_BASIS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _earth_ephemeris_body_frame(frame: BodyFrame) -> EphemerisBodyFrame:
    return EphemerisBodyFrame(
        body_id=frame.name,
        mean_radius_km=frame.mean_radius_km,
        equatorial_radius_km=frame.equatorial_radius_km,
        polar_radius_km=frame.polar_radius_km,
        gravitational_parameter_km3_s2=frame.gravitational_parameter_km3_s2,
        j2=frame.j2,
        orbit_basis=EARTH_ORBIT_BASIS,
    )


EARTH_TEST_EPHEMERIS_BODY_FRAME = _earth_ephemeris_body_frame(EARTH_TEST_BODY_FRAME)
EARTH_TEST_EPHEMERIS_BODY_FRAMES = {
    "earth": EARTH_TEST_EPHEMERIS_BODY_FRAME,
}


EARTH_ORIGIN_BODY_STATE = BodyState(
    body_id="earth",
    position_km=Vec3(0.0, 0.0, 0.0),
    velocity_km_s=Vec3(0.0, 0.0, 0.0),
)
EARTH_ORIGIN_BODY_STATES = {"earth": EARTH_ORIGIN_BODY_STATE}


def earth_elements_from_params(
    altitude_km: float,
    inclination_deg: float,
    raan_deg: float,
    true_anomaly_deg: float,
) -> OrbitalElements:
    """A circular Earth orbit; for e == 0 the true and mean anomalies agree."""
    return OrbitalElements(
        semi_major_axis_km=EARTH_TEST_BODY_FRAME.mean_radius_km + altitude_km,
        inclination_rad=math.radians(inclination_deg),
        raan_rad=math.radians(raan_deg),
        mean_anomaly_rad=math.radians(true_anomaly_deg),
    )


def earth_orbital_period(altitude_km: float) -> float:
    return orbital_period_s(
        earth_elements_from_params(altitude_km, 0.0, 0.0, 0.0), EARTH_TEST_BODY_FRAME
    )


def earth_propagate_eci(elements: OrbitalElements, dt: float):
    """Two-body state on ICRF axes (Earth's orbit basis is ICRF) from the production kernel."""
    states = orbit_states(
        ElementsBatch.from_elements([elements]),
        np.array([dt]),
        model="two-body",
        body_frame=EARTH_TEST_BODY_FRAME,
    )
    return (
        Vec3(float(states.px[0, 0]), float(states.py[0, 0]), float(states.pz[0, 0])),
        Vec3(float(states.vx[0, 0]), float(states.vy[0, 0]), float(states.vz[0, 0])),
    )


def _earth_fixed(elements: OrbitalElements, epoch: float, dt: float, model: OrbitModel):
    states = analytic_satellite_states(
        physics_for("earth"),
        body_frame=EARTH_TEST_BODY_FRAME,
        model=model,
        elements=[elements],
        phases_s=[dt],
        master_sim_time_unix=epoch + dt,
    )
    return (
        EcefVec3(Vec3(*map(float, states.position_fixed_km[0]))),
        EcefVec3(Vec3(*map(float, states.velocity_fixed_km_s[0]))),
        states.geodetic[0],
    )


def earth_propagate_keplerian(elements: OrbitalElements, epoch: float, dt: float):
    """Earth-fixed position, velocity and geodetic position, two-body model."""
    return _earth_fixed(elements, epoch, dt, "two-body")


def earth_propagate_j2_mean_elements(elements: OrbitalElements, epoch: float, dt: float):
    """Earth-fixed position, velocity and geodetic position, J2 mean-element model."""
    return _earth_fixed(elements, epoch, dt, "j2-mean-elements")


def earth_geodetic_to_ecef(geo: GeoPosition):
    return EcefVec3(body_fixed_from_geodetic(geo, EARTH_TEST_BODY_FRAME))


def earth_ecef_to_geodetic(ecef):
    return geodetic_from_body_fixed(ecef, EARTH_TEST_BODY_FRAME)


def earth_eci_to_ecef_velocity(pos_eci, vel_eci, unix_timestamp: float):
    """Velocity relative to the rotating Earth, from Earth's measured orientation."""
    orientation = physics_for("earth").orientation("earth", unix_timestamp)
    velocity = orientation.rotation @ np.array(vel_eci) + orientation.rotation_rate @ np.array(
        pos_eci
    )
    return EcefVec3(Vec3(*map(float, velocity)))


@functools.cache
def physics_for(*bodies: str) -> PhysicsContext:
    """The platform's real ephemeris data for the given bodies, built once."""
    return PhysicsContext(bodies)
