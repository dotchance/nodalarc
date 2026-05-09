# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""OME propagation engine boundary.

The current production implementation is explicit circular Keplerian
propagation. Higher-fidelity propagators such as J2 or SGP4 must register as
separate propagator IDs with their own validation and error budgets; this
module must never silently downgrade an unsupported model into Keplerian.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nodalarc.constellation_loader import SatelliteNode
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.events import NodePosition

from ome.propagator import EcefVec3, GeoPosition, propagate_keplerian

PropagatorId = Literal["keplerian-circular"]


@dataclass(frozen=True)
class PropagatedState:
    """Propagated physical state for one satellite at one simulation tick."""

    node_id: str
    sim_time_unix: float
    position_ecef_km: EcefVec3
    velocity_ecef_km_s: EcefVec3
    geodetic: GeoPosition
    propagator_id: str


def propagate_satellites(
    *,
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    epoch_unix: float,
    dt: float,
    propagator_id: str = "keplerian-circular",
) -> dict[str, PropagatedState]:
    """Propagate all satellites for one tick.

    Unsupported propagators are fatal configuration/engine errors. A session
    that asks for J2 or SGP4 before those engines exist must fail before
    dispatch rather than receiving lower-fidelity Keplerian positions.
    """
    if propagator_id != "keplerian-circular":
        raise ValueError(f"Unsupported OME propagator: {propagator_id!r}")

    sim_time_unix = epoch_unix + dt
    states: dict[str, PropagatedState] = {}
    for sat in satellites:
        node_id = addressing.sat_id(sat.plane, sat.slot)
        pos_ecef, vel_ecef, geo = propagate_keplerian(sat.elements, epoch_unix, dt)
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
