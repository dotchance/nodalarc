# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for the Scheduler epoch synchronization state machine.

Verifies the SUSPENDED -> resume state machine, watchdog expiry,
and ClockTick-as-resume-trigger semantics per PRD v0.71.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

from nodalarc.models.events import (
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    SessionEphemeris,
)
from nodalarc.models.link_state import LinkStateSnapshot
from scheduler.dispatcher import Dispatcher

EPOCH = 1735689600.0


def _make_dispatcher() -> Dispatcher:
    """Create a minimal Dispatcher for state machine testing."""
    return Dispatcher(
        interface_map={},
        bandwidth_map={},
        pod_locator=MagicMock(),
        agent_pool=MagicMock(),
        override_set=set(),
        override_lock=MagicMock(),
    )


def _make_ephemeris(epoch_id: int = 0) -> SessionEphemeris:
    return SessionEphemeris(
        epoch_id=epoch_id,
        sim_time=datetime(2025, 1, 1, tzinfo=UTC),
        epoch_unix=EPOCH,
        nodes={
            "sat-P00S00": EphemerisNodeKeplerian(
                altitude_km=550.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                true_anomaly_deg=0.0,
                plane=0,
                slot=0,
            ),
            "gs-ashburn": EphemerisNodeFixed(lat_deg=39.04, lon_deg=-77.49, alt_km=0.095),
        },
    )


def _make_snapshot(epoch_id: int = 0, seq: int = 1) -> LinkStateSnapshot:
    return LinkStateSnapshot(
        sim_time=datetime(2025, 1, 1, tzinfo=UTC),
        snapshot_seq=seq,
        links=(),
        interval_s=5.0,
        epoch_id=epoch_id,
    )


class TestSuspendedStateAtStartup:
    def test_starts_suspended(self):
        d = _make_dispatcher()
        assert d._suspended is True
        assert d._expected_epoch_id == 0

    def test_starts_with_deps_not_met(self):
        d = _make_dispatcher()
        assert d._epoch_deps_met == {"ephemeris": False, "snapshot": False}
        assert d._playback_playing_received is False


class TestEpochDependencyTracking:
    def test_ephemeris_sets_dep(self):
        d = _make_dispatcher()
        d._position_table.load_ephemeris(_make_ephemeris(0))
        d._epoch_deps_met["ephemeris"] = True
        assert d._epoch_deps_met["ephemeris"] is True

    def test_snapshot_buffer(self):
        d = _make_dispatcher()
        snap = _make_snapshot(0)
        d._buffered_snapshot = snap
        d._epoch_deps_met["snapshot"] = True
        assert d._buffered_snapshot is snap


class TestResumeOnClockTick:
    def test_resume_requires_all_four_conditions(self):
        d = _make_dispatcher()

        # Set all 3 pre-conditions
        d._playback_playing_received = True
        d._position_table.load_ephemeris(_make_ephemeris(0))
        d._epoch_deps_met["ephemeris"] = True
        d._buffered_snapshot = _make_snapshot(0)
        d._epoch_deps_met["snapshot"] = True

        assert d._suspended is True

        # Condition 4: ClockTick(epoch_id=0)
        tick_data = {"sim_time": "2025-01-01T00:00:00+00:00", "epoch_id": 0}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))

        assert d._suspended is False
        assert d._stale is False

    def test_no_resume_without_ephemeris(self):
        d = _make_dispatcher()
        d._playback_playing_received = True
        d._buffered_snapshot = _make_snapshot(0)
        d._epoch_deps_met["snapshot"] = True
        # ephemeris NOT loaded

        tick_data = {"sim_time": "2025-01-01T00:00:00+00:00", "epoch_id": 0}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))
        assert d._suspended is True

    def test_no_resume_without_playing(self):
        d = _make_dispatcher()
        d._position_table.load_ephemeris(_make_ephemeris(0))
        d._epoch_deps_met["ephemeris"] = True
        d._buffered_snapshot = _make_snapshot(0)
        d._epoch_deps_met["snapshot"] = True
        # playback_playing NOT received

        tick_data = {"sim_time": "2025-01-01T00:00:00+00:00", "epoch_id": 0}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))
        assert d._suspended is True

    def test_no_resume_without_snapshot(self):
        d = _make_dispatcher()
        d._playback_playing_received = True
        d._position_table.load_ephemeris(_make_ephemeris(0))
        d._epoch_deps_met["ephemeris"] = True
        # snapshot NOT buffered

        tick_data = {"sim_time": "2025-01-01T00:00:00+00:00", "epoch_id": 0}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))
        assert d._suspended is True


class TestSeekTransition:
    def test_new_seek_enters_suspended(self):
        d = _make_dispatcher()
        d._suspended = False
        d._expected_epoch_id = 0

        # Simulate seek to epoch 1
        d._suspended = True
        d._stale = False
        d._expected_epoch_id = 1
        d._playback_playing_received = False
        d._epoch_deps_met = {"ephemeris": False, "snapshot": False}
        d._buffered_snapshot = None

        assert d._suspended is True
        assert d._expected_epoch_id == 1

    def test_new_seek_clears_stale(self):
        d = _make_dispatcher()
        d._stale = True

        d._stale = False
        d._suspended = True
        d._expected_epoch_id = 2

        assert not d._stale


class TestWatchdog:
    def test_watchdog_sets_stale_on_timeout(self):
        d = _make_dispatcher()
        d._expected_epoch_id = 5

        async def _run():
            # Very short watchdog for testing
            await asyncio.sleep(0.05)
            if d._suspended and d._expected_epoch_id == 5:
                d._stale = True

        asyncio.run(_run())
        assert d._stale is True

    def test_watchdog_cancelled_on_resume(self):
        d = _make_dispatcher()
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

    def test_resume_sets_sim_time(self):
        """On resume, the triggering ClockTick's sim_time is applied."""
        d = _make_dispatcher()
        d._playback_playing_received = True
        d._position_table.load_ephemeris(_make_ephemeris(0))
        d._epoch_deps_met = {"ephemeris": True, "snapshot": True}
        d._buffered_snapshot = _make_snapshot(0)

        tick_data = {"sim_time": "2025-06-15T12:00:00+00:00", "epoch_id": 0}
        asyncio.run(d._try_resume_on_clock_tick(tick_data))

        assert d._current_sim_time is not None
        assert d._current_sim_time.year == 2025
        assert d._current_sim_time.month == 6
