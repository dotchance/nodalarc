"""Tests for AlmanacPublisher (NATS) — mock NATS, never connect to real server."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from nodalpath.models.almanac_event import AlmanacEvent


def _utcnow() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def mock_nc():
    """Mock NATS connection."""
    nc = AsyncMock()
    nc.is_closed = False
    nc.publish = AsyncMock()
    nc.drain = AsyncMock()
    nc.close = AsyncMock()
    return nc


@pytest.fixture
def publisher(mock_nc):
    from nodalpath.integration.nats_publisher import AlmanacPublisher

    pub = AlmanacPublisher()
    pub._nc = mock_nc
    return pub


class TestAlmanacPublisher:
    def test_publish_sends_to_nats_subject(self, publisher, mock_nc):
        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc",
        )
        asyncio.run(publisher.publish(event))

        mock_nc.publish.assert_called_once()
        subject = mock_nc.publish.call_args[0][0]
        payload = mock_nc.publish.call_args[0][1]
        assert subject == "nodalarc.nodalpath.almanac"
        data = json.loads(payload)
        assert data["event_type"] == "path_computed"
        assert data["topology_state_id"] == "topo-abc"

    def test_publish_never_raises_on_error(self, publisher, mock_nc):
        mock_nc.publish.side_effect = Exception("NATS error")
        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc",
        )
        # Should not raise
        asyncio.run(publisher.publish(event))

    def test_publish_table_pushed_fields(self, publisher, mock_nc):
        asyncio.run(
            publisher.publish_table_pushed(
                sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
                topology_state_id="topo-abc",
                nodes_attempted=10,
                nodes_succeeded=9,
                nodes_failed=1,
                push_duration_ms=100.0,
            )
        )
        payload = mock_nc.publish.call_args[0][1]
        data = json.loads(payload)
        assert data["event_type"] == "table_pushed"
        assert data["nodes_attempted"] == 10
        assert data["nodes_succeeded"] == 9
        assert data["push_duration_ms"] == 100.0

    def test_publish_path_computed_fields(self, publisher, mock_nc):
        asyncio.run(
            publisher.publish_path_computed(
                sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
                topology_state_id="topo-xyz",
            )
        )
        payload = mock_nc.publish.call_args[0][1]
        data = json.loads(payload)
        assert data["event_type"] == "path_computed"
        assert data["topology_state_id"] == "topo-xyz"

    def test_publish_deviation_fields(self, publisher, mock_nc):
        asyncio.run(
            publisher.publish_deviation(
                sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
                topology_state_id="topo-abc",
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                reason="scenario_inject_down",
            )
        )
        payload = mock_nc.publish.call_args[0][1]
        data = json.loads(payload)
        assert data["event_type"] == "deviation_detected"
        assert data["deviation_node_a"] == "sat-P00S00"
        assert data["deviation_node_b"] == "sat-P00S01"
        assert data["deviation_reason"] == "scenario_inject_down"

    def test_publish_skips_when_not_connected(self):
        from nodalpath.integration.nats_publisher import AlmanacPublisher

        pub = AlmanacPublisher()  # _nc is None
        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc",
        )
        # Should not raise
        asyncio.run(pub.publish(event))

    def test_close_drains_and_closes(self, publisher, mock_nc):
        asyncio.run(publisher.close())
        mock_nc.drain.assert_called_once()
        mock_nc.close.assert_called_once()
