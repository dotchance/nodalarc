"""An operator controls the session's time, and the network follows the time shown.

A session's time may move in steps of any size; the tests read the step from the feed.
Every test leaves the session running at 1x.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from .harness.client import Operator
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
