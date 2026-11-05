# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME names each contact of a fixed long-delay link: the sim time it opened, within its epoch.

The Node Agent keeps one delay queue per contact and refuses to change a contact it does not
carry, so the name must stay the same while a contact is open, change when the link comes up
again, and change at a seek even when the new contact opens at the same sim time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
    contact_id,
)
from nodalarc.scheduling_checkpoint import (
    decode_retained_replay_anchor,
    encode_retained_replay_anchor,
)
from ome.event_diff import diff_isl_visibility_events
from ome.event_stream import _track_isl_contacts
from ome.isl_engine import IslFeasibilityResult, ScheduledIsl
from ome.replay_anchor import build_replay_anchor, replay_state_from_anchor
from ome.snapshot_builder import LinkSnapshotSource, build_link_state_snapshot

T0 = datetime(2027, 2, 19, tzinfo=UTC)
TRUNK = ("earth-relay", "mars-relay")
LOCAL = ("earth-a", "earth-b")


def _isl_result(pair: tuple[str, str], *, feasible: bool) -> IslFeasibilityResult:
    return IslFeasibilityResult(
        pair=pair,
        link_type="cross_plane_isl",
        feasible=feasible,
        range_km=1.0e8,
        one_way_a_to_b_ms=333_564.1,
        one_way_b_to_a_ms=333_571.9,
        reject_reason="ok" if feasible else "los_blocked",
        terminal_type="optical",
        terminal_role_a="trunk",
        terminal_role_b="trunk",
        interface_a="isl0",
        interface_b="isl0",
        applied_max_range_km=4.0e8,
        applied_max_tracking_rate_deg_s=None,
        applied_field_of_regard_deg=None,
    )


def _scheduled(pair: tuple[str, str], *, scheduled: bool) -> ScheduledIsl:
    return ScheduledIsl(
        pair=pair,
        terminal_role_a="trunk",
        terminal_role_b="trunk",
        range_km=1.0e8,
        one_way_a_to_b_ms=333_564.1,
        one_way_b_to_a_ms=333_571.9,
        scheduled=scheduled,
        unscheduled_reason=None,
    )


def test_a_contact_opens_at_the_tick_its_link_comes_up_and_keeps_that_opening():
    contacts: dict = {}
    states = [(False, False), (True, True), (True, True), (True, False), (True, True)]
    seen = []
    for tick, state in enumerate(states):
        sim_time = T0 + timedelta(seconds=tick)
        _track_isl_contacts(
            contacts,
            long_delay_pairs=frozenset({TRUNK}),
            isl_state={TRUNK: state, LOCAL: (True, True)},
            sim_time=sim_time,
        )
        seen.append(contacts.get(TRUNK))
    # Down: none. Up at tick 1: opened then, and still that opening at tick 2. Visible but not
    # scheduled at tick 3: the contact ended. Up again at tick 4: a new contact.
    assert seen == [
        None,
        T0 + timedelta(seconds=1),
        T0 + timedelta(seconds=1),
        None,
        T0 + timedelta(seconds=4),
    ]
    # A local link never has a contact.
    assert LOCAL not in contacts


def test_the_event_that_brings_a_long_delay_link_up_opens_its_contact():
    up = diff_isl_visibility_events(
        sim_time=T0,
        feasibility={
            TRUNK: _isl_result(TRUNK, feasible=True),
            LOCAL: _isl_result(LOCAL, feasible=True),
        },
        scheduled_links={
            TRUNK: _scheduled(TRUNK, scheduled=True),
            LOCAL: _scheduled(LOCAL, scheduled=True),
        },
        previous_state={},
        long_delay_pairs=frozenset({TRUNK}),
    )
    by_pair = {(event.node_a, event.node_b): event for event in up.events}
    assert by_pair[TRUNK].contact_opened_at == T0
    assert by_pair[LOCAL].contact_opened_at is None

    later = T0 + timedelta(minutes=5)
    down = diff_isl_visibility_events(
        sim_time=later,
        feasibility={TRUNK: _isl_result(TRUNK, feasible=False)},
        scheduled_links={TRUNK: _scheduled(TRUNK, scheduled=False)},
        previous_state=up.state,
        long_delay_pairs=frozenset({TRUNK}),
    )
    (event,) = down.events
    assert not event.visible
    assert event.contact_opened_at is None


def _source(isl_state, contacts) -> LinkSnapshotSource:
    return LinkSnapshotSource(
        isl_state=isl_state,
        ground_state={},
        associations={},
        pending_teardowns={},
        ranges_km={pair: 1.0e8 for pair, state in isl_state.items() if state == (True, True)},
        latencies_ms={
            pair: (333_564.1, 333_571.9)
            for pair, state in isl_state.items()
            if state == (True, True)
        },
        contacts_opened=contacts,
    )


def test_the_snapshot_carries_each_open_contact_through_the_wire():
    now = T0 + timedelta(seconds=30)
    snapshot = build_link_state_snapshot(
        _source({TRUNK: (True, True), LOCAL: (True, True)}, {TRUNK: T0}),
        interface_map={TRUNK: ("isl0", "isl0"), LOCAL: ("isl0", "isl1")},
        sim_time=now,
        seq=7,
        interval_s=5.0,
        epoch_id=2,
    )
    wire = snapshot.model_dump_json()
    decoded = LinkStateSnapshot.model_validate_json(wire)
    assert decoded.model_dump_json() == wire
    links = {(link.node_a, link.node_b): link for link in decoded.links}
    assert links[TRUNK].contact_opened_at == T0
    assert links[LOCAL].contact_opened_at is None
    assert (
        contact_id(decoded.epoch_id, links[TRUNK].contact_opened_at)
        == "2@2027-02-19T00:00:00.000000Z"
    )


def test_the_snapshot_refuses_a_contact_on_a_link_that_is_not_up():
    with pytest.raises(ValueError, match="open contact but its carrier is not up"):
        build_link_state_snapshot(
            _source({TRUNK: (True, False)}, {TRUNK: T0}),
            interface_map={TRUNK: ("isl0", "isl0")},
            sim_time=T0,
            seq=1,
            interval_s=5.0,
        )


def _link(**overrides) -> dict:
    values = {
        "node_a": TRUNK[0],
        "node_b": TRUNK[1],
        "interface_a": "isl0",
        "interface_b": "isl0",
        "admin": AdminState.UP,
        "carrier": CarrierState.UP,
        "routing": RoutingState.UNKNOWN,
        "range_km": 1.0e8,
        "latency_a_to_b_ms": 333_564.1,
        "latency_b_to_a_ms": 333_571.9,
        "link_type": "isl",
        "contact_opened_at": T0,
        "sim_time": T0 + timedelta(seconds=10),
    }
    values.update(overrides)
    return values


def test_the_models_refuse_a_contact_where_no_contact_can_be():
    LinkState(**_link())
    with pytest.raises(ValueError, match="carrier is up"):
        LinkState(
            **_link(
                carrier=CarrierState.DOWN,
                range_km=None,
                latency_a_to_b_ms=None,
                latency_b_to_a_ms=None,
            )
        )
    with pytest.raises(ValueError, match="after the snapshot time"):
        LinkState(**_link(contact_opened_at=T0 + timedelta(seconds=11)))
    with pytest.raises(ValueError, match="not at the event's sim time"):
        VisibilityEvent(
            sim_time=T0,
            node_a=TRUNK[0],
            node_b=TRUNK[1],
            visible=True,
            scheduled=True,
            range_km=1.0e8,
            elevation_deg=None,
            terminal_type="optical",
            link_type="isl",
            visibility_reject_reason="ok",
            unscheduled_reason=None,
            contact_opened_at=T0 - timedelta(seconds=1),
        )


def test_the_replay_anchor_keeps_each_contact_opening():
    anchor = build_replay_anchor(
        epoch_id=3,
        step=12,
        isl_state={TRUNK: (True, True)},
        gs_state={},
        associations={},
        teardowns={},
        receive_windows={},
        isl_contacts={TRUNK: T0},
        ground_station_ids=frozenset(),
        written_at=1.0,
    )
    decoded = decode_retained_replay_anchor(encode_retained_replay_anchor(anchor))
    assert decoded is not None
    *_rest, contacts = replay_state_from_anchor(decoded)
    assert contacts == {TRUNK: T0}


def test_a_shipped_trunk_keeps_its_contact_while_up_and_a_seek_opens_a_new_one():
    """The Earth-Mars trunk of a shipped session through the production tick: the resolver's
    class reaches OME, the trunk's contact keeps its opening from tick to tick, and the state a
    seek starts from opens a contact at the seek target, named in the new epoch."""
    from nodalarc.models.session import session_epoch_unix
    from ome.event_stream import compute_step

    from tests.unit.sessions.test_shipped_sessions_contract import (
        _assert_tick_round_trips,
        _shipped_step_context,
    )

    cfg, ctx = _shipped_step_context(Path("catalog/nodalarc/sessions/earth-mars-hdtn-relay.yaml"))
    assert ctx.long_delay_pairs == cfg.resolved.long_delay_fixed_pairs()
    (trunk,) = ctx.long_delay_pairs
    epoch_unix = session_epoch_unix(cfg.resolved.time)

    def run(start_unix: float, steps: int) -> list[LinkState]:
        isl: dict = {}
        gs: dict = {}
        contacts: dict = {}
        assoc: dict = {}
        teardowns: dict = {}
        trunk_states = []
        for step in range(steps):
            result = compute_step(
                ctx, start_unix, step, 60.0, 0.0, isl, gs, assoc, teardowns, isl_contacts=contacts
            )
            assoc, teardowns = result.associations, result.pending_teardowns
            snapshot = _assert_tick_round_trips(cfg, ctx, result, step, 60.0)
            trunk_states.append(
                next(link for link in snapshot.links if (link.node_a, link.node_b) == trunk)
            )
        return trunk_states

    first = run(epoch_unix, 4)
    start = datetime.fromtimestamp(epoch_unix, UTC)
    assert all(link.carrier == CarrierState.UP for link in first)
    assert {link.contact_opened_at for link in first} == {start}

    seek_target = epoch_unix + 3 * 3600
    after_seek = run(seek_target, 2)
    opened = after_seek[0].contact_opened_at
    assert opened == datetime.fromtimestamp(seek_target, UTC)
    assert contact_id(1, opened) != contact_id(0, start)
