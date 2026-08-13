# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for build_session_ephemeris() and epoch_id stamping."""

from __future__ import annotations

from datetime import UTC, datetime

from nodalarc.frames import EcefVec3, GeoPosition, Vec3
from nodalarc.link_metadata import LinkRuleMetadata
from nodalarc.models.events import (
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    EphemerisNodeTLE,
    SessionEphemeris,
)
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.session import GroundSchedulingConfig
from nodalarc.ome_runtime import IslTerminal, SatelliteNode
from ome.event_stream import build_link_state_snapshot, build_session_ephemeris, build_step_context
from ome.snapshot_builder import LinkSnapshotSource

from tests.conftest import load_runtime_ome_test_inputs
from tests.ome_runtime_fixtures import StaticOmeAddressing
from tests.physics_fixtures import EARTH_TEST_BODY_FRAMES, earth_elements_from_params


def _ground_scheduling() -> GroundSchedulingConfig:
    return GroundSchedulingConfig(
        selection_policy=SelectionPolicySpec(name="highest-elevation", params={}),
        handover_policy=HandoverPolicySpec(name="none", params={}),
    )


def _load_test_ctx():
    """Load a small test constellation and build StepContext."""
    session, _resolved, gs_file, sats, addressing, neighbors, candidates = (
        load_runtime_ome_test_inputs(origin="test.session_ephemeris")
    )

    ctx = build_step_context(
        satellites=sats,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        propagator_id=session.orbit.propagator,
        ground_scheduling=session.scheduling.ground,
        ground_candidate_satellites_by_gs=candidates,
        ground_link_model=session.ground_link_model,
        body_frames=session.body_frames,
    )
    return ctx, sats, gs_file


# The loaded session's start time: the owned validity anchor of every
# satellite's working elements. Guarded by test_epoch_matches_the_owned_anchor.
EPOCH = 1780876800.0  # 2026-06-08T00:00:00 UTC


class TestBuildSessionEphemeris:
    def test_epoch_matches_the_owned_anchor(self):
        """EPOCH must be the elements' validity anchor, not an arbitrary date.

        A hard-coded 2025 epoch against 2026-anchored elements once blessed
        a 523-day silent relabeling on the wire. This pins the alignment so
        a changed session fixture cannot quietly reintroduce it.
        """
        ctx, sats, _ = _load_test_ctx()
        for sat in sats:
            assert sat.elements_epoch_unix == EPOCH

    def test_wire_elements_are_advanced_to_a_later_epoch(self):
        """A later wire epoch carries every advanced field, never a relabel.

        All six element fields are asserted, and an eccentric J2 satellite
        joins the real circular population so every secular component
        (RAAN, argument of perigee, mean anomaly) provably changes.
        """
        import math as _math

        from nodalarc.ome_runtime import SatelliteNode, satellite_propagator_id
        from nodalarc.orbital import OrbitalElements
        from nodalarc.propagator import advance_mean_elements

        ctx, sats, _ = _load_test_ctx()
        eccentric = SatelliteNode(
            plane=9,
            slot=9,
            elements=OrbitalElements(
                semi_major_axis_km=26_600.0,
                eccentricity=0.74,
                inclination_rad=_math.radians(63.4),
                raan_rad=_math.radians(270.0),
                argument_of_perigee_rad=_math.radians(270.0),
                mean_anomaly_rad=_math.radians(10.0),
            ),
            elements_epoch_unix=EPOCH,
            isl_terminal_count=0,
            ground_terminal_count=0,
            node_id="earth-test-sat-p09s09",
            central_body="earth",
            propagator_id="j2-mean-elements",
        )
        probe_sats = [*sats[:3], eccentric]
        ctx.satellites.append(eccentric)
        try:
            shifted = EPOCH + 3600.0
            eph = build_session_ephemeris(ctx, shifted, epoch_id=1)
        finally:
            ctx.satellites.remove(eccentric)

        for sat in probe_sats:
            nid = sat.node_id or ctx.addressing.sat_id(sat.plane, sat.slot)
            node = eph.nodes[nid]
            expected = advance_mean_elements(
                sat.elements,
                3600.0,
                body_frame=ctx.body_frames[sat.central_body],
                propagator_id=satellite_propagator_id(sat, ctx.propagator_id),
            )
            assert node.semi_major_axis_km == expected.semi_major_axis_km
            assert node.eccentricity == expected.eccentricity
            assert abs(_math.radians(node.inclination_deg) - expected.inclination_rad) < 1e-12
            assert abs(_math.radians(node.raan_deg) - expected.raan_rad) < 1e-12
            assert (
                abs(_math.radians(node.argument_of_perigee_deg) - expected.argument_of_perigee_rad)
                < 1e-12
            )
            assert abs(_math.radians(node.mean_anomaly_deg) - expected.mean_anomaly_rad) < 1e-12
            assert node.raan_deg != _math.degrees(sat.elements.raan_rad)
            assert node.mean_anomaly_deg != _math.degrees(sat.elements.mean_anomaly_rad)
            if sat.elements.eccentricity > 0.0:
                assert node.argument_of_perigee_deg != _math.degrees(
                    sat.elements.argument_of_perigee_rad
                )

    def test_satellite_mapped_to_configured_mean_element_propagator(self):
        ctx, sats, _ = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        sat = eph.nodes[ctx.addressing.sat_id(0, 0)]
        assert isinstance(sat, EphemerisNodeKeplerian)
        assert sat.type == "keplerian"
        assert sat.plane == 0
        assert sat.slot == 0
        assert sat.semi_major_axis_km > 6500  # must be a valid LEO-size orbit
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
            ground_candidate_satellites_by_gs=ctx.ground_candidate_satellites_by_gs,
            ground_link_model=ctx.ground_link_model,
            body_frames=ctx.body_frames,
        )
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        sat = eph.nodes[ctx.addressing.sat_id(0, 0)]
        assert isinstance(sat, EphemerisNodeKeplerian)
        assert sat.propagator == "j2-mean-elements"

    def test_mixed_ephemeris_uses_per_satellite_propagator_identity(self):
        ctx, sats, gs_file = _load_test_ctx()
        sats = list(sats)
        sats[0].propagator_id = "two-body"
        for sat in sats[1:]:
            sat.propagator_id = "j2-mean-elements"
        ctx = build_step_context(
            satellites=sats,
            addressing=ctx.addressing,
            gs_file=gs_file,
            neighbors=frozenset(),
            propagator_id="mixed",
            ground_scheduling=_ground_scheduling(),
            ground_candidate_satellites_by_gs=ctx.ground_candidate_satellites_by_gs,
            ground_link_model=ctx.ground_link_model,
            body_frames=ctx.body_frames,
        )

        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)

        first = eph.nodes[ctx.addressing.sat_id(sats[0].plane, sats[0].slot)]
        second = eph.nodes[ctx.addressing.sat_id(sats[1].plane, sats[1].slot)]
        assert isinstance(first, EphemerisNodeKeplerian)
        assert isinstance(second, EphemerisNodeKeplerian)
        assert first.propagator == "two-body"
        assert second.propagator == "j2-mean-elements"

    def test_tle_satellite_mapped_to_tle_ephemeris(self):
        node_id = "tle-sat-p00s00"
        sats = [
            SatelliteNode(
                plane=0,
                slot=0,
                elements=earth_elements_from_params(420.0, 51.6, 21.5, 21.8),
                isl_terminal_count=2,
                ground_terminal_count=1,
                node_id=node_id,
                local_node_id="sat-P00S00",
                segment_id="tle",
                central_body="earth",
                isl_terminals=(
                    IslTerminal(
                        type="optical",
                        count=2,
                        max_range_km=5000.0,
                        bandwidth_mbps=1000.0,
                        max_tracking_rate_deg_s=3.0,
                        field_of_regard_deg=360.0,
                    ),
                ),
                tle_line_1=(
                    "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
                ),
                tle_line_2=(
                    "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"
                ),
                norad_id=25544,
            )
        ]
        ctx = build_step_context(
            satellites=sats,
            addressing=StaticOmeAddressing(satellite_ids=(node_id,)),
            gs_file=None,
            neighbors=frozenset(),
            propagator_id="sgp4-tle",
            body_frames=EARTH_TEST_BODY_FRAMES,
        )

        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        sat = eph.nodes["tle-sat-p00s00"]
        assert isinstance(sat, EphemerisNodeTLE)
        assert sat.type == "tle"
        assert sat.norad_id == 25544
        assert sat.tle_line_1.startswith("1 25544")

    def test_ground_station_mapped_to_fixed(self):
        ctx, _, gs_file = _load_test_ctx()
        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)
        gs_nodes = {k: v for k, v in eph.nodes.items() if k in ctx.gs_positions}
        assert len(gs_nodes) > 0, "Expected at least one ground station"
        gs_name, gs = next(iter(gs_nodes.items()))
        assert isinstance(gs, EphemerisNodeFixed)
        assert gs.type == "fixed"
        assert -90 <= gs.lat_deg <= 90
        assert -180 <= gs.lon_deg <= 180

    def test_node_metadata_carried_into_session_ephemeris(self):
        ctx, sats, gs_file = _load_test_ctx()
        sat_id = ctx.addressing.sat_id(sats[0].plane, sats[0].slot)
        gs_id = next(iter(ctx.gs_positions))
        ctx = build_step_context(
            satellites=sats,
            addressing=ctx.addressing,
            gs_file=gs_file,
            neighbors=frozenset(),
            propagator_id=ctx.propagator_id,
            ground_scheduling=_ground_scheduling(),
            ground_candidate_satellites_by_gs=ctx.ground_candidate_satellites_by_gs,
            ground_link_model=ctx.ground_link_model,
            body_frames=ctx.body_frames,
            node_metadata={
                sat_id: {
                    "segment_id": "leo",
                    "local_node_id": "sat-P00S00",
                    "namespace": "leo",
                    "tags": ("earth", "leo", "access"),
                },
                gs_id: {
                    "segment_id": "ground",
                    "local_node_id": "gs-denver",
                    "namespace": "ground",
                    "tags": ("earth", "ground"),
                },
            },
        )

        eph = build_session_ephemeris(ctx, EPOCH, epoch_id=0)

        sat = eph.nodes[sat_id]
        gs = eph.nodes[gs_id]
        assert isinstance(sat, EphemerisNodeKeplerian)
        assert sat.segment_id == "leo"
        assert sat.local_node_id == "sat-P00S00"
        assert sat.namespace == "leo"
        assert sat.tags == ("earth", "leo", "access")
        assert isinstance(gs, EphemerisNodeFixed)
        assert gs.segment_id == "ground"
        assert gs.local_node_id == "gs-denver"
        assert gs.namespace == "ground"
        assert gs.tags == ("earth", "ground")

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

        for sat in sats[:3]:
            nid = ctx.addressing.sat_id(sat.plane, sat.slot)
            node = eph.nodes[nid]
            assert isinstance(node, EphemerisNodeKeplerian)
            assert abs(node.semi_major_axis_km - sat.elements.semi_major_axis_km) < 0.001
            assert abs(node.eccentricity - sat.elements.eccentricity) < 1e-12
            assert (
                abs(
                    node.argument_of_perigee_deg
                    - math.degrees(sat.elements.argument_of_perigee_rad)
                )
                < 0.001
            )
            assert abs(node.mean_anomaly_deg - math.degrees(sat.elements.mean_anomaly_rad)) < 0.001
            assert abs(node.inclination_deg - math.degrees(sat.elements.inclination_rad)) < 0.001


class TestLinkStateSnapshotEpochId:
    def test_epoch_id_stamped(self):
        snap = build_link_state_snapshot(
            LinkSnapshotSource(
                isl_state={},
                ground_state={},
                associations={},
                pending_teardowns={},
                propagated_states={},
            ),
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
            LinkSnapshotSource(
                isl_state={},
                ground_state={},
                associations={},
                pending_teardowns={},
                propagated_states={},
            ),
            interface_map={},
            bandwidth_map={},
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            seq=1,
            interval_s=5.0,
        )
        assert snap.epoch_id == 0

    def test_snapshot_carries_declared_link_rule_metadata(self):
        pair = ("leo-sat-p00s00", "meo-sat-p00s00")
        snap = build_link_state_snapshot(
            LinkSnapshotSource(
                isl_state={pair: (True, True)},
                ground_state={},
                associations={},
                pending_teardowns={},
                propagated_states={},
            ),
            interface_map={pair: ("isl0", "isl1")},
            bandwidth_map={pair: 1000.0},
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            seq=1,
            interval_s=5.0,
            fixed_positions={
                pair[0]: (EcefVec3(Vec3(7000.0, 0.0, 0.0)), GeoPosition(0.0, 0.0, 0.0)),
                pair[1]: (EcefVec3(Vec3(9000.0, 0.0, 0.0)), GeoPosition(0.0, 0.0, 0.0)),
            },
            rule_map={
                pair: LinkRuleMetadata(
                    link_rule_id="leo-to-meo-relay-candidates",
                    topology_mode="nearest_n",
                    endpoint_segments=("leo", "meo"),
                )
            },
        )

        restored = type(snap).model_validate_json(snap.model_dump_json())
        link = restored.links[0]
        assert link.link_rule_id == "leo-to-meo-relay-candidates"
        assert link.topology_mode == "nearest_n"
        assert link.endpoint_segments == ("leo", "meo")
