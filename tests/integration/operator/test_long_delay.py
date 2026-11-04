"""A long-delay link gives each direction its own delay, the light time of a signal sent that way.

The two directions of a link between bodies differ, because each end moves while a signal
crosses: about 3 ms near an Earth-Mars opposition, and tens of milliseconds a month later. The
test seeks the session forward until the difference stands well above what IP timestamps
resolve, measures each direction with numbered pings, and seeks back to where it started.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from . import truths
from .harness.client import Operator
from .harness.network import Clock, Link, link_key, sim_seconds, watch

pytestmark = [pytest.mark.integration, pytest.mark.timeout(3600)]

# A link is taken as long-delay when both its directions are shown taking more than this; the
# platform's own bound is 10 s, and every link that long here is one between bodies.
LONG_DELAY_MS = 60_000.0
SEEK_STEP = timedelta(days=15)
MOST_SEEK_STEPS = 6


def _link_now(operator: Operator, key: tuple[str, str]) -> Link | None:
    link = {link_key(link): link for link in operator.state()["links"]}.get(key)
    return link if link is not None and link["state"] == "active" else None


def _seek_and_wait_for(operator: Operator, target: datetime, key: tuple[str, str]) -> Link | None:
    operator.playback("seek", target_sim_time=target.isoformat())
    operator.watch_state(
        180.0,
        until=lambda heard: (
            "links" in heard[-1][1]
            and sim_seconds(heard[-1][1]) >= target.timestamp()
            and any(
                link_key(link) == key and link["state"] == "active"
                for link in heard[-1][1]["links"]
            )
        ),
    )
    return _link_now(operator, key)


def test_each_direction_of_a_long_delay_link_takes_its_own_delay(
    operator: Operator, clock: Clock
) -> None:
    shown = watch(operator, 6.0)
    long_links = [
        link
        for link in shown.latest["links"]
        if link["state"] == "active"
        and min(link["latency_a_to_b_ms"], link["latency_b_to_a_ms"]) > LONG_DELAY_MS
    ]
    if not long_links:
        pytest.skip("did not run: the session shows no link taking more than a minute each way")
    link = long_links[0]
    key = link_key(link)
    started = datetime.fromtimestamp(clock.sim_time, UTC)
    target = started
    disagreements: list[str] = []
    try:
        for _ in range(MOST_SEEK_STEPS + 1):
            now = _link_now(operator, key)
            assert now is not None, f"{key}: the link is not shown active at {target.isoformat()}"
            difference = abs(now["latency_a_to_b_ms"] - now["latency_b_to_a_ms"])
            print(
                f"direction: {key} at {target.isoformat()}: directions differ by {difference:.1f} ms"
            )
            if difference > truths.ASYMMETRY_TO_JUDGE_MS:
                break
            target += SEEK_STEP
            assert _seek_and_wait_for(operator, target, key) is not None, (
                f"{key}: the link is not shown active after a seek to {target.isoformat()}"
            )
        else:
            pytest.skip(
                f"did not run: {key}'s directions stayed within {truths.ASYMMETRY_TO_JUDGE_MS} ms "
                f"of each other up to {target.isoformat()}"
            )
        disagreements = truths.each_direction_of_a_long_delay_link_takes_its_own_delay(
            operator, watch(operator, 6.0), now
        )
    finally:
        if target != started:
            operator.playback("seek", target_sim_time=started.isoformat())
    assert not disagreements, "\n".join(disagreements)
