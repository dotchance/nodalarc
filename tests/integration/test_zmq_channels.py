"""Integration test: ZMQ wire format and channel verification.

PRD Appendix B: encode/decode round-trip for all topics, port constants
are unique, topic prefix bytes match expected names, PUB/SUB with topic
filtering works via inproc.
"""

from __future__ import annotations

import json
import time

import pytest
import zmq
from nodalarc.zmq_channels import (
    TOPIC_ADAPTER_EVENT,
    TOPIC_CLOCK_TICK,
    TOPIC_CONVERGENCE_RESULT,
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_POSITION_EVENT,
    TOPIC_PROBE_RESULT,
    TOPIC_VISIBILITY_EVENT,
    decode_message,
    encode_message,
    mi_convergence_gate_port,
    mi_events_port,
    ome_events_port,
    probe_daemon_port,
    to_events_port,
    to_scenario_inject_port,
    vf_static_port,
    vs_api_http_port,
)

pytestmark = pytest.mark.integration

ALL_TOPICS = [
    TOPIC_POSITION_EVENT,
    TOPIC_VISIBILITY_EVENT,
    TOPIC_CLOCK_TICK,
    TOPIC_LINK_UP,
    TOPIC_LINK_DOWN,
    TOPIC_LATENCY_UPDATE,
    TOPIC_CONVERGENCE_RESULT,
    TOPIC_PROBE_RESULT,
    TOPIC_ADAPTER_EVENT,
]


def _all_ports() -> list[int]:
    return [
        ome_events_port(),
        to_events_port(),
        mi_events_port(),
        mi_convergence_gate_port(),
        to_scenario_inject_port(),
        vs_api_http_port(),
        vf_static_port(),
        probe_daemon_port(),
    ]


class TestEncodeDecodeRoundTrip:
    @pytest.mark.parametrize("topic", ALL_TOPICS, ids=lambda t: t.decode())
    def test_round_trip_each_topic(self, topic):
        """encode → decode round-trips for each topic prefix."""
        payload = json.dumps({"key": "value", "topic": topic.decode()}).encode()
        raw = encode_message(topic, payload)
        decoded_topic, decoded_payload = decode_message(raw)
        assert decoded_topic == topic
        assert decoded_payload == payload

    def test_empty_payload(self):
        raw = encode_message(TOPIC_CLOCK_TICK, b"")
        topic, payload = decode_message(raw)
        assert topic == TOPIC_CLOCK_TICK
        assert payload == b""

    def test_binary_payload(self):
        payload = bytes(range(256))
        # The payload should not contain null bytes since topic is terminated by null
        # But the wire format splits on FIRST null only, so payload CAN contain nulls
        raw = encode_message(TOPIC_CLOCK_TICK, payload)
        topic, decoded = decode_message(raw)
        assert topic == TOPIC_CLOCK_TICK
        assert decoded == payload

    def test_null_byte_in_topic_rejected(self):
        """Topic with null byte causes split at wrong position."""
        bad_topic = b"Bad\x00Topic"
        payload = b'{"test": true}'
        raw = encode_message(bad_topic, payload)
        topic, _ = decode_message(raw)
        # Splits on first null byte, so topic is truncated
        assert topic == b"Bad"


class TestPortConstants:
    def test_all_ports_defined(self):
        for port in _all_ports():
            assert isinstance(port, int)
            assert 1024 <= port <= 65535

    def test_all_ports_unique(self):
        ports = _all_ports()
        assert len(ports) == len(set(ports)), (
            f"Duplicate ports found: {[p for p in ports if ports.count(p) > 1]}"
        )

    def test_port_values_stable(self):
        """Port assignments match PRD-specified values."""
        assert ome_events_port() == 5560
        assert to_events_port() == 5561
        assert mi_events_port() == 5562
        assert mi_convergence_gate_port() == 5563
        assert to_scenario_inject_port() == 5564
        assert vs_api_http_port() == 8080
        assert vf_static_port() == 8081
        assert probe_daemon_port() == 9100


class TestTopicPrefixes:
    def test_all_topics_are_bytes(self):
        for topic in ALL_TOPICS:
            assert isinstance(topic, bytes)

    def test_topic_names_match_expected(self):
        expected = {
            b"PositionEvent": TOPIC_POSITION_EVENT,
            b"VisibilityEvent": TOPIC_VISIBILITY_EVENT,
            b"ClockTick": TOPIC_CLOCK_TICK,
            b"LinkUp": TOPIC_LINK_UP,
            b"LinkDown": TOPIC_LINK_DOWN,
            b"LatencyUpdate": TOPIC_LATENCY_UPDATE,
            b"ConvergenceResult": TOPIC_CONVERGENCE_RESULT,
            b"ProbeResult": TOPIC_PROBE_RESULT,
            b"AdapterEvent": TOPIC_ADAPTER_EVENT,
        }
        for name, topic in expected.items():
            assert topic == name, f"Topic {topic!r} should be {name!r}"

    def test_no_null_bytes_in_topics(self):
        for topic in ALL_TOPICS:
            assert b"\x00" not in topic, f"Topic {topic!r} should not contain null bytes"


class TestPubSubFiltering:
    def test_inproc_pub_sub_all_topics(self, zmq_context):
        """PUB/SUB with empty subscription receives all messages."""
        pub = zmq_context.socket(zmq.PUB)
        sub = zmq_context.socket(zmq.SUB)
        addr = f"inproc://test-all-{id(self)}"
        pub.bind(addr)
        sub.connect(addr)
        sub.setsockopt(zmq.SUBSCRIBE, b"")

        time.sleep(0.05)

        pub.send(encode_message(TOPIC_CLOCK_TICK, b'{"t":1}'))
        pub.send(encode_message(TOPIC_LINK_UP, b'{"t":2}'))

        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)

        received = []
        for _ in range(10):
            socks = dict(poller.poll(500))
            if sub in socks:
                raw = sub.recv()
                topic, payload = decode_message(raw)
                received.append(topic)
            if len(received) >= 2:
                break

        assert TOPIC_CLOCK_TICK in received
        assert TOPIC_LINK_UP in received

        sub.close()
        pub.close()

    def test_inproc_topic_filtering(self, zmq_context):
        """Subscribing to a specific topic filters out others."""
        pub = zmq_context.socket(zmq.PUB)
        sub = zmq_context.socket(zmq.SUB)
        addr = f"inproc://test-filter-{id(self)}"
        pub.bind(addr)
        sub.connect(addr)
        sub.setsockopt(zmq.SUBSCRIBE, TOPIC_LINK_UP)

        time.sleep(0.05)

        pub.send(encode_message(TOPIC_CLOCK_TICK, b'{"skip": true}'))
        pub.send(encode_message(TOPIC_LINK_UP, b'{"want": true}'))
        pub.send(encode_message(TOPIC_LINK_DOWN, b'{"skip": true}'))

        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)

        received = []
        for _ in range(10):
            socks = dict(poller.poll(500))
            if sub in socks:
                raw = sub.recv()
                topic, payload = decode_message(raw)
                received.append(topic)
            else:
                break

        assert received == [TOPIC_LINK_UP]

        sub.close()
        pub.close()
