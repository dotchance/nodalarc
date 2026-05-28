# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for build_session_ephemeris() and epoch_id stamping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.events import (
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    EphemerisNodeTLE,
    SessionEphemeris,
)
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.session import GroundSchedulingConfig, SessionConfig
from ome.event_stream import build_link_state_snapshot, build_session_ephemeris, build_step_context

from tests.conftest import FIXTURES_DIR


def _ground_scheduling() -> GroundSchedulingConfig:
    return GroundSchedulingConfig(
        selection_policy=SelectionPolicySpec(name="highest-elevation", params={}),
        handover_policy=HandoverPolicySpec(name="none", params={}),
    )


def _load_test_ctx():
    """Load a small test constellation and build StepContext."""
    session_path = Path("configs/sessions/demo-36-ospf.yaml")
    if not session_path.exists():
        pytest.skip("demo-36-ospf.yaml not available")

    data = yaml.safe_load(session_path.read_text())
    session = SessionConfig.model_validate(data)
    cc = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)
    sats = expand_constellation(cc)
    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(cc, addressing)

    ctx = build_step_context(
        satellites=sats,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        propagator_id=session.orbit.propagator,
        ground_scheduling=session.scheduling.ground,
    )
    return ctx, sats, gs_file


EPOCH = 1735689600.0  # 2025-01-01T00:00:00 UTC


class TestBuildSessionEphemeris:
    def test_satellite_mapped_to_configured_mean_element_propagator(self):
        ctx, sats, _ = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        # First satellite should be P00S00
        sat = eph.nodes["sat-P00S00"]
        assert isinstance(sat, EphemerisNodeKeplerian)
        assert sat.type == "keplerian"
        assert sat.plane == 0
        assert sat.slot == 0
        assert sat.altitude_km > 160  # must be a valid LEO altitude
        assert sat.propagator == ctx.propagator_id

    def test_j2_ephemeris_preserves_propagator_identity(self):
        ctx, sats, gs_file = _load_test_ctx()
        ctx = build_step_context(
            satellites=sats,
            addressing=ctx.addressing,
            gs_file=gs_file,
            neighbors=frozenset(),
            propagator_id="j2-mean-elements",
            ground_scheduling=_ground_scheduling(),
        )
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        sat = eph.nodes["sat-P00S00"]
        assert isinstance(sat, EphemerisNodeKeplerian)
        assert sat.propagator == "j2-mean-elements"

    def test_tle_satellite_mapped_to_tle_ephemeris(self):
        cc = load_constellation(
            {
                "mode": "tle",
                "name": "sample-tle",
                "tle_file": str(FIXTURES_DIR / "tles/sample.tle"),
                "filter": {"max_count": 1},
                "default_terminals": {
                    "isl": [
                        {
                            "type": "optical",
                            "count": 2,
                            "max_range_km": 5000,
                            "bandwidth_mbps": 1000,
                            "max_tracking_rate_deg_s": 3.0,
                        }
                    ],
                    "ground": [{"type": "rf", "count": 1, "bandwidth_mbps": 1000}],
                },
            }
        )
        sats = expand_constellation(cc)
        ctx = build_step_context(
            satellites=sats,
            addressing=AddressingScheme(),
            gs_file=None,
            neighbors=frozenset(),
            propagator_id="sgp4-tle",
        )

        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        sat = eph.nodes["sat-P00S00"]
        assert isinstance(sat, EphemerisNodeTLE)
        assert sat.type == "tle"
        assert sat.norad_id == 25544
        assert sat.tle_line_1.startswith("1 25544")

    def test_ground_station_mapped_to_fixed(self):
        ctx, _, gs_file = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        # Find any ground station node
        gs_nodes = {k: v for k, v in eph.nodes.items() if k.startswith("gs-")}
        assert len(gs_nodes) > 0, "Expected at least one ground station"
        gs_name, gs = next(iter(gs_nodes.items()))
        assert isinstance(gs, EphemerisNodeFixed)
        assert gs.type == "fixed"
        assert -90 <= gs.lat_deg <= 90
        assert -180 <= gs.lon_deg <= 180

    def test_epoch_id_preserved(self):
        ctx, _, _ = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=7)
        assert eph.epoch_id == 7

    def test_node_count_matches_constellation(self):
        ctx, sats, gs_file = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        expected_sats = len(sats)
        expected_gs = len(gs_file.stations) if gs_file else 0
        assert len(eph.nodes) == expected_sats + expected_gs

    def test_epoch_unix_stored(self):
        ctx, _, _ = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        assert eph.epoch_unix == EPOCH

    def test_json_round_trip(self):
        ctx, _, _ = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        restored = SessionEphemeris.model_validate_json(eph.model_dump_json())
        assert restored == eph

    def test_orbital_elements_consistency(self):
        """Elements in ephemeris should match the original satellite elements."""
        ctx, sats, _ = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)

        import math

        from nodalarc.constants import EARTH_RADIUS_KM

        for sat in sats[:3]:
            nid = ctx.addressing.sat_id(sat.plane, sat.slot)
            node = eph.nodes[nid]
            assert isinstance(node, EphemerisNodeKeplerian)
            expected_alt = sat.elements.semi_major_axis_km - EARTH_RADIUS_KM
            assert abs(node.altitude_km - expected_alt) < 0.001
            assert abs(node.inclination_deg - math.degrees(sat.elements.inclination_rad)) < 0.001


class TestLinkStateSnapshotEpochId:
    def test_epoch_id_stamped(self):
        snap = build_link_state_snapshot(
            isl_state={},
            gs_state={},
            interface_map={},
            bandwidth_map={},
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            seq=1,
            interval_s=5.0,
            epoch_id=42,
        )
        assert snap.epoch_id == 42

    def test_epoch_id_default_zero(self):
        snap = build_link_state_snapshot(
            isl_state={},
            gs_state={},
            interface_map={},
            bandwidth_map={},
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            seq=1,
            interval_s=5.0,
        )
        assert snap.epoch_id == 0
