# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""OME propagation engine boundary.

The production implementations are explicit two-body Keplerian propagation, J2
mean-element secular propagation, and SGP4/TLE propagation. Each model is
selected by its own propagator ID with its own validation and error budget; this
module must never silently downgrade an unsupported model into Keplerian.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nodalarc.body_frames import BodyFrame
from nodalarc.frames import CommonVec3, EcefVec3, GcrsVec3, GeoPosition, Vec3
from nodalarc.models.events import NodePosition
from nodalarc.ome_runtime import (
    OmeAddressing,
    PropagatorId,
    SatelliteNode,
    SessionPropagatorId,
    satellite_node_id,
    satellite_propagator_id,
)
from nodalarc.physics import BodyState, PhysicsContext
from nodalarc.physics.kepler import OrbitModel
from nodalarc.physics.lighttime import Endpoint, OrbitEndpoint, TleEndpoint
from nodalarc.physics.satellites import (
    SatelliteStates,
    analytic_satellite_states,
    orbit_model_for,
    tle_satellite_states,
)


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
    position_common_km: CommonVec3
    velocity_common_km_s: CommonVec3
    body_origin_common_km: CommonVec3

    def __post_init__(self) -> None:
        # Common-frame slots refuse relabeled vectors at runtime. A plain
        # Vec3 wearing an EcefVec3 or EciVec3 alias is exactly the
        # frame-mislabel that once fed Earth-fixed state into cross-body
        # geometry; only a constructed CommonVec3 passes.
        for field_name in (
            "position_common_km",
            "velocity_common_km_s",
            "body_origin_common_km",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, CommonVec3):
                raise TypeError(
                    f"PropagatedState.{field_name} requires CommonVec3; got "
                    f"{type(value).__name__}: common-frame state must be "
                    "constructed from GCRS vectors, never relabeled"
                )


def _common_vec(origin: Vec3, local_inertial: GcrsVec3) -> CommonVec3:
    # The body-centered input must carry GcrsVec3 provenance. EcefVec3 is a
    # NewType, invisible at runtime, so `pos_inertial = pos_ecef` (the exact
    # defect that fed ITRS into cross-body geometry) would otherwise still
    # compose silently. Only a vector deliberately constructed on the common
    # axes passes.
    if not isinstance(local_inertial, GcrsVec3):
        raise TypeError(
            f"common-frame composition requires GcrsVec3; got "
            f"{type(local_inertial).__name__}: body-centered state must be "
            "constructed on GCRS axes, never relabeled from a body-fixed frame"
        )
    return CommonVec3(
        origin.x + local_inertial.x,
        origin.y + local_inertial.y,
        origin.z + local_inertial.z,
    )


def element_anchor_epoch_unix(sat, node_id: str) -> float:
    """The working elements' validity instant, refused loudly when absent.

    Working elements are valid at ``sat.elements_epoch_unix``. Every
    consumer derives one authoritative instant ``sim_time = epoch_unix +
    dt`` and computes canonical phase time as ``sim_time - anchor``; when
    that equals ``dt`` exactly, ``dt`` itself is used, which is the same
    number on the same path. The reported timestamp and the evaluated
    state therefore always describe one instant, and paths whose
    arithmetic was already exact stay bit-identical.
    """
    anchor = getattr(sat, "elements_epoch_unix", None)
    if anchor is None:
        raise ValueError(
            f"satellite {node_id!r} working elements carry no validity epoch; "
            "propagation derives elapsed time from the owned anchor and "
            "refuses unanchored keplerian elements"
        )
    return anchor


def satellite_endpoints(
    *,
    satellites: list[SatelliteNode],
    addressing: OmeAddressing,
    propagator_id: SessionPropagatorId,
    body_frames: Mapping[str, BodyFrame],
) -> dict[str, Endpoint]:
    """Each satellite as a signal endpoint, with the orbit model, elements and anchor
    ``propagate_satellites`` uses, so a signal sent at a tick starts where the tick's state is."""
    endpoints: dict[str, Endpoint] = {}
    for sat in satellites:
        node_id = satellite_node_id(sat, addressing)
        sat_propagator_id = satellite_propagator_id(sat, propagator_id)
        if sat_propagator_id == "sgp4-tle":
            if sat.central_body != "earth" or sat.tle_line_1 is None or sat.tle_line_2 is None:
                raise ValueError(
                    f"Satellite {node_id} needs Earth TLE lines for orbit.propagator='sgp4-tle'"
                )
            endpoints[node_id] = TleEndpoint("earth", sat.tle_line_1, sat.tle_line_2)
            continue
        endpoints[node_id] = OrbitEndpoint(
            sat.central_body,
            _required_body_frame(body_frames, sat.central_body, node_id),
            orbit_model_for(sat_propagator_id),
            sat.elements,
            element_anchor_epoch_unix(sat, node_id),
        )
    return endpoints


def propagate_satellites(
    *,
    satellites: list[SatelliteNode],
    addressing: OmeAddressing,
    epoch_unix: float,
    dt: float,
    propagator_id: SessionPropagatorId,
    body_frames: Mapping[str, BodyFrame],
    body_states: Mapping[str, BodyState],
    physics: PhysicsContext,
) -> dict[str, PropagatedState]:
    """Propagate all satellites for one tick.

    Analytic satellites (two-body and J2 mean elements) run through the shared
    physics module, one batch per central body and orbit model, each with its
    own phase from its elements' validity epoch. SGP4/TLE satellites run as one
    Earth batch through the same module, so both kinds take Earth orientation
    from one source. SGP4/TLE requests require TLE records on every such
    satellite; missing records fail before any other model can stand in.
    Unsupported propagators are fatal configuration errors.
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
    node_ids = [satellite_node_id(sat, addressing) for sat in satellites]
    sat_propagator_ids: list[PropagatorId] = [
        satellite_propagator_id(sat, propagator_id) for sat in satellites
    ]

    groups: dict[tuple[str, OrbitModel], list[int]] = {}
    for index, (sat, sat_propagator_id) in enumerate(
        zip(satellites, sat_propagator_ids, strict=True)
    ):
        if sat_propagator_id != "sgp4-tle":
            groups.setdefault((sat.central_body, orbit_model_for(sat_propagator_id)), []).append(
                index
            )

    computed: dict[int, tuple[EcefVec3, EcefVec3, GeoPosition, GcrsVec3, GcrsVec3]] = {}
    for (central_body, model), indices in groups.items():
        body_frame = _required_body_frame(body_frames, central_body, node_ids[indices[0]])
        phases = [
            sim_time_unix - element_anchor_epoch_unix(satellites[i], node_ids[i]) for i in indices
        ]
        states = analytic_satellite_states(
            physics,
            body_frame=body_frame,
            model=model,
            elements=[satellites[i].elements for i in indices],
            phases_s=phases,
            master_sim_time_unix=sim_time_unix,
        )
        _record_rows(computed, indices, states)

    tle_indices = [i for i, pid in enumerate(sat_propagator_ids) if pid == "sgp4-tle"]
    tles: list[tuple[str, str]] = []
    for index in tle_indices:
        sat = satellites[index]
        if sat.central_body != "earth":
            raise ValueError(
                f"Satellite {node_ids[index]} uses central_body={sat.central_body!r}; "
                "SGP4/TLE propagation is Earth-only"
            )
        if sat.tle_line_1 is None or sat.tle_line_2 is None:
            raise ValueError(
                f"Satellite {node_ids[index]} has no TLE lines; "
                "orbit.propagator='sgp4-tle' requires a TLE constellation"
            )
        tles.append((sat.tle_line_1, sat.tle_line_2))
    if tle_indices:
        states = tle_satellite_states(
            physics,
            body_frame=_required_body_frame(body_frames, "earth", node_ids[tle_indices[0]]),
            tles=tles,
            master_sim_time_unix=sim_time_unix,
        )
        _record_rows(computed, tle_indices, states)

    states_out: dict[str, PropagatedState] = {}
    for index, sat in enumerate(satellites):
        node_id = node_ids[index]
        central_body = sat.central_body
        sat_propagator_id = sat_propagator_ids[index]
        body_state = body_states.get(central_body)
        if body_state is None:
            raise ValueError(
                f"Propagation missing common-frame ephemeris state for central_body={central_body!r} "
                f"while propagating {node_id!r}"
            )
        pos_ecef, vel_ecef, geo, pos_common_axes, vel_common_axes = computed[index]
        states_out[node_id] = PropagatedState(
            node_id=node_id,
            sim_time_unix=sim_time_unix,
            position_ecef_km=pos_ecef,
            velocity_ecef_km_s=vel_ecef,
            position_common_km=_common_vec(body_state.position_km, pos_common_axes),
            velocity_common_km_s=_common_vec(body_state.velocity_km_s, vel_common_axes),
            body_origin_common_km=CommonVec3(*body_state.position_km),
            central_body=central_body,
            geodetic=geo,
            propagator_id=sat_propagator_id,
        )
    return states_out


def _record_rows(
    computed: dict[int, tuple[EcefVec3, EcefVec3, GeoPosition, GcrsVec3, GcrsVec3]],
    indices: list[int],
    states: SatelliteStates,
) -> None:
    for row, sat_index in enumerate(indices):
        computed[sat_index] = (
            EcefVec3(Vec3(*map(float, states.position_fixed_km[row]))),
            EcefVec3(Vec3(*map(float, states.velocity_fixed_km_s[row]))),
            states.geodetic[row],
            # Body-centered on ICRF axes, the common frame's axes.
            GcrsVec3(*map(float, states.position_icrf_km[row])),
            GcrsVec3(*map(float, states.velocity_icrf_km_s[row])),
        )


def _required_body_frame(
    body_frames: Mapping[str, BodyFrame], central_body: str, node_id: str
) -> BodyFrame:
    try:
        return body_frames[central_body]
    except KeyError as exc:
        raise ValueError(
            f"Propagation missing resolved body primitive facts for central_body={central_body!r} "
            f"while propagating {node_id!r}"
        ) from exc


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
