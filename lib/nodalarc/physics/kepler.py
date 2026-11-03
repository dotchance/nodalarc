# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Orbit states for the two analytic orbit models.

The Kepler step (orbital elements to a position and velocity) comes from
SPICE ``conics`` through SpiceyPy, not from NodalArc code. NodalArc's own
Kepler solvers were removed in favour of the library NASA's navigation teams
use. ANISE was the first choice, but its two-body propagation
(``Orbit.at_epoch`` in 0.10.6) was 11,480 km from SPICE after one second on
a lunar orbit started at periapsis, and it refuses exactly circular orbits;
upstream tracks the defect as nyx-space/anise issue #821.

SPICE code is not thread safe (NAIF states this), and SpiceyPy calls CSPICE
through ctypes, which releases the Python interpreter lock during the call.
After each call SpiceyPy reads and resets CSPICE's global error state with
further calls. ``SPICE_LOCK`` is therefore held across each whole SpiceyPy
call, its error check included, by every caller in the process.

All states here are in the central body's orbit-element basis (see
``PhysicsContext.orbit_basis``), relative to the body's center.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import spiceypy

from nodalarc.body_frames import BodyFrame
from nodalarc.orbital import OrbitalElements

SPICE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class OrbitStates:
    """Positions (km) and velocities (km/s), each an (N, 3) array."""

    position_km: np.ndarray
    velocity_km_s: np.ndarray


def _conics_row(
    elements: OrbitalElements,
    *,
    raan_rad: float,
    argument_of_perigee_rad: float,
    mean_anomaly_rad: float,
    mu_km3_s2: float,
    dt_s: float,
) -> np.ndarray:
    # conics takes perifocal distance, eccentricity, inclination, node,
    # argument of periapsis, mean anomaly at t0, t0 and GM, and returns the
    # state at et = t0 + dt. NodalArc elements carry the semi-major axis.
    perifocal_km = elements.semi_major_axis_km * (1.0 - elements.eccentricity)
    return np.asarray(
        spiceypy.conics(
            [
                perifocal_km,
                elements.eccentricity,
                elements.inclination_rad,
                raan_rad,
                argument_of_perigee_rad,
                mean_anomaly_rad,
                0.0,
                mu_km3_s2,
            ],
            dt_s,
        ),
        dtype=np.float64,
    )


def two_body_states(
    elements: Sequence[OrbitalElements],
    phases_s: Sequence[float],
    *,
    mu_km3_s2: float,
) -> OrbitStates:
    """Two-body states, each element set propagated by its own phase time."""
    if len(elements) != len(phases_s):
        raise ValueError(f"{len(elements)} element sets but {len(phases_s)} phase times")
    rows: np.ndarray = np.empty((len(elements), 6), dtype=np.float64)
    with SPICE_LOCK:
        for index, (el, phase) in enumerate(zip(elements, phases_s, strict=True)):
            rows[index] = _conics_row(
                el,
                raan_rad=el.raan_rad,
                argument_of_perigee_rad=el.argument_of_perigee_rad,
                mean_anomaly_rad=el.mean_anomaly_rad,
                mu_km3_s2=mu_km3_s2,
                dt_s=float(phase),
            )
    return OrbitStates(position_km=rows[:, :3], velocity_km_s=rows[:, 3:])


def j2_mean_element_secular_rates(
    elements: OrbitalElements,
    *,
    body_frame: BodyFrame,
) -> tuple[float, float, float]:
    """(node, argument-of-perigee, mean-anomaly) secular rates in rad/s.

    The first-order secular J2 model is NodalArc's declared approximation
    and stays NodalArc code: no qualified library with a matching model was
    found (searched: ANISE, Skyfield, SPICE, Orekit, Nyx), and a different
    model would move the satellites of every existing session that uses it.
    For circular orbits the argument of perigee is undefined, so its rate is
    zero, which keeps the circular J2 contract existing sessions rely on.
    """
    a = elements.semi_major_axis_km
    e = elements.eccentricity
    p = a * (1.0 - e * e)
    if p <= 0.0:
        raise ValueError("semi-latus rectum must be positive")
    n = math.sqrt(body_frame.gravitational_parameter_km3_s2 / (a * a * a))
    cos_i = math.cos(elements.inclination_rad)
    radius_ratio = body_frame.equatorial_radius_km / p
    j2_factor = body_frame.j2 * (radius_ratio * radius_ratio)
    raan_dot = -1.5 * j2_factor * n * cos_i
    argp_dot = 0.0 if e == 0.0 else 0.75 * j2_factor * n * (5.0 * (cos_i * cos_i) - 1.0)
    mean_anomaly_dot = n * (
        1.0 + 0.75 * j2_factor * math.sqrt(1.0 - e * e) * (3.0 * (cos_i * cos_i) - 1.0)
    )
    return raan_dot, argp_dot, mean_anomaly_dot


def j2_mean_states(
    elements: Sequence[OrbitalElements],
    phases_s: Sequence[float],
    *,
    body_frame: BodyFrame,
) -> OrbitStates:
    """States of the first-order secular J2 mean-element model.

    The secular rates move the node, the argument of perigee and the mean
    anomaly; SPICE ``conics`` turns the moved elements into a position.

    ``conics`` returns a two-body velocity for the moved elements, which
    omits the motion caused by the elements changing: for a circular 550 km
    orbit at 53 degrees, 3.548 m/s after one hour while the positions agreed
    exactly. The velocity here is the time derivative of the J2 trajectory:
    the two-body velocity scaled by the J2 mean-anomaly rate over the
    two-body mean motion, plus the node rate times (pole cross r), plus the
    perigee rate times (orbit normal cross r). It matched the previous
    NodalArc velocity and a numerical derivative of the positions to well
    under a micrometre per second.
    """
    if len(elements) != len(phases_s):
        raise ValueError(f"{len(elements)} element sets but {len(phases_s)} phase times")
    mu = body_frame.gravitational_parameter_km3_s2
    count = len(elements)
    rows: np.ndarray = np.empty((count, 6), dtype=np.float64)
    rates: np.ndarray = np.empty((count, 3), dtype=np.float64)
    two_body_motion: np.ndarray = np.empty(count, dtype=np.float64)
    with SPICE_LOCK:
        for index, (el, phase) in enumerate(zip(elements, phases_s, strict=True)):
            raan_dot, argp_dot, mean_anomaly_dot = j2_mean_element_secular_rates(
                el, body_frame=body_frame
            )
            dt = float(phase)
            rows[index] = _conics_row(
                el,
                raan_rad=el.raan_rad + raan_dot * dt,
                argument_of_perigee_rad=el.argument_of_perigee_rad + argp_dot * dt,
                mean_anomaly_rad=el.mean_anomaly_rad + mean_anomaly_dot * dt,
                mu_km3_s2=mu,
                dt_s=0.0,
            )
            rates[index] = (raan_dot, argp_dot, mean_anomaly_dot)
            a = el.semi_major_axis_km
            two_body_motion[index] = math.sqrt(mu / (a * a * a))
    position = rows[:, :3]
    kepler_velocity = rows[:, 3:]
    normal = np.cross(position, kepler_velocity)
    normal /= np.linalg.norm(normal, axis=1, keepdims=True)
    pole = np.array([0.0, 0.0, 1.0])
    velocity = (
        (rates[:, 2] / two_body_motion)[:, None] * kepler_velocity
        + rates[:, 0][:, None] * np.cross(pole, position)
        + rates[:, 1][:, None] * np.cross(normal, position)
    )
    return OrbitStates(position_km=position, velocity_km_s=velocity)


def advance_mean_elements(
    elements: OrbitalElements,
    dt_s: float,
    *,
    body_frame: BodyFrame,
    propagator_id: str,
) -> OrbitalElements:
    """The element set moved forward by ``dt_s`` seconds under its own model.

    Propagation takes elements valid at the pacing epoch. This moves an
    authored element set, valid at its orbit's declared epoch, to a new
    anchor with the same mean rates the models use, so advancing by ``a``
    and propagating ``b`` equals propagating ``a + b``. ``dt_s == 0.0``
    returns ``elements`` unchanged.
    """
    if dt_s == 0.0:
        return elements
    if propagator_id in ("two-body", "keplerian-circular"):
        a = elements.semi_major_axis_km
        n = math.sqrt(body_frame.gravitational_parameter_km3_s2 / (a * a * a))
        return OrbitalElements(
            semi_major_axis_km=a,
            inclination_rad=elements.inclination_rad,
            raan_rad=elements.raan_rad,
            eccentricity=elements.eccentricity,
            argument_of_perigee_rad=elements.argument_of_perigee_rad,
            mean_anomaly_rad=(elements.mean_anomaly_rad + n * dt_s) % math.tau,
        )
    if propagator_id == "j2-mean-elements":
        raan_dot, argp_dot, mean_anomaly_dot = j2_mean_element_secular_rates(
            elements, body_frame=body_frame
        )
        return OrbitalElements(
            semi_major_axis_km=elements.semi_major_axis_km,
            inclination_rad=elements.inclination_rad,
            raan_rad=(elements.raan_rad + raan_dot * dt_s) % math.tau,
            eccentricity=elements.eccentricity,
            argument_of_perigee_rad=(elements.argument_of_perigee_rad + argp_dot * dt_s) % math.tau,
            mean_anomaly_rad=(elements.mean_anomaly_rad + mean_anomaly_dot * dt_s) % math.tau,
        )
    raise ValueError(
        f"advance_mean_elements cannot advance propagator {propagator_id!r}; "
        "SGP4/TLE elements carry their own epoch and must not be re-anchored"
    )
