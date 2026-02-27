"""Test ZeroMQ encode/decode with real zmq.Context inproc PUB/SUB.

Proves that topic prefix + null byte + JSON payload round-trips
correctly through actual ZeroMQ sockets.
"""

import time
from datetime import datetime, timezone

import zmq

from nodalarc.models.events import PositionEvent, VisibilityEvent
from nodalarc.models.link_events import LinkUp
from nodalarc.zmq_channels import (
    TOPIC_LINK_UP,
    TOPIC_POSITION_EVENT,
    TOPIC_VISIBILITY_EVENT,
    decode_message,
    encode_message,
)

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class TestEncodeDecodeRaw:
    """Test encode/decode without ZeroMQ sockets."""

    def test_encode_decode_round_trip(self):
        payload = b'{"test": 1}'
        msg = encode_message(TOPIC_POSITION_EVENT, payload)
        topic, decoded_payload = decode_message(msg)
        assert topic == TOPIC_POSITION_EVENT
        assert decoded_payload == payload

    def test_null_byte_in_topic_rejected(self):
        """decode_message splits on the FIRST null byte."""
        msg = encode_message(b"Topic\x00Extra", b'{"data": 1}')
        topic, payload = decode_message(msg)
        assert topic == b"Topic"
        # The rest including the "Extra" is part of the payload
        assert payload.startswith(b"Extra")


class TestZmqInprocPubSub:
    """Test with real ZeroMQ inproc PUB/SUB pair."""

    def test_position_event_via_zmq(self, zmq_context):
        pub = zmq_context.socket(zmq.PUB)
        sub = zmq_context.socket(zmq.SUB)
        pub.bind("inproc://test-position")
        sub.connect("inproc://test-position")
        sub.subscribe(TOPIC_POSITION_EVENT)

        # Small delay for subscription to propagate
        time.sleep(0.05)

        evt = PositionEvent(
            sim_time=NOW, node_id="sat-P00S00",
            lat_deg=33.0, lon_deg=-118.0, alt_km=550.0,
            vel_x_km_s=7.0, vel_y_km_s=0.5, vel_z_km_s=0.1,
        )
        msg = encode_message(TOPIC_POSITION_EVENT, evt.model_dump_json().encode())
        pub.send(msg)

        raw = sub.recv()
        topic, payload = decode_message(raw)
        assert topic == TOPIC_POSITION_EVENT
        restored = PositionEvent.model_validate_json(payload)
        assert restored == evt

        pub.close()
        sub.close()

    def test_visibility_event_via_zmq(self, zmq_context):
        pub = zmq_context.socket(zmq.PUB)
        sub = zmq_context.socket(zmq.SUB)
        pub.bind("inproc://test-visibility")
        sub.connect("inproc://test-visibility")
        sub.subscribe(TOPIC_VISIBILITY_EVENT)

        time.sleep(0.05)

        evt = VisibilityEvent(
            sim_time=NOW, node_a="sat-P00S00", node_b="sat-P00S01",
            visible=True, scheduled=True, range_km=1200.0,
            elevation_deg=None, terminal_type="optical",
        )
        msg = encode_message(TOPIC_VISIBILITY_EVENT, evt.model_dump_json().encode())
        pub.send(msg)

        raw = sub.recv()
        topic, payload = decode_message(raw)
        assert topic == TOPIC_VISIBILITY_EVENT
        restored = VisibilityEvent.model_validate_json(payload)
        assert restored == evt

        pub.close()
        sub.close()

    def test_link_up_via_zmq(self, zmq_context):
        pub = zmq_context.socket(zmq.PUB)
        sub = zmq_context.socket(zmq.SUB)
        pub.bind("inproc://test-linkup")
        sub.connect("inproc://test-linkup")
        sub.subscribe(TOPIC_LINK_UP)

        time.sleep(0.05)

        evt = LinkUp(
            sim_time=NOW, wall_time=NOW,
            node_a="sat-P00S00", node_b="sat-P00S01",
            interface_a="isl0", interface_b="isl1",
            latency_ms=5.0, bandwidth_mbps=1000.0,
            reason="vis_gained",
        )
        msg = encode_message(TOPIC_LINK_UP, evt.model_dump_json().encode())
        pub.send(msg)

        raw = sub.recv()
        topic, payload = decode_message(raw)
        assert topic == TOPIC_LINK_UP
        restored = LinkUp.model_validate_json(payload)
        assert restored == evt

        pub.close()
        sub.close()

    def test_topic_filtering(self, zmq_context):
        """Subscriber with topic filter only receives matching messages."""
        pub = zmq_context.socket(zmq.PUB)
        sub = zmq_context.socket(zmq.SUB)
        pub.bind("inproc://test-filter")
        sub.connect("inproc://test-filter")
        # Subscribe ONLY to VisibilityEvent
        sub.subscribe(TOPIC_VISIBILITY_EVENT)

        time.sleep(0.05)

        # Send a PositionEvent (should be filtered out)
        pos_msg = encode_message(TOPIC_POSITION_EVENT, b'{"ignored": true}')
        pub.send(pos_msg)

        # Send a VisibilityEvent (should be received)
        vis_evt = VisibilityEvent(
            sim_time=NOW, node_a="sat-P00S00", node_b="sat-P00S01",
            visible=False, scheduled=False, range_km=5000.0,
            elevation_deg=None, terminal_type="optical",
        )
        vis_msg = encode_message(TOPIC_VISIBILITY_EVENT, vis_evt.model_dump_json().encode())
        pub.send(vis_msg)

        # We should only get the visibility event
        raw = sub.recv()
        topic, _ = decode_message(raw)
        assert topic == TOPIC_VISIBILITY_EVENT

        pub.close()
        sub.close()
