# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""The physics context of a resolved session, bound to the files it recorded.

The resolved session names the ephemeris files its results come from. Every
service that computes positions for a session builds its context here, so a
service whose image carries different data refuses the session instead of
publishing positions from other data under the same record. A shared
propagation function does not by itself guarantee identical inputs.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np

from nodalarc.body_frames import BodyFrame, body_runtime_support_for
from nodalarc.frames import CommonVec3, GeoPosition
from nodalarc.orbital import OrbitalElements
from nodalarc.physics.context import PhysicsContext
from nodalarc.physics.errors import PhysicsDataError
from nodalarc.physics.geodesy import body_fixed_from_geodetic
from nodalarc.physics.kepler import OrbitModel
from nodalarc.physics.link_delay import LinkEnd
from nodalarc.physics.satellites import analytic_satellite_states

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nodalarc.models.resolved_session import (
        ResolvedBodyFacts,
        ResolvedNode,
        ResolvedOrbitFacts,
        ResolvedSession,
    )

_ORBIT_MODELS: dict[str, OrbitModel] = {
    "two_body": "two-body",
    "j2_mean_elements": "j2-mean-elements",
}


def session_active_bodies(resolved: ResolvedSession) -> frozenset[str]:
    """Every body a node of the session orbits or stands on."""
    active = frozenset(
        body
        for node in resolved.nodes
        for body in (node.central_body, node.reference_body)
        if body is not None
    )
    if not active:
        raise ValueError("resolved session contains no active body references")
    return active


def session_physics(resolved: ResolvedSession) -> PhysicsContext:
    """The session's physics context, refused unless it loads the recorded files."""
    physics = PhysicsContext(session_active_bodies(resolved))
    loaded = tuple((f.file_id, f.sha256) for f in physics.selected_files)
    recorded = tuple((f.file_id, f.sha256) for f in resolved.ephemeris_files)
    if loaded != recorded:
        raise PhysicsDataError(
            f"this service's ephemeris data {loaded} differs from the files the resolved "
            f"session recorded {recorded}"
        )
    return physics


def body_frame_from_facts(facts: ResolvedBodyFacts) -> BodyFrame:
    """A body's constants: catalog facts plus the runtime's J2 and rotation rate."""
    try:
        runtime_support = body_runtime_support_for(facts.body_id)
    except ValueError as exc:
        raise ValueError(
            f"body {facts.body_id!r} has primitive physical facts, but the runtime "
            "has no J2 support for that body"
        ) from exc
    return BodyFrame(
        name=facts.body_id,
        mean_radius_km=facts.mean_radius_km,
        equatorial_radius_km=facts.equatorial_radius_km,
        polar_radius_km=facts.polar_radius_km,
        gravitational_parameter_km3_s2=facts.gravitational_parameter_km3_s2,
        j2=runtime_support.j2,
    )


def orbital_elements_from_facts(orbit: ResolvedOrbitFacts) -> OrbitalElements:
    """The orbit's authored elements, valid at the orbit's declared epoch."""
    return OrbitalElements(
        semi_major_axis_km=orbit.semi_major_axis_km,
        inclination_rad=math.radians(orbit.inclination_deg),
        raan_rad=math.radians(orbit.raan_deg),
        mean_anomaly_rad=math.radians(orbit.mean_anomaly_deg),
        eccentricity=orbit.eccentricity,
        argument_of_perigee_rad=math.radians(orbit.argument_of_perigee_deg),
    )


def orbit_epoch_unix(raw: str, node_id: str) -> float:
    """The orbit's declared epoch as Unix seconds; refuses naive timestamps."""
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"satellite {node_id!r} orbit epoch {raw!r} lacks an explicit UTC offset")
    return parsed.timestamp()


def session_node_positions(
    resolved: ResolvedSession, physics: PhysicsContext, master_sim_time_unix: float
) -> dict[str, CommonVec3]:
    """Every node's position in the common frame at one instant.

    The common frame has Earth's center as origin and ICRF axes. Satellites
    come from their orbit model at the instant, rotated from their body's
    orbit basis onto ICRF axes; TLE satellites from SGP4 through Skyfield's
    GCRS; surface sites from the catalog ellipsoid through the body's measured
    orientation. Each adds its body's position, so distances between nodes of
    different bodies, and between ground and space nodes, are real distances.
    A node with neither orbit facts nor a surface position is refused.
    """
    from nodalarc.propagator import propagate_sgp4_tle_gcrs

    frames = {facts.body_id: body_frame_from_facts(facts) for facts in resolved.bodies}
    origins = physics.body_states(session_active_bodies(resolved), master_sim_time_unix)

    def frame_for(body: str, node_id: str) -> BodyFrame:
        try:
            return frames[body]
        except KeyError as exc:
            raise ValueError(f"node {node_id!r} needs body facts for {body!r}") from exc

    def common(body: str, relative: np.ndarray) -> CommonVec3:
        origin = origins[body].position_km
        return CommonVec3(
            origin.x + float(relative[0]),
            origin.y + float(relative[1]),
            origin.z + float(relative[2]),
        )

    positions: dict[str, CommonVec3] = {}
    groups: dict[tuple[str, OrbitModel], list[tuple[str, OrbitalElements, float]]] = {}
    for node in resolved.nodes:
        orbit = node.orbit
        if orbit is not None and orbit.propagator in _ORBIT_MODELS:
            phase = master_sim_time_unix - orbit_epoch_unix(orbit.epoch, node.node_id)
            groups.setdefault((orbit.central_body, _ORBIT_MODELS[orbit.propagator]), []).append(
                (node.node_id, orbital_elements_from_facts(orbit), phase)
            )
        elif orbit is not None and orbit.propagator == "sgp4_tle":
            if orbit.tle_line_1 is None or orbit.tle_line_2 is None:
                raise ValueError(f"space node {node.node_id!r} is missing resolved TLE records")
            if orbit.central_body != "earth":
                raise ValueError(
                    f"space node {node.node_id!r} uses SGP4/TLE around {orbit.central_body!r}; "
                    "SGP4/TLE is Earth-only"
                )
            position_gcrs, _velocity = propagate_sgp4_tle_gcrs(
                orbit.tle_line_1, orbit.tle_line_2, master_sim_time_unix
            )
            positions[node.node_id] = common("earth", np.array(position_gcrs))
        elif orbit is not None:
            raise ValueError(
                f"node {node.node_id!r} uses propagator {orbit.propagator!r}, which has no "
                "position at this instant in the current runtime"
            )
        elif node.surface_position is not None:
            site = node.surface_position
            fixed = body_fixed_from_geodetic(
                GeoPosition(site.lat_deg, site.lon_deg, site.alt_m / 1000.0),
                frame_for(site.body, node.node_id),
            )
            rotation = physics.orientation(site.body, master_sim_time_unix).rotation
            positions[node.node_id] = common(site.body, rotation.T @ np.array(fixed))
        else:
            raise ValueError(
                f"node {node.node_id!r} has neither resolved orbit facts nor a surface position"
            )
    for (body, model), members in groups.items():
        states = analytic_satellite_states(
            physics,
            body_frame=frame_for(body, members[0][0]),
            model=model,
            elements=[elements for _node_id, elements, _phase in members],
            phases_s=[phase for _node_id, _elements, phase in members],
            master_sim_time_unix=master_sim_time_unix,
        )
        for row, (node_id, _elements, _phase) in enumerate(members):
            positions[node_id] = common(body, states.position_icrf_km[row])
    return positions


def node_link_end(node: ResolvedNode, bodies: Mapping[str, ResolvedBodyFacts]) -> LinkEnd:
    """A node as a link endpoint: the body it orbits or stands on, and the farthest it gets from
    that body's center (an orbit's apoapsis radius; a site's equatorial radius plus altitude)."""
    if node.orbit is not None:
        reach_km = node.orbit.semi_major_axis_km * (1.0 + node.orbit.eccentricity)
        return LinkEnd(node.orbit.central_body, reach_km)
    if node.surface_position is not None:
        site = node.surface_position
        try:
            radius_km = bodies[site.body].equatorial_radius_km
        except KeyError as exc:
            raise ValueError(f"node {node.node_id!r} needs body facts for {site.body!r}") from exc
        return LinkEnd(site.body, radius_km + site.alt_m / 1000.0)
    raise ValueError(
        f"node {node.node_id!r} has neither resolved orbit facts nor a surface position"
    )
