# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Unit tests for OME ground visibility evaluation."""

from __future__ import annotations

import nodalarc.constellation_loader as constellation_loader
import pytest
from nodalarc.body_frames import LUNA_BODY_FRAME
from nodalarc.frames import EcefVec3, GeoPosition, Vec3
from nodalarc.geo import geodetic_to_ecef
from nodalarc.ground_terminals import TerminalPhysicsProfile
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.ground_station import GroundStationConfig, GroundStationFile, GroundTerminalDef
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from ome.event_stream import build_step_context
from ome.ground_visibility_engine import GroundPassLookahead, evaluate_ground_visibility
from ome.propagation_engine import PropagatedState
from ome.visibility import GroundVisibility


def _state(node_id: str, geo: GeoPosition, velocity: Vec3 | None = None) -> PropagatedState:
    return PropagatedState(
        node_id=node_id,
        sim_time_unix=0.0,
        position_ecef_km=geodetic_to_ecef(geo),
        velocity_ecef_km_s=EcefVec3(velocity or Vec3(0.0, 0.0, 0.0)),
        geodetic=geo,
        propagator_id="test-fixture",
    )


def _gs_default_kwargs(gs_id: str = "gs-equator") -> dict:
    """Minimum per-GS context required by Direction 2 + Direction 3."""
    return {
        "gs_tenant_ids": {gs_id: "default"},
        "gs_reference_bodies": {gs_id: "earth"},
    }


def _physical_kwargs(
    *,
    gs_id: str = "gs-equator",
    sat_id: str = "sat-a",
    max_range_km: float = 2400.0,
    field_of_regard_deg: float = 130.0,
    max_tracking_rate_deg_s: float = 6.0,
) -> dict:
    return {
        "simulation_fidelity": "physical_v1",
        "gs_terminal_profiles": {
            gs_id: TerminalPhysicsProfile(
                profile_id=f"{gs_id}.terminals",
                max_range_km=max_range_km,
                field_of_regard_deg=field_of_regard_deg,
                max_tracking_rate_deg_s=max_tracking_rate_deg_s,
                boresight=TerminalBoresight(mode="local_vertical"),
            )
        },
        "sat_ground_terminal_profiles": {
            sat_id: TerminalPhysicsProfile(
                profile_id=f"{sat_id}.ground_terminals",
                max_range_km=max_range_km,
                field_of_regard_deg=field_of_regard_deg,
                max_tracking_rate_deg_s=max_tracking_rate_deg_s,
                boresight=SatGroundTerminalBoresight(
                    target_body="earth",
                    mode="nadir",
                ),
                target_body="earth",
            )
        },
    }


def test_ground_visibility_evaluates_all_station_satellite_pairs_with_physical_constraints():
    gs_geo = GeoPosition(0.0, 0.0, 0.0)
    sat_geo = GeoPosition(0.0, 0.0, 550.0)

    result = evaluate_ground_visibility(
        satellite_ids=("sat-a",),
        sat_states={"sat-a": _state("sat-a", sat_geo)},
        gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
        gs_min_elevations={"gs-equator": 25.0},
        **_gs_default_kwargs(),
        **_physical_kwargs(),
    )

    pair = ("gs-equator", "sat-a")
    assert pair in result.decisions
    decision = result.decisions[pair]
    assert decision.visible is True
    assert decision.range_km > 500.0
    assert decision.elevation_deg > 0.0
    assert decision.tenant_id == "default"
    assert decision.reference_body == "earth"
    assert decision.observer_frame == "body_local"
    assert decision.reject_reason == "ok"
    assert decision.rejecting_endpoint == "none"
    assert decision.applied_gs_max_range_km == 2400.0
    assert decision.applied_sat_max_range_km == 2400.0
    assert decision.applied_gs_field_of_regard_deg == 130.0
    assert decision.applied_sat_field_of_regard_deg == 130.0
    assert decision.applied_gs_max_tracking_rate_deg_s == 6.0
    assert decision.applied_sat_max_tracking_rate_deg_s == 6.0
    assert decision.applied_gs_boresight_mode == "local_vertical"
    assert decision.applied_sat_boresight_mode == "nadir"
    assert decision.applied_gs_terminal_profile == "gs-equator.terminals"
    assert decision.applied_sat_terminal_profile == "sat-a.ground_terminals"
    assert result.visible_per_station["gs-equator"][0].sat_id == "sat-a"


def test_physical_visibility_rejects_range_before_allocator_candidate_set():
    gs_geo = GeoPosition(0.0, 0.0, 0.0)

    result = evaluate_ground_visibility(
        satellite_ids=("sat-a",),
        sat_states={"sat-a": _state("sat-a", GeoPosition(0.0, 0.0, 550.0))},
        gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
        gs_min_elevations={"gs-equator": 25.0},
        **_gs_default_kwargs(),
        **_physical_kwargs(max_range_km=100.0),
    )

    decision = result.decisions[("gs-equator", "sat-a")]
    assert decision.visible is False
    assert decision.reject_reason == "range_exceeded"
    assert decision.rejecting_endpoint == "both"
    assert result.visible_per_station["gs-equator"] == []


def test_physical_visibility_requires_endpoint_profiles():
    gs_geo = GeoPosition(0.0, 0.0, 0.0)

    with pytest.raises(ValueError, match="ground terminal profiles"):
        evaluate_ground_visibility(
            satellite_ids=("sat-a",),
            sat_states={"sat-a": _state("sat-a", GeoPosition(0.0, 0.0, 550.0))},
            gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
            gs_min_elevations={"gs-equator": 25.0},
            **_gs_default_kwargs(),
        )


def test_ground_visibility_missing_propagated_state_fails_loudly():
    gs_geo = GeoPosition(0.0, 0.0, 0.0)

    with pytest.raises(ValueError, match="Missing propagated satellite state"):
        evaluate_ground_visibility(
            satellite_ids=("sat-missing",),
            sat_states={},
            gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
            gs_min_elevations={"gs-equator": 25.0},
            **_gs_default_kwargs(),
            **_physical_kwargs(sat_id="sat-missing"),
        )


def test_ground_visibility_missing_tenant_fails_loudly():
    """Direction 2: every visibility decision must carry tenant scope."""
    gs_geo = GeoPosition(0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="tenant_id"):
        evaluate_ground_visibility(
            satellite_ids=("sat-a",),
            sat_states={"sat-a": _state("sat-a", GeoPosition(0.0, 0.0, 550.0))},
            gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
            gs_min_elevations={"gs-equator": 25.0},
            gs_tenant_ids={},
            gs_reference_bodies={"gs-equator": "earth"},
        )


def test_ground_visibility_missing_reference_body_fails_loudly():
    """Direction 3: every visibility decision is anchored to a specific body."""
    gs_geo = GeoPosition(0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="reference_body"):
        evaluate_ground_visibility(
            satellite_ids=("sat-a",),
            sat_states={"sat-a": _state("sat-a", GeoPosition(0.0, 0.0, 550.0))},
            gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
            gs_min_elevations={"gs-equator": 25.0},
            gs_tenant_ids={"gs-equator": "default"},
            gs_reference_bodies={},
        )


def test_ground_visibility_carries_rejection_reason_for_invisible_pair():
    """Non-visible pairs carry the typed `reject_reason`."""
    gs_geo = GeoPosition(0.0, 0.0, 0.0)
    sat_geo = GeoPosition(10.0, 0.0, 550.0)
    result = evaluate_ground_visibility(
        satellite_ids=("sat-a",),
        sat_states={"sat-a": _state("sat-a", sat_geo)},
        gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
        gs_min_elevations={"gs-equator": 25.0},
        **_gs_default_kwargs(),
        **_physical_kwargs(),
    )
    decision = result.decisions[("gs-equator", "sat-a")]
    assert decision.visible is False
    assert decision.reject_reason == "elevation_below_min"
    assert decision.rejecting_endpoint == "none"


def test_longest_remaining_pass_populates_sampled_dwell(monkeypatch):
    gs_geo = GeoPosition(0.0, 0.0, 0.0)

    def fake_check_ground_visibility(_gs_ecef, _gs_geo, sat_ecef, _min_elev):
        # sat-short uses y=1 and drops at t=2; sat-long uses y=2 and drops at t=4.
        visible_until = 2.0 if sat_ecef.y == 1.0 else 4.0
        visible = sat_ecef.x < visible_until
        elevation = 70.0 if sat_ecef.y == 1.0 else 30.0
        return GroundVisibility(
            sat_id="",
            visible=visible,
            elevation_deg=elevation if visible else -10.0,
            range_km=1000.0,
            remaining_visible_s=None,
            reject_reason="ok" if visible else "elevation_below_min",
        )

    def fake_propagate_satellites(**kwargs):
        dt = float(kwargs["dt"])
        return {
            "sat-short": PropagatedState(
                "sat-short",
                dt,
                EcefVec3(Vec3(dt, 1.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test",
            ),
            "sat-long": PropagatedState(
                "sat-long",
                dt,
                EcefVec3(Vec3(dt, 2.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test",
            ),
        }

    monkeypatch.setattr(
        "ome.ground_visibility_engine.check_ground_visibility",
        fake_check_ground_visibility,
    )
    monkeypatch.setattr(
        "ome.ground_visibility_engine.propagate_satellites",
        fake_propagate_satellites,
    )

    result = evaluate_ground_visibility(
        satellite_ids=("sat-short", "sat-long"),
        sat_states={
            "sat-short": PropagatedState(
                "sat-short",
                0.0,
                EcefVec3(Vec3(0.0, 1.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test",
            ),
            "sat-long": PropagatedState(
                "sat-long",
                0.0,
                EcefVec3(Vec3(0.0, 2.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test",
            ),
        },
        gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
        gs_min_elevations={"gs-equator": 25.0},
        gs_policies={"gs-equator": "longest-remaining-pass"},
        pass_lookahead=GroundPassLookahead(
            satellites=(),
            addressing=object(),
            epoch_unix=0.0,
            step=0,
            step_seconds=1,
            horizon_ticks=5,
            propagator_id="test",
            simulation_fidelity="geometry_only",
        ),
        simulation_fidelity="geometry_only",
        **_gs_default_kwargs(),
    )

    remaining = {
        gv.sat_id: gv.remaining_visible_s for gv in result.visible_per_station["gs-equator"]
    }
    assert remaining == {"sat-short": 1.0, "sat-long": 3.0}


def test_longest_remaining_pass_without_lookahead_fails_loudly():
    gs_geo = GeoPosition(0.0, 0.0, 0.0)

    with pytest.raises(ValueError, match="requires pass lookahead"):
        evaluate_ground_visibility(
            satellite_ids=("sat-a",),
            sat_states={"sat-a": _state("sat-a", GeoPosition(0.0, 0.0, 550.0))},
            gs_positions={"gs-equator": (geodetic_to_ecef(gs_geo), gs_geo)},
            gs_min_elevations={"gs-equator": 25.0},
            gs_policies={"gs-equator": "longest-remaining-pass"},
            **_gs_default_kwargs(),
            **_physical_kwargs(),
        )


def test_satellite_profiles_select_matching_target_body_for_cislunar_relay():
    gs_id = "gs-luna"
    sat_id = "sat-relay"
    luna_radius = LUNA_BODY_FRAME.equatorial_radius_km
    gs_geo = GeoPosition(0.0, 0.0, 0.0)
    gs_ecef = EcefVec3(Vec3(luna_radius, 0.0, 0.0))
    sat_state = PropagatedState(
        sat_id,
        0.0,
        EcefVec3(Vec3(luna_radius + 550.0, 0.0, 0.0)),
        EcefVec3(Vec3(0.0, 0.0, 0.0)),
        GeoPosition(0.0, 0.0, 550.0),
        "test-fixture",
    )

    result = evaluate_ground_visibility(
        satellite_ids=(sat_id,),
        sat_states={sat_id: sat_state},
        gs_positions={gs_id: (gs_ecef, gs_geo)},
        gs_min_elevations={gs_id: 25.0},
        gs_tenant_ids={gs_id: "default"},
        gs_reference_bodies={gs_id: "luna"},
        simulation_fidelity="physical_v1",
        gs_terminal_profiles={
            gs_id: TerminalPhysicsProfile(
                profile_id=f"{gs_id}.terminals",
                max_range_km=2000.0,
                field_of_regard_deg=120.0,
                max_tracking_rate_deg_s=10.0,
                boresight=TerminalBoresight(mode="local_vertical"),
            )
        },
        sat_ground_terminal_profiles={
            sat_id: (
                TerminalPhysicsProfile(
                    profile_id=f"{sat_id}.ground_terminals[0]",
                    max_range_km=2000.0,
                    field_of_regard_deg=120.0,
                    max_tracking_rate_deg_s=10.0,
                    boresight=SatGroundTerminalBoresight(
                        target_body="earth",
                        mode="nadir",
                    ),
                    target_body="earth",
                ),
                TerminalPhysicsProfile(
                    profile_id=f"{sat_id}.ground_terminals[1]",
                    max_range_km=2000.0,
                    field_of_regard_deg=120.0,
                    max_tracking_rate_deg_s=10.0,
                    boresight=SatGroundTerminalBoresight(
                        target_body="luna",
                        mode="nadir",
                    ),
                    target_body="luna",
                ),
            )
        },
    )

    decision = result.decisions[(gs_id, sat_id)]
    assert decision.visible
    assert decision.applied_sat_terminal_profile == f"{sat_id}.ground_terminals[1]"
    assert decision.reference_body == "luna"


def test_cislunar_satellite_type_config_flows_through_build_context_to_engine(
    tmp_path,
    monkeypatch,
):
    sat_type_dir = tmp_path / "satellite-types"
    sat_type_dir.mkdir()
    (sat_type_dir / "cislunar-relay.yaml").write_text(
        """
satellite_type:
  name: cislunar-relay
  isl_terminals:
    - type: optical
      count: 1
      max_range_km: 5000.0
      bandwidth_mbps: 100000.0
      max_tracking_rate_deg_s: 5.0
  ground_terminals:
    - type: rf
      count: 1
      bandwidth_mbps: 1000.0
      max_range_km: 2000.0
      field_of_regard_deg: 120.0
      max_tracking_rate_deg_s: 6.0
      boresight:
        target_body: earth
        mode: nadir
    - type: rf
      count: 1
      bandwidth_mbps: 1000.0
      max_range_km: 2000.0
      field_of_regard_deg: 120.0
      max_tracking_rate_deg_s: 6.0
      boresight:
        target_body: luna
        mode: nadir
"""
    )
    monkeypatch.setattr(constellation_loader, "_SAT_TYPE_DIR", sat_type_dir)
    constellation_loader.load_satellite_type.cache_clear()

    constellation = constellation_loader.load_constellation(
        {
            "mode": "parametric",
            "name": "cislunar-config-test",
            "satellite_type": "cislunar-relay",
            "orbit": {
                "altitude_km": 550.0,
                "inclination_deg": 0.0,
                "pattern": "walker-delta",
            },
            "planes": {
                "count": 1,
                "raan_spacing_deg": 360.0,
                "sats_per_plane": 1,
                "phase_offset_deg": 0.0,
            },
        }
    )
    satellites = constellation_loader.expand_constellation(constellation)
    addressing = AddressingScheme()
    gs_file = GroundStationFile(
        default_terminals=[
            GroundTerminalDef(
                type="rf",
                count=1,
                bandwidth_mbps=1000.0,
                tracking_capacity=1,
                max_range_km=2000.0,
                field_of_regard_deg=120.0,
                max_tracking_rate_deg_s=6.0,
                boresight=TerminalBoresight(mode="local_vertical"),
            )
        ],
        stations=[
            GroundStationConfig(
                name="luna",
                lat_deg=0.0,
                lon_deg=0.0,
                reference_body="luna",
            )
        ],
    )
    ctx = build_step_context(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=frozenset(),
        propagator_id="keplerian-circular",
        simulation_fidelity="physical_v1",
    )

    sat_id = addressing.sat_id(0, 0)
    gs_id = addressing.gs_id("luna")
    luna_radius = LUNA_BODY_FRAME.equatorial_radius_km
    gs_geo = GeoPosition(0.0, 0.0, 0.0)
    result = evaluate_ground_visibility(
        satellite_ids=(sat_id,),
        sat_states={
            sat_id: PropagatedState(
                sat_id,
                0.0,
                EcefVec3(Vec3(luna_radius + 550.0, 0.0, 0.0)),
                EcefVec3(Vec3(0.0, 0.0, 0.0)),
                GeoPosition(0.0, 0.0, 550.0),
                "test-fixture",
            )
        },
        gs_positions={gs_id: (EcefVec3(Vec3(luna_radius, 0.0, 0.0)), gs_geo)},
        gs_min_elevations=ctx.gs_min_elevations,
        gs_tenant_ids=ctx.gs_tenant_ids,
        gs_reference_bodies=ctx.gs_reference_bodies,
        simulation_fidelity="physical_v1",
        gs_terminal_profiles=ctx.gs_terminal_profiles,
        sat_ground_terminal_profiles=ctx.sat_ground_terminal_profiles,
    )

    decision = result.decisions[(gs_id, sat_id)]
    assert decision.visible
    assert decision.reference_body == "luna"
    assert decision.applied_sat_terminal_profile == f"{sat_id}.ground_terminals[1]"
