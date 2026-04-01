"""Tests for three-layer link state models (PRD Section 4.1B).

Round-trip serialization, enum validation, snapshot semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)


def _make_link(**overrides) -> LinkState:
    defaults = {
        "node_a": "sat-P00S00",
        "node_b": "sat-P00S01",
        "interface_a": "isl0",
        "interface_b": "isl1",
        "admin": AdminState.UP,
        "carrier": CarrierState.UP,
        "routing": RoutingState.ADJACENT,
        "latency_ms": 3.5,
        "bandwidth_mbps": 1000.0,
        "link_type": "isl",
        "sim_time": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return LinkState(**defaults)


class TestLinkState:
    def test_round_trip_json(self):
        link = _make_link()
        json_str = link.model_dump_json()
        restored = LinkState.model_validate_json(json_str)
        assert restored == link

    def test_admin_down_with_none_latency(self):
        link = _make_link(
            admin=AdminState.DOWN,
            carrier=CarrierState.DOWN,
            routing=RoutingState.DOWN,
            latency_ms=None,
            bandwidth_mbps=None,
        )
        assert link.admin == AdminState.DOWN
        assert link.latency_ms is None

    def test_ground_link_type(self):
        link = _make_link(
            node_a="gs-ashburn",
            node_b="sat-P00S00",
            interface_a="gnd0",
            interface_b="gnd0",
            link_type="ground",
            carrier=CarrierState.LOWERLAYERDOWN,
            routing=RoutingState.DOWN,
            latency_ms=None,
            bandwidth_mbps=None,
        )
        assert link.link_type == "ground"
        assert link.carrier == CarrierState.LOWERLAYERDOWN

    def test_frozen(self):
        link = _make_link()
        with pytest.raises(Exception):
            link.admin = AdminState.DOWN

    def test_invalid_link_type_rejected(self):
        with pytest.raises(Exception):
            _make_link(link_type="invalid")


class TestLinkStateSnapshot:
    def test_round_trip_json(self):
        links = (
            _make_link(node_a="sat-P00S00", node_b="sat-P00S01"),
            _make_link(
                node_a="gs-ashburn",
                node_b="sat-P00S00",
                interface_a="gnd0",
                interface_b="gnd0",
                link_type="ground",
            ),
        )
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=42,
            links=links,
            interval_s=5.0,
        )
        json_str = snapshot.model_dump_json()
        restored = LinkStateSnapshot.model_validate_json(json_str)
        assert restored == snapshot
        assert len(restored.links) == 2
        assert restored.snapshot_seq == 42

    def test_empty_snapshot(self):
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(),
            interval_s=5.0,
        )
        assert len(snapshot.links) == 0

    def test_snapshot_seq_is_int(self):
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=100,
            links=(),
            interval_s=5.0,
        )
        assert isinstance(snapshot.snapshot_seq, int)

    def test_frozen(self):
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(),
            interval_s=5.0,
        )
        with pytest.raises(Exception):
            snapshot.snapshot_seq = 2


class TestEnums:
    def test_admin_state_values(self):
        assert AdminState.UP == "UP"
        assert AdminState.DOWN == "DOWN"

    def test_carrier_state_values(self):
        assert CarrierState.UP == "UP"
        assert CarrierState.LOWERLAYERDOWN == "LOWERLAYERDOWN"
        assert CarrierState.DOWN == "DOWN"

    def test_routing_state_values(self):
        assert RoutingState.ADJACENT == "ADJACENT"
        assert RoutingState.INITIALIZING == "INITIALIZING"
        assert RoutingState.DOWN == "DOWN"
        assert RoutingState.UNKNOWN == "UNKNOWN"

    def test_enum_string_serialization(self):
        link = _make_link()
        data = link.model_dump(mode="json")
        assert data["admin"] == "UP"
        assert data["carrier"] == "UP"
        assert data["routing"] == "ADJACENT"
