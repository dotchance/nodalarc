# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Links come and go as the sky moves, and NodalArc keeps showing the truth afterward.

Change of topology is the normal condition of a session. The test lets the sky move, then asks
the routers and sends the packets again.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from .harness.client import Operator
from .harness.network import (
    Clock,
    Link,
    Node,
    ReferencePositions,
    ShownNetwork,
    interfaces_with_carrier,
    let_the_sky_move,
    link_interface,
    link_key,
    loopback_of,
    next_change_of_nearest,
    peer_of,
    ping_reply_times_ms,
    sim_seconds,
    wait_for_handover_overlap,
    watch,
)
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


# The relay handover runs at a tenth of real time, so the one tick a relay holds both links
# lasts ten seconds: long enough for the feed to show it and for the relay's router to answer.
_HANDOVER_SPEED = 0.1
# Sim seconds the session runs before the change of nearest, for the old link to come up.
_LEAD_S = 10.0
# The change of nearest is read from the ephemeris to the second. OME decides once a tick.
_CHANGE_ALLOWANCE_S = 5.0


def _relay_and_its_far_candidates(shown: ShownNetwork) -> tuple[str, str, list[Node]] | None:
    """A router in orbit whose link to a satellite of another body has its terminals assigned
    per pass, the rule of that link, and the routers of the far segment it may link to."""
    nodes = shown.nodes
    for link in shown.steady_links().values():
        if link["link_type"] != "inter_body_relay" or link["topology_mode"] != "visible_candidates":
            continue
        for relay in link_key(link):
            far_segment = nodes[peer_of(link, relay)]["segment_id"]
            candidates = [
                node
                for node in nodes.values()
                if node["segment_id"] == far_segment
                and node["node_type"] == "satellite"
                and node["role"] == "router"
            ]
            if nodes[relay]["role"] == "router" and len(candidates) > 1:
                return relay, link["link_rule_id"], candidates
    return None


def _links_of(snapshot: dict, relay: str, rule_id: str) -> dict[str, Link]:
    """The relay's active links of one rule, by the node at the far end."""
    return {
        peer_of(link, relay): link
        for link in snapshot["links"]
        if link["link_rule_id"] == rule_id and relay in link_key(link)
    }


def test_a_relay_hands_over_to_the_nearer_satellite_of_the_other_body(
    operator: Operator, clock: Clock
) -> None:
    """A relay's link to another body's satellites moves to the satellite that comes nearer.

    The session's ephemeris states the second at which another far satellite becomes the
    nearest. The session seeks to just before that second. NodalArc then shows the relay
    linked to the satellite that was nearest, and at the change it shows the old link ending
    with the nearer satellite as its successor. While both links are shown the relay's own
    router reports both interfaces up. Afterward the old interface is down, and the relay
    pings a router of the other routing domain across the new link.
    """
    shown = watch(operator, 10.0)
    found = _relay_and_its_far_candidates(shown)
    if found is None:
        pytest.skip(
            "did not run: the session shows no router in orbit linked to another body's "
            "satellites on terminals assigned per pass"
        )
    relay, rule_id, candidates = found
    predicted = next_change_of_nearest(
        ReferencePositions(shown),
        shown.nodes[relay],
        candidates,
        sim_seconds(shown.latest) + 60.0,
        within_s=6 * 3600.0,
    )
    if predicted is None:
        pytest.skip(f"did not run: the satellite nearest to {relay} does not change in 6 hours")
    change, old_far, new_far = predicted
    target = datetime.fromtimestamp(change - _LEAD_S, UTC)
    print(
        f"relay handover: {relay} ({rule_id}): nearest changes from {old_far} to {new_far} at "
        f"{datetime.fromtimestamp(change, UTC).isoformat()}; seeking to {target.isoformat()}"
    )

    held: list[tuple[float, dict[str, Link]]] = []

    def handover_began(messages: list[tuple[float, dict]]) -> bool:
        snapshot = messages[-1][1]
        if "links" not in snapshot or sim_seconds(snapshot) < target.timestamp():
            return False
        links = _links_of(snapshot, relay, rule_id)
        held.append((sim_seconds(snapshot), links))
        return (
            any(link["successor_pair"] for link in links.values())
            or sim_seconds(snapshot) > change + _CHANGE_ALLOWANCE_S
        )

    operator.playback("set_speed", factor=_HANDOVER_SPEED)
    try:
        operator.playback("seek", target_sim_time=target.isoformat())
        operator.watch_state(
            (_LEAD_S + 2 * _CHANGE_ALLOWANCE_S) / _HANDOVER_SPEED + 60.0, until=handover_began
        )
        listed_before: tuple[str, ...] | None = None
        for at, links in held:
            listed = tuple(
                f"{far}" + (" (ending)" if link["successor_pair"] else "")
                for far, link in sorted(links.items())
            )
            if listed != listed_before:
                print(
                    f"relay handover: from {datetime.fromtimestamp(at, UTC).isoformat()} "
                    f"NodalArc shows {relay} linked to: {', '.join(listed) or 'no satellite'}"
                )
                listed_before = listed
        if not any(links for _, links in held):
            pytest.skip(
                f"did not run: {relay} held no link of {rule_id} from {target.isoformat()} to "
                f"the change of nearest"
            )
        began, links = held[-1]
        ending = {far: link for far, link in links.items() if link["successor_pair"]}
        assert ending, (
            f"{relay}: the nearest satellite changed from {old_far} to {new_far} at "
            f"{datetime.fromtimestamp(change, UTC).isoformat()}; NodalArc showed no handover by "
            f"{datetime.fromtimestamp(began, UTC).isoformat()} and holds {sorted(links)}"
        )
        # The link that ends is the one to the satellite that was nearest, its successor is the
        # link to the satellite that is nearest now, and the successor is up beside it.
        assert set(ending) == {old_far}, f"{relay}: the link ending is to {sorted(ending)}"
        old = ending[old_far]
        assert set(old["successor_pair"]) == {relay, new_far}, (
            f"{relay}: the successor shown is {old['successor_pair']}; {new_far} is nearest"
        )
        assert new_far in links, f"{relay}: the successor link to {new_far} is not shown active"
        new = links[new_far]
        assert abs(began - change) <= _CHANGE_ALLOWANCE_S, (
            f"{relay}: the handover began at {datetime.fromtimestamp(began, UTC).isoformat()}; "
            f"the nearest satellite changed at {datetime.fromtimestamp(change, UTC).isoformat()}"
        )
        earlier = [set(links) for at, links in held[:-1] if links]
        assert all(far == {old_far} for far in earlier), (
            f"{relay}: before the change it was linked to {earlier}; {old_far} was nearest"
        )

        # The relay's own router holds both links while NodalArc shows both.
        old_interface, new_interface = link_interface(old, relay), link_interface(new, relay)
        both = [old_interface, new_interface]
        while True:
            with operator.terminal(relay) as terminal:
                up = interfaces_with_carrier(terminal, both)
            both_shown = any(
                link_key(link) == link_key(old) and link["successor_pair"]
                for link in operator.state()["links"]
            )
            print(
                f"relay handover: both links shown: {both_shown}; {old_interface} to {old_far} "
                f"and {new_interface} to {new_far}: carrier on {sorted(up)}"
            )
            if set(both) <= up:
                break
            assert both_shown, (
                f"{relay}: NodalArc showed {old_interface} to {old_far} and {new_interface} to "
                f"{new_far} up together; the router never reported both up"
            )
            time.sleep(0.5)

        after = operator.watch_state(
            3 / _HANDOVER_SPEED + 30.0,
            until=lambda messages: (
                "links" in messages[-1][1]
                and set(_links_of(messages[-1][1], relay, rule_id)) == {new_far}
            ),
        )
        remaining = _links_of(after[-1][1], relay, rule_id)
        assert set(remaining) == {new_far}, (
            f"{relay}: after the handover NodalArc shows links to {sorted(remaining)}"
        )
    finally:
        operator.playback("set_speed", factor=1.0)

    with operator.terminal(relay) as terminal:
        up = interfaces_with_carrier(terminal, both)
    print(f"relay handover: after the handover carrier on {sorted(up)}")
    assert up == {new_interface}, (
        f"{relay}: after the handover {new_interface} to {new_far} has "
        f"{'carrier' if new_interface in up else 'no carrier'} and {old_interface} to "
        f"{old_far} has {'carrier' if old_interface in up else 'no carrier'}"
    )

    # The satellite the relay left is now one hop beyond the new link, in the other domain.
    destination = loopback_of(shown.nodes[old_far])
    round_trip = remaining[new_far]["latency_a_to_b_ms"] + remaining[new_far]["latency_b_to_a_ms"]
    with operator.terminal(relay) as terminal:
        printed = terminal.run(f"ping {destination}", interrupt_after=20.0)
    print(f"relay handover: {relay} ping {old_far} ({destination}):\n{printed}")
    replies = ping_reply_times_ms(printed)
    assert replies, f"{relay}: ping {old_far} ({destination}) across {new_far} gets no reply"
    assert min(replies) >= round_trip, (
        f"{relay}: ping {old_far} answered in {min(replies):.3f} ms; the link to {new_far} "
        f"alone takes {round_trip:.3f} ms there and back"
    )
