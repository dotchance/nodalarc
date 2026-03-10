"""Tests for nodalpath.orchestrator.timeline_reader."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nodalarc.models.events import (
    ClockTick,
    TimelinePositionSnapshot,
    VisibilityEvent,
)
from nodalpath.orchestrator.timeline_reader import read_timeline


class TestReadTimeline:
    """Tests for read_timeline()."""

    def test_yields_records_in_order(self, synthetic_timeline_path: Path) -> None:
        """Records are yielded in the order they appear in the file."""
        records = list(read_timeline(synthetic_timeline_path))
        assert len(records) > 0
        # First record should be a Snapshot (t=0)
        assert isinstance(records[0], TimelinePositionSnapshot)
        # Subsequent records at t=0 should be VisibilityEvents
        for r in records[1:7]:
            assert isinstance(r, VisibilityEvent)

    def test_correct_model_types(self, synthetic_timeline_path: Path) -> None:
        """Each event_type maps to the correct Pydantic model."""
        records = list(read_timeline(synthetic_timeline_path))
        snapshots = [r for r in records if isinstance(r, TimelinePositionSnapshot)]
        vis_events = [r for r in records if isinstance(r, VisibilityEvent)]
        assert len(snapshots) == 1
        assert len(vis_events) == 8  # 4 ISL + 2 GS at t=0, 1 down at t=30, 1 up at t=60

    def test_start_time_filter(self, synthetic_timeline_path: Path) -> None:
        """Records before start_time are excluded."""
        # Filter out t=0 events, keep t=30 and t=60
        records = list(read_timeline(
            synthetic_timeline_path,
            start_time="2026-03-01T14:30:29+00:00",
        ))
        # Should only have events at t=30 and t=60
        assert len(records) == 2
        for r in records:
            assert isinstance(r, VisibilityEvent)

    def test_end_time_filter(self, synthetic_timeline_path: Path) -> None:
        """Records after end_time are excluded."""
        # Keep only t=0 events
        records = list(read_timeline(
            synthetic_timeline_path,
            end_time="2026-03-01T14:30:00+00:00",
        ))
        # Should have snapshot + 6 visibility events at t=0
        assert len(records) == 7

    def test_malformed_line_skipped(self, tmp_path: Path) -> None:
        """Malformed JSON lines are logged and skipped."""
        path = tmp_path / "bad.jsonl"
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        good_event = VisibilityEvent(
            sim_time=t0, node_a="sat-P00S00", node_b="sat-P00S01",
            visible=True, scheduled=True, range_km=2000.0,
            elevation_deg=None, terminal_type="optical",
        )
        good_record = json.dumps({
            "timestamp_s": 0.0,
            "event_type": "VisibilityEvent",
            "data": good_event.model_dump(mode="json"),
        })
        with open(path, "w") as f:
            f.write("NOT VALID JSON\n")
            f.write(good_record + "\n")
            f.write("{bad json too\n")

        records = list(read_timeline(path))
        assert len(records) == 1
        assert isinstance(records[0], VisibilityEvent)

    def test_empty_file_yields_nothing(self, tmp_path: Path) -> None:
        """An empty timeline file yields zero records."""
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        records = list(read_timeline(path))
        assert len(records) == 0

    def test_unknown_event_type_skipped(self, tmp_path: Path) -> None:
        """Unknown event types are skipped with a warning."""
        path = tmp_path / "unknown.jsonl"
        record = json.dumps({
            "timestamp_s": 0.0,
            "event_type": "UnknownEvent",
            "data": {"foo": "bar"},
        })
        path.write_text(record + "\n")
        records = list(read_timeline(path))
        assert len(records) == 0
