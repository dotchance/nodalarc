"""Test timeline precomputation and JSON Lines I/O."""

import json

import pytest
from nodalarc.models.addressing import NeighborAssignment
from nodalarc.models.events import ClockTick, VisibilityEvent
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.session import GroundSchedulingConfig
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from nodalarc.ome_runtime import (
    GroundStation,
    GroundStationFile,
    GroundTerminal,
    IslTerminal,
    SatelliteGroundTerminal,
    SatelliteNode,
)
from ome.event_stream import (
    precompute_timeline,
    read_timeline_jsonl,
    write_timeline_jsonl,
)

from tests.ome_runtime_fixtures import StaticOmeAddressing
from tests.physics_fixtures import EARTH_TEST_BODY_FRAMES, earth_elements_from_params

EPOCH = 1735689600.0


def _ground_scheduling() -> GroundSchedulingConfig:
    return GroundSchedulingConfig(
        selection_policy=SelectionPolicySpec(name="highest-elevation", params={}),
        handover_policy=HandoverPolicySpec(name="none", params={}),
    )


def _four_node_runtime():
    terminal = IslTerminal(
        type="optical",
        count=2,
        max_range_km=5000.0,
        bandwidth_mbps=1000.0,
        max_tracking_rate_deg_s=3.0,
        field_of_regard_deg=360.0,
    )
    access_terminal = SatelliteGroundTerminal(
        type="optical",
        count=1,
        interface_indices=(0,),
        bandwidth_mbps=1000.0,
        max_range_km=2000.0,
        field_of_regard_deg=120.0,
        max_tracking_rate_deg_s=1.5,
        boresight=SatGroundTerminalBoresight(target_body="earth", mode="nadir"),
    )
    nodes = []
    for plane, slot, raan_deg, anomaly_deg in (
        (0, 0, 0.0, 0.0),
        (0, 1, 0.0, 180.0),
        (1, 0, 45.0, 0.0),
        (1, 1, 45.0, 180.0),
    ):
        nodes.append(
            SatelliteNode(
                elements_epoch_unix=EPOCH,
                plane=plane,
                slot=slot,
                elements=earth_elements_from_params(550.0, 53.0, raan_deg, anomaly_deg),
                isl_terminal_count=2,
                ground_terminal_count=1,
                node_id=f"timeline-sat-p{plane:02d}s{slot:02d}",
                local_node_id=f"sat-P{plane:02d}S{slot:02d}",
                segment_id="timeline",
                central_body="earth",
                isl_terminals=(terminal,),
                ground_terminals=(access_terminal,),
            )
        )

    by_location = {(node.plane, node.slot): str(node.node_id) for node in nodes}
    assignments = []
    for plane, slot in by_location:
        node_id = by_location[(plane, slot)]
        assignments.extend(
            (
                (
                    node_id,
                    NeighborAssignment(
                        interface="isl0",
                        peer_node_id=by_location[(plane, 1 - slot)],
                        link_type="intra_plane_isl",
                        priority=0,
                    ),
                ),
                (
                    node_id,
                    NeighborAssignment(
                        interface="isl1",
                        peer_node_id=by_location[(1 - plane, slot)],
                        link_type="cross_plane_isl",
                        priority=1,
                    ),
                ),
            )
        )
    addressing = StaticOmeAddressing(
        satellite_ids=tuple(by_location.values()),
        ground_station_ids=("gs-equator",),
        ground_aliases={"equator": "gs-equator"},
    )
    return nodes, addressing, frozenset(assignments)


@pytest.fixture
def four_node_timeline():
    """Precompute a short timeline for the custom-example constellation."""
    sats, addressing, neighbors = _four_node_runtime()
    gs_file = GroundStationFile(
        default_terminals=[
            GroundTerminal(
                type="optical",
                count=1,
                interface_indices=(0,),
                bandwidth_mbps=1000.0,
                tracking_capacity=1,
                max_range_km=2000.0,
                field_of_regard_deg=120.0,
                max_tracking_rate_deg_s=1.5,
                boresight=TerminalBoresight(mode="local_vertical"),
            )
        ],
        default_min_elevation_deg=25.0,
        default_selection_policy=SelectionPolicySpec(name="highest-elevation", params={}),
        stations=[
            GroundStation(
                name="equator",
                lat_deg=0.0,
                lon_deg=0.0,
                reference_body="earth",
            )
        ],
    )

    events = precompute_timeline(
        satellites=sats,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=EPOCH,
        duration_s=60.0,  # Short: 60 seconds
        propagator_id="keplerian-circular",
        step_seconds=10,
        ground_scheduling=_ground_scheduling(),
        ground_candidate_satellites_by_gs={"gs-equator": tuple(str(sat.node_id) for sat in sats)},
        body_frames=EARTH_TEST_BODY_FRAMES,
    )
    return events


class TestClockTickEmission:
    def test_clock_tick_every_step(self, four_node_timeline):
        """ClockTick emitted every step_seconds."""
        ticks = [e for e in four_node_timeline if e.event_type == "ClockTick"]
        # 60s / 10s = 6 steps + step 0 = 7 ticks
        assert len(ticks) == 7

    def test_clock_tick_timestamps(self, four_node_timeline):
        ticks = [e for e in four_node_timeline if e.event_type == "ClockTick"]
        timestamps = [e.timestamp_s for e in ticks]
        assert timestamps == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]

    def test_clock_tick_is_correct_model(self, four_node_timeline):
        ticks = [e for e in four_node_timeline if e.event_type == "ClockTick"]
        first_tick = ticks[0].data
        assert isinstance(first_tick, ClockTick)
        assert first_tick.compression_ratio == 1.0


class TestVisibilityEvents:
    def test_visibility_events_are_correct_type(self, four_node_timeline):
        vis_events = [e for e in four_node_timeline if e.event_type == "VisibilityEvent"]
        for event in vis_events:
            assert isinstance(event.data, VisibilityEvent)
            # Alphabetically ordered (enforced by model validator)
            assert event.data.node_a < event.data.node_b

    def test_events_ordered_by_timestamp(self, four_node_timeline):
        """All events should be non-decreasing in timestamp."""
        timestamps = [e.timestamp_s for e in four_node_timeline]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


class TestJsonLinesIO:
    def test_write_and_read_round_trip(self, four_node_timeline, tmp_path):
        out_path = tmp_path / "timeline.jsonl"
        write_timeline_jsonl(four_node_timeline, out_path)
        assert out_path.exists()

        records = read_timeline_jsonl(out_path)
        assert len(records) == len(four_node_timeline)

    def test_each_line_is_valid_json(self, four_node_timeline, tmp_path):
        out_path = tmp_path / "timeline.jsonl"
        write_timeline_jsonl(four_node_timeline, out_path)

        with open(out_path) as f:
            for line in f:
                record = json.loads(line)
                assert "timestamp_s" in record
                assert "event_type" in record
                assert "data" in record

    def test_clock_tick_data_in_jsonl(self, four_node_timeline, tmp_path):
        out_path = tmp_path / "timeline.jsonl"
        write_timeline_jsonl(four_node_timeline, out_path)

        records = read_timeline_jsonl(out_path)
        ticks = [r for r in records if r["event_type"] == "ClockTick"]
        assert len(ticks) == 7

    def test_no_snapshot_events_in_timeline(self, four_node_timeline, tmp_path):
        """PRD v0.71: Snapshot events are no longer emitted by compute_step."""
        out_path = tmp_path / "timeline.jsonl"
        write_timeline_jsonl(four_node_timeline, out_path)

        records = read_timeline_jsonl(out_path)
        snapshots = [r for r in records if r["event_type"] == "Snapshot"]
        assert len(snapshots) == 0


class TestNoGroundStations:
    def test_timeline_without_gs(self):
        """Timeline works without ground stations."""
        sats, addressing, neighbors = _four_node_runtime()

        events = precompute_timeline(
            satellites=sats,
            addressing=addressing,
            gs_file=None,
            neighbors=neighbors,
            epoch_unix=EPOCH,
            duration_s=10.0,
            propagator_id="keplerian-circular",
            step_seconds=5,
            body_frames=EARTH_TEST_BODY_FRAMES,
        )
        ticks = [e for e in events if e.event_type == "ClockTick"]
        assert len(ticks) == 3  # 0, 5, 10
        # No Snapshot events (PRD v0.71 — positions distributed via SessionEphemeris)
        snapshots = [e for e in events if e.event_type == "Snapshot"]
        assert len(snapshots) == 0
