"""A recorded session's history is what the session showed while it ran.

A user who deploys with "Record session history" reviews the run later. The link events NodalArc
recorded have to be the link changes it showed live, and a session deployed without recording has
to say so.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest

from .harness.client import Operator, Refused
from .harness.network import Clock, ground_links

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]


@pytest.fixture
def recorded_session(operator: Operator) -> Iterator[None]:
    """The running session, deployed again with recording. Afterward it runs as it was found."""
    found = next(session for session in operator.sessions() if session.get("active"))
    was_recording = _records(operator)
    operator.wait_for_session(found, operator.switch(found, record_history=True))
    yield
    if not was_recording:
        operator.run_session(found)


def _records(operator: Operator) -> bool:
    recording = operator.state()["history_recording"]  # None for a session without recording
    return recording is not None and recording["state"] == "recording"


def _recorded_link_events(operator: Operator, **filters: str) -> list[dict[str, Any]]:
    """Every page of the recorded link events that match."""
    events: list[dict[str, Any]] = []
    cursor: dict[str, str] = {}
    while True:
        page = operator.get("/api/v1/links", **filters, **cursor)
        events.extend(page["events"])
        assert page["returned"] == len(page["events"]) <= 200
        if page["next_cursor"] is None:
            assert len({event["id"] for event in events}) == len(events) == page["total"], (
                f"the pages hold {len(events)} events, {page['total']} match"
            )
            return events
        cursor = {"cursor": page["next_cursor"]}


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
        for event in _recorded_link_events(operator, start=started, end=ended)
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
    if _records(operator):
        pytest.skip("did not run: the running session records its history")
    with pytest.raises(Refused) as refusal:
        operator.get("/api/v1/links")
    print(refusal.value.status, refusal.value.body)
    assert refusal.value.status == 409 and refusal.value.body["code"] == "history.not_recorded"
