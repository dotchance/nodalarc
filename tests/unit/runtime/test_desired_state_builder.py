# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for Scheduler desired-state construction from OME authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_state import AdminState, CarrierState, LinkState, RoutingState
from scheduler.desired_state import (
    desired_link_from_snapshot_link,
    desired_link_from_visibility,
    require_ome_geometry,
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
        link_type="isl",
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )

    pair, info = desired_link_from_visibility(
        event,
        interface_map={PAIR: ("isl0", "isl1")},
        ground_station_ids=frozenset(),
        long_delay_pairs=frozenset(),
        epoch_id=0,
    )

    assert pair == PAIR
    assert info.interface_a == "isl0"
    assert info.interface_b == "isl1"
    assert info.range_km == event.range_km
    assert info.latency_a_to_b_ms == event.latency_a_to_b_ms
    assert info.latency_b_to_a_ms == event.latency_b_to_a_ms
    assert info.latencies_ms == (7.412, 7.415)
    assert info.authority_sim_time == SIM
    assert info.authority_source == "visibility_event"


def test_ground_visibility_event_uses_terminal_indices():
    event = VisibilityEvent(
        sim_time=SIM,
        node_a="gs-a",
        node_b="sat-a",
        visible=True,
        scheduled=True,
        range_km=1500.0,
        latency_a_to_b_ms=5.0,
        latency_b_to_a_ms=5.0,
        elevation_deg=45.0,
        terminal_type="rf",
        link_type="ground",
        gs_terminal_index=2,
        sat_terminal_index=1,
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )

    pair, info = desired_link_from_visibility(
        event,
        interface_map={},
        ground_station_ids=frozenset({"gs-a"}),
        long_delay_pairs=frozenset(),
        epoch_id=0,
    )

    assert pair == ("gs-a", "sat-a")
    assert info.interface_a == "term2"
    assert info.interface_b == "gnd1"
    assert info.link_type == "ground"


def test_ground_visibility_event_maps_interfaces_by_endpoint_role_for_sat_first_pair():
    event = VisibilityEvent(
        sim_time=SIM,
        node_a="luna-sat-p01s00",
        node_b="lunar-ground-gs-nearside-relay-site",
        visible=True,
        scheduled=True,
        range_km=1800.0,
        latency_a_to_b_ms=6.0,
        latency_b_to_a_ms=6.0,
        elevation_deg=38.0,
        terminal_type="rf",
        link_type="ground",
        gs_terminal_index=0,
        sat_terminal_index=3,
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )

    pair, info = desired_link_from_visibility(
        event,
        interface_map={},
        ground_station_ids=frozenset({"lunar-ground-gs-nearside-relay-site"}),
        long_delay_pairs=frozenset(),
        epoch_id=0,
    )

    assert pair == ("luna-sat-p01s00", "lunar-ground-gs-nearside-relay-site")
    assert info.interface_a == "gnd3"
    assert info.interface_b == "term0"
    assert info.link_type == "ground"


def test_ground_visibility_event_requires_exactly_one_ground_endpoint():
    event = VisibilityEvent(
        sim_time=SIM,
        node_a="sat-a",
        node_b="sat-b",
        visible=True,
        scheduled=True,
        range_km=1800.0,
        latency_a_to_b_ms=6.0,
        latency_b_to_a_ms=6.0,
        elevation_deg=38.0,
        terminal_type="rf",
        link_type="ground",
        gs_terminal_index=0,
        sat_terminal_index=3,
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )

    with pytest.raises(ValueError, match="no ground endpoint"):
        desired_link_from_visibility(
            event,
            interface_map={},
            ground_station_ids=frozenset({"gs-a"}),
            long_delay_pairs=frozenset(),
            epoch_id=0,
        )

    with pytest.raises(ValueError, match="two ground endpoints"):
        desired_link_from_visibility(
            event,
            interface_map={},
            ground_station_ids=frozenset({"sat-a", "sat-b"}),
            long_delay_pairs=frozenset(),
            epoch_id=0,
        )


def test_missing_isl_interface_map_fails_loudly():
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
        link_type="isl",
        visibility_reject_reason="ok",
        unscheduled_reason=None,
    )

    with pytest.raises(ValueError, match="no configured ISL interfaces"):
        desired_link_from_visibility(
            event,
            interface_map={},
            ground_station_ids=frozenset(),
            long_delay_pairs=frozenset(),
            epoch_id=0,
        )


def test_snapshot_link_builds_desired_link_and_skips_down_links():
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
        link_type="isl",
        sim_time=SIM,
    )
    assert (
        desired_link_from_snapshot_link(
            down,
            interface_map={PAIR: ("isl0", "isl1")},
            ground_station_ids=frozenset(),
            snapshot_sim_time=SIM,
            snapshot_seq=42,
            long_delay_pairs=frozenset(),
            epoch_id=0,
        )
        is None
    )

    up = down.model_copy(
        update={
            "admin": AdminState.UP,
            "carrier": CarrierState.UP,
            "range_km": 3333.0,
            "latency_a_to_b_ms": 11.118,
            "latency_b_to_a_ms": 11.121,
        }
    )
    pair, info = desired_link_from_snapshot_link(
        up,
        interface_map={PAIR: ("isl0", "isl1")},
        ground_station_ids=frozenset(),
        snapshot_sim_time=SIM,
        snapshot_seq=42,
        long_delay_pairs=frozenset(),
        epoch_id=0,
    )
    assert pair == PAIR
    assert info.range_km == 3333.0
    assert info.latencies_ms == (11.118, 11.121)
    assert info.authority_sim_time == SIM
    assert info.authority_source == "link_state_snapshot"
    assert info.authority_sequence == 42


def test_snapshot_ground_link_maps_interfaces_by_endpoint_role_for_sat_first_pair():
    pair = ("luna-sat-p01s00", "lunar-ground-gs-nearside-relay-site")
    link = LinkState(
        node_a=pair[0],
        node_b=pair[1],
        interface_a="",
        interface_b="",
        admin=AdminState.UP,
        carrier=CarrierState.UP,
        routing=RoutingState.UNKNOWN,
        range_km=1800.0,
        latency_a_to_b_ms=6.0,
        latency_b_to_a_ms=6.0,
        link_type="ground",
        sim_time=SIM,
        gs_terminal_index=0,
        sat_terminal_index=3,
    )

    built_pair, info = desired_link_from_snapshot_link(
        link,
        interface_map={},
        ground_station_ids=frozenset({"lunar-ground-gs-nearside-relay-site"}),
        snapshot_sim_time=SIM,
        snapshot_seq=11,
        long_delay_pairs=frozenset(),
        epoch_id=0,
    )

    assert built_pair == pair
    assert info.interface_a == "gnd3"
    assert info.interface_b == "term0"
    assert info.authority_source == "link_state_snapshot"


def test_snapshot_link_sim_time_mismatch_fails_loudly():
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
        link_type="isl",
        sim_time=SIM - timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="sim_time does not match"):
        desired_link_from_snapshot_link(
            link,
            interface_map={PAIR: ("isl0", "isl1")},
            ground_station_ids=frozenset(),
            snapshot_sim_time=SIM,
            snapshot_seq=42,
            long_delay_pairs=frozenset(),
            epoch_id=0,
        )
