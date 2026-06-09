# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Direct tests for the OME propagation engine boundary."""

from __future__ import annotations

import math

import pytest
from nodalarc.constellation_loader import SatelliteNode
from nodalarc.models.addressing import AddressingScheme
from nodalarc.orbital import OrbitalElements
from ome.propagation_engine import build_node_positions, propagate_satellites
from ome.propagator import GeoPosition

from tests.physics_fixtures import (
    EARTH_ORIGIN_BODY_STATES,
    EARTH_TEST_BODY_FRAMES,
    earth_elements_from_params,
    earth_geodetic_to_ecef,
)

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"
ISS_TLE_EPOCH_UNIX = 1615896900.000275
SAT_0_ID = "earth-leo-sat-p00s00"
SAT_1_ID = "earth-leo-sat-p00s01"


def _propagation_inputs() -> dict:
    return {
        "body_frames": EARTH_TEST_BODY_FRAMES,
        "body_states": EARTH_ORIGIN_BODY_STATES,
    }


def _satellite(node_id: str = SAT_0_ID, slot: int = 0) -> SatelliteNode:
    return SatelliteNode(
        plane=0,
        slot=slot,
        node_id=node_id,
        central_body="earth",
        elements=earth_elements_from_params(550.0, 53.0, 0.0, 0.0),
        isl_terminal_count=2,
        ground_terminal_count=1,
    )


def _tle_satellite() -> SatelliteNode:
    return SatelliteNode(
        plane=0,
        slot=0,
        node_id=SAT_0_ID,
        central_body="earth",
        elements=earth_elements_from_params(420.0, 51.6, 21.5, 21.8),
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
        propagator_id="two-body",
        **_propagation_inputs(),
    )

    node_id = SAT_0_ID
    state = states[node_id]
    assert state.node_id == node_id
    assert state.sim_time_unix == epoch_unix + 12.0
    assert state.propagator_id == "two-body"
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
        **_propagation_inputs(),
    )

    state = states[SAT_0_ID]
    assert state.propagator_id == "j2-mean-elements"
    assert state.sim_time_unix == epoch_unix + 86400.0
    assert abs(state.position_ecef_km.x) + abs(state.position_ecef_km.y) > 0.0


def test_two_body_propagator_accepts_eccentric_elements():
    addressing = AddressingScheme()
    epoch_unix = 1735689600.0
    sat = SatelliteNode(
        plane=0,
        slot=0,
        elements=OrbitalElements(
            semi_major_axis_km=26_600.0,
            eccentricity=0.74,
            inclination_rad=math.radians(63.4),
            raan_rad=math.radians(270.0),
            argument_of_perigee_rad=math.radians(270.0),
            mean_anomaly_rad=0.0,
        ),
        node_id=SAT_0_ID,
        central_body="earth",
        isl_terminal_count=2,
        ground_terminal_count=1,
    )

    states = propagate_satellites(
        satellites=[sat],
        addressing=addressing,
        epoch_unix=epoch_unix,
        dt=0.0,
        propagator_id="two-body",
        **_propagation_inputs(),
    )

    state = states[SAT_0_ID]
    radius = math.sqrt(
        state.position_ecef_km.x**2 + state.position_ecef_km.y**2 + state.position_ecef_km.z**2
    )
    assert state.propagator_id == "two-body"
    assert radius == pytest.approx(26_600.0 * (1.0 - 0.74), abs=50.0)


def test_mixed_session_uses_per_satellite_propagator_ids():
    addressing = AddressingScheme()
    epoch_unix = 1735689600.0
    two_body = _satellite()
    two_body.propagator_id = "two-body"
    j2 = SatelliteNode(
        plane=0,
        slot=1,
        node_id=SAT_1_ID,
        central_body="earth",
        elements=earth_elements_from_params(550.0, 53.0, 45.0, 20.0),
        isl_terminal_count=2,
        ground_terminal_count=1,
        propagator_id="j2-mean-elements",
    )

    states = propagate_satellites(
        satellites=[two_body, j2],
        addressing=addressing,
        epoch_unix=epoch_unix,
        dt=86400.0,
        propagator_id="mixed",
        **_propagation_inputs(),
    )

    assert states[SAT_0_ID].propagator_id == "two-body"
    assert states[SAT_1_ID].propagator_id == "j2-mean-elements"


def test_mixed_session_requires_per_satellite_propagator_id():
    with pytest.raises(ValueError, match="requires every satellite"):
        propagate_satellites(
            satellites=[_satellite()],
            addressing=AddressingScheme(),
            epoch_unix=1735689600.0,
            dt=0.0,
            propagator_id="mixed",
            **_propagation_inputs(),
        )


def test_sgp4_propagator_requires_tle_record():
    with pytest.raises(ValueError, match="requires a TLE constellation"):
        propagate_satellites(
            satellites=[_satellite()],
            addressing=AddressingScheme(),
            epoch_unix=ISS_TLE_EPOCH_UNIX,
            dt=0.0,
            propagator_id="sgp4-tle",
            **_propagation_inputs(),
        )


def test_sgp4_propagator_is_explicitly_selectable():
    addressing = AddressingScheme()

    states = propagate_satellites(
        satellites=[_tle_satellite()],
        addressing=addressing,
        epoch_unix=ISS_TLE_EPOCH_UNIX,
        dt=0.0,
        propagator_id="sgp4-tle",
        **_propagation_inputs(),
    )

    state = states[SAT_0_ID]
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
            **_propagation_inputs(),
        )


def test_build_node_positions_preserves_satellite_and_ground_states():
    addressing = AddressingScheme()
    sat_state = propagate_satellites(
        satellites=[_satellite()],
        addressing=addressing,
        epoch_unix=1735689600.0,
        dt=0.0,
        propagator_id="two-body",
        **_propagation_inputs(),
    )
    gs_geo = GeoPosition(64.1466, -21.9426, 0.05)
    gs_ecef = earth_geodetic_to_ecef(gs_geo)

    positions = build_node_positions(sat_state, {"gs-reykjavik": (gs_ecef, gs_geo)})

    sat_pos = positions[SAT_0_ID]
    assert abs(sat_pos.vel_x_km_s) + abs(sat_pos.vel_y_km_s) + abs(sat_pos.vel_z_km_s) > 0
    gs_pos = positions["gs-reykjavik"]
    assert gs_pos.lat_deg == gs_geo.lat_deg
    assert gs_pos.lon_deg == gs_geo.lon_deg
    assert gs_pos.alt_km == gs_geo.alt_km
    assert gs_pos.vel_x_km_s == 0.0
    assert gs_pos.vel_y_km_s == 0.0
    assert gs_pos.vel_z_km_s == 0.0
