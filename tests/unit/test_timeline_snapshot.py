"""Test timeline precomputation and JSON Lines I/O."""

import json

import pytest
from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.events import ClockTick, TimelinePositionSnapshot, VisibilityEvent
from pydantic import TypeAdapter

from ome.event_stream import (
    precompute_timeline,
    read_timeline_jsonl,
    write_timeline_jsonl,
)
from tests.conftest import CONFIGS_DIR

adapter = TypeAdapter(ConstellationConfig)
EPOCH = 1735689600.0


@pytest.fixture
def four_node_timeline():
    """Precompute a short timeline for the custom-example constellation."""
    config = load_constellation(CONFIGS_DIR / "constellations/custom-example.yaml")
    sats = expand_constellation(config)
    addressing = AddressingScheme()
    neighbors = assign_isl_neighbors(config, addressing)
    gs_file = load_ground_stations(CONFIGS_DIR / "ground-stations/sets/global.yaml")

    events = precompute_timeline(
        satellites=sats,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=EPOCH,
        duration_s=60.0,  # Short: 60 seconds
        step_seconds=10,
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


class TestSnapshotEmission:
    def test_snapshot_every_step(self, four_node_timeline):
        """Snapshot emitted every step alongside ClockTick."""
        snapshots = [e for e in four_node_timeline if e.event_type == "Snapshot"]
        assert len(snapshots) == 7

    def test_snapshot_contains_all_nodes(self, four_node_timeline):
        """Each snapshot contains positions for all 4 satellites + 7 ground stations."""
        snapshots = [e for e in four_node_timeline if e.event_type == "Snapshot"]
        first = snapshots[0].data
        assert isinstance(first, TimelinePositionSnapshot)
        # 4 sats + 7 ground stations = 11 nodes
        assert len(first.positions) == 11

    def test_snapshot_has_satellite_positions(self, four_node_timeline):
        snapshots = [e for e in four_node_timeline if e.event_type == "Snapshot"]
        positions = snapshots[0].data.positions
        assert "sat-P00S00" in positions
        assert "sat-P00S01" in positions
        assert "sat-P01S00" in positions
        assert "sat-P01S01" in positions

    def test_snapshot_has_gs_positions(self, four_node_timeline):
        snapshots = [e for e in four_node_timeline if e.event_type == "Snapshot"]
        positions = snapshots[0].data.positions
        assert "gs-hawthorne" in positions
        assert "gs-mcmurdo" in positions

    def test_satellite_has_velocity(self, four_node_timeline):
        snapshots = [e for e in four_node_timeline if e.event_type == "Snapshot"]
        pos = snapshots[0].data.positions["sat-P00S00"]
        # Satellite should have non-zero velocity
        vel_mag = (pos.vel_x_km_s**2 + pos.vel_y_km_s**2 + pos.vel_z_km_s**2) ** 0.5
        assert vel_mag > 7.0  # LEO velocity > 7 km/s

    def test_gs_has_zero_velocity(self, four_node_timeline):
        snapshots = [e for e in four_node_timeline if e.event_type == "Snapshot"]
        pos = snapshots[0].data.positions["gs-hawthorne"]
        assert pos.vel_x_km_s == 0.0
        assert pos.vel_y_km_s == 0.0
        assert pos.vel_z_km_s == 0.0


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

    def test_snapshot_data_in_jsonl(self, four_node_timeline, tmp_path):
        out_path = tmp_path / "timeline.jsonl"
        write_timeline_jsonl(four_node_timeline, out_path)

        records = read_timeline_jsonl(out_path)
        snapshots = [r for r in records if r["event_type"] == "Snapshot"]
        assert len(snapshots) == 7
        # First snapshot should have positions
        assert "positions" in snapshots[0]["data"]


class TestNoGroundStations:
    def test_timeline_without_gs(self):
        """Timeline works without ground stations."""
        config = load_constellation(CONFIGS_DIR / "constellations/custom-example.yaml")
        sats = expand_constellation(config)
        addressing = AddressingScheme()
        neighbors = assign_isl_neighbors(config, addressing)

        events = precompute_timeline(
            satellites=sats,
            addressing=addressing,
            gs_file=None,
            neighbors=neighbors,
            epoch_unix=EPOCH,
            duration_s=10.0,
            step_seconds=5,
        )
        ticks = [e for e in events if e.event_type == "ClockTick"]
        assert len(ticks) == 3  # 0, 5, 10
        # Only satellite positions (no GS)
        snapshots = [e for e in events if e.event_type == "Snapshot"]
        positions = snapshots[0].data.positions
        assert len(positions) == 4
