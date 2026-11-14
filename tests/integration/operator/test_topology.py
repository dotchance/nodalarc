# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Links come and go as the sky moves, and NodalArc keeps showing the truth afterward.

Change of topology is the normal condition of a session. The test lets the sky move, then asks
the routers and sends the packets again.
"""

from __future__ import annotations

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
    recorded_link_events,
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


# Sim seconds the session runs after the seek and before the change of nearest, for the old
# link to come up.
_LEAD_S = 20.0
# The change of nearest is read from the ephemeris to the second. OME decides once a tick, the
# Scheduler applies a tick's decisions when the next tick begins, and the feed is built once
# a second.
_CHANGE_ALLOWANCE_S = 8.0


def _relay_and_its_far_candidates(shown: ShownNetwork) -> tuple[str, list[Node]] | None:
    """A router in orbit whose link to a satellite of another body has its terminals assigned
    per pass, and the routers of the far segment it may link to."""
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
                return relay, candidates
    return None


def _links_to(snapshot: dict, relay: str, far: set[str]) -> dict[str, Link]:
    """The relay's active links to the far satellites, by the satellite at the far end."""
    return {
        peer_of(link, relay): link
        for link in snapshot["links"]
        if relay in link_key(link) and peer_of(link, relay) in far
    }


def _wall(event: dict) -> datetime:
    return datetime.fromisoformat(event["wall_time"])


def test_a_relay_hands_over_to_the_nearer_satellite_of_the_other_body(
    operator: Operator, recorded_session: None, clock: Clock
) -> None:
    """A relay's link to another body's satellites moves to the satellite that comes nearer.

    The session's ephemeris states the second at which another far satellite becomes the
    nearest. The session seeks to just before that second. NodalArc then shows the relay
    linked to the satellite that was nearest, and after the change to the nearer one. The
    recorded history states the order in the kernel: the Scheduler records a LinkUp and a
    LinkDown only once the Node Agent has proven it, and the new link's LinkUp comes before
    the old link's LinkDown. Afterward the relay's own router has carrier on the new
    interface alone, and the relay pings a router of the other routing domain across the
    new link.
    """
    shown = watch(operator, 10.0)
    found = _relay_and_its_far_candidates(shown)
    if found is None:
        pytest.skip(
            "did not run: the session shows no router in orbit linked to another body's "
            "satellites on terminals assigned per pass"
        )
    relay, candidates = found
    far = {node["node_id"] for node in candidates}
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
        f"relay handover: {relay}: nearest changes from {old_far} to {new_far} at "
        f"{datetime.fromtimestamp(change, UTC).isoformat()}; seeking to {target.isoformat()}"
    )

    held: list[tuple[float, dict[str, Link]]] = []

    def handed_over(messages: list[tuple[float, dict]]) -> bool:
        snapshot = messages[-1][1]
        if "links" not in snapshot or sim_seconds(snapshot) < target.timestamp():
            return False
        links = _links_to(snapshot, relay, far)
        held.append((sim_seconds(snapshot), links))
        return set(links) == {new_far} or sim_seconds(snapshot) > change + _CHANGE_ALLOWANCE_S

    operator.playback("seek", target_sim_time=target.isoformat())
    operator.watch_state(_LEAD_S + 2 * _CHANGE_ALLOWANCE_S + 60.0, until=handed_over)

    listed_before: tuple[str, ...] | None = None
    for at, links in held:
        listed = tuple(sorted(links))
        if listed != listed_before:
            print(
                f"relay handover: from {datetime.fromtimestamp(at, UTC).isoformat()} "
                f"NodalArc shows {relay} linked to: {', '.join(listed) or 'no far satellite'}"
            )
            listed_before = listed
    assert held, f"the feed showed no snapshot at or after {target.isoformat()}"
    ended, links = held[-1]
    assert set(links) == {new_far}, (
        f"{relay}: the nearest satellite changed from {old_far} to {new_far} at "
        f"{datetime.fromtimestamp(change, UTC).isoformat()}; by "
        f"{datetime.fromtimestamp(ended, UTC).isoformat()} NodalArc shows it linked to "
        f"{sorted(links) or 'no far satellite'}"
    )
    # Before the change the relay was linked to the satellite that was nearest, and NodalArc
    # showed it linked to no third satellite.
    linked = [set(links) for _, links in held if links]
    assert {old_far} in linked, f"{relay}: NodalArc never showed it linked to {old_far}"
    assert all(peers <= {old_far, new_far} for peers in linked), (
        f"{relay}: NodalArc showed it linked to {linked}"
    )
    old = next(links[old_far] for _, links in held if old_far in links)
    new = links[new_far]
    old_interface, new_interface = link_interface(old, relay), link_interface(new, relay)

    # The order in the kernel, from the recorded history.
    events = [
        event
        for event in recorded_link_events(operator)
        if relay in (event["node_a"], event["node_b"])
        and datetime.fromisoformat(event["sim_time"]).timestamp() >= target.timestamp()
    ]
    ups = [
        event
        for event in events
        if event["event_type"] == "LinkUp" and new_far in (event["node_a"], event["node_b"])
    ]
    downs = [
        event
        for event in events
        if event["event_type"] == "LinkDown" and old_far in (event["node_a"], event["node_b"])
    ]
    for event in sorted(ups + downs, key=_wall):
        print(
            f"relay handover: recorded {event['event_type']} {event['node_a']} "
            f"{event['interface_a']} <-> {event['node_b']} {event['interface_b']} at sim "
            f"{event['sim_time']}, wall {event['wall_time']}, reason {event['reason']}"
        )
    assert ups, f"{relay}: no LinkUp to {new_far} is recorded after {target.isoformat()}"
    assert downs, f"{relay}: no LinkDown from {old_far} is recorded after {target.isoformat()}"
    assert _wall(ups[0]) < _wall(downs[-1]), (
        f"{relay}: the link to {old_far} went down at {downs[-1]['wall_time']}, before the "
        f"link to {new_far} came up at {ups[0]['wall_time']}"
    )

    both = [old_interface, new_interface]
    with operator.terminal(relay) as terminal:
        up = interfaces_with_carrier(terminal, both)
    print(
        f"relay handover: after the handover {old_interface} was to {old_far}, "
        f"{new_interface} is to {new_far}; carrier on {sorted(up)}"
    )
    assert up == {new_interface}, (
        f"{relay}: after the handover {new_interface} to {new_far} has "
        f"{'carrier' if new_interface in up else 'no carrier'} and {old_interface} to "
        f"{old_far} has {'carrier' if old_interface in up else 'no carrier'}"
    )

    # The satellite the relay left is now one hop beyond the new link, in the other domain.
    destination = loopback_of(shown.nodes[old_far])
    round_trip = new["latency_a_to_b_ms"] + new["latency_b_to_a_ms"]
    with operator.terminal(relay) as terminal:
        printed = terminal.run(f"ping {destination}", interrupt_after=20.0)
    print(f"relay handover: {relay} ping {old_far} ({destination}):\n{printed}")
    replies = ping_reply_times_ms(printed)
    assert replies, f"{relay}: ping {old_far} ({destination}) across {new_far} gets no reply"
    assert min(replies) >= round_trip, (
        f"{relay}: ping {old_far} answered in {min(replies):.3f} ms; the link to {new_far} "
        f"alone takes {round_trip:.3f} ms there and back"
    )
