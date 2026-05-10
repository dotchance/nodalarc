# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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

from nodalarc.constellation_loader import SatelliteNode
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.events import NodePosition

from ome.propagator import (
    EcefVec3,
    GeoPosition,
    propagate_j2_mean_elements,
    propagate_keplerian,
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


def propagate_satellites(
    *,
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    epoch_unix: float,
    dt: float,
    propagator_id: PropagatorId,
) -> dict[str, PropagatedState]:
    """Propagate all satellites for one tick.

    Unsupported propagators are fatal configuration/engine errors. SGP4/TLE
    requests require actual TLE records on every satellite; missing records
    fail before any lower-fidelity substitute can be used.
    """
    if propagator_id not in ("keplerian-circular", "j2-mean-elements", "sgp4-tle"):
        raise ValueError(f"Unsupported OME propagator: {propagator_id!r}")

    sim_time_unix = epoch_unix + dt
    states: dict[str, PropagatedState] = {}
    for sat in satellites:
        node_id = addressing.sat_id(sat.plane, sat.slot)
        if propagator_id == "keplerian-circular":
            pos_ecef, vel_ecef, geo = propagate_keplerian(sat.elements, epoch_unix, dt)
        elif propagator_id == "j2-mean-elements":
            pos_ecef, vel_ecef, geo = propagate_j2_mean_elements(sat.elements, epoch_unix, dt)
        else:
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
        states[node_id] = PropagatedState(
            node_id=node_id,
            sim_time_unix=sim_time_unix,
            position_ecef_km=pos_ecef,
            velocity_ecef_km_s=vel_ecef,
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
