"""Links come and go as the sky moves, and NodalArc keeps showing the truth afterward.

Change of topology is the normal condition of a session. The test lets the sky move, then asks
the routers and sends the packets again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from .harness.client import Operator
from .harness.network import Clock, let_the_sky_move, sim_seconds, wait_for_handover_overlap
from .truths import assert_session_is_truthful, make_before_break_handover_holds_both_links

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1200)]


def test_after_ground_links_change_the_session_still_shows_the_truth(
    operator: Operator, clock: Clock
) -> None:
    changes, started, ended = let_the_sky_move(operator)
    print(f"sky moved from {started} to {ended}: {changes}")
    if not changes:
        pytest.skip(f"no ground link of this session changed between {started} and {ended}")
    assert_session_is_truthful(operator)


def test_a_make_before_break_station_holds_both_links_during_a_handover(
    operator: Operator, clock: Clock
) -> None:
    outcome = make_before_break_handover_holds_both_links(operator)
    if isinstance(outcome, str):
        pytest.skip(f"did not run: {outcome}")
    assert not outcome, "\n".join(outcome)


def test_a_seek_in_the_middle_of_a_handover_leaves_the_session_truthful(
    operator: Operator, clock: Clock
) -> None:
    """The user seeks while a station holds its old link and its successor.

    The handover in progress belongs to the time the session left. After the seek every truth
    holds again: no link of the abandoned handover stays shown or stays in a router.
    """
    overlap = wait_for_handover_overlap(operator)
    if overlap is None:
        pytest.skip("did not run: no make-before-break overlap began in 50 sim minutes")
    ground, old, _successor = overlap
    target = datetime.fromtimestamp(sim_seconds(operator.state()), UTC) + timedelta(minutes=10)
    print(
        f"{ground}: seeking to {target.isoformat()} with "
        f"{old['teardown_remaining_ticks']} ticks of overlap left"
    )

    operator.playback("seek", target_sim_time=target.isoformat())
    operator.watch_state(
        60.0,
        until=lambda messages: (
            "links" in messages[-1][1] and sim_seconds(messages[-1][1]) >= target.timestamp()
        ),
    )
    assert_session_is_truthful(operator)
