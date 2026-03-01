"""Test RealtimeDispatcher — basic event handling with mock ZMQ inproc.

PRD 1B exit criterion: "Both Real-Time and Discrete-Event modes execute
successfully." Tests that RealtimeDispatcher processes VisibilityEvent →
LinkUp/LinkDown using inproc ZMQ sockets (no network).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import pytest
import zmq

from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.zmq_channels import (
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_VISIBILITY_EVENT,
    decode_message,
    encode_message,
)
from orchestrator.realtime_dispatcher import RealtimeDispatcher


@pytest.fixture
def zmq_context():
    ctx = zmq.Context()
    yield ctx
    ctx.term()


@pytest.fixture
def interface_map():
    """Simple interface map for 2-sat ISL."""
    return {
        ("sat-P00S00", "sat-P00S01"): ("isl0", "isl0"),
    }


@pytest.fixture
def bandwidth_map():
    return {
        ("sat-P00S00", "sat-P00S01"): 1000.0,
    }


def _make_vis_event(node_a, node_b, visible, scheduled):
    now = datetime.now(timezone.utc)
    return VisibilityEvent(
        sim_time=now,
        node_a=node_a,
        node_b=node_b,
        visible=visible,
        scheduled=scheduled,
        range_km=500.0,
        elevation_deg=None,
        terminal_type="optical",
    )


class TestHandleVisibility:
    def test_visible_scheduled_produces_link_up(self, interface_map, bandwidth_map):
        """visible=True, scheduled=True → dispatcher records link as active."""
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            override_set=override_set,
            override_lock=lock,
            pid_map={},
        )

        # Mock pub socket using a simple recorder
        sent_messages = []

        class MockSocket:
            def send(self, data):
                sent_messages.append(data)

        vis = _make_vis_event("sat-P00S00", "sat-P00S01", True, True)
        dispatcher._handle_visibility(vis, MockSocket())

        assert ("sat-P00S00", "sat-P00S01") in dispatcher._active_links
        assert len(sent_messages) == 1
        topic, payload = decode_message(sent_messages[0])
        assert topic == TOPIC_LINK_UP

    def test_invisible_produces_link_down(self, interface_map, bandwidth_map):
        """visible=False → dispatcher removes link from active set."""
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            override_set=override_set,
            override_lock=lock,
            pid_map={},
        )

        sent_messages = []

        class MockSocket:
            def send(self, data):
                sent_messages.append(data)

        mock = MockSocket()

        # First bring link up
        vis_up = _make_vis_event("sat-P00S00", "sat-P00S01", True, True)
        dispatcher._handle_visibility(vis_up, mock)
        assert ("sat-P00S00", "sat-P00S01") in dispatcher._active_links

        # Then bring link down
        vis_down = _make_vis_event("sat-P00S00", "sat-P00S01", False, False)
        dispatcher._handle_visibility(vis_down, mock)
        assert ("sat-P00S00", "sat-P00S01") not in dispatcher._active_links

        # Should have LinkUp then LinkDown
        assert len(sent_messages) == 2
        topic_up, _ = decode_message(sent_messages[0])
        topic_down, _ = decode_message(sent_messages[1])
        assert topic_up == TOPIC_LINK_UP
        assert topic_down == TOPIC_LINK_DOWN

    def test_visible_unscheduled_does_not_link_up(self, interface_map, bandwidth_map):
        """visible=True, scheduled=False → dispatcher does NOT create link."""
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            override_set=override_set,
            override_lock=lock,
            pid_map={},
        )

        sent_messages = []

        class MockSocket:
            def send(self, data):
                sent_messages.append(data)

        vis = _make_vis_event("sat-P00S00", "sat-P00S01", True, False)
        dispatcher._handle_visibility(vis, MockSocket())

        assert ("sat-P00S00", "sat-P00S01") not in dispatcher._active_links
        assert len(sent_messages) == 0

    def test_override_blocks_link_up(self, interface_map, bandwidth_map):
        """Override set prevents link from being created."""
        override_set: set[tuple[str, str]] = {("sat-P00S00", "sat-P00S01")}
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            override_set=override_set,
            override_lock=lock,
            pid_map={},
        )

        sent_messages = []

        class MockSocket:
            def send(self, data):
                sent_messages.append(data)

        vis = _make_vis_event("sat-P00S00", "sat-P00S01", True, True)
        dispatcher._handle_visibility(vis, MockSocket())

        assert ("sat-P00S00", "sat-P00S01") not in dispatcher._active_links
        assert len(sent_messages) == 0

    def test_duplicate_link_up_ignored(self, interface_map, bandwidth_map):
        """Second LinkUp for same pair is ignored (already active)."""
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        dispatcher = RealtimeDispatcher(
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            override_set=override_set,
            override_lock=lock,
            pid_map={},
        )

        sent_messages = []

        class MockSocket:
            def send(self, data):
                sent_messages.append(data)

        mock = MockSocket()
        vis = _make_vis_event("sat-P00S00", "sat-P00S01", True, True)
        dispatcher._handle_visibility(vis, mock)
        dispatcher._handle_visibility(vis, mock)

        # Only one LinkUp sent
        assert len(sent_messages) == 1
