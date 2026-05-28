# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Foundational ground-link physics contracts.

These tests lock the Phase 2 promise: a scheduled ground link must first pass
body-aware LOS, range, field-of-regard, and topocentric tracking constraints.
"""

from __future__ import annotations

import math

import pytest
from nodalarc.body_frames import EARTH_BODY_FRAME, LUNA_BODY_FRAME, BodyFrame
from nodalarc.constellation_loader import SatelliteNode
from nodalarc.ground_terminals import TerminalPhysicsProfile
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.constellation import GroundTerminal
from nodalarc.models.ground_station import GroundStationConfig, GroundStationFile, GroundTerminalDef
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from nodalarc.orbital import elements_from_params
from ome.event_stream import build_step_context, compute_step
from ome.propagation_engine import propagate_satellites
from ome.propagator import GeoPosition, Vec3, geodetic_to_ecef
from ome.visibility import (
    check_ground_visibility,
    compute_elevation_angle,
    compute_topocentric_angular_velocity,
    has_line_of_sight,
)


def _terminal_profiles() -> tuple[TerminalPhysicsProfile, TerminalPhysicsProfile]:
    gs_profile = TerminalPhysicsProfile(
        profile_id="gs-overhead.terminals",
        max_range_km=2000.0,
        field_of_regard_deg=120.0,
        max_tracking_rate_deg_s=2.0,
        boresight=TerminalBoresight(mode="local_vertical", half_angle_deg=60.0),
    )
    sat_profile = TerminalPhysicsProfile(
        profile_id="sat-P00S00.ground_terminals",
        max_range_km=2000.0,
        field_of_regard_deg=120.0,
        max_tracking_rate_deg_s=2.0,
        boresight=SatGroundTerminalBoresight(
            target_body="earth",
            mode="nadir",
            half_angle_deg=60.0,
        ),
        target_body="earth",
    )
    return gs_profile, sat_profile


@pytest.mark.parametrize("body_frame", [EARTH_BODY_FRAME, LUNA_BODY_FRAME], ids=lambda b: b.name)
def test_analytic_tangent_point_is_zero_elevation_for_supported_bodies(body_frame: BodyFrame):
    observer_geo = GeoPosition(0.0, 0.0, 0.0)
    observer = Vec3(body_frame.equatorial_radius_km, 0.0, 0.0)
    orbit_radius = body_frame.equatorial_radius_km + 550.0
    central_angle = math.acos(body_frame.equatorial_radius_km / orbit_radius)
    target = Vec3(
        orbit_radius * math.cos(central_angle),
        orbit_radius * math.sin(central_angle),
        0.0,
    )

    assert has_line_of_sight(observer, target, body_frame)
    elevation = compute_elevation_angle(observer, observer_geo, target, body_frame)
    assert elevation == pytest.approx(0.0, abs=1e-9)


def test_range_constraint_rejects_clear_los_before_allocator_can_see_pair():
    observer_geo = GeoPosition(0.0, 0.0, 0.0)
    observer = geodetic_to_ecef(observer_geo)
    earth_radius = EARTH_BODY_FRAME.equatorial_radius_km
    orbit_radius = earth_radius + 550.0
    desired_slant_km = 2200.0
    central_angle = math.acos(
        (earth_radius**2 + orbit_radius**2 - desired_slant_km**2)
        / (2.0 * earth_radius * orbit_radius)
    )
    target = Vec3(
        orbit_radius * math.cos(central_angle),
        orbit_radius * math.sin(central_angle),
        0.0,
    )

    result = check_ground_visibility(
        observer,
        observer_geo,
        target,
        min_elevation_deg=-90.0,
        max_range_km=2000.0,
        gs_boresight=TerminalBoresight(mode="local_vertical", half_angle_deg=90.0),
        sat_boresight=SatGroundTerminalBoresight(
            target_body="earth",
            mode="nadir",
            half_angle_deg=90.0,
        ),
        body_frame=EARTH_BODY_FRAME,
    )

    assert result.range_km == pytest.approx(desired_slant_km, abs=1e-6)
    assert result.reject_reason == "range_exceeded"
    assert not result.visible


@pytest.mark.parametrize("body_frame", [EARTH_BODY_FRAME, LUNA_BODY_FRAME], ids=lambda b: b.name)
def test_topocentric_angular_velocity_accounts_for_observing_body_rotation(
    body_frame: BodyFrame,
):
    observer = Vec3(body_frame.equatorial_radius_km, 0.0, 0.0)
    target = Vec3(body_frame.equatorial_radius_km + 550.0, 0.0, 0.0)
    inertial_velocity_in_body_axes = Vec3(0.0, 7.6, 0.0)
    body_fixed_velocity = Vec3(
        inertial_velocity_in_body_axes.x + body_frame.rotation_rate_rad_s * target.y,
        inertial_velocity_in_body_axes.y - body_frame.rotation_rate_rad_s * target.x,
        inertial_velocity_in_body_axes.z,
    )

    assert compute_topocentric_angular_velocity(
        observer,
        target,
        inertial_velocity_in_body_axes,
        body_frame,
        velocity_frame="inertial",
    ) == pytest.approx(
        compute_topocentric_angular_velocity(
            observer,
            target,
            body_fixed_velocity,
            body_frame,
            velocity_frame="body_fixed",
        ),
        abs=1e-12,
    )


def test_compute_step_schedules_only_pairs_that_pass_applied_ground_physics():
    epoch_unix = 1704067200.0
    addressing = AddressingScheme()
    sat = SatelliteNode(
        plane=0,
        slot=0,
        elements=elements_from_params(
            altitude_km=550.0,
            inclination_deg=0.0,
            raan_deg=0.0,
            true_anomaly_deg=0.0,
        ),
        isl_terminal_count=0,
        ground_terminal_count=1,
        isl_terminals=(),
        ground_terminals=(
            GroundTerminal(
                type="rf",
                count=1,
                bandwidth_mbps=1000.0,
                max_range_km=2000.0,
                field_of_regard_deg=120.0,
                max_tracking_rate_deg_s=2.0,
                boresight=SatGroundTerminalBoresight(
                    target_body="earth",
                    mode="nadir",
                    half_angle_deg=60.0,
                ),
            ),
        ),
    )
    propagated = propagate_satellites(
        satellites=[sat],
        addressing=addressing,
        epoch_unix=epoch_unix,
        dt=0.0,
        propagator_id="keplerian-circular",
    )
    sat_state = propagated["sat-P00S00"]
    gs_file = GroundStationFile(
        default_terminals=[
            GroundTerminalDef(
                type="rf",
                count=1,
                bandwidth_mbps=1000.0,
                tracking_capacity=1,
                max_range_km=2000.0,
                field_of_regard_deg=120.0,
                max_tracking_rate_deg_s=2.0,
                boresight=TerminalBoresight(mode="local_vertical", half_angle_deg=60.0),
            )
        ],
        default_min_elevation_deg=25.0,
        default_scheduling_policy="highest-elevation",
        stations=[
            GroundStationConfig(
                name="overhead",
                lat_deg=sat_state.geodetic.lat_deg,
                lon_deg=sat_state.geodetic.lon_deg,
                alt_m=0.0,
            )
        ],
    )
    ctx = build_step_context(
        satellites=[sat],
        addressing=addressing,
        gs_file=gs_file,
        neighbors=frozenset(),
        propagator_id="keplerian-circular",
        simulation_fidelity="physical_v1",
    )

    result = compute_step(
        ctx,
        epoch_unix,
        step=0,
        step_seconds=1,
        timestamp_offset=0.0,
        isl_state={},
        gs_state={},
        current_associations={},
    )

    pair = ("gs-overhead", "sat-P00S00")
    assert result.associations == {pair: (0, 0)}
    decision = result.ground_decisions[pair]
    assert decision.visible
    assert decision.reject_reason == "ok"
    assert decision.applied_max_range_km == 2000.0
    assert decision.applied_field_of_regard_deg == 120.0
    assert decision.applied_max_tracking_rate_deg_s == 2.0
    assert decision.range_km <= decision.applied_max_range_km

    gs_ecef, gs_geo = ctx.gs_positions["gs-overhead"]
    gs_profile, sat_profile = _terminal_profiles()
    recomputed = check_ground_visibility(
        gs_ecef,
        gs_geo,
        result.propagated_states["sat-P00S00"].position_ecef_km,
        ctx.gs_min_elevations["gs-overhead"],
        max_range_km=min(gs_profile.max_range_km, sat_profile.max_range_km),
        gs_boresight=gs_profile.boresight,
        sat_boresight=sat_profile.boresight,
        max_tracking_rate_deg_s=min(
            gs_profile.max_tracking_rate_deg_s,
            sat_profile.max_tracking_rate_deg_s,
        ),
        sat_velocity_ecef_km_s=result.propagated_states["sat-P00S00"].velocity_ecef_km_s,
        body_frame=EARTH_BODY_FRAME,
    )
    assert recomputed.visible
    assert recomputed.reject_reason == "ok"
