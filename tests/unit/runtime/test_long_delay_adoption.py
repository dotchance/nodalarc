# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""What a restarted Scheduler adopts of the long-delay contacts in the kernel, from the survey of
both ends of a link, and when the contacts it recovers close."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nodalarc.proto import node_agent_pb2
from scheduler.actuation import EndSurvey
from scheduler.contact_drain import RECOVERED_REASON, recover_drain
from scheduler.desired_state import Contact, StageLoss
from scheduler.dispatch_actuator import stage_only_records
from scheduler.long_delay_adoption import (
    AdoptedOpen,
    RecoveredContact,
    adopted_link_info,
    classify_surveyed_pair,
)
from scheduler.node_agent_batches import build_link_down_batch_plan

TRUNK = ("earth-relay", "mars-relay")
INTERFACES = ("isl3", "isl0")
OLD = Contact(epoch_id=2, opened_at=datetime(2027, 2, 19, 3, tzinfo=UTC))
NEW = Contact(epoch_id=2, opened_at=datetime(2027, 2, 19, 4, tzinfo=UTC))
# The receive queue delays each end carries: earth-relay receives what mars-relay transmits.
EARTH_RECEIVES_MS = 1_113_451.9
MARS_RECEIVES_MS = 1_113_442.7
BOUND_S = 1_335.2


def _survey(earth: tuple[Contact | None, bool], mars: tuple[Contact | None, bool]):
    def end(contact, stopped, delay_ms):
        if contact is None:
            return EndSurvey(
                carried_contact_id="",
                admission_stopped=False,
                receive_latency_ms=0.0,
                carried_peer="",
            )
        return EndSurvey(
            carried_contact_id=contact.contact_id,
            admission_stopped=stopped,
            receive_latency_ms=delay_ms,
            carried_peer="peer/isl0",
        )

    return {
        TRUNK[0]: end(*earth, EARTH_RECEIVES_MS),
        TRUNK[1]: end(*mars, MARS_RECEIVES_MS),
    }


def test_a_contact_open_at_both_ends_is_adopted_with_the_delays_its_queues_carry():
    found = classify_surveyed_pair(TRUNK, _survey((OLD, False), (OLD, False)))
    assert isinstance(found, AdoptedOpen)
    assert found.contact == OLD
    info = adopted_link_info(TRUNK, INTERFACES, found, link_type="isl")
    # node_a's transmissions are delayed in node_b's receive queue.
    assert (info.latency_a_to_b_ms, info.latency_b_to_a_ms) == (MARS_RECEIVES_MS, EARTH_RECEIVES_MS)
    assert info.commanded_netem_ms == (MARS_RECEIVES_MS, EARTH_RECEIVES_MS)
    assert info.drain is None and info.contact == OLD


@pytest.mark.parametrize(
    ("earth", "mars", "carried"),
    [
        # Draining: both stopped.
        ((OLD, True), (OLD, True), {TRUNK[0]: OLD, TRUNK[1]: OLD}),
        # An interrupted stop or open: one end admits, the other does not.
        ((OLD, False), (OLD, True), {TRUNK[0]: OLD, TRUNK[1]: OLD}),
        # One end already closed, or a first PREPARE that reached one end.
        ((None, False), (OLD, True), {TRUNK[1]: OLD}),
        # An interrupted reopen: one end prepared the new contact, the other carries the old.
        ((NEW, True), (OLD, True), {TRUNK[0]: NEW, TRUNK[1]: OLD}),
    ],
    ids=["draining", "half-stopped", "one-end", "half-reopened"],
)
def test_every_other_carried_state_is_recovered_under_the_oldest_contact(earth, mars, carried):
    found = classify_surveyed_pair(TRUNK, _survey(earth, mars))
    assert isinstance(found, RecoveredContact)
    assert found.contact == OLD
    assert found.carried == {node: contact.contact_id for node, contact in carried.items()}


def test_a_link_whose_ends_carry_nothing_is_not_adopted():
    assert classify_surveyed_pair(TRUNK, _survey((None, False), (None, False))) is None


def test_a_carried_name_that_is_no_contact_is_refused():
    surveys = _survey((OLD, False), (OLD, False))
    surveys[TRUNK[0]] = EndSurvey(
        carried_contact_id="garbage",
        admission_stopped=False,
        receive_latency_ms=1.0,
        carried_peer="",
    )
    with pytest.raises(ValueError, match="not a long-delay contact name"):
        classify_surveyed_pair(TRUNK, surveys)


def test_a_recovered_drain_closes_each_end_after_the_bound_from_the_proven_stop():
    """The deadline counts from the stop acknowledgement, never from the restart, and lasts the
    resolver's bound on the link's one-way delay: a packet queued under a longer delay than the
    queue now carries still arrives before its end closes."""
    found = classify_surveyed_pair(TRUNK, _survey((NEW, True), (OLD, True)))
    info = adopted_link_info(TRUNK, INTERFACES, found, link_type="isl")
    stopped_wall = datetime(2026, 11, 5, 19, tzinfo=UTC)
    drain = recover_drain(
        TRUNK,
        info,
        carried=found.carried,
        stopped_mono=500.0,
        stopped_wall=stopped_wall,
        longest_delay_s=BOUND_S,
    )
    assert drain.reason == RECOVERED_REASON
    assert {end.node_id: end.close_due for end in drain.ends} == {
        TRUNK[0]: 500.0 + BOUND_S,
        TRUNK[1]: 500.0 + BOUND_S,
    }
    assert {end.node_id: end.receives_until for end in drain.ends} == dict.fromkeys(
        TRUNK, stopped_wall + timedelta(seconds=BOUND_S)
    )
    info.drain = drain
    # Each end's close names the contact its kernel carries.
    plan = build_link_down_batch_plan(
        pairs={TRUNK},
        actual_links={TRUNK: info},
        locator=_Locator(),
        gs_capacities={},
        stage=node_agent_pb2.LINK_DOWN_STAGE_CLOSE,
        ends={TRUNK: frozenset(TRUNK)},
    )
    named = {
        entry.node_id: entry.contact_id
        for entries in plan.agent_ifaces.values()
        for entry in entries
    }
    assert named == {TRUNK[0]: NEW.contact_id, TRUNK[1]: OLD.contact_id}


def test_the_recovery_stop_reaches_only_carrying_ends_with_their_own_contacts():
    found = classify_surveyed_pair(TRUNK, _survey((None, False), (OLD, False)))
    info = adopted_link_info(TRUNK, INTERFACES, found, link_type="isl")
    plan = build_link_down_batch_plan(
        pairs={TRUNK},
        actual_links={TRUNK: info},
        locator=_Locator(),
        gs_capacities={},
        stage=node_agent_pb2.LINK_DOWN_STAGE_STOP_ADMISSION,
        ends={TRUNK: frozenset(found.carried)},
        contact_ids={(TRUNK[1], INTERFACES[1]): OLD.contact_id},
    )
    entries = [entry for batch in plan.agent_ifaces.values() for entry in batch]
    assert [(entry.node_id, entry.contact_id) for entry in entries] == [(TRUNK[1], OLD.contact_id)]


def test_an_end_stopped_at_recovery_that_reports_nothing_at_the_reopen_keeps_its_record():
    info = adopted_link_info(
        TRUNK,
        INTERFACES,
        classify_surveyed_pair(TRUNK, _survey((OLD, False), (OLD, True))),
        link_type="isl",
    )
    info.stage_losses = {
        TRUNK[0]: StageLoss(untransmitted_at_stop=40, transmit_dropped=1),
        TRUNK[1]: StageLoss(),
    }
    (record,) = stage_only_records(TRUNK, info, reported=frozenset({TRUNK[1]}), cause="reopened")
    assert (record.node_id, record.interface, record.untransmitted_at_stop) == (
        TRUNK[0],
        INTERFACES[0],
        40,
    )
    assert (record.transmit_dropped, record.discarded_at_close, record.cause) == (1, 0, "reopened")


class _Locator:
    def link_locality(self, node_a: str, node_b: str) -> int:
        return node_agent_pb2.LOCALITY_LOCAL

    def agent_addr(self, node_id: str) -> str:
        return f"agent-{node_id}"


def test_ground_ends_are_grouped_into_passes_by_contact_and_peer():
    """A restarted Scheduler finds a ground pass's two ends by the contact and the peer each
    end's tag records: the station's term0 and the orbiter's gnd0 carry one pass; the station's
    term1 still drains an earlier pass whose orbiter end already closed."""
    from scheduler.long_delay_adoption import ground_contacts

    station, orbiter = "dss-43", "mars-orbiter"

    def carried(contact, peer, *, stopped):
        return EndSurvey(
            carried_contact_id=contact.contact_id,
            admission_stopped=stopped,
            receive_latency_ms=MARS_RECEIVES_MS,
            carried_peer=peer,
        )

    reports = {
        (station, "term0"): carried(NEW, f"{orbiter}/gnd0", stopped=False),
        (orbiter, "gnd0"): carried(NEW, f"{station}/term0", stopped=False),
        (station, "term1"): carried(OLD, f"{orbiter}/gnd1", stopped=True),
        (orbiter, "gnd1"): EndSurvey(
            carried_contact_id="", admission_stopped=False, receive_latency_ms=0.0, carried_peer=""
        ),
    }
    passes = ground_contacts(reports)
    pair = (station, orbiter)
    assert set(passes) == {(pair, NEW.contact_id), (pair, OLD.contact_id)}
    interfaces, surveys = passes[(pair, NEW.contact_id)]
    assert interfaces == {station: "term0", orbiter: "gnd0"} and set(surveys) == set(pair)
    assert isinstance(classify_surveyed_pair(pair, surveys), AdoptedOpen)

    interfaces, surveys = passes[(pair, OLD.contact_id)]
    assert interfaces == {station: "term1", orbiter: "gnd1"} and set(surveys) == {station}
    empty = reports[(orbiter, "gnd1")]
    found = classify_surveyed_pair(pair, {node: surveys.get(node, empty) for node in pair})
    assert isinstance(found, RecoveredContact)
    assert found.carried == {station: OLD.contact_id}
    info = adopted_link_info(pair, ("term1", "gnd1"), found, link_type="ground")
    assert (info.link_type, info.interface_a, info.interface_b) == ("ground", "term1", "gnd1")
