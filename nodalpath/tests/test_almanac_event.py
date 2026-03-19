"""Tests for AlmanacEvent model."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from nodalpath.models.almanac_event import AlmanacEvent


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TestAlmanacEventSerialization:
    def test_path_computed_round_trip(self):
        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc123",
        )
        data = json.loads(event.model_dump_json())
        restored = AlmanacEvent.model_validate(data)
        assert restored.event_type == "path_computed"
        assert restored.topology_state_id == "topo-abc123"
        assert restored.sim_time == event.sim_time

    def test_table_pushed_fields(self):
        event = AlmanacEvent(
            event_type="table_pushed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc123",
            nodes_attempted=10,
            nodes_succeeded=9,
            nodes_failed=1,
            push_duration_ms=150.5,
        )
        assert event.nodes_attempted == 10
        assert event.nodes_succeeded == 9
        assert event.nodes_failed == 1
        assert event.push_duration_ms == 150.5

    def test_deviation_fields(self):
        event = AlmanacEvent(
            event_type="deviation_detected",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc123",
            deviation_node_a="sat-P00S00",
            deviation_node_b="sat-P00S01",
            deviation_reason="scenario_inject_down",
        )
        assert event.deviation_node_a == "sat-P00S00"
        assert event.deviation_node_b == "sat-P00S01"
        assert event.deviation_reason == "scenario_inject_down"

    def test_optional_fields_default_none(self):
        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc123",
        )
        assert event.node_id is None
        assert event.nodes_attempted is None
        assert event.nodes_succeeded is None
        assert event.nodes_failed is None
        assert event.push_duration_ms is None
        assert event.deviation_node_a is None
        assert event.deviation_node_b is None
        assert event.deviation_reason is None

    def test_frozen_immutability(self):
        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc123",
        )
        with pytest.raises(Exception):
            event.event_type = "table_pushed"

    def test_sim_time_is_datetime(self):
        data = {
            "event_type": "path_computed",
            "sim_time": "2026-03-01T14:30:00+00:00",
            "wall_time": "2026-03-01T14:30:01+00:00",
            "topology_state_id": "topo-abc123",
        }
        event = AlmanacEvent.model_validate(data)
        assert isinstance(event.sim_time, datetime)

    def test_wall_time_tz_aware(self):
        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=datetime(2026, 3, 1, 14, 30, 1, tzinfo=UTC),
            topology_state_id="topo-abc123",
        )
        assert event.wall_time.tzinfo is not None
