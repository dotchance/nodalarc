# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Direct tests for the OME propagation engine boundary."""

from __future__ import annotations

import math

import pytest
from nodalarc.constellation_loader import SatelliteNode
from nodalarc.models.addressing import AddressingScheme
from nodalarc.orbital import elements_from_params
from ome.propagation_engine import build_node_positions, propagate_satellites
from ome.propagator import GeoPosition, geodetic_to_ecef

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"
ISS_TLE_EPOCH_UNIX = 1615896900.000275


def _satellite() -> SatelliteNode:
    return SatelliteNode(
        plane=0,
        slot=0,
        elements=elements_from_params(550.0, 53.0, 0.0, 0.0),
        isl_terminal_count=2,
        ground_terminal_count=1,
    )


def _tle_satellite() -> SatelliteNode:
    return SatelliteNode(
        plane=0,
        slot=0,
        elements=elements_from_params(420.0, 51.6, 21.5, 21.8),
        isl_terminal_count=2,
        ground_terminal_count=1,
        tle_line_1=ISS_TLE_LINE_1,
        tle_line_2=ISS_TLE_LINE_2,
        norad_id=25544,
    )


def test_propagate_satellites_returns_typed_state_with_model_identity():
    addressing = AddressingScheme()
    epoch_unix = 1735689600.0

    states = propagate_satellites(
        satellites=[_satellite()],
        addressing=addressing,
        epoch_unix=epoch_unix,
        dt=12.0,
        propagator_id="keplerian-circular",
    )

    node_id = addressing.sat_id(0, 0)
    state = states[node_id]
    assert state.node_id == node_id
    assert state.sim_time_unix == epoch_unix + 12.0
    assert state.propagator_id == "keplerian-circular"
    position_norm = math.sqrt(
        state.position_ecef_km.x**2 + state.position_ecef_km.y**2 + state.position_ecef_km.z**2
    )
    velocity_norm = math.sqrt(
        state.velocity_ecef_km_s.x**2
        + state.velocity_ecef_km_s.y**2
        + state.velocity_ecef_km_s.z**2
    )
    assert position_norm > 6500.0
    assert velocity_norm > 7.0


def test_j2_propagator_is_explicitly_selectable():
    addressing = AddressingScheme()
    epoch_unix = 1735689600.0

    states = propagate_satellites(
        satellites=[_satellite()],
        addressing=addressing,
        epoch_unix=epoch_unix,
        dt=86400.0,
        propagator_id="j2-mean-elements",
    )

    state = states[addressing.sat_id(0, 0)]
    assert state.propagator_id == "j2-mean-elements"
    assert state.sim_time_unix == epoch_unix + 86400.0
    assert abs(state.position_ecef_km.x) + abs(state.position_ecef_km.y) > 0.0


def test_sgp4_propagator_requires_tle_record():
    with pytest.raises(ValueError, match="requires a TLE constellation"):
        propagate_satellites(
            satellites=[_satellite()],
            addressing=AddressingScheme(),
            epoch_unix=ISS_TLE_EPOCH_UNIX,
            dt=0.0,
            propagator_id="sgp4-tle",
        )


def test_sgp4_propagator_is_explicitly_selectable():
    addressing = AddressingScheme()

    states = propagate_satellites(
        satellites=[_tle_satellite()],
        addressing=addressing,
        epoch_unix=ISS_TLE_EPOCH_UNIX,
        dt=0.0,
        propagator_id="sgp4-tle",
    )

    state = states[addressing.sat_id(0, 0)]
    assert state.propagator_id == "sgp4-tle"
    assert state.position_ecef_km.x == pytest.approx(-4329.375350762542, abs=1e-6)
    assert state.position_ecef_km.y == pytest.approx(2211.9930425759426, abs=1e-6)
    assert state.position_ecef_km.z == pytest.approx(4740.40568912658, abs=1e-6)
    assert state.velocity_ecef_km_s.x == pytest.approx(-5.240188571438462, abs=1e-9)


def test_unknown_propagator_fails_loudly():
    with pytest.raises(ValueError, match="Unsupported OME propagator"):
        propagate_satellites(
            satellites=[_satellite()],
            addressing=AddressingScheme(),
            epoch_unix=1735689600.0,
            dt=0.0,
            propagator_id="unknown",
        )


def test_build_node_positions_preserves_satellite_and_ground_states():
    addressing = AddressingScheme()
    sat_state = propagate_satellites(
        satellites=[_satellite()],
        addressing=addressing,
        epoch_unix=1735689600.0,
        dt=0.0,
        propagator_id="keplerian-circular",
    )
    gs_geo = GeoPosition(64.1466, -21.9426, 0.05)
    gs_ecef = geodetic_to_ecef(gs_geo)

    positions = build_node_positions(sat_state, {"gs-reykjavik": (gs_ecef, gs_geo)})

    sat_pos = positions[addressing.sat_id(0, 0)]
    assert abs(sat_pos.vel_x_km_s) + abs(sat_pos.vel_y_km_s) + abs(sat_pos.vel_z_km_s) > 0
    gs_pos = positions["gs-reykjavik"]
    assert gs_pos.lat_deg == gs_geo.lat_deg
    assert gs_pos.lon_deg == gs_geo.lon_deg
    assert gs_pos.alt_km == gs_geo.alt_km
    assert gs_pos.vel_x_km_s == 0.0
    assert gs_pos.vel_y_km_s == 0.0
    assert gs_pos.vel_z_km_s == 0.0
