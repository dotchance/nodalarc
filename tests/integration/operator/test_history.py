# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""A recorded session's history is what the session showed while it ran.

A user who deploys with "Record session history" reviews the run later. The link events NodalArc
recorded have to be the link changes it showed live, and a session deployed without recording has
to say so.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from .harness.client import Operator, Refused
from .harness.network import Clock, ground_links, recorded_link_events, records_history

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]


def test_the_recorded_link_history_is_what_the_session_showed(
    operator: Operator, recorded_session: None, clock: Clock
) -> None:
    assert operator.state()["history_recording"] == {"state": "recording", "error": None}

    shown: list[tuple[str, tuple[str, str]]] = []

    def six_changes(messages: list[tuple[float, dict[str, Any]]]) -> bool:
        snapshots = [message for _, message in messages if "links" in message]
        if len(snapshots) < 2:
            return False
        before, now = ground_links(snapshots[-2]), ground_links(snapshots[-1])
        shown.extend(("LinkUp", pair) for pair in now - before)
        shown.extend(("LinkDown", pair) for pair in before - now)
        return len(shown) >= 6

    operator.playback("set_speed", factor=10.0)
    try:
        heard = operator.watch_state(300.0, until=six_changes)
    finally:
        operator.playback("set_speed", factor=1.0)
    snapshots = [message for _, message in heard if "links" in message]
    if not shown:
        pytest.skip("did not run: no ground link of this session changed while it was watched")
    started, ended = snapshots[0]["sim_time"], snapshots[-1]["sim_time"]

    ground_pairs = {pair for _, pair in shown} | ground_links(snapshots[0])
    recorded = [
        (event["event_type"], pair)
        for event in recorded_link_events(operator, start=started, end=ended)
        if event["event_type"] in ("LinkUp", "LinkDown")
        and event["sim_time"] > started
        and (pair := (event["node_a"], event["node_b"])) in ground_pairs
    ]
    print(f"from {started} to {ended}: shown {sorted(shown)}")
    print(f"recorded {sorted(recorded)}")

    missing = Counter(shown) - Counter(recorded)
    assert not missing, f"NodalArc showed these link changes and did not record them: {missing}"

    def net(events: list[tuple[str, tuple[str, str]]]) -> dict[tuple[str, str], int]:
        change: Counter[tuple[str, str]] = Counter()
        for kind, pair in events:
            change[pair] += 1 if kind == "LinkUp" else -1
        return {pair: count for pair, count in change.items() if count}

    assert net(recorded) == net(shown), (
        "the recorded history ends with other links up than the session showed: "
        f"recorded {net(recorded)}, shown {net(shown)}"
    )


def test_a_session_deployed_without_recording_says_it_has_no_history(operator: Operator) -> None:
    if records_history(operator):
        pytest.skip("did not run: the running session records its history")
    with pytest.raises(Refused) as refusal:
        operator.get("/api/v1/links")
    print(refusal.value.status, refusal.value.body)
    assert refusal.value.status == 409 and refusal.value.body["code"] == "history.not_recorded"
