"""Tests for LiveOrchestrator — mock NATS and push_scheduler."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from nodalarc.models.events import NodePosition, TimelinePositionSnapshot, VisibilityEvent
from nodalarc.models.link_events import LinkDown, LinkUp

from nodalpath.integration.live_orchestrator import LiveOrchestrator
from nodalpath.models.topology import TopologyNode
from nodalpath.push.push_scheduler import PushResult

# --- Fixtures ---


@pytest.fixture
def node_registry():
    return {
        "sat-P00S00": TopologyNode(
            node_id="sat-P00S00",
            node_type="satellite",
            sid=16001,
            loopback_ipv4="10.0.0.1",
            plane=0,
            slot=0,
        ),
        "sat-P00S01": TopologyNode(
            node_id="sat-P00S01",
            node_type="satellite",
            sid=16002,
            loopback_ipv4="10.0.0.2",
            plane=0,
            slot=1,
        ),
        "gs-alpha": TopologyNode(
            node_id="gs-alpha",
            node_type="ground_station",
            sid=24000,
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
    pub.publish = AsyncMock()
    pub.publish_path_computed = AsyncMock()
    pub.publish_table_pushed = AsyncMock()
    pub.publish_deviation = AsyncMock()
    pub.connect = AsyncMock()
    pub.close = AsyncMock()
    return pub


@pytest.fixture
def orchestrator(node_registry, interface_map, prefix_map, mock_push_scheduler, mock_publisher):
    return LiveOrchestrator(
        node_registry=node_registry,
        interface_map=interface_map,
        prefix_map=prefix_map,
        bandwidth_map=None,
        push_scheduler=mock_push_scheduler,
        publisher=mock_publisher,
    )


def _make_nats_msg(data: bytes):
    """Create a minimal NATS-like message object with .data attribute."""
    return SimpleNamespace(data=data)


def _make_vis_msg(
    node_a: str,
    node_b: str,
    visible: bool,
    scheduled: bool,
    sim_time: datetime,
    range_km: float = 2000.0,
):
    event = VisibilityEvent(
        sim_time=sim_time,
        node_a=node_a,
        node_b=node_b,
        visible=visible,
        scheduled=scheduled,
        range_km=range_km,
        elevation_deg=None,
        terminal_type="optical",
    )
    return _make_nats_msg(event.model_dump_json().encode())


def _make_snapshot_msg(sim_time: datetime):
    snap = TimelinePositionSnapshot(
        sim_time=sim_time,
        positions={
            "sat-P00S00": NodePosition(
                lat_deg=45.0,
                lon_deg=0.0,
                alt_km=550.0,
                vel_x_km_s=0.0,
                vel_y_km_s=7.5,
                vel_z_km_s=0.0,
            ),
            "sat-P00S01": NodePosition(
                lat_deg=45.0,
                lon_deg=30.0,
                alt_km=550.0,
                vel_x_km_s=0.0,
                vel_y_km_s=7.5,
                vel_z_km_s=0.0,
            ),
        },
    )
    return _make_nats_msg(snap.model_dump_json().encode())


def _make_link_down_msg(
    node_a: str,
    node_b: str,
    reason: str,
    sim_time: datetime,
):
    event = LinkDown(
        sim_time=sim_time,
        wall_time=datetime.now(UTC),
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0",
        interface_b="isl0",
        reason=reason,
    )
    return _make_nats_msg(event.model_dump_json().encode())


def _make_link_up_msg(
    node_a: str,
    node_b: str,
    reason: str,
    sim_time: datetime,
):
    event = LinkUp(
        sim_time=sim_time,
        wall_time=datetime.now(UTC),
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0",
        interface_b="isl0",
        latency_ms=3.5,
        bandwidth_mbps=1000.0,
        reason=reason,
    )
    return _make_nats_msg(event.model_dump_json().encode())


def _run(coro):
    """Run an async coroutine synchronously for tests."""
    return asyncio.run(coro)


# --- Tests ---


class TestLiveOrchestratorHandlers:
    def test_visibility_event_updates_builder(self, orchestrator):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        msg = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))
        assert ("sat-P00S00", "sat-P00S01") in orchestrator._builder.active_link_set

    def test_link_up_adds_to_active_set(self, orchestrator):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        msg = _make_vis_msg("gs-alpha", "sat-P00S00", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))
        assert ("gs-alpha", "sat-P00S00") in orchestrator._builder.active_link_set

    def test_link_down_removes_from_active_set(self, orchestrator):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        msg_up = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg_up))
        assert ("sat-P00S00", "sat-P00S01") in orchestrator._builder.active_link_set

        msg_down = _make_vis_msg("sat-P00S00", "sat-P00S01", False, True, t0)
        _run(orchestrator._on_visibility_event(msg_down))
        assert ("sat-P00S00", "sat-P00S01") not in orchestrator._builder.active_link_set

    def test_transition_detected_on_sim_time_boundary(
        self, orchestrator, mock_push_scheduler, mock_publisher
    ):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        msg = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))

        msg2 = _make_vis_msg("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._on_visibility_event(msg2))

        assert orchestrator.transition_count == 1
        mock_publisher.publish_path_computed.assert_called_once()
        mock_push_scheduler.push_entry.assert_called_once()
        mock_publisher.publish_table_pushed.assert_called_once()

    def test_no_transition_on_identical_link_set(self, orchestrator, mock_push_scheduler):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)
        t2 = datetime(2026, 3, 1, 14, 31, 0, tzinfo=UTC)

        msg = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))

        msg2 = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t1)
        _run(orchestrator._on_visibility_event(msg2))
        assert orchestrator.transition_count == 1

        msg3 = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t2)
        _run(orchestrator._on_visibility_event(msg3))
        assert orchestrator.transition_count == 1

    def test_transition_calls_push_scheduler(self, orchestrator, mock_push_scheduler):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        msg = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))

        msg2 = _make_vis_msg("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._on_visibility_event(msg2))

        mock_push_scheduler.push_entry.assert_called_once()
        args = mock_push_scheduler.push_entry.call_args
        entry = args[0][0]
        assert entry.sim_time == t0.isoformat()

    def test_transition_publishes_path_computed(self, orchestrator, mock_publisher):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        msg = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))
        msg2 = _make_vis_msg("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._on_visibility_event(msg2))

        mock_publisher.publish_path_computed.assert_called_once()
        call_kwargs = mock_publisher.publish_path_computed.call_args[1]
        assert call_kwargs["sim_time"] == t0

    def test_transition_publishes_table_pushed(self, orchestrator, mock_publisher):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        msg = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))
        msg2 = _make_vis_msg("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._on_visibility_event(msg2))

        mock_publisher.publish_table_pushed.assert_called_once()
        call_kwargs = mock_publisher.publish_table_pushed.call_args[1]
        assert call_kwargs["nodes_attempted"] == 3
        assert call_kwargs["nodes_succeeded"] == 3

    def test_to_link_down_deviation_triggers_recompute(
        self, orchestrator, mock_push_scheduler, mock_publisher
    ):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        msg = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))
        msg2 = _make_vis_msg("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._on_visibility_event(msg2))

        mock_push_scheduler.reset_mock()
        mock_publisher.reset_mock()

        msg_ld = _make_link_down_msg("sat-P00S00", "sat-P00S01", "scenario_inject_down", t1)
        _run(orchestrator._on_link_down(msg_ld))

        mock_publisher.publish_deviation.assert_called_once()
        mock_publisher.publish.assert_called_once()
        assert mock_push_scheduler.push_entry.call_count == 1

    def test_to_link_down_normal_no_recompute(
        self, orchestrator, mock_push_scheduler, mock_publisher
    ):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        msg = _make_vis_msg("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._on_visibility_event(msg))
        msg2 = _make_vis_msg("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._on_visibility_event(msg2))

        mock_push_scheduler.reset_mock()
        mock_publisher.reset_mock()

        msg_ld = _make_link_down_msg("sat-P00S00", "sat-P00S01", "vis_lost", t1)
        _run(orchestrator._on_link_down(msg_ld))

        mock_publisher.publish_deviation.assert_not_called()
        mock_push_scheduler.push_entry.assert_not_called()

    def test_stop_sets_running_false(self, orchestrator):
        """stop() sets _running to False."""
        orchestrator._running = True
        orchestrator.stop()
        assert not orchestrator._running

    def test_position_snapshot_applied(self, orchestrator):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        msg = _make_snapshot_msg(t0)
        _run(orchestrator._on_position_snapshot(msg))
        assert len(orchestrator._builder._positions) > 0
