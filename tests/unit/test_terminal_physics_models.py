# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Contracts for terminal physics schema boundaries."""

from __future__ import annotations

import pytest
from nodalarc.constellation_loader import load_ground_stations, load_satellite_type
from nodalarc.models.constellation import GroundTerminal
from nodalarc.models.ground_station import GroundTerminalDef as StationGroundTerminalDef
from nodalarc.models.satellite_type import GroundTerminalDef as SatelliteTypeGroundTerminalDef
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from pydantic import ValidationError

from tests.conftest import CONFIGS_DIR


def _station_terminal(**updates):
    data = {
        "type": "rf",
        "count": 1,
        "bandwidth_mbps": 1000.0,
        "tracking_capacity": 1,
        "max_range_km": 2000.0,
        "field_of_regard_deg": 120.0,
        "max_tracking_rate_deg_s": 1.5,
        "boresight": TerminalBoresight(mode="local_vertical", half_angle_deg=60.0),
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
            half_angle_deg=60.0,
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


@pytest.mark.parametrize(
    ("model", "factory", "boresight"),
    [
        (
            StationGroundTerminalDef,
            _station_terminal,
            TerminalBoresight(mode="local_vertical", half_angle_deg=45.0),
        ),
        (
            GroundTerminal,
            _satellite_terminal,
            SatGroundTerminalBoresight(target_body="earth", mode="nadir", half_angle_deg=45.0),
        ),
        (
            SatelliteTypeGroundTerminalDef,
            _satellite_terminal,
            SatGroundTerminalBoresight(target_body="earth", mode="nadir", half_angle_deg=45.0),
        ),
    ],
)
def test_field_of_regard_and_boresight_half_angle_must_match(model, factory, boresight):
    with pytest.raises(ValidationError, match="half_angle_deg"):
        model(**factory(field_of_regard_deg=120.0, boresight=boresight))


def test_satellite_ground_boresight_accepts_only_nadir_until_orientation_model_exists():
    with pytest.raises(ValidationError, match="nadir"):
        SatGroundTerminalBoresight(
            target_body="earth",
            mode="configured",
            half_angle_deg=60.0,
        )


def test_ground_boresight_does_not_accept_satellite_nadir_mode():
    with pytest.raises(ValidationError, match="local_vertical"):
        TerminalBoresight(mode="nadir", half_angle_deg=60.0)


def _assert_catalog_ground_physics_is_not_placeholder(term) -> None:
    assert term.max_range_km is not None
    assert term.field_of_regard_deg is not None
    assert term.max_tracking_rate_deg_s is not None
    assert term.boresight is not None
    assert term.max_range_km != 5000.0
    assert term.field_of_regard_deg != 180.0
    assert term.max_tracking_rate_deg_s != 3.0


def test_ground_station_catalog_does_not_use_permissive_phase1_placeholders():
    station_files = sorted((CONFIGS_DIR / "ground-stations" / "stations").glob("*.yaml"))
    catalog_files = [CONFIGS_DIR / "ground-stations" / "custom-example.yaml", *station_files]

    for path in catalog_files:
        gs_file = load_ground_stations(path)
        for term in gs_file.default_terminals:
            _assert_catalog_ground_physics_is_not_placeholder(term)
        for station in gs_file.stations:
            for term in station.terminals or gs_file.default_terminals:
                _assert_catalog_ground_physics_is_not_placeholder(term)


def test_satellite_type_catalog_ground_terminals_do_not_use_permissive_phase1_placeholders():
    for path in sorted((CONFIGS_DIR / "satellite-types").glob("*.yaml")):
        sat_type = load_satellite_type(path.stem)
        for term in sat_type.ground_terminals:
            _assert_catalog_ground_physics_is_not_placeholder(term)
