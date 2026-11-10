# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Inputs for OME's per-direction link checks, built from both ends of a link at one instant.

Constraint tests state a link's geometry as both ends' positions (and velocities) in their body's
fixed frame at one instant. These helpers build the link's two directions (a to b, b to a) from
that geometry, run the production checks in ``ome.direction_checks`` on them, and return the
link's verdict. In each direction an end points straight at the other end, the path length is the
distance between the ends, and the line-of-sight rate is the relative velocity perpendicular to
the line of sight divided by the distance. No body blocks the path unless a test says so.

Light time, aberration and occlusion come from the signal layer, which is tested against real
endpoints in tests/unit/physics.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from nodalarc.frames import GeoPosition
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from ome.direction_checks import (
    DirectionEnds,
    EndArrays,
    GroundLimits,
    GroundLimitTable,
    GroundPairVerdict,
    IslLimits,
    ground_directions,
    ground_pair_verdicts,
    isl_reasons,
)

type Vector = Sequence[float]

AT_REST: Vector = (0.0, 0.0, 0.0)


def _array(value: Vector) -> np.ndarray:
    return np.array([float(component) for component in value])


def _ends(
    pos_a: Vector, vel_a: Vector, pos_b: Vector, vel_b: Vector
) -> tuple[EndArrays, EndArrays, float]:
    """End a pointing at b, end b pointing at a, and the distance between them."""
    a, b = _array(pos_a), _array(pos_b)
    sight = b - a
    distance = float(np.linalg.norm(sight))
    if distance == 0.0:
        raise ValueError("the two ends coincide; a direction between them has no pointing")
    toward_b = sight / distance
    relative = _array(vel_b) - _array(vel_a)
    across = relative - (relative @ toward_b) * toward_b
    rate = np.array([float(np.linalg.norm(across)) / distance])
    # Both ends belong to one body centred at the origin, so each points toward it along -position.
    end_a = EndArrays(a[None, :], toward_b[None, :], rate, _toward_centre(a))
    end_b = EndArrays(b[None, :], -toward_b[None, :], rate, _toward_centre(b))
    return end_a, end_b, distance


def _toward_centre(position: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(position))
    return (-position / length if length > 0.0 else np.zeros(3))[None, :]


def _stack(first: EndArrays, second: EndArrays) -> EndArrays:
    return EndArrays(
        np.concatenate([first.fixed_km, second.fixed_km]),
        np.concatenate([first.pointing_fixed, second.pointing_fixed]),
        np.concatenate([first.rate_rad_s, second.rate_rad_s]),
        np.concatenate([first.toward_other_body_fixed, second.toward_other_body_fixed]),
    )


def isl_reason(
    pos_a: Vector,
    vel_a: Vector,
    pos_b: Vector,
    vel_b: Vector,
    *,
    max_range_km: float = math.inf,
    field_of_regard_deg: float = 360.0,
    max_tracking_rate_deg_s: float | None = None,
    polar_seam_latitudes_deg: tuple[float, float] | None = None,
    latitude_threshold_deg: float = 70.0,
    blocked: bool = False,
) -> str:
    """The ISL's verdict: "ok", or the first failing constraint of a->b, then of b->a.

    ``polar_seam_latitudes_deg`` holds the latitudes of a and b and turns the polar-seam cutoff
    on; without it the cutoff is off and no latitude is read.
    """
    end_a, end_b, distance = _ends(pos_a, vel_a, pos_b, vel_b)
    limit = IslLimits(
        max_range_km=max_range_km,
        field_of_regard_deg=field_of_regard_deg,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        polar_seam=polar_seam_latitudes_deg is not None,
    )
    lat_a, lat_b = polar_seam_latitudes_deg or (math.nan, math.nan)
    ends = DirectionEnds(
        path_km=np.array([distance, distance]),
        blocked=np.array([blocked, blocked]),
        transmitter=_stack(end_a, end_b),
        receiver=_stack(end_b, end_a),
    )
    reasons = isl_reasons(
        ends,
        [limit, limit],
        transmitter_latitude_deg=np.array([lat_a, lat_b]),
        receiver_latitude_deg=np.array([lat_b, lat_a]),
        latitude_threshold_deg=latitude_threshold_deg,
    )
    return str(reasons[0]) if reasons[0] != "ok" else str(reasons[1])


def ground_verdict(
    gs_km: Vector,
    gs_geo: GeoPosition,
    sat_km: Vector,
    *,
    min_elevation_deg: float,
    sat_velocity_km_s: Vector = AT_REST,
    gs_max_range_km: float | None = None,
    sat_max_range_km: float | None = None,
    gs_boresight: TerminalBoresight | None = None,
    gs_field_of_regard_deg: float | None = None,
    sat_boresight: SatGroundTerminalBoresight | None = None,
    sat_field_of_regard_deg: float | None = None,
    gs_max_tracking_rate_deg_s: float | None = None,
    sat_max_tracking_rate_deg_s: float | None = None,
) -> GroundPairVerdict:
    """The ground candidate's verdict from its uplink (the station transmits) and its downlink.

    The station is fixed on its body, so the satellite's body-fixed velocity is the relative
    velocity. At one instant both directions see the same two ends, so the uplink and the
    downlink are one evaluation.
    """
    station, satellite, distance = _ends(gs_km, AT_REST, sat_km, sat_velocity_km_s)
    limits = GroundLimits(
        gs_lat_deg=gs_geo.lat_deg,
        gs_lon_deg=gs_geo.lon_deg,
        min_elevation_deg=min_elevation_deg,
        gs_max_range_km=gs_max_range_km,
        sat_max_range_km=sat_max_range_km,
        gs_boresight=gs_boresight,
        gs_field_of_regard_deg=gs_field_of_regard_deg,
        sat_boresight=sat_boresight,
        sat_field_of_regard_deg=sat_field_of_regard_deg,
        gs_max_tracking_rate_deg_s=gs_max_tracking_rate_deg_s,
        sat_max_tracking_rate_deg_s=sat_max_tracking_rate_deg_s,
    )
    directions = ground_directions(
        station=station,
        satellite=satellite,
        path_km=np.array([distance]),
        blocked=np.array([False]),
        table=GroundLimitTable.from_limits([limits]),
    )
    (verdict,) = ground_pair_verdicts(directions, directions)
    return verdict
