# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Contracts for terminal physics schema boundaries."""

from __future__ import annotations

import pytest
from nodalarc.ground_terminals import TerminalPhysicsProfile
from nodalarc.models.constellation import GroundTerminal
from nodalarc.models.ground_station import GroundTerminalDef as StationGroundTerminalDef
from nodalarc.models.satellite_type import GroundTerminalDef as SatelliteTypeGroundTerminalDef
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from pydantic import ValidationError


def _station_terminal(**updates):
    data = {
        "type": "rf",
        "count": 1,
        "bandwidth_mbps": 1000.0,
        "tracking_capacity": 1,
        "max_range_km": 2000.0,
        "field_of_regard_deg": 120.0,
        "max_tracking_rate_deg_s": 1.5,
        "boresight": TerminalBoresight(mode="local_vertical"),
    }
    data.update(updates)
    return data


def _satellite_terminal(**updates):
    data = {
        "type": "rf",
        "count": 1,
        "bandwidth_mbps": 1000.0,
        "max_range_km": 2000.0,
        "field_of_regard_deg": 120.0,
        "max_tracking_rate_deg_s": 1.5,
        "boresight": SatGroundTerminalBoresight(
            target_body="earth",
            mode="nadir",
        ),
    }
    data.update(updates)
    return data


@pytest.mark.parametrize(
    ("model", "factory"),
    [
        (StationGroundTerminalDef, _station_terminal),
        (GroundTerminal, _satellite_terminal),
        (SatelliteTypeGroundTerminalDef, _satellite_terminal),
    ],
)
def test_ground_link_field_of_regard_is_a_forward_cone_not_full_sphere(model, factory):
    with pytest.raises(ValidationError, match="field_of_regard_deg"):
        model(**factory(field_of_regard_deg=181.0))


def test_ground_boresight_configured_topocentric_requires_azimuth_and_elevation():
    with pytest.raises(ValidationError, match="configured_topocentric"):
        TerminalBoresight(mode="configured_topocentric", configured_az_deg=180.0)

    boresight = TerminalBoresight(
        mode="configured_topocentric",
        configured_az_deg=180.0,
        configured_el_deg=45.0,
    )
    assert boresight.configured_az_deg == 180.0
    assert boresight.configured_el_deg == 45.0


def test_ground_boresight_rejects_removed_configured_inertial_mode():
    with pytest.raises(ValidationError, match="configured_topocentric"):
        TerminalBoresight(
            mode="configured_inertial",
            configured_az_deg=180.0,
            configured_el_deg=45.0,
        )


def test_satellite_ground_boresight_accepts_only_nadir_until_orientation_model_exists():
    with pytest.raises(ValidationError, match="nadir"):
        SatGroundTerminalBoresight(
            target_body="earth",
            mode="configured",
        )


def test_ground_boresight_does_not_accept_satellite_nadir_mode():
    with pytest.raises(ValidationError, match="local_vertical"):
        TerminalBoresight(mode="nadir")


def test_satellite_ground_boresight_rejects_unknown_target_body():
    with pytest.raises(ValidationError, match="Input should be"):
        SatGroundTerminalBoresight(target_body="lunar", mode="nadir")


def test_terminal_physics_profile_target_body_matches_satellite_boresight():
    with pytest.raises(ValueError, match="target_body must match"):
        TerminalPhysicsProfile(
            profile_id="sat.ground_terminals",
            max_range_km=2000.0,
            field_of_regard_deg=120.0,
            max_tracking_rate_deg_s=1.5,
            boresight=SatGroundTerminalBoresight(target_body="luna", mode="nadir"),
            target_body="earth",
        )


def test_terminal_physics_profile_ground_boresight_cannot_claim_target_body():
    with pytest.raises(ValueError, match="only valid for satellite"):
        TerminalPhysicsProfile(
            profile_id="gs.terminals",
            max_range_km=2000.0,
            field_of_regard_deg=120.0,
            max_tracking_rate_deg_s=1.5,
            boresight=TerminalBoresight(mode="local_vertical"),
            target_body="earth",
        )
