"""Tests for LookaheadWorker JSONL reading and future entry storage."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nodalpath.integration.lookahead_worker import (
    LookaheadWorker,
    _add_seconds_to_iso,
    _read_new_events,
)


def _write_events(path: Path, events: list[dict]) -> None:
    with open(path, "a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_read_new_events_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "timeline.jsonl"
    path.write_text("")
    events, pos = _read_new_events(path, 0)
    assert events == []
    assert pos == 0


def test_read_new_events_skips_clocktick(tmp_path: Path) -> None:
    path = tmp_path / "timeline.jsonl"
    _write_events(path, [
        {"event_type": "ClockTick", "data": {}},
        {"event_type": "VisibilityEvent", "data": {"sim_time": "2026-01-01T00:01:00Z"}},
    ])
    events, pos = _read_new_events(path, 0)
    assert len(events) == 1
    assert events[0][0] == "VisibilityEvent"


def test_read_new_events_incremental(tmp_path: Path) -> None:
    path = tmp_path / "timeline.jsonl"
    _write_events(path, [{"event_type": "Snapshot", "data": {"sim_time": "T1"}}])
    events1, pos1 = _read_new_events(path, 0)
    assert len(events1) == 1

    _write_events(path, [{"event_type": "Snapshot", "data": {"sim_time": "T2"}}])
    events2, pos2 = _read_new_events(path, pos1)
    assert len(events2) == 1
    assert events2[0][1]["sim_time"] == "T2"
    assert pos2 > pos1


def test_add_seconds_to_iso() -> None:
    result = _add_seconds_to_iso("2026-01-01T00:00:00+00:00", 3600)
    assert "01:00:00" in result


def test_read_new_events_missing_file(tmp_path: Path) -> None:
    events, pos = _read_new_events(tmp_path / "missing.jsonl", 0)
    assert events == []
    assert pos == 0


def test_lookahead_worker_sets_status_waiting(tmp_path: Path) -> None:
    """Worker sets 'waiting' status when no new data is available."""
    path = tmp_path / "timeline.jsonl"
    path.write_text("")

    console_state = MagicMock()
    store = MagicMock()
    store.get_timeline_ticks.return_value = [
        {"sim_time": "2026-01-01T00:01:00+00:00", "is_future": False}
    ]

    worker = LookaheadWorker(
        timeline_path=path,
        node_registry={},
        interface_map={},
        prefix_map={},
        bandwidth_map=None,
        almanac_store=store,
        lookahead_horizon_s=60,
        console_state=console_state,
    )

    async def run_briefly():
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.1)
        worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())
    calls = [c.args[0] for c in console_state.record_lookahead_status.call_args_list]
    assert "waiting" in calls
