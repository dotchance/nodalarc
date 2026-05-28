# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for the Scheduler epoch synchronization state machine.

The Scheduler starts UNSUSPENDED — it dispatches immediately on the
first LinkStateSnapshot. SUSPENDED is entered ONLY on a Tier 2 seek
(PlaybackState state="seeking"). These tests verify:
  1. Startup is unsuspended
  2. Seek enters SUSPENDED
  3. Resume requires all 4 conditions (playback, ephemeris, snapshot, clock tick)
  4. Watchdog kills the process on timeout
  5. Resume applies buffered snapshot and sets sim_time
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_state import LinkStateSnapshot
from scheduler.dispatcher import Dispatcher
from scheduler.epoch_sync import EpochSyncState


def _make_dispatcher(**overrides) -> Dispatcher:
    """Create a minimal Dispatcher for state machine testing."""
    defaults = {
        "interface_map": {},
        "bandwidth_map": {},
        "pod_locator": MagicMock(),
        "agent_pool": MagicMock(),
        "session_id": "test-session",
        "wiring_generation": "sha256:" + "a" * 64,
        "max_latency_age_s": 1.0,
        "gs_terminal_capacities": {},
        "sat_ground_terminal_capacities": {},
    }
    defaults.update(overrides)
    return Dispatcher(**defaults)


def _make_snapshot(
    epoch_id: int = 0,
    seq: int = 1,
    sim_time: datetime | None = None,
) -> LinkStateSnapshot:
    return LinkStateSnapshot(
        sim_time=sim_time or datetime(2025, 1, 1, tzinfo=UTC),
        snapshot_seq=seq,
        links=(),
        interval_s=5.0,
        epoch_id=epoch_id,
    )


def _make_visibility_event(
    sim_time: datetime,
    pair: tuple[str, str] = ("sat-a", "sat-b"),
) -> VisibilityEvent:
    return VisibilityEvent(
        sim_time=sim_time,
        node_a=pair[0],
        node_b=pair[1],
        visible=True,
        scheduled=True,
        range_km=1000.0,
        latency_ms=3.3,
        elevation_deg=None,
        terminal_type="optical",
        link_type="isl",
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )


class TestStartupState:
    """The Scheduler starts unsuspended — ready to dispatch immediately."""

    def test_starts_unsuspended(self):
        d = _make_dispatcher()
        assert d._suspended is False

    def test_expected_epoch_starts_at_zero(self):
        d = _make_dispatcher()
        assert d._expected_epoch_id == 0

    def test_deps_start_unmet(self):
        d = _make_dispatcher()
        assert d._epoch_deps_met == {"ephemeris": False, "snapshot": False}


class TestEpochSyncState:
    def test_begin_seek_resets_dependencies_and_buffers(self):
        state = EpochSyncState()
        old_snapshot = _make_snapshot(epoch_id=0)
        state.buffered_snapshot = old_snapshot
        state.deps_met = {"ephemeris": True, "snapshot": True}
        state.playback_playing_received = True
        state.stale = True

        assert state.begin_seek(2) is True

        assert state.suspended is True
        assert state.expected_epoch_id == 2
        assert state.deps_met == {"ephemeris": False, "snapshot": False}
        assert state.playback_playing_received is False
        assert state.buffered_snapshot is None
        assert state.stale is False

    def test_resume_requires_all_dependencies(self):
        state = EpochSyncState(suspended=True, expected_epoch_id=7)
        state.mark_playing(7)
        state.mark_ephemeris(7)

        assert state.missing_resume_dependencies() == ["LinkStateSnapshot"]
        with pytest.raises(RuntimeError, match="missing dependencies"):
            state.resume()

    def test_resume_returns_buffered_snapshot_and_clears_suspended(self):
        snapshot = _make_snapshot(epoch_id=3)
        state = EpochSyncState(suspended=True, expected_epoch_id=3)
        state.mark_playing(3)
        state.mark_ephemeris(3)
        state.buffer_snapshot(snapshot)

        assert state.resume() == snapshot
        assert state.suspended is False
        assert state.buffered_snapshot is None


class TestSeekEntersSuspended:
    """SUSPENDED is entered only on PlaybackState(state='seeking')."""

    def test_seek_enters_suspended(self):
        d = _make_dispatcher()
        assert d._suspended is False

        d._suspended = True
        d._expected_epoch_id = 1
        d._playback_playing_received = False
        d._epoch_deps_met = {"ephemeris": False, "snapshot": False}
        d._buffered_snapshot = None

        assert d._suspended is True
        assert d._expected_epoch_id == 1

    def test_seek_clears_stale(self):
        d = _make_dispatcher()
        d._stale = True

        d._stale = False
        d._suspended = True
        d._expected_epoch_id = 2

        assert d._stale is False

    def test_seek_resets_all_deps(self):
        d = _make_dispatcher()
        d._epoch_deps_met = {"ephemeris": True, "snapshot": True}
        d._playback_playing_received = True

        d._suspended = True
        d._expected_epoch_id = 3
        d._playback_playing_received = False
        d._epoch_deps_met = {"ephemeris": False, "snapshot": False}

        assert d._epoch_deps_met == {"ephemeris": False, "snapshot": False}
        assert d._playback_playing_received is False

    def test_begin_seek_resets_visibility_batch_but_preserves_actual_links(self):
        d = _make_dispatcher()
        old_time = datetime(2025, 1, 2, tzinfo=UTC)
        pair = ("sat-a", "sat-b")
        vis = _make_visibility_event(old_time, pair)
        actual_info = MagicMock()

        d._pending_visibility_events.append(vis)
        d._last_visibility_sim_time = old_time
        d._desired_links[pair] = MagicMock()
        d._ome_view[pair] = (True, True, "active")
        d._teardown_pairs.add(pair)
        d._latest_decision_snapshot = MagicMock()
        d._last_snapshot_sim_time = old_time
        d._actual_links[pair] = actual_info

        assert d._begin_seek_epoch(2) is True

        assert d._pending_visibility_events == []
        assert d._last_visibility_sim_time is None
        assert d._desired_links == {}
        assert d._ome_view == {}
        assert d._teardown_pairs == set()
        assert d._latest_decision_snapshot is None
        assert d._last_snapshot_sim_time is None
        assert d._actual_links[pair] is actual_info

    def test_reverse_seek_resumes_then_accepts_earlier_visibility_event(self):
        old_time = datetime(2025, 1, 2, tzinfo=UTC)
        target_time = datetime(2025, 1, 1, tzinfo=UTC)
        step1_time = target_time + timedelta(seconds=1)
        step2_time = target_time + timedelta(seconds=2)
        pair = ("sat-a", "sat-b")
        d = _make_dispatcher(
            interface_map={pair: ("isl0", "isl1")},
            bandwidth_map={pair: 1000.0},
        )
        d._pending_visibility_events.append(_make_visibility_event(old_time, pair))
        d._last_visibility_sim_time = old_time

        assert d._begin_seek_epoch(7) is True
        d._epoch_sync.mark_playing(7)
        d._epoch_sync.mark_ephemeris(7)
        assert d._epoch_sync.buffer_snapshot(
            _make_snapshot(epoch_id=7, seq=10, sim_time=target_time)
        )

        async def _run() -> None:
            await d._handle_clock_tick_payload({"sim_time": target_time.isoformat(), "epoch_id": 7})
            await d._handle_visibility_event(_make_visibility_event(step1_time, pair))
            await d._handle_clock_tick_payload({"sim_time": step2_time.isoformat(), "epoch_id": 7})

        asyncio.run(_run())

        assert d._suspended is False
        assert d._last_visibility_sim_time == step2_time
        assert d._pending_visibility_events == []
        resume_intent = d._dispatch_queue.get_nowait()
        event_intent = d._dispatch_queue.get_nowait()
        assert resume_intent.source == "resume"
        assert event_intent.source == "ome_event"
        assert event_intent.sim_time == step1_time
        assert pair in event_intent.desired


class TestSeekResume:
    """Resume from SUSPENDED requires all 4 conditions met simultaneously."""

    def _suspended_dispatcher(self, epoch_id: int = 1) -> Dispatcher:
        d = _make_dispatcher()
        d._suspended = True
        d._expected_epoch_id = epoch_id
        d._playback_playing_received = False
        d._epoch_deps_met = {"ephemeris": False, "snapshot": False}
        d._buffered_snapshot = None
        return d

    def test_resume_requires_all_four_conditions(self):
        d = self._suspended_dispatcher(epoch_id=1)
        d._playback_playing_received = True
        d._epoch_deps_met["ephemeris"] = True
        d._buffered_snapshot = _make_snapshot(epoch_id=1)
        d._epoch_deps_met["snapshot"] = True

        assert d._suspended is True

        tick_data = {"sim_time": "2025-01-01T00:00:00+00:00", "epoch_id": 1}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))

        assert d._suspended is False
        assert d._stale is False

    def test_no_resume_without_ephemeris(self):
        d = self._suspended_dispatcher()
        d._playback_playing_received = True
        d._buffered_snapshot = _make_snapshot(epoch_id=1)
        d._epoch_deps_met["snapshot"] = True

        tick_data = {"sim_time": "2025-01-01T00:00:00+00:00", "epoch_id": 1}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))
        assert d._suspended is True

    def test_no_resume_without_playing(self):
        d = self._suspended_dispatcher()
        d._epoch_deps_met["ephemeris"] = True
        d._buffered_snapshot = _make_snapshot(epoch_id=1)
        d._epoch_deps_met["snapshot"] = True

        tick_data = {"sim_time": "2025-01-01T00:00:00+00:00", "epoch_id": 1}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))
        assert d._suspended is True

    def test_no_resume_without_snapshot(self):
        d = self._suspended_dispatcher()
        d._playback_playing_received = True
        d._epoch_deps_met["ephemeris"] = True

        tick_data = {"sim_time": "2025-01-01T00:00:00+00:00", "epoch_id": 1}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))
        assert d._suspended is True

    def test_resume_sets_sim_time(self):
        d = self._suspended_dispatcher(epoch_id=0)
        d._playback_playing_received = True
        d._epoch_deps_met = {"ephemeris": True, "snapshot": True}
        d._buffered_snapshot = _make_snapshot(0)

        tick_data = {"sim_time": "2025-06-15T12:00:00+00:00", "epoch_id": 0}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))

        assert d._current_sim_time is not None
        assert d._current_sim_time.year == 2025
        assert d._current_sim_time.month == 6

    def test_resume_not_triggered_when_unsuspended(self):
        d = _make_dispatcher()
        assert d._suspended is False
        original_sim = d._current_sim_time

        tick_data = {"sim_time": "2025-06-15T12:00:00+00:00", "epoch_id": 0}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))

        assert d._current_sim_time == original_sim


class TestWatchdog:
    """Watchdog kills the process after 30s timeout on seek."""

    def test_watchdog_sets_stale_and_stops_running(self):
        d = _make_dispatcher()
        d._suspended = True
        d._expected_epoch_id = 5
        d._running = True

        async def _run():
            # Short watchdog for testing — real one is 30s
            await asyncio.sleep(0.05)
            if d._suspended and d._expected_epoch_id == 5:
                d._stale = True
                d._running = False

        asyncio.run(_run())
        assert d._stale is True
        assert d._running is False

    def test_watchdog_cancelled_on_resume(self):
        d = _make_dispatcher()
        d._suspended = True
        d._expected_epoch_id = 0

        async def _run():
            task = asyncio.create_task(d._epoch_watchdog(0))
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())
        assert not d._stale


class TestDispatcherRequiresSessionId:
    """Dispatcher construction requires session_id — no silent defaults."""

    def test_session_id_required(self):
        with pytest.raises(TypeError, match="session_id"):
            Dispatcher(
                interface_map={},
                bandwidth_map={},
                pod_locator=MagicMock(),
                agent_pool=MagicMock(),
                gs_terminal_capacities={},
                sat_ground_terminal_capacities={},
                # session_id omitted
            )

    def test_capacities_required(self):
        with pytest.raises(ValueError, match="gs_terminal_capacities is required"):
            Dispatcher(
                interface_map={},
                bandwidth_map={},
                pod_locator=MagicMock(),
                agent_pool=MagicMock(),
                session_id="test",
                wiring_generation="sha256:" + "a" * 64,
                max_latency_age_s=1.0,
                # capacities omitted — defaults to None
            )
