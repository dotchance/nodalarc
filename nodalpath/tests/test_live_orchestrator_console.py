"""Tests for LiveOrchestrator console_state integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_events import LinkDown
from nodalarc.zmq_channels import encode_message
from nodalpath.console.state import ConsoleState
from nodalpath.integration.live_orchestrator import LiveOrchestrator
from nodalpath.models.topology import TopologyNode
from nodalpath.push.push_scheduler import PushResult


# --- Fixtures ---

@pytest.fixture
def node_registry():
    return {
        "sat-P00S00": TopologyNode(
            node_id="sat-P00S00", node_type="satellite", sid=16001,
            loopback_ipv4="10.0.0.1", plane=0, slot=0,
        ),
        "sat-P00S01": TopologyNode(
            node_id="sat-P00S01", node_type="satellite", sid=16002,
            loopback_ipv4="10.0.0.2", plane=0, slot=1,
        ),
        "gs-alpha": TopologyNode(
            node_id="gs-alpha", node_type="ground_station", sid=24000,
            loopback_ipv4="10.2.0.1",
        ),
    }


@pytest.fixture
def interface_map():
    return {
        ("sat-P00S00", "sat-P00S01"): ("isl0", "isl0"),
        ("gs-alpha", "sat-P00S00"): ("gnd0", "gnd0"),
    }


@pytest.fixture
def prefix_map():
    return {"gs-alpha": "172.16.0.0/24"}


@pytest.fixture
def mock_push_scheduler():
    scheduler = MagicMock()
    scheduler.push_entry.return_value = PushResult(
        topology_state_id="topo-abc",
        sim_time="2026-03-01T14:30:00+00:00",
        nodes_attempted=3,
        nodes_succeeded=3,
        nodes_failed=0,
        nodes_skipped=0,
        push_duration_ms=50.0,
    )
    return scheduler


@pytest.fixture
def mock_publisher():
    pub = MagicMock()
    pub.publish = MagicMock()
    pub.publish_path_computed = MagicMock()
    pub.publish_table_pushed = MagicMock()
    pub.publish_deviation = MagicMock()
    return pub


@pytest.fixture
def console_state():
    return ConsoleState(
        session_path="/tmp/test",
        transport="grpc",
        dry_run=False,
        nodes_in_registry=3,
    )


def _make_orchestrator(node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state=None):
    return LiveOrchestrator(
        node_registry=node_registry,
        interface_map=interface_map,
        prefix_map=prefix_map,
        bandwidth_map=None,
        push_scheduler=mock_push_scheduler,
        publisher=mock_publisher,
        ome_connect="tcp://127.0.0.1:5560",
        to_connect="tcp://127.0.0.1:5561",
        console_state=console_state,
    )


def _make_vis_event_raw(node_a, node_b, visible, scheduled, sim_time, range_km=2000.0):
    event = VisibilityEvent(
        sim_time=sim_time, node_a=node_a, node_b=node_b,
        visible=visible, scheduled=scheduled, range_km=range_km,
        elevation_deg=None, terminal_type="optical",
    )
    return encode_message(b"VisibilityEvent", event.model_dump_json().encode())


def _make_link_down_raw(node_a, node_b, reason, sim_time):
    event = LinkDown(
        sim_time=sim_time,
        wall_time=datetime.now(timezone.utc),
        node_a=node_a, node_b=node_b,
        interface_a="isl0", interface_b="isl0",
        reason=reason,
    )
    return encode_message(b"LinkDown", event.model_dump_json().encode())


def _run(coro):
    return asyncio.run(coro)


# --- Tests ---

class TestConsoleStateNone:
    def test_console_state_none_no_crash(self, node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher):
        """Passing console_state=None runs without error through transition path."""
        orch = _make_orchestrator(node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state=None)
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=timezone.utc)
        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orch._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orch._handle_ome_message(raw2))
        assert orch.transition_count == 1


class TestConsoleStateTransition:
    def test_transition_records_to_console_state(self, node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state):
        orch = _make_orchestrator(node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state)
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=timezone.utc)
        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orch._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orch._handle_ome_message(raw2))

        assert console_state.transition_count == 1
        snap = console_state.snapshot()
        assert len(snap["almanac_history"]) == 1

    def test_push_result_recorded_to_console_state(self, node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state):
        orch = _make_orchestrator(node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state)
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=timezone.utc)
        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orch._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orch._handle_ome_message(raw2))

        snap = console_state.snapshot()
        assert len(snap["push_history"]) == 1
        assert snap["push_history"][0]["nodes_attempted"] == 3


class TestConsoleStateDeviation:
    def test_deviation_records_to_console_state(self, node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state):
        orch = _make_orchestrator(node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state)
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=timezone.utc)

        # Set up a transition so deviation detector has an almanac entry
        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orch._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orch._handle_ome_message(raw2))

        # Now inject a deviation
        raw_ld = _make_link_down_raw("sat-P00S00", "sat-P00S01", "scenario_inject_down", t1)
        _run(orch._handle_to_message(raw_ld))

        assert console_state.deviation_count == 1
        snap = console_state.snapshot()
        assert len(snap["deviation_history"]) == 1
        assert snap["deviation_history"][0]["reason"] == "scenario_inject_down"


class TestConsoleStateRecompute:
    def test_recomputation_records_to_console_state(self, node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state):
        orch = _make_orchestrator(node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state)
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=timezone.utc)

        # Set up transition
        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orch._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orch._handle_ome_message(raw2))

        # Trigger deviation → recompute
        raw_ld = _make_link_down_raw("sat-P00S00", "sat-P00S01", "scenario_inject_down", t1)
        _run(orch._handle_to_message(raw_ld))

        assert console_state.recomputation_count == 1

    def test_manual_recompute_request_processed(self, node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state):
        """request_recompute() flag is consumed and triggers _recompute."""
        orch = _make_orchestrator(node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher, console_state)
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=timezone.utc)

        # Set up a transition so _current_sim_time is set
        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orch._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orch._handle_ome_message(raw2))

        initial_recomp = console_state.recomputation_count

        # Request recompute and simulate consuming in main loop
        console_state.request_recompute()
        assert console_state.consume_recompute_request() is True

        # Simulate what the main loop does
        _run(orch._recompute(orch._current_sim_time.isoformat()))

        assert console_state.recomputation_count == initial_recomp + 1
        # Push result from recompute should also be recorded
        snap = console_state.snapshot()
        # Transition push + recompute push
        assert len(snap["push_history"]) >= 2
