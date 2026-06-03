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
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.ground_station import GroundStationConfig, GroundStationFile, GroundTerminalDef
from nodalarc.models.session import GroundSchedulingConfig
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from nodalarc.orbital import elements_from_params
from ome.event_stream import build_step_context, compute_step
from ome.propagation_engine import propagate_satellites
from ome.propagator import GeoPosition, Vec3
from ome.visibility import (
    check_ground_visibility,
    compute_elevation_angle,
    compute_topocentric_angular_velocity,
    has_line_of_sight,
)


def _ground_scheduling() -> GroundSchedulingConfig:
    return GroundSchedulingConfig(
        selection_policy=SelectionPolicySpec(name="highest-elevation", params={}),
        handover_policy=HandoverPolicySpec(name="none", params={}),
    )


def _terminal_profiles() -> tuple[TerminalPhysicsProfile, TerminalPhysicsProfile]:
    gs_profile = TerminalPhysicsProfile(
        profile_id="gs-overhead.terminals",
        max_range_km=2000.0,
        field_of_regard_deg=120.0,
        max_tracking_rate_deg_s=2.0,
        boresight=TerminalBoresight(mode="local_vertical"),
    )
    sat_profile = TerminalPhysicsProfile(
        profile_id="sat-P00S00.ground_terminals",
        max_range_km=2000.0,
        field_of_regard_deg=120.0,
        max_tracking_rate_deg_s=2.0,
        boresight=SatGroundTerminalBoresight(
            target_body="earth",
            mode="nadir",
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
    elevation = compute_elevation_angle(observer, observer_geo, target)
    assert elevation == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("body_frame", [EARTH_BODY_FRAME, LUNA_BODY_FRAME], ids=lambda b: b.name)
def test_subsatellite_point_is_ninety_degree_elevation_for_supported_bodies(
    body_frame: BodyFrame,
):
    observer_geo = GeoPosition(0.0, 0.0, 0.0)
    observer = Vec3(body_frame.equatorial_radius_km, 0.0, 0.0)
    target = Vec3(body_frame.equatorial_radius_km + 550.0, 0.0, 0.0)

    result = check_ground_visibility(
        observer,
        observer_geo,
        target,
        min_elevation_deg=25.0,
        gs_max_range_km=2000.0,
        sat_max_range_km=2000.0,
        gs_boresight=TerminalBoresight(mode="local_vertical"),
        gs_field_of_regard_deg=120.0,
        sat_boresight=SatGroundTerminalBoresight(
            target_body=body_frame.name,
            mode="nadir",
        ),
        sat_field_of_regard_deg=120.0,
        body_frame=body_frame,
    )

    assert result.visible
    assert result.reject_reason == "ok"
    assert result.rejecting_endpoint == "none"
    assert result.elevation_deg == pytest.approx(90.0, abs=1e-9)


@pytest.mark.parametrize(
    ("body_frame", "desired_slant_km", "max_range_km"),
    [
        (EARTH_BODY_FRAME, 2200.0, 2000.0),
        (LUNA_BODY_FRAME, 1000.0, 800.0),
    ],
    ids=lambda value: value.name if isinstance(value, BodyFrame) else str(value),
)
def test_range_constraint_rejects_clear_los_before_allocator_can_see_pair(
    body_frame: BodyFrame,
    desired_slant_km: float,
    max_range_km: float,
):
    observer_geo = GeoPosition(0.0, 0.0, 0.0)
    observer = Vec3(body_frame.equatorial_radius_km, 0.0, 0.0)
    orbit_radius = body_frame.equatorial_radius_km + 550.0
    central_angle = math.acos(
        (body_frame.equatorial_radius_km**2 + orbit_radius**2 - desired_slant_km**2)
        / (2.0 * body_frame.equatorial_radius_km * orbit_radius)
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
        gs_max_range_km=max_range_km,
        sat_max_range_km=max_range_km + 1000.0,
        gs_boresight=TerminalBoresight(mode="local_vertical"),
        gs_field_of_regard_deg=180.0,
        sat_boresight=SatGroundTerminalBoresight(
            target_body=body_frame.name,
            mode="nadir",
        ),
        sat_field_of_regard_deg=180.0,
        body_frame=body_frame,
    )

    assert result.range_km == pytest.approx(desired_slant_km, abs=1e-6)
    assert result.reject_reason == "range_exceeded"
    assert result.rejecting_endpoint == "ground"
    assert not result.visible


@pytest.mark.parametrize("body_frame", [EARTH_BODY_FRAME, LUNA_BODY_FRAME], ids=lambda b: b.name)
def test_topocentric_angular_velocity_accounts_for_observing_body_rotation(
    body_frame: BodyFrame,
):
    observer = Vec3(body_frame.equatorial_radius_km, 0.0, 0.0)
    altitude_km = 550.0
    target_radius_km = body_frame.equatorial_radius_km + altitude_km
    target = Vec3(target_radius_km, 0.0, 0.0)
    inertial_velocity_in_body_axes = Vec3(0.0, 7.6, 0.0)

    expected_deg_s = math.degrees(
        abs(7.6 - body_frame.rotation_rate_rad_s * target_radius_km) / altitude_km
    )
    assert compute_topocentric_angular_velocity(
        observer,
        target,
        inertial_velocity_in_body_axes,
        body_frame,
        velocity_frame="inertial",
    ) == pytest.approx(expected_deg_s, rel=1e-12, abs=1e-12)


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
                boresight=TerminalBoresight(mode="local_vertical"),
            )
        ],
        default_min_elevation_deg=25.0,
        default_selection_policy=SelectionPolicySpec(name="highest-elevation"),
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
        ground_scheduling=_ground_scheduling(),
        ground_link_model="terminal_physics",
        ground_candidate_satellites_by_gs={"gs-overhead": ("sat-P00S00",)},
    )

    pair = ("gs-overhead", "sat-P00S00")
    associations: dict[tuple[str, str], tuple[int, int]] = {}
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]] = {}
    saw_scheduled_pair = False

    for step in range(60):
        result = compute_step(
            ctx,
            epoch_unix,
            step=step,
            step_seconds=1,
            timestamp_offset=0.0,
            isl_state={},
            gs_state=gs_state,
            current_associations=associations,
        )
        associations = result.associations
        for scheduled_pair in result.associations:
            saw_scheduled_pair = True
            decision = result.ground_decisions[scheduled_pair]
            assert decision.visible
            assert decision.reject_reason == "ok"
            assert decision.rejecting_endpoint == "none"
            assert decision.applied_gs_max_range_km == 2000.0
            assert decision.applied_sat_max_range_km == 2000.0
            assert decision.applied_gs_field_of_regard_deg == 120.0
            assert decision.applied_sat_field_of_regard_deg == 120.0
            assert decision.applied_gs_max_tracking_rate_deg_s == 2.0
            assert decision.applied_sat_max_tracking_rate_deg_s == 2.0

            gs_ecef, gs_geo = ctx.gs_positions[scheduled_pair[0]]
            gs_profile, sat_profile = _terminal_profiles()
            recomputed = check_ground_visibility(
                gs_ecef,
                gs_geo,
                result.propagated_states[scheduled_pair[1]].position_ecef_km,
                ctx.gs_min_elevations[scheduled_pair[0]],
                gs_max_range_km=gs_profile.max_range_km,
                sat_max_range_km=sat_profile.max_range_km,
                gs_boresight=gs_profile.boresight,
                gs_field_of_regard_deg=gs_profile.field_of_regard_deg,
                sat_boresight=sat_profile.boresight,
                sat_field_of_regard_deg=sat_profile.field_of_regard_deg,
                gs_max_tracking_rate_deg_s=gs_profile.max_tracking_rate_deg_s,
                sat_max_tracking_rate_deg_s=sat_profile.max_tracking_rate_deg_s,
                sat_velocity_ecef_km_s=result.propagated_states[
                    scheduled_pair[1]
                ].velocity_ecef_km_s,
                body_frame=EARTH_BODY_FRAME,
            )
            assert recomputed.visible
            assert recomputed.reject_reason == "ok"

    assert saw_scheduled_pair
    assert pair in associations
