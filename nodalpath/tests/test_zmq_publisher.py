"""Tests for AlmanacPublisher — mock ZMQ, never connect to real sockets."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from nodalarc.zmq_channels import TOPIC_ALMANAC_EVENT, decode_message

from nodalpath.models.almanac_event import AlmanacEvent


def _utcnow() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def mock_zmq():
    """Mock zmq.Context and socket to avoid real ZMQ."""
    with patch("nodalpath.integration.zmq_publisher.zmq") as mock:
        mock_ctx = MagicMock()
        mock_sock = MagicMock()
        mock.Context.return_value = mock_ctx
        mock_ctx.socket.return_value = mock_sock
        mock.PUB = 1
        mock.NOBLOCK = 1
        yield mock, mock_ctx, mock_sock


class TestAlmanacPublisher:
    def test_publish_sends_encoded_message(self, mock_zmq):
        _, _, mock_sock = mock_zmq
        from nodalpath.integration.zmq_publisher import AlmanacPublisher

        pub = AlmanacPublisher("tcp://127.0.0.1:5567")
        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc",
        )
        pub.publish(event)

        mock_sock.send.assert_called_once()
        raw = mock_sock.send.call_args[0][0]
        topic, payload = decode_message(raw)
        assert topic == TOPIC_ALMANAC_EVENT
        data = json.loads(payload)
        assert data["event_type"] == "path_computed"
        assert data["topology_state_id"] == "topo-abc"

    def test_publish_never_raises_on_zmq_error(self, mock_zmq):
        _, _, mock_sock = mock_zmq
        from nodalpath.integration.zmq_publisher import AlmanacPublisher

        pub = AlmanacPublisher("tcp://127.0.0.1:5567")
        mock_sock.send.side_effect = Exception("ZMQ socket error")

        event = AlmanacEvent(
            event_type="path_computed",
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            wall_time=_utcnow(),
            topology_state_id="topo-abc",
        )
        # Should not raise
        pub.publish(event)

    def test_publish_table_pushed_fields(self, mock_zmq):
        _, _, mock_sock = mock_zmq
        from nodalpath.integration.zmq_publisher import AlmanacPublisher

        pub = AlmanacPublisher("tcp://127.0.0.1:5567")
        pub.publish_table_pushed(
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            topology_state_id="topo-abc",
            nodes_attempted=10,
            nodes_succeeded=9,
            nodes_failed=1,
            push_duration_ms=100.0,
        )
        raw = mock_sock.send.call_args[0][0]
        _, payload = decode_message(raw)
        data = json.loads(payload)
        assert data["event_type"] == "table_pushed"
        assert data["nodes_attempted"] == 10
        assert data["nodes_succeeded"] == 9
        assert data["push_duration_ms"] == 100.0

    def test_publish_path_computed_fields(self, mock_zmq):
        _, _, mock_sock = mock_zmq
        from nodalpath.integration.zmq_publisher import AlmanacPublisher

        pub = AlmanacPublisher("tcp://127.0.0.1:5567")
        pub.publish_path_computed(
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            topology_state_id="topo-xyz",
        )
        raw = mock_sock.send.call_args[0][0]
        _, payload = decode_message(raw)
        data = json.loads(payload)
        assert data["event_type"] == "path_computed"
        assert data["topology_state_id"] == "topo-xyz"

    def test_publish_deviation_fields(self, mock_zmq):
        _, _, mock_sock = mock_zmq
        from nodalpath.integration.zmq_publisher import AlmanacPublisher

        pub = AlmanacPublisher("tcp://127.0.0.1:5567")
        pub.publish_deviation(
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            topology_state_id="topo-abc",
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            reason="scenario_inject_down",
        )
        raw = mock_sock.send.call_args[0][0]
        _, payload = decode_message(raw)
        data = json.loads(payload)
        assert data["event_type"] == "deviation_detected"
        assert data["deviation_node_a"] == "sat-P00S00"
        assert data["deviation_node_b"] == "sat-P00S01"
        assert data["deviation_reason"] == "scenario_inject_down"

    def test_topic_prefix_correct(self, mock_zmq):
        _, _, mock_sock = mock_zmq
        from nodalpath.integration.zmq_publisher import AlmanacPublisher

        pub = AlmanacPublisher("tcp://127.0.0.1:5567")
        pub.publish_path_computed(
            sim_time=datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC),
            topology_state_id="topo-abc",
        )
        raw = mock_sock.send.call_args[0][0]
        assert raw.startswith(TOPIC_ALMANAC_EVENT + b"\x00")

    def test_close_terminates_context(self, mock_zmq):
        _, mock_ctx, mock_sock = mock_zmq
        from nodalpath.integration.zmq_publisher import AlmanacPublisher

        pub = AlmanacPublisher("tcp://127.0.0.1:5567")
        pub.close()
        mock_sock.close.assert_called_once()
        mock_ctx.term.assert_called_once()
