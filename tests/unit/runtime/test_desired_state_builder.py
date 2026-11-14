# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Tests for Scheduler desired-state construction from OME authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_state import AdminState, CarrierState, LinkState, RoutingState
from scheduler.desired_state import (
    desired_link,
    require_ome_geometry,
    snapshot_row_is_up,
)

SIM = datetime(2026, 1, 1, tzinfo=UTC)
PAIR = ("sat-a", "sat-b")


def _geometry(range_km, a_to_b, b_to_a):
    return require_ome_geometry(
        PAIR,
        range_km=range_km,
        latency_a_to_b_ms=a_to_b,
        latency_b_to_a_ms=b_to_a,
        source="event",
    )


def test_require_ome_geometry_rejects_missing_or_negative_values():
    with pytest.raises(ValueError, match="range_km"):
        _geometry(None, 1.0, 1.0)

    with pytest.raises(ValueError, match="latency_a_to_b_ms"):
        _geometry(1.0, None, 1.0)

    with pytest.raises(ValueError, match="latency_b_to_a_ms"):
        _geometry(1.0, 1.0, None)

    with pytest.raises(ValueError, match="negative range_km"):
        _geometry(-1.0, 1.0, 1.0)

    with pytest.raises(ValueError, match="negative latency"):
        _geometry(1.0, -1.0, 1.0)

    with pytest.raises(ValueError, match="negative latency"):
        _geometry(1.0, 1.0, -1.0)


def test_require_ome_geometry_returns_range_and_each_direction():
    assert _geometry(2222.25, 7.412, 7.415) == (2222.25, 7.412, 7.415)


def _from_event(event, *, interface_map=None, terminal_interface_names=None):
    return desired_link(
        event,
        interface_map=interface_map or {},
        terminal_interface_names=terminal_interface_names or {},
        long_delay_pairs=frozenset(),
        epoch_id=0,
        authority_sim_time=event.sim_time,
        authority_source="visibility_event",
    )


def _from_row(link, *, snapshot_seq, interface_map=None, terminal_interface_names=None):
    return desired_link(
        link,
        interface_map=interface_map or {},
        terminal_interface_names=terminal_interface_names or {},
        long_delay_pairs=frozenset(),
        epoch_id=0,
        authority_sim_time=SIM,
        authority_source="link_state_snapshot",
        authority_sequence=snapshot_seq,
    )


def _terminal_event(node_a, node_b, *, terminal_index_a, terminal_index_b):
    return VisibilityEvent(
        sim_time=SIM,
        node_a=node_a,
        node_b=node_b,
        visible=True,
        scheduled=True,
        range_km=1800.0,
        latency_a_to_b_ms=6.0,
        latency_b_to_a_ms=6.0,
        elevation_deg=38.0,
        terminal_type="rf",
        terminal_index_a=terminal_index_a,
        terminal_index_b=terminal_index_b,
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )


def test_visibility_event_builds_desired_link_without_recomputing_geometry():
    event = VisibilityEvent(
        sim_time=SIM,
        node_a=PAIR[0],
        node_b=PAIR[1],
        visible=True,
        scheduled=True,
        range_km=2222.25,
        latency_a_to_b_ms=7.412,
        latency_b_to_a_ms=7.415,
        elevation_deg=None,
        terminal_type="optical",
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )

    pair, info = _from_event(event, interface_map={PAIR: ("isl0", "isl1")})

    assert pair == PAIR
    assert info.interface_a == "isl0"
    assert info.interface_b == "isl1"
    assert info.range_km == event.range_km
    assert info.latency_a_to_b_ms == event.latency_a_to_b_ms
    assert info.latency_b_to_a_ms == event.latency_b_to_a_ms
    assert info.latencies_ms == (7.412, 7.415)
    assert info.authority_sim_time == SIM
    assert info.authority_source == "visibility_event"


def test_a_terminal_index_names_the_interface_the_resolved_session_gives_that_node():
    """The names below follow no pattern, so they can only come from the node's own list."""
    event = _terminal_event("gs-a", "sat-a", terminal_index_a=2, terminal_index_b=1)

    pair, info = _from_event(
        event,
        terminal_interface_names={
            "gs-a": {0: "uplink5", 2: "uplink7"},
            "sat-a": {1: "feeder4", 2: "feeder9"},
        },
    )

    assert pair == ("gs-a", "sat-a")
    assert info.interface_a == "uplink7"
    assert info.interface_b == "feeder4"
    assert info.assigned_terminals


def test_each_end_takes_its_own_nodes_interface_whichever_node_sorts_first():
    """The same two nodes as a site-first pair and as a satellite-first pair."""
    names = {
        "luna-sat-p01s00": {3: "gnd3"},
        "lunar-ground-gs-nearside-relay-site": {0: "term0"},
        "a-ground-site": {0: "term0"},
    }
    sat_first = _terminal_event(
        "luna-sat-p01s00",
        "lunar-ground-gs-nearside-relay-site",
        terminal_index_a=3,
        terminal_index_b=0,
    )
    site_first = _terminal_event(
        "a-ground-site", "luna-sat-p01s00", terminal_index_a=0, terminal_index_b=3
    )

    pair, info = _from_event(sat_first, terminal_interface_names=names)
    assert pair == ("luna-sat-p01s00", "lunar-ground-gs-nearside-relay-site")
    assert (info.interface_a, info.interface_b) == ("gnd3", "term0")

    pair, info = _from_event(site_first, terminal_interface_names=names)
    assert pair == ("a-ground-site", "luna-sat-p01s00")
    assert (info.interface_a, info.interface_b) == ("term0", "gnd3")


def test_a_terminal_the_resolved_session_does_not_give_the_node_fails_loudly():
    names = {"sat-a": {0: "gnd0"}, "sat-b": {3: "gnd3"}}

    with pytest.raises(ValueError, match="names terminal 5 at sat-a"):
        _from_event(
            _terminal_event("sat-a", "sat-b", terminal_index_a=5, terminal_index_b=3),
            terminal_interface_names=names,
        )

    with pytest.raises(ValueError, match="names terminal 3 at sat-b"):
        _from_event(
            _terminal_event("sat-a", "sat-b", terminal_index_a=0, terminal_index_b=3),
            terminal_interface_names={"sat-a": {0: "gnd0"}},
        )

    with pytest.raises(ValueError, match="a terminal at one end only"):
        _from_event(
            _terminal_event("sat-a", "sat-b", terminal_index_a=0, terminal_index_b=None),
            terminal_interface_names=names,
        )


def test_a_link_with_no_terminals_and_no_fixed_interfaces_fails_loudly():
    event = VisibilityEvent(
        sim_time=SIM,
        node_a=PAIR[0],
        node_b=PAIR[1],
        visible=True,
        scheduled=True,
        range_km=2222.25,
        latency_a_to_b_ms=7.412,
        latency_b_to_a_ms=7.412,
        elevation_deg=None,
        terminal_type="optical",
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )

    with pytest.raises(ValueError, match="fixes no interfaces for the pair"):
        _from_event(event)


def test_snapshot_row_builds_desired_link_and_a_down_row_is_not_up():
    down = LinkState(
        node_a=PAIR[0],
        node_b=PAIR[1],
        interface_a="isl0",
        interface_b="isl1",
        admin=AdminState.DOWN,
        carrier=CarrierState.DOWN,
        routing=RoutingState.UNKNOWN,
        range_km=None,
        latency_a_to_b_ms=None,
        latency_b_to_a_ms=None,
        sim_time=SIM,
    )
    assert not snapshot_row_is_up(down, snapshot_sim_time=SIM)

    up = down.model_copy(
        update={
            "admin": AdminState.UP,
            "carrier": CarrierState.UP,
            "range_km": 3333.0,
            "latency_a_to_b_ms": 11.118,
            "latency_b_to_a_ms": 11.121,
        }
    )
    assert snapshot_row_is_up(up, snapshot_sim_time=SIM)
    pair, info = _from_row(up, snapshot_seq=42, interface_map={PAIR: ("isl0", "isl1")})
    assert pair == PAIR
    assert info.range_km == 3333.0
    assert info.latencies_ms == (11.118, 11.121)
    assert info.authority_sim_time == SIM
    assert info.authority_source == "link_state_snapshot"
    assert info.authority_sequence == 42


def test_snapshot_row_with_terminals_takes_each_nodes_interface():
    pair = ("luna-sat-p01s00", "lunar-ground-gs-nearside-relay-site")
    link = LinkState(
        node_a=pair[0],
        node_b=pair[1],
        interface_a="gnd3",
        interface_b="term0",
        admin=AdminState.UP,
        carrier=CarrierState.UP,
        routing=RoutingState.UNKNOWN,
        range_km=1800.0,
        latency_a_to_b_ms=6.0,
        latency_b_to_a_ms=6.0,
        sim_time=SIM,
        terminal_index_a=3,
        terminal_index_b=0,
    )

    built_pair, info = _from_row(
        link,
        snapshot_seq=11,
        terminal_interface_names={pair[0]: {3: "gnd3"}, pair[1]: {0: "term0"}},
    )

    assert built_pair == pair
    assert info.interface_a == "gnd3"
    assert info.interface_b == "term0"
    assert info.authority_source == "link_state_snapshot"


def test_snapshot_row_sim_time_mismatch_fails_loudly():
    link = LinkState(
        node_a=PAIR[0],
        node_b=PAIR[1],
        interface_a="isl0",
        interface_b="isl1",
        admin=AdminState.UP,
        carrier=CarrierState.UP,
        routing=RoutingState.UNKNOWN,
        range_km=3333.0,
        latency_a_to_b_ms=11.118,
        latency_b_to_a_ms=11.118,
        sim_time=SIM - timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="sim_time does not match"):
        snapshot_row_is_up(link, snapshot_sim_time=SIM)
