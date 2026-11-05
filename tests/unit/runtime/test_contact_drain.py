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

import pytest
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


def _result(operation: str, counts):
    """One Node Agent result for the Mars end of the trunk, carrying ``counts``."""
    from nodalarc.models.scheduler_ops import ActuationFailureClass
    from scheduler.actuation import AgentCommandResult, build_actuation_result

    agent = AgentCommandResult(
        agent_addr="agent-mars",
        operation=operation,
        requested=(("mars-relay", "isl0"),),
        success_acks=frozenset({("agent-mars", "mars-relay", "isl0")}),
        failure_class=ActuationFailureClass.NONE,
        dirty_kernel=False,
        unknown_outcome=False,
        fence_failure=False,
        writer_fenced=False,
        details={},
        end_losses={("mars-relay", "isl0"): counts},
    )
    return build_actuation_result(
        operation=operation,
        requested_pairs=[TRUNK],
        pair_agent_ifaces={TRUNK: {("agent-mars", "mars-relay", "isl0")}},
        pair_link_type={TRUNK: "isl"},
        pair_gs_id={TRUNK: None},
        agent_results=[agent],
    )


def _counts(**reported):
    from scheduler.actuation import EndLossCounts

    fields = dict.fromkeys(
        (
            "admission_taken",
            "queue_dropped",
            "discarded_at_close",
            "untransmitted_at_stop",
            "transmit_dropped",
        ),
        0,
    )
    return EndLossCounts(**{**fields, **reported}, discarded_exact=True)


def test_an_ends_record_joins_what_it_reported_at_open_stop_and_close():
    """The Node Agent reports each count once, at the stage that counted it: refused while the
    contact was prepared (OPEN), purged before transmission and the transmit queue's drops
    (STOP), refused after the stop, receive queue drops and what the queue held (CLOSE)."""
    from nodalarc.proto import node_agent_pb2
    from scheduler.dispatch_actuator import end_loss_records, record_stage_losses

    contact = _trunk()
    record_stage_losses(
        _result("BatchLinkUp", _counts(admission_taken=12)),
        pair=TRUNK,
        info=contact,
        stage=node_agent_pb2.LINK_UP_STAGE_OPEN,
    )
    record_stage_losses(
        _result("BatchLinkDown", _counts(untransmitted_at_stop=135, transmit_dropped=4)),
        pair=TRUNK,
        info=contact,
        stage=node_agent_pb2.LINK_DOWN_STAGE_STOP_ADMISSION,
    )
    closed = _result(
        "BatchLinkDown", _counts(admission_taken=1775, queue_dropped=7, discarded_at_close=2)
    )
    (loss,) = end_loss_records(closed, pair=TRUNK, info=contact, cause="drain_deadline")
    assert (loss.node_id, loss.cause) == ("mars-relay", "drain_deadline")
    assert loss.refused_before_open == 12
    assert loss.untransmitted_at_stop == 135
    assert loss.transmit_dropped == 4
    assert loss.admission_taken == 1775
    assert (loss.queue_dropped, loss.discarded_at_close, loss.discarded_exact) == (7, 2, True)

    # The next contact on the same terminal starts its own record.
    (fresh,) = end_loss_records(closed, pair=TRUNK, info=_trunk(), cause="drain_deadline")
    assert (fresh.refused_before_open, fresh.untransmitted_at_stop) == (0, 0)


def test_a_stage_that_reports_a_count_it_does_not_produce_is_refused():
    from nodalarc.proto import node_agent_pb2
    from scheduler.dispatch_actuator import record_stage_losses

    with pytest.raises(ValueError, match="does not produce"):
        record_stage_losses(
            _result("BatchLinkUp", _counts(admission_taken=3, queue_dropped=1)),
            pair=TRUNK,
            info=_trunk(),
            stage=node_agent_pb2.LINK_UP_STAGE_OPEN,
        )
