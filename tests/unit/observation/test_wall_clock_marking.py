# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""A wall-clock result is representative only when OME ran its whole interval at 1x.

The record is OME's own: the playback control handler and the pacing thread append a regime at
every change, and every PlaybackState carries the record. The judgment covers the result's full
interval, so a change made and undone during a measurement still marks it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import ome.main as ome_main
from nodalarc.models.events import PlaybackControlCommand, PlaybackState, TimeRegime
from nodalarc.time_regime import TimeRegimeRecordGap, regimes_during, representative

T0 = datetime(2027, 2, 19, 12, 0, tzinfo=UTC)


def _regime(seconds: float, cause: str, *, state="playing", rate=1.0, degraded=False):
    return TimeRegime(
        started_at=T0 + timedelta(seconds=seconds),
        cause=cause,
        state=state,
        commanded_rate=rate,
        pacing_degraded=degraded,
    )


RECORD = (
    _regime(0, "start"),
    _regime(100, "speed", rate=10.0),
    _regime(110, "speed", rate=1.0),
    _regime(200, "pause", state="paused"),
    _regime(260, "resume"),
    _regime(300, "pacing_degraded", degraded=True),
    _regime(320, "pacing_recovered"),
    _regime(400, "seek", state="seeking"),
    _regime(401, "seek"),
)


def _judge(start_s: float, end_s: float | None):
    during = regimes_during(
        T0 + timedelta(seconds=start_s),
        None if end_s is None else T0 + timedelta(seconds=end_s),
        RECORD,
    )
    return representative(during), [regime.cause for regime in during]


def test_a_measurement_inside_one_regime_at_1x_is_representative():
    assert _judge(10, 90) == (True, ["start"])
    assert _judge(120, 190) == (True, ["speed"])


def test_a_speed_change_made_and_undone_during_a_measurement_still_marks_it():
    assert _judge(90, 120) == (False, ["start", "speed", "speed"])


def test_a_measurement_entirely_at_another_speed_is_marked():
    assert _judge(101, 109) == (False, ["speed"])


def test_a_pause_a_shortfall_in_delivered_rate_and_a_seek_each_mark_a_measurement():
    assert _judge(190, 270) == (False, ["speed", "pause", "resume"])
    assert _judge(305, 310) == (False, ["pacing_degraded"])
    assert _judge(390, 405) == (False, ["pacing_recovered", "seek", "seek"])


def test_a_running_measurement_takes_every_regime_since_it_started():
    assert _judge(395, None) == (False, ["pacing_recovered", "seek", "seek"])


def test_a_record_that_starts_after_the_measurement_cannot_judge_it():
    try:
        regimes_during(T0 - timedelta(seconds=1), T0, RECORD)
    except TimeRegimeRecordGap:
        pass
    else:
        raise AssertionError("a gap in the record was judged")


def _reset_ome_controls() -> None:
    ome_main._time_accel = 1.0
    ome_main._seek_target = None
    ome_main._seeking = False
    ome_main._paused = False
    ome_main._epoch_id = 0
    ome_main._initial_epoch_committed = True
    ome_main._pause_reason = None
    ome_main._coverage_limit = None
    ome_main._pacing_degraded_now = False
    ome_main._time_regimes.clear()
    ome_main._record_time_regime("start", state="playing")


def test_ome_publishes_every_speed_change_with_its_record():
    _reset_ome_controls()
    published: list[PlaybackState] = []

    async def _publish() -> None:
        published.append(ome_main._playback_state(ome_main._epoch_id))

    # The same speed again is no change of regime.
    for factor in (10.0, 10.0, 1.0):
        asyncio.run(
            ome_main._handle_playback_control_command(
                PlaybackControlCommand(action="set_speed", factor=factor), _publish
            )
        )
    asyncio.run(
        ome_main._handle_playback_control_command(PlaybackControlCommand(action="pause"), _publish)
    )

    assert [state.state for state in published] == ["playing", "playing", "paused"]
    record = published[-1].time_regimes
    assert [(r.cause, r.state, r.commanded_rate) for r in record] == [
        ("start", "playing", 1.0),
        ("speed", "playing", 10.0),
        ("speed", "playing", 1.0),
        ("pause", "paused", 1.0),
    ]
    # Back at 1x, a measurement that began before the first change is still marked.
    during = regimes_during(record[0].started_at, None, record)
    assert not representative(during)


def test_ome_keeps_the_regime_in_effect_at_the_start_of_its_window():
    _reset_ome_controls()
    first = ome_main._time_regimes[0]
    # A regime older than the window, then one inside it: the older one stays as the regime in
    # effect at the window's start; the one before it goes.
    stale = first.model_copy(
        update={
            "started_at": first.started_at - timedelta(seconds=2 * ome_main._TIME_REGIME_WINDOW_S)
        }
    )
    ome_main._time_regimes.clear()
    ome_main._time_regimes.append(stale)
    ome_main._time_regimes.append(
        stale.model_copy(
            update={
                "started_at": first.started_at
                - timedelta(seconds=ome_main._TIME_REGIME_WINDOW_S + 5),
                "cause": "speed",
            }
        )
    )
    ome_main._record_time_regime("pause", state="paused")
    assert [regime.cause for regime in ome_main._time_regime_record()] == ["speed", "pause"]
