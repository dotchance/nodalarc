# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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


def test_require_ome_geometry_preserves_authoritative_values():
    assert require_ome_geometry(
        PAIR,
        range_km=1234.5,
        latency_ms=4.117,
        source="VisibilityEvent",
    ) == (1234.5, 4.117)


def test_require_ome_geometry_rejects_missing_or_negative_values():
    with pytest.raises(ValueError, match="range_km"):
        require_ome_geometry(PAIR, range_km=None, latency_ms=1.0, source="event")

    with pytest.raises(ValueError, match="latency_ms"):
        require_ome_geometry(PAIR, range_km=1.0, latency_ms=None, source="event")

    with pytest.raises(ValueError, match="negative range_km"):
        require_ome_geometry(PAIR, range_km=-1.0, latency_ms=1.0, source="event")

    with pytest.raises(ValueError, match="negative latency_ms"):
        require_ome_geometry(PAIR, range_km=1.0, latency_ms=-1.0, source="event")


def test_visibility_event_builds_desired_link_without_recomputing_geometry():
    event = VisibilityEvent(
        sim_time=SIM,
        node_a=PAIR[0],
        node_b=PAIR[1],
        visible=True,
        scheduled=True,
        range_km=2222.25,
        latency_ms=7.412,
        elevation_deg=None,
        terminal_type="optical",
        link_type="isl",
    )

    pair, info = desired_link_from_visibility(
        event,
        interface_map={PAIR: ("isl0", "isl1")},
        bandwidth_map={PAIR: 1000.0},
    )

    assert pair == PAIR
    assert info.interface_a == "isl0"
    assert info.interface_b == "isl1"
    assert info.range_km == event.range_km
    assert info.latency_ms == event.latency_ms
    assert info.bandwidth_mbps == 1000.0
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
        latency_ms=5.0,
        elevation_deg=45.0,
        terminal_type="rf",
        link_type="ground",
        gs_terminal_index=2,
        sat_terminal_index=1,
    )

    pair, info = desired_link_from_visibility(
        event,
        interface_map={},
        bandwidth_map={("gs-a", "sat-a"): 500.0},
    )

    assert pair == ("gs-a", "sat-a")
    assert info.interface_a == "term2"
    assert info.interface_b == "gnd1"
    assert info.link_type == "ground"


def test_missing_isl_interface_map_fails_loudly():
    event = VisibilityEvent(
        sim_time=SIM,
        node_a=PAIR[0],
        node_b=PAIR[1],
        visible=True,
        scheduled=True,
        range_km=2222.25,
        latency_ms=7.412,
        elevation_deg=None,
        terminal_type="optical",
        link_type="isl",
    )

    with pytest.raises(ValueError, match="no configured ISL interfaces"):
        desired_link_from_visibility(event, interface_map={}, bandwidth_map={PAIR: 1000.0})


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
        latency_ms=None,
        bandwidth_mbps=1000.0,
        link_type="isl",
        sim_time=SIM,
    )
    assert (
        desired_link_from_snapshot_link(
            down,
            interface_map={PAIR: ("isl0", "isl1")},
            bandwidth_map={PAIR: 1000.0},
            snapshot_sim_time=SIM,
            snapshot_seq=42,
        )
        is None
    )

    up = down.model_copy(
        update={
            "admin": AdminState.UP,
            "carrier": CarrierState.UP,
            "range_km": 3333.0,
            "latency_ms": 11.118,
        }
    )
    pair, info = desired_link_from_snapshot_link(
        up,
        interface_map={PAIR: ("isl0", "isl1")},
        bandwidth_map={PAIR: 1000.0},
        snapshot_sim_time=SIM,
        snapshot_seq=42,
    )
    assert pair == PAIR
    assert info.range_km == 3333.0
    assert info.latency_ms == 11.118
    assert info.authority_sim_time == SIM
    assert info.authority_source == "link_state_snapshot"
    assert info.authority_sequence == 42


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
        latency_ms=11.118,
        bandwidth_mbps=1000.0,
        link_type="isl",
        sim_time=SIM - timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="sim_time does not match"):
        desired_link_from_snapshot_link(
            link,
            interface_map={PAIR: ("isl0", "isl1")},
            bandwidth_map={PAIR: 1000.0},
            snapshot_sim_time=SIM,
            snapshot_seq=42,
        )


def test_missing_or_nonpositive_bandwidth_fails_loudly():
    event = VisibilityEvent(
        sim_time=SIM,
        node_a=PAIR[0],
        node_b=PAIR[1],
        visible=True,
        scheduled=True,
        range_km=2222.25,
        latency_ms=7.412,
        elevation_deg=None,
        terminal_type="optical",
        link_type="isl",
    )

    with pytest.raises(ValueError, match="unknown physical rate"):
        desired_link_from_visibility(
            event,
            interface_map={PAIR: ("isl0", "isl1")},
            bandwidth_map={},
        )

    with pytest.raises(ValueError, match="unknown physical rate"):
        desired_link_from_visibility(
            event,
            interface_map={PAIR: ("isl0", "isl1")},
            bandwidth_map={PAIR: 0.0},
        )
