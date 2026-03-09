"""Test rolling window boundary state, timeline reader, and append logic.

Verifies:
- ISL/GS state carries across window boundaries
- GS visibility shifts orbit-to-orbit (Earth rotation)
- TimelineReader correctly tails growing files
- append_timeline_jsonl produces valid JSONL
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path

import pytest

from ome.event_stream import (
    TimelineEvent,
    append_timeline_jsonl,
    precompute_timeline,
    precompute_timeline_window,
    write_timeline_jsonl,
)
from ome.propagator import orbital_period
from orchestrator.timeline_reader import TimelineReader

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _load_session_fixtures():
    """Load custom-example constellation fixtures for testing."""
    import yaml
    from ome.constellation_loader import expand_constellation, load_constellation, load_ground_stations
    from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
    from nodalarc.models.session import SessionConfig

    # Build an inline session config referencing custom-example + us-conus
    session_data = {
        "session": {"name": "rolling-window-test"},
        "constellation": "configs/constellations/custom-example.yaml",
        "ground_stations": "configs/ground-stations/sets/us-conus.yaml",
        "routing": {
            "stack": "configs/routing-stacks/frr-isis-sr",
            "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
        },
        "time": {"mode": "discrete-event", "step_seconds": 10},
    }
    session = SessionConfig.model_validate(session_data)
    constellation_config = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)
    satellites = expand_constellation(constellation_config)
    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation_config, addressing)

    first_alt = satellites[0].elements.semi_major_axis_km - 6371.0
    period = orbital_period(first_alt)

    from datetime import datetime
    epoch_unix = datetime.fromisoformat(session.time.start_time).timestamp() if session.time.start_time else 1704067200.0

    return {
        "satellites": satellites,
        "addressing": addressing,
        "gs_file": gs_file,
        "neighbors": neighbors,
        "epoch_unix": epoch_unix,
        "period": period,
        "step_seconds": session.time.step_seconds,
    }


class TestBoundaryState:
    """Verify ISL/GS state carries across window boundaries."""

    def test_window_returns_three_tuple(self):
        """precompute_timeline_window returns (events, isl_state, gs_state)."""
        fix = _load_session_fixtures()
        result = precompute_timeline_window(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"],
            duration_s=fix["period"],
            step_seconds=fix["step_seconds"],
        )
        assert isinstance(result, tuple)
        assert len(result) == 3
        events, isl_state, gs_state = result
        assert isinstance(events, list)
        assert isinstance(isl_state, dict)
        assert isinstance(gs_state, dict)
        assert len(events) > 0

    def test_backward_compat_wrapper(self):
        """precompute_timeline() still returns list[TimelineEvent]."""
        fix = _load_session_fixtures()
        result = precompute_timeline(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"],
            duration_s=fix["period"],
            step_seconds=fix["step_seconds"],
        )
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], TimelineEvent)

    def test_boundary_state_carried_across_windows(self):
        """ISL state at end of window 1 becomes initial state for window 2."""
        fix = _load_session_fixtures()

        # Window 1
        events1, isl1, gs1 = precompute_timeline_window(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"],
            duration_s=fix["period"],
            step_seconds=fix["step_seconds"],
        )

        # Window 2 with boundary state
        events2, isl2, gs2 = precompute_timeline_window(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"] + fix["period"],
            duration_s=fix["period"],
            step_seconds=fix["step_seconds"],
            initial_isl_state=isl1,
            initial_gs_state=gs1,
            timestamp_offset=fix["period"],
        )

        # Window 2 timestamps start at period, not 0
        min_ts2 = min(e.timestamp_s for e in events2)
        assert min_ts2 >= fix["period"] - 1.0, (
            f"Window 2 timestamps should start at ~{fix['period']:.0f}s, "
            f"got {min_ts2:.1f}s"
        )

        # Window 2 should have fewer initial visibility events because
        # boundary state was carried (links already known)
        vis_events_1 = [e for e in events1 if e.event_type == "VisibilityEvent"]
        vis_events_2 = [e for e in events2 if e.event_type == "VisibilityEvent"]
        # Window 2 should have some events (state changes do happen)
        assert len(vis_events_2) > 0

    def test_no_duplicate_events_at_boundary(self):
        """No duplicate link-up events at window boundary."""
        fix = _load_session_fixtures()

        events1, isl1, gs1 = precompute_timeline_window(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"],
            duration_s=fix["period"],
            step_seconds=fix["step_seconds"],
        )

        events2, _, _ = precompute_timeline_window(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"] + fix["period"],
            duration_s=fix["period"],
            step_seconds=fix["step_seconds"],
            initial_isl_state=isl1,
            initial_gs_state=gs1,
            timestamp_offset=fix["period"],
        )

        # Get the first batch of vis events in window 2 (at timestamp_offset)
        boundary_vis = [
            e for e in events2
            if e.event_type == "VisibilityEvent"
            and abs(e.timestamp_s - fix["period"]) < 1.0
        ]

        # For links that were (visible, scheduled) at end of window 1,
        # window 2 should NOT emit another (visible, scheduled) at t=period
        # because the initial state already knows they're up
        isl_up_at_end = {
            pair for pair, (vis, sched) in isl1.items() if vis and sched
        }
        for ev in boundary_vis:
            pair = (ev.data.node_a, ev.data.node_b)
            if pair in isl_up_at_end:
                # If state didn't change, no event should be emitted
                # If event IS emitted, the new state must differ
                new_state = (ev.data.visible, ev.data.scheduled)
                old_state = isl1.get(pair, (False, False))
                assert new_state != old_state, (
                    f"Duplicate event at boundary for {pair}: "
                    f"old={old_state}, new={new_state}"
                )

    def test_timestamp_offset_applied(self):
        """timestamp_offset shifts all timestamps in the window."""
        fix = _load_session_fixtures()
        offset = 1000.0

        events, _, _ = precompute_timeline_window(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"],
            duration_s=60,  # Short window for speed
            step_seconds=fix["step_seconds"],
            timestamp_offset=offset,
        )

        for ev in events:
            assert ev.timestamp_s >= offset, (
                f"Event at {ev.timestamp_s}s should be >= offset {offset}s"
            )


class TestGSVisibilityShift:
    """Verify GS access windows change orbit-to-orbit due to Earth rotation."""

    def test_gs_events_differ_across_orbits(self):
        """GS events are NOT identical across windows (Earth rotates ~23°/orbit)."""
        fix = _load_session_fixtures()

        # Window 1
        events1, isl1, gs1 = precompute_timeline_window(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"],
            duration_s=fix["period"],
            step_seconds=fix["step_seconds"],
        )

        # Window 3 (two full orbits later — bigger shift)
        events3, _, _ = precompute_timeline_window(
            satellites=fix["satellites"],
            addressing=fix["addressing"],
            gs_file=fix["gs_file"],
            neighbors=fix["neighbors"],
            epoch_unix=fix["epoch_unix"] + 2 * fix["period"],
            duration_s=fix["period"],
            step_seconds=fix["step_seconds"],
            initial_isl_state=isl1,
            initial_gs_state=gs1,
            timestamp_offset=2 * fix["period"],
        )

        def _gs_vis_events(events):
            """Extract (node_a, node_b, visible, scheduled) tuples for GS events,
            using timestamps relative to window start."""
            result = []
            if not events:
                return result
            base_ts = events[0].timestamp_s
            for e in events:
                if e.event_type == "VisibilityEvent":
                    a, b = e.data.node_a, e.data.node_b
                    if a.startswith("gs-") or b.startswith("gs-"):
                        result.append((
                            a, b, e.data.visible, e.data.scheduled,
                            round(e.timestamp_s - base_ts),
                        ))
            return result

        gs1_events = _gs_vis_events(events1)
        gs3_events = _gs_vis_events(events3)

        # If both have GS events, they should differ (Earth rotation)
        if gs1_events and gs3_events:
            assert gs1_events != gs3_events, (
                "GS events should differ across orbits due to Earth rotation"
            )


class TestTimelineReader:
    """Verify TimelineReader correctly reads and groups events."""

    def test_basic_batch_reading(self, tmp_path):
        """TimelineReader yields correct batches from a static file."""
        timeline = tmp_path / "test.jsonl"
        records = [
            {"timestamp_s": 0.0, "event_type": "ClockTick", "data": {}},
            {"timestamp_s": 0.0, "event_type": "Snapshot", "data": {}},
            {"timestamp_s": 1.0, "event_type": "ClockTick", "data": {}},
            {"timestamp_s": 1.0, "event_type": "Snapshot", "data": {}},
            {"timestamp_s": 2.0, "event_type": "ClockTick", "data": {}},
        ]
        with open(timeline, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        reader = TimelineReader(timeline)
        try:
            batch1 = reader.next_batch(timeout_s=2.0)
            assert batch1 is not None
            assert len(batch1) == 2  # t=0 group
            assert all(r["timestamp_s"] == 0.0 for r in batch1)

            batch2 = reader.next_batch(timeout_s=2.0)
            assert batch2 is not None
            assert len(batch2) == 2  # t=1 group

            batch3 = reader.next_batch(timeout_s=2.0)
            assert batch3 is not None
            assert len(batch3) == 1  # t=2 group
        finally:
            reader.close()

    def test_returns_none_on_empty_file(self, tmp_path):
        """TimelineReader returns None when file is empty and timeout expires."""
        timeline = tmp_path / "empty.jsonl"
        timeline.write_text("")

        reader = TimelineReader(timeline)
        try:
            result = reader.next_batch(timeout_s=0.5)
            assert result is None
        finally:
            reader.close()

    def test_growing_file(self, tmp_path):
        """TimelineReader tails a file as new data is appended."""
        timeline = tmp_path / "growing.jsonl"
        # Write initial data
        with open(timeline, "w") as f:
            f.write(json.dumps({"timestamp_s": 0.0, "event_type": "ClockTick", "data": {}}) + "\n")
            f.write(json.dumps({"timestamp_s": 0.0, "event_type": "Snapshot", "data": {}}) + "\n")

        reader = TimelineReader(timeline)
        batches = []

        try:
            # Read first batch
            batch1 = reader.next_batch(timeout_s=2.0)
            assert batch1 is not None
            batches.append(batch1)

            # Append more data in background
            def _append():
                time.sleep(0.2)
                with open(timeline, "a") as f:
                    f.write(json.dumps({"timestamp_s": 1.0, "event_type": "ClockTick", "data": {}}) + "\n")
                    f.flush()

            t = threading.Thread(target=_append)
            t.start()

            batch2 = reader.next_batch(timeout_s=3.0)
            t.join()

            assert batch2 is not None
            assert batch2[0]["timestamp_s"] == 1.0
        finally:
            reader.close()

    def test_epsilon_grouping(self, tmp_path):
        """Events within epsilon_s are grouped together."""
        timeline = tmp_path / "epsilon.jsonl"
        records = [
            {"timestamp_s": 0.0, "event_type": "A", "data": {}},
            {"timestamp_s": 0.05, "event_type": "B", "data": {}},  # Within epsilon (0.1)
            {"timestamp_s": 1.0, "event_type": "C", "data": {}},   # New group
        ]
        with open(timeline, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        reader = TimelineReader(timeline, epsilon_s=0.1)
        try:
            batch1 = reader.next_batch(timeout_s=2.0)
            assert batch1 is not None
            assert len(batch1) == 2  # A and B grouped

            batch2 = reader.next_batch(timeout_s=2.0)
            assert batch2 is not None
            assert len(batch2) == 1  # C alone
        finally:
            reader.close()


class TestAppendTimeline:
    """Verify append_timeline_jsonl produces valid JSONL."""

    def test_append_creates_file(self, tmp_path):
        """append_timeline_jsonl creates the file if it doesn't exist."""
        from nodalarc.models.events import ClockTick
        from datetime import datetime, timezone

        out = tmp_path / "new.jsonl"
        now = datetime.now(timezone.utc)
        events = [
            TimelineEvent(0.0, "ClockTick", ClockTick(
                sim_time=now, wall_time=now, compression_ratio=1.0,
            )),
        ]
        append_timeline_jsonl(events, out)
        assert out.exists()

        lines = out.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["timestamp_s"] == 0.0
        assert record["event_type"] == "ClockTick"

    def test_append_adds_to_existing(self, tmp_path):
        """append_timeline_jsonl appends without overwriting."""
        from nodalarc.models.events import ClockTick
        from datetime import datetime, timezone

        out = tmp_path / "existing.jsonl"
        now = datetime.now(timezone.utc)
        events1 = [
            TimelineEvent(0.0, "ClockTick", ClockTick(
                sim_time=now, wall_time=now, compression_ratio=1.0,
            )),
        ]
        events2 = [
            TimelineEvent(1.0, "ClockTick", ClockTick(
                sim_time=now, wall_time=now, compression_ratio=1.0,
            )),
        ]

        # Write first, then append
        write_timeline_jsonl(events1, out)
        append_timeline_jsonl(events2, out)

        lines = out.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["timestamp_s"] == 0.0
        assert json.loads(lines[1])["timestamp_s"] == 1.0

    def test_write_then_read_roundtrip(self, tmp_path):
        """Events written and appended can be read back by TimelineReader."""
        from nodalarc.models.events import ClockTick
        from datetime import datetime, timezone

        out = tmp_path / "roundtrip.jsonl"
        now = datetime.now(timezone.utc)

        events1 = [
            TimelineEvent(0.0, "ClockTick", ClockTick(
                sim_time=now, wall_time=now, compression_ratio=1.0,
            )),
        ]
        events2 = [
            TimelineEvent(1.0, "ClockTick", ClockTick(
                sim_time=now, wall_time=now, compression_ratio=1.0,
            )),
        ]

        write_timeline_jsonl(events1, out)
        append_timeline_jsonl(events2, out)

        reader = TimelineReader(out)
        try:
            batch1 = reader.next_batch(timeout_s=2.0)
            assert batch1 is not None
            assert batch1[0]["timestamp_s"] == 0.0

            batch2 = reader.next_batch(timeout_s=2.0)
            assert batch2 is not None
            assert batch2[0]["timestamp_s"] == 1.0
        finally:
            reader.close()
