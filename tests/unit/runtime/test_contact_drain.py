# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The Scheduler's order of a long-delay contact: stop admission, keep the contact draining,
close each end when the last packet sent toward it has arrived in real time.

The records are the Scheduler's own link records. The deadlines are on the Scheduler's
monotonic clock, from the OME light time of the direction each end receives; no sim time takes
part, so speed, pause and seek cannot move a close.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scheduler.contact_drain import (
    due_ends,
    next_close_due,
    plan_long_delay_contacts,
    remaining_ends,
    start_drain,
)
from scheduler.desired_state import ActiveLinkInfo, Contact

TRUNK = ("earth-relay", "mars-relay")
OPENED = datetime(2027, 2, 19, tzinfo=UTC)
# Unequal light times each way: both ends move while the signal travels.
A_TO_B_MS = 1_113_442.7
B_TO_A_MS = 1_113_451.9


def _trunk(*, epoch: int = 0, opened: datetime = OPENED, a_to_b_ms: float = A_TO_B_MS):
    return ActiveLinkInfo(
        interface_a="isl3",
        interface_b="isl0",
        latency_a_to_b_ms=a_to_b_ms,
        latency_b_to_a_ms=B_TO_A_MS,
        link_type="isl",
        range_km=3.338e8,
        contact=Contact(epoch_id=epoch, opened_at=opened),
    )


def _draining(*, stopped_mono: float = 100.0):
    info = _trunk()
    info.drain = start_drain(
        TRUNK, info, stopped_mono=stopped_mono, stopped_wall=OPENED, reason="vis_lost"
    )
    return info


def test_each_end_closes_after_the_light_time_of_the_direction_it_receives():
    drain = _draining(stopped_mono=100.0).drain
    by_node = {end.node_id: end for end in drain.ends}
    # node_a receives what node_b transmits (b_to_a), and node_b what node_a transmits.
    assert by_node["earth-relay"].close_due == 100.0 + B_TO_A_MS / 1000.0
    assert by_node["mars-relay"].close_due == 100.0 + A_TO_B_MS / 1000.0
    assert by_node["earth-relay"].receives_until == OPENED + timedelta(milliseconds=B_TO_A_MS)


def test_an_end_is_due_only_when_its_own_last_packet_has_arrived():
    actual = {TRUNK: _draining(stopped_mono=100.0)}
    mars_due = 100.0 + A_TO_B_MS / 1000.0
    earth_due = 100.0 + B_TO_A_MS / 1000.0
    assert due_ends(actual, now_mono=mars_due - 0.001) == {}
    assert due_ends(actual, now_mono=mars_due) == {TRUNK: frozenset({"mars-relay"})}
    assert due_ends(actual, now_mono=earth_due) == {TRUNK: frozenset(TRUNK)}
    assert next_close_due(actual) == mars_due


def test_a_contact_that_ends_stops_once_and_then_waits_for_its_deadlines():
    ended = plan_long_delay_contacts({TRUNK: _trunk()}, {}, epoch_id=0, down_reasons={})
    assert ended.stop == frozenset({TRUNK})
    assert not ended.forced and not ended.open

    # Once draining, a later reconcile with the contact still gone changes nothing: no second
    # stop, and no close before the deadline.
    waiting = plan_long_delay_contacts({TRUNK: _draining()}, {}, epoch_id=0, down_reasons={})
    assert waiting.empty


def test_a_draining_contact_that_comes_back_reopens_instead_of_closing():
    back = _trunk(opened=OPENED + timedelta(hours=1))
    plan = plan_long_delay_contacts(
        {TRUNK: _draining()}, {TRUNK: back}, epoch_id=0, down_reasons={}
    )
    assert plan.open == frozenset({TRUNK})
    assert not plan.forced and not plan.stop


def test_a_renamed_contact_on_an_open_link_reopens_and_an_unchanged_one_only_retimes():
    renamed = plan_long_delay_contacts(
        {TRUNK: _trunk()},
        {TRUNK: _trunk(opened=OPENED + timedelta(minutes=3))},
        epoch_id=0,
        down_reasons={},
    )
    assert renamed.open == frozenset({TRUNK})

    retimed = plan_long_delay_contacts(
        {TRUNK: _trunk()}, {TRUNK: _trunk(a_to_b_ms=A_TO_B_MS + 4.5)}, epoch_id=0, down_reasons={}
    )
    assert retimed.latency == frozenset({TRUNK})
    assert not retimed.open


def test_a_seek_discards_every_contact_of_the_old_epoch_draining_ones_included():
    reopened_after_seek = _trunk(epoch=1, opened=OPENED)
    plan = plan_long_delay_contacts(
        {TRUNK: _trunk(epoch=0)}, {TRUNK: reopened_after_seek}, epoch_id=1, down_reasons={}
    )
    # Same opening sim time, new epoch: the old contact closes at once and a new one opens.
    assert plan.forced == {TRUNK: "seek"}
    assert plan.open == frozenset({TRUNK})

    draining = plan_long_delay_contacts({TRUNK: _draining()}, {}, epoch_id=1, down_reasons={})
    assert draining.forced == {TRUNK: "seek"}
    assert remaining_ends(TRUNK, _draining()) == frozenset(TRUNK)


def test_a_scenario_command_closes_a_contact_without_a_drain():
    plan = plan_long_delay_contacts(
        {TRUNK: _trunk()}, {}, epoch_id=0, down_reasons={TRUNK: "scenario_inject_down"}
    )
    assert plan.forced == {TRUNK: "scenario"}
    assert not plan.stop
