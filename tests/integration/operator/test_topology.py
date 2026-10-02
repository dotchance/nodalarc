"""Links come and go as the sky moves, and NodalArc keeps showing the truth afterward.

Change of topology is the normal condition of a session. The test lets the sky move, then asks
the routers and sends the packets again.
"""

from __future__ import annotations

from typing import Any

import pytest

from .harness.client import Operator
from .harness.network import Clock, link_key
from .truths import assert_session_is_truthful

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1200)]

FAST = 30.0
ENOUGH_CHANGES = 6
LONGEST_WAIT = 240.0


def _ground_links(snapshot: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        link_key(link)
        for link in snapshot["links"]
        if link["link_type"] == "ground" and link["state"] == "active"
    }


def test_after_ground_links_change_the_session_still_shows_the_truth(
    operator: Operator, clock: Clock
) -> None:
    changes: list[str] = []

    def enough(messages: list[tuple[float, dict[str, Any]]]) -> bool:
        snapshots = [message for _, message in messages if "links" in message]
        if len(snapshots) < 2:
            return False
        before, now = _ground_links(snapshots[-2]), _ground_links(snapshots[-1])
        changes.extend(f"up {a} <-> {b}" for a, b in sorted(now - before))
        changes.extend(f"down {a} <-> {b}" for a, b in sorted(before - now))
        return len(changes) >= ENOUGH_CHANGES

    operator.playback("set_speed", factor=FAST)
    heard = operator.watch_state(LONGEST_WAIT, until=enough)
    operator.playback("set_speed", factor=1.0)

    snapshots = [message for _, message in heard if "links" in message]
    print(f"sky moved from {snapshots[0]['sim_time']} to {snapshots[-1]['sim_time']}: {changes}")
    if not changes:
        pytest.skip(
            f"no ground link of this session changed between {snapshots[0]['sim_time']} "
            f"and {snapshots[-1]['sim_time']}"
        )
    assert_session_is_truthful(operator)
