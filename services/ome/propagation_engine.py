# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME propagation engine boundary.

The production implementations are explicit two-body Keplerian propagation, J2
mean-element secular propagation, and SGP4/TLE propagation. Each model is
selected by its own propagator ID with its own validation and error budget; this
module must never silently downgrade an unsupported model into Keplerian.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import numpy as np
from nodalarc.body_frames import BodyFrame
from nodalarc.constellation_loader import SatelliteNode, satellite_node_id
from nodalarc.ephemeris_runtime import CommonBodyState
from nodalarc.frames import EcefVec3, EciVec3, Vec3
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.events import NodePosition
from nodalarc.propagation_kernel import (
    ElementsBatch,
    body_rotation_angle_batch,
    eci_to_body_fixed_batch,
    eci_to_body_fixed_velocity_batch,
    propagate_eci_batch,
)
from nodalarc.propagator import body_fixed_to_geodetic

from ome.propagator import (
    GeoPosition,
    propagate_keplerian_for_body,
    propagate_sgp4_tle,
)

PropagatorId = Literal["two-body", "keplerian-circular", "j2-mean-elements", "sgp4-tle"]
SessionPropagatorId = PropagatorId | Literal["mixed"]


@dataclass(frozen=True)
class PropagatedState:
    """Propagated physical state for one satellite at one simulation tick."""

    node_id: str
    sim_time_unix: float
    position_ecef_km: EcefVec3
    velocity_ecef_km_s: EcefVec3
    geodetic: GeoPosition
    propagator_id: PropagatorId
    central_body: str
    position_common_km: EcefVec3 | None = None
    velocity_common_km_s: EcefVec3 | None = None
    body_origin_common_km: EcefVec3 | None = None

    def __post_init__(self) -> None:
        if self.position_common_km is None:
            object.__setattr__(self, "position_common_km", self.position_ecef_km)
        if self.velocity_common_km_s is None:
            object.__setattr__(self, "velocity_common_km_s", self.velocity_ecef_km_s)
        if self.body_origin_common_km is None:
            object.__setattr__(
                self,
                "body_origin_common_km",
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
            )


def _common_vec(origin: Vec3, local_inertial: Vec3) -> EcefVec3:
    return EcefVec3(
        Vec3(
            origin.x + local_inertial.x,
            origin.y + local_inertial.y,
            origin.z + local_inertial.z,
        )
    )


def _satellite_propagator_id(sat, session_propagator_id: SessionPropagatorId) -> PropagatorId:
    sat_propagator_id = getattr(sat, "propagator_id", None)
    if sat_propagator_id is not None:
        if sat_propagator_id not in (
            "two-body",
            "keplerian-circular",
            "j2-mean-elements",
            "sgp4-tle",
        ):
            raise ValueError(f"Unsupported satellite propagator: {sat_propagator_id!r}")
        return sat_propagator_id
    if session_propagator_id == "mixed":
        raise ValueError("OME mixed propagation requires every satellite to carry propagator_id")
    return session_propagator_id


def propagate_satellites(
    *,
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    epoch_unix: float,
    dt: float,
    propagator_id: SessionPropagatorId,
    body_frames: Mapping[str, BodyFrame],
    body_states: Mapping[str, CommonBodyState],
) -> dict[str, PropagatedState]:
    """Propagate all satellites for one tick.

    Unsupported propagators are fatal configuration/engine errors. SGP4/TLE
    requests require actual TLE records on every satellite; missing records
    fail before any lower-fidelity substitute can be used.
    """
    if propagator_id not in (
        "mixed",
        "two-body",
        "keplerian-circular",
        "j2-mean-elements",
        "sgp4-tle",
    ):
        raise ValueError(f"Unsupported OME propagator: {propagator_id!r}")

    sim_time_unix = epoch_unix + dt
    states_by_body = dict(body_states)
    states: dict[str, PropagatedState] = {}

    # The J2 mean-element population — the hot path — propagates as ONE
    # kernel batch per central body instead of satellite by satellite.
    # The kernel is bit-identical to the scalar wrapper (enforced by the
    # equivalence suite), geodetic conversion below consumes those
    # identical body-fixed positions, and states are still assembled in
    # the ORIGINAL satellite order, so every downstream value and
    # iteration order is byte-unchanged from the scalar path.
    j2_by_body: dict[str, list[int]] = {}
    sat_propagator_ids: list[str] = []
    for index, sat in enumerate(satellites):
        sat_propagator_ids.append(_satellite_propagator_id(sat, propagator_id))
        if sat_propagator_ids[-1] == "j2-mean-elements":
            j2_by_body.setdefault(sat.central_body, []).append(index)

    j2_states: dict[int, tuple] = {}
    dt_column = np.array([dt], dtype=np.float64)
    time_column = np.array([sim_time_unix], dtype=np.float64)
    for central_body, indices in j2_by_body.items():
        first_node_id = satellite_node_id(satellites[indices[0]], addressing)
        try:
            body_frame = body_frames[central_body]
        except KeyError as exc:
            raise ValueError(
                f"Propagation missing resolved body primitive facts for central_body={central_body!r} "
                f"while propagating {first_node_id!r}"
            ) from exc
        batch = ElementsBatch.from_elements([satellites[i].elements for i in indices])
        eci = propagate_eci_batch(batch, dt_column, body_frame=body_frame)
        theta = body_rotation_angle_batch(body_frame, time_column)
        bx, by, bz = eci_to_body_fixed_batch(eci.px, eci.py, eci.pz, theta)
        vbx, vby, vbz = eci_to_body_fixed_velocity_batch(
            eci.vx,
            eci.vy,
            eci.vz,
            bx,
            by,
            theta,
            rotation_rate_rad_s=body_frame.rotation_rate_rad_s,
        )
        for row, sat_index in enumerate(indices):
            pos_fixed = EcefVec3(Vec3(float(bx[row, 0]), float(by[row, 0]), float(bz[row, 0])))
            vel_fixed = EcefVec3(Vec3(float(vbx[row, 0]), float(vby[row, 0]), float(vbz[row, 0])))
            pos_inertial = EciVec3(
                Vec3(float(eci.px[row, 0]), float(eci.py[row, 0]), float(eci.pz[row, 0]))
            )
            vel_inertial = EciVec3(
                Vec3(float(eci.vx[row, 0]), float(eci.vy[row, 0]), float(eci.vz[row, 0]))
            )
            geo = body_fixed_to_geodetic(pos_fixed, body_frame)
            j2_states[sat_index] = (pos_fixed, vel_fixed, geo, pos_inertial, vel_inertial)

    for index, sat in enumerate(satellites):
        node_id = satellite_node_id(sat, addressing)
        central_body = sat.central_body
        sat_propagator_id = sat_propagator_ids[index]
        try:
            body_frame = body_frames[central_body]
        except KeyError as exc:
            raise ValueError(
                f"Propagation missing resolved body primitive facts for central_body={central_body!r} "
                f"while propagating {node_id!r}"
            ) from exc
        body_state = states_by_body.get(central_body)
        if body_state is None:
            raise ValueError(
                f"Propagation missing common-frame ephemeris state for central_body={central_body!r} "
                f"while propagating {node_id!r}"
            )
        if sat_propagator_id == "j2-mean-elements":
            pos_ecef, vel_ecef, geo, pos_inertial, vel_inertial = j2_states[index]
        elif sat_propagator_id in ("two-body", "keplerian-circular"):
            pos_ecef, vel_ecef, geo, pos_inertial, vel_inertial = propagate_keplerian_for_body(
                sat.elements,
                epoch_unix,
                dt,
                body_frame=body_frame,
            )
        else:
            if central_body != "earth":
                raise ValueError(
                    f"Satellite {node_id} uses central_body={central_body!r}; "
                    "SGP4/TLE propagation is Earth-only"
                )
            if sat.tle_line_1 is None or sat.tle_line_2 is None:
                raise ValueError(
                    f"Satellite {node_id} has no TLE lines; "
                    "orbit.propagator='sgp4-tle' requires a TLE constellation"
                )
            pos_ecef, vel_ecef, geo = propagate_sgp4_tle(
                sat.tle_line_1,
                sat.tle_line_2,
                epoch_unix,
                dt,
                body_frame=body_frame,
            )
            pos_inertial = pos_ecef
            vel_inertial = vel_ecef
        pos_common = _common_vec(body_state.position_km, pos_inertial)
        vel_common = _common_vec(body_state.velocity_km_s, vel_inertial)
        states[node_id] = PropagatedState(
            node_id=node_id,
            sim_time_unix=sim_time_unix,
            position_ecef_km=pos_ecef,
            velocity_ecef_km_s=vel_ecef,
            position_common_km=pos_common,
            velocity_common_km_s=vel_common,
            body_origin_common_km=EcefVec3(body_state.position_km),
            central_body=central_body,
            geodetic=geo,
            propagator_id=sat_propagator_id,
        )
    return states


def build_node_positions(
    sat_states: dict[str, PropagatedState],
    gs_positions: dict[str, tuple[EcefVec3, GeoPosition]],
) -> dict[str, NodePosition]:
    """Build public position snapshots from propagated and fixed node states."""
    positions: dict[str, NodePosition] = {}

    for node_id, state in sat_states.items():
        geo = state.geodetic
        vel_ecef = state.velocity_ecef_km_s
        positions[node_id] = NodePosition(
            lat_deg=geo.lat_deg,
            lon_deg=geo.lon_deg,
            alt_km=geo.alt_km,
            vel_x_km_s=vel_ecef.x,
            vel_y_km_s=vel_ecef.y,
            vel_z_km_s=vel_ecef.z,
        )

    for node_id, (_ecef, geo) in gs_positions.items():
        positions[node_id] = NodePosition(
            lat_deg=geo.lat_deg,
            lon_deg=geo.lon_deg,
            alt_km=geo.alt_km,
            vel_x_km_s=0.0,
            vel_y_km_s=0.0,
            vel_z_km_s=0.0,
        )

    return positions
