# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""An operator controls the session's time, and the network follows the time shown.

A session's time may move in steps of any size; the tests read the step from the feed.
Every test leaves the session running at 1x.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from .harness.client import Operator, Refused
from .harness.network import Clock, sim_seconds
from .truths import assert_session_is_truthful, clock_runs_at_the_reported_speed

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


def _sim_times(operator: Operator, seconds: float) -> list[float]:
    return [
        sim_seconds(message) for _, message in operator.watch_state(seconds) if "links" in message
    ]


def test_pause_stops_the_clock_and_resume_restarts_it(operator: Operator, clock: Clock) -> None:
    long_enough = max(5.0, 2.5 * clock.wall_seconds_per_step)

    operator.playback("pause")
    paused = _sim_times(operator, long_enough)[1:]  # the first snapshot may predate the pause
    assert operator.state()["playback_paused"] is True
    assert len(set(paused)) == 1, f"sim time moved while paused: {paused}"

    operator.playback("resume")
    resumed = _sim_times(operator, long_enough)
    assert operator.state()["playback_paused"] is False
    assert resumed[-1] > paused[-1], f"sim time did not move in {long_enough:.0f} s after resume"


def test_a_faster_speed_is_the_speed_the_clock_runs(operator: Operator, clock: Clock) -> None:
    operator.playback("set_speed", factor=10.0)
    assert operator.state()["playback_speed"] == 10.0
    assert not clock_runs_at_the_reported_speed(operator)


def test_a_seek_moves_the_session_and_the_network_follows(operator: Operator, clock: Clock) -> None:
    step = timedelta(seconds=clock.wall_seconds_per_step * clock.sim_per_wall)
    target = datetime.fromtimestamp(clock.sim_time, UTC) + timedelta(minutes=20)

    operator.playback("seek", target_sim_time=target.isoformat())
    heard = operator.watch_state(
        30.0 + 3 * clock.wall_seconds_per_step,
        until=lambda messages: (
            "links" in messages[-1][1] and sim_seconds(messages[-1][1]) >= target.timestamp()
        ),
    )
    arrived = datetime.fromtimestamp(sim_seconds(heard[-1][1]), UTC)
    print(f"seek: asked for {target.isoformat()}; the session shows {arrived.isoformat()}")
    assert timedelta(0) <= arrived - target <= timedelta(seconds=30) + 3 * step

    assert_session_is_truthful(operator)


def test_the_clock_stops_where_the_ephemeris_data_ends(operator: Operator, clock: Clock) -> None:
    """A seek past the data is refused and names the usable interval. A session run to the end of
    that interval pauses at its last tick, says why, and refuses to resume; a seek back runs."""
    with pytest.raises(Refused) as beyond:
        operator.playback("seek", target_sim_time="2200-01-01T00:00:00+00:00")
    assert beyond.value.status == 400
    assert beyond.value.body["code"] == "playback.ephemeris_coverage"
    named = re.fullmatch(
        r"Cannot seek there: the (\w+) data supports this session from (\S+) to (\S+)",
        beyond.value.body["message"],
    )
    assert named, beyond.value.body["message"]
    usable_until = datetime.fromisoformat(named[3])

    step_s = clock.wall_seconds_per_step * clock.sim_per_wall
    operator.playback("set_speed", factor=60.0)  # a seek keeps the speed; none is taken during one
    operator.playback("seek", target_sim_time=(usable_until - timedelta(minutes=10)).isoformat())
    operator.watch_state(
        120.0,
        until=lambda heard: heard[-1][1].get("playback_pause_reason") == "ephemeris_coverage_ended",
    )
    stopped = operator.state()
    print(f"usable until {usable_until.isoformat()}; the clock stopped at {stopped['sim_time']}")
    assert stopped["playback_paused"] is True
    assert stopped["playback_pause_reason"] == "ephemeris_coverage_ended"
    assert stopped["playback_coverage"]["body_id"] == named[1]
    # The refusal's text gives whole seconds.
    limit = datetime.fromisoformat(stopped["playback_coverage"]["usable_until"])
    assert timedelta(0) <= limit - usable_until < timedelta(seconds=1)
    stopped_at = datetime.fromisoformat(stopped["sim_time"])
    assert limit - timedelta(seconds=step_s) < stopped_at <= limit

    with pytest.raises(Refused) as resumed:
        operator.playback("resume")
    assert resumed.value.status == 409
    assert resumed.value.body["code"] == "playback.ephemeris_coverage_ended"
    still = _sim_times(operator, max(5.0, 2.5 * clock.wall_seconds_per_step))
    assert set(still) == {stopped_at.timestamp()}, f"sim time moved after the stop: {still}"

    operator.playback("set_speed", factor=1.0)  # the truths below hold at 1x
    operator.playback(
        "seek", target_sim_time=datetime.fromtimestamp(clock.sim_time, UTC).isoformat()
    )
    back = operator.state()
    assert back["playback_paused"] is False
    assert back["playback_pause_reason"] is None
    assert back["playback_coverage"] is None
    assert_session_is_truthful(operator)
