# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME propagation engine boundary.

The production implementations are explicit circular Keplerian propagation,
circular mean-element J2 secular propagation, and SGP4/TLE propagation. Each
model is selected by its own propagator ID with its own validation and error
budget; this module must never silently downgrade an unsupported model into
Keplerian.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nodalarc.body_frames import body_frame_for
from nodalarc.constellation_loader import SatelliteNode, satellite_node_id
from nodalarc.ephemeris_runtime import CommonBodyState
from nodalarc.frames import EcefVec3, Vec3
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.events import NodePosition

from ome.propagator import (
    GeoPosition,
    propagate_j2_mean_elements_for_body,
    propagate_keplerian_for_body,
    propagate_sgp4_tle,
)

PropagatorId = Literal["keplerian-circular", "j2-mean-elements", "sgp4-tle"]


@dataclass(frozen=True)
class PropagatedState:
    """Propagated physical state for one satellite at one simulation tick."""

    node_id: str
    sim_time_unix: float
    position_ecef_km: EcefVec3
    velocity_ecef_km_s: EcefVec3
    geodetic: GeoPosition
    propagator_id: PropagatorId
    position_common_km: EcefVec3 | None = None
    velocity_common_km_s: EcefVec3 | None = None
    body_origin_common_km: EcefVec3 | None = None
    central_body: str = "earth"

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


def _zero_body_state(body_id: str = "earth") -> CommonBodyState:
    return CommonBodyState(
        body_id=body_id,
        position_km=Vec3(0.0, 0.0, 0.0),
        velocity_km_s=Vec3(0.0, 0.0, 0.0),
        provider="none",
        kernel_id=f"{body_id}-origin",
        quality_tier="analytic",
        frame="gcrs-earth-origin",
    )


def _common_vec(origin: Vec3, local_inertial: Vec3) -> EcefVec3:
    return EcefVec3(
        Vec3(
            origin.x + local_inertial.x,
            origin.y + local_inertial.y,
            origin.z + local_inertial.z,
        )
    )


def propagate_satellites(
    *,
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    epoch_unix: float,
    dt: float,
    propagator_id: PropagatorId,
    body_states: dict[str, CommonBodyState] | None = None,
) -> dict[str, PropagatedState]:
    """Propagate all satellites for one tick.

    Unsupported propagators are fatal configuration/engine errors. SGP4/TLE
    requests require actual TLE records on every satellite; missing records
    fail before any lower-fidelity substitute can be used.
    """
    if propagator_id not in ("keplerian-circular", "j2-mean-elements", "sgp4-tle"):
        raise ValueError(f"Unsupported OME propagator: {propagator_id!r}")

    sim_time_unix = epoch_unix + dt
    states_by_body = dict(body_states or {"earth": _zero_body_state("earth")})
    states: dict[str, PropagatedState] = {}
    for sat in satellites:
        node_id = satellite_node_id(sat, addressing)
        central_body = getattr(sat, "central_body", "earth")
        body_frame = body_frame_for(central_body)
        body_state = states_by_body.get(central_body)
        if body_state is None:
            raise ValueError(
                f"Propagation missing common-frame ephemeris state for central_body={central_body!r} "
                f"while propagating {node_id!r}"
            )
        if propagator_id == "keplerian-circular":
            pos_ecef, vel_ecef, geo, pos_inertial, vel_inertial = propagate_keplerian_for_body(
                sat.elements,
                epoch_unix,
                dt,
                body_frame=body_frame,
            )
        elif propagator_id == "j2-mean-elements":
            (
                pos_ecef,
                vel_ecef,
                geo,
                pos_inertial,
                vel_inertial,
            ) = propagate_j2_mean_elements_for_body(
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
            propagator_id=propagator_id,
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
