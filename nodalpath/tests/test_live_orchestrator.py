"""Tests for LiveOrchestrator — mock ZMQ sockets and push_scheduler."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from nodalarc.models.events import NodePosition, TimelinePositionSnapshot, VisibilityEvent
from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.zmq_channels import encode_message

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
    pub.publish = MagicMock()
    pub.publish_path_computed = MagicMock()
    pub.publish_table_pushed = MagicMock()
    pub.publish_deviation = MagicMock()
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
        ome_connect="tcp://127.0.0.1:5560",
        to_connect="tcp://127.0.0.1:5561",
    )


def _make_vis_event_raw(
    node_a: str,
    node_b: str,
    visible: bool,
    scheduled: bool,
    sim_time: datetime,
    range_km: float = 2000.0,
) -> bytes:
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
    payload = event.model_dump_json().encode()
    return encode_message(b"VisibilityEvent", payload)


def _make_snapshot_raw(sim_time: datetime) -> bytes:
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
    payload = snap.model_dump_json().encode()
    return encode_message(b"Snapshot", payload)


def _make_link_down_raw(
    node_a: str,
    node_b: str,
    reason: str,
    sim_time: datetime,
) -> bytes:
    event = LinkDown(
        sim_time=sim_time,
        wall_time=datetime.now(UTC),
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0",
        interface_b="isl0",
        reason=reason,
    )
    payload = event.model_dump_json().encode()
    return encode_message(b"LinkDown", payload)


def _make_link_up_raw(
    node_a: str,
    node_b: str,
    reason: str,
    sim_time: datetime,
) -> bytes:
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
    payload = event.model_dump_json().encode()
    return encode_message(b"LinkUp", payload)


def _run(coro):
    """Run an async coroutine synchronously for tests."""
    return asyncio.run(coro)


# --- Tests ---


class TestLiveOrchestratorHandlers:
    def test_visibility_event_updates_builder(self, orchestrator):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))
        assert ("sat-P00S00", "sat-P00S01") in orchestrator._builder.active_link_set

    def test_link_up_adds_to_active_set(self, orchestrator):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        raw = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))
        assert ("gs-alpha", "sat-P00S00") in orchestrator._builder.active_link_set

    def test_link_down_removes_from_active_set(self, orchestrator):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        raw_up = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw_up))
        assert ("sat-P00S00", "sat-P00S01") in orchestrator._builder.active_link_set

        raw_down = _make_vis_event_raw("sat-P00S00", "sat-P00S01", False, True, t0)
        _run(orchestrator._handle_ome_message(raw_down))
        assert ("sat-P00S00", "sat-P00S01") not in orchestrator._builder.active_link_set

    def test_transition_detected_on_sim_time_boundary(
        self, orchestrator, mock_push_scheduler, mock_publisher
    ):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))

        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._handle_ome_message(raw2))

        assert orchestrator.transition_count == 1
        mock_publisher.publish_path_computed.assert_called_once()
        mock_push_scheduler.push_entry.assert_called_once()
        mock_publisher.publish_table_pushed.assert_called_once()

    def test_no_transition_on_identical_link_set(self, orchestrator, mock_push_scheduler):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)
        t2 = datetime(2026, 3, 1, 14, 31, 0, tzinfo=UTC)

        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))

        raw2 = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t1)
        _run(orchestrator._handle_ome_message(raw2))
        assert orchestrator.transition_count == 1

        raw3 = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t2)
        _run(orchestrator._handle_ome_message(raw3))
        assert orchestrator.transition_count == 1

    def test_transition_calls_push_scheduler(self, orchestrator, mock_push_scheduler):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))

        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._handle_ome_message(raw2))

        mock_push_scheduler.push_entry.assert_called_once()
        args = mock_push_scheduler.push_entry.call_args
        entry = args[0][0]
        assert entry.sim_time == t0.isoformat()

    def test_transition_publishes_path_computed(self, orchestrator, mock_publisher):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._handle_ome_message(raw2))

        mock_publisher.publish_path_computed.assert_called_once()
        call_kwargs = mock_publisher.publish_path_computed.call_args[1]
        assert call_kwargs["sim_time"] == t0

    def test_transition_publishes_table_pushed(self, orchestrator, mock_publisher):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._handle_ome_message(raw2))

        mock_publisher.publish_table_pushed.assert_called_once()
        call_kwargs = mock_publisher.publish_table_pushed.call_args[1]
        assert call_kwargs["nodes_attempted"] == 3
        assert call_kwargs["nodes_succeeded"] == 3

    def test_to_link_down_deviation_triggers_recompute(
        self, orchestrator, mock_push_scheduler, mock_publisher
    ):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._handle_ome_message(raw2))

        mock_push_scheduler.reset_mock()
        mock_publisher.reset_mock()

        raw_ld = _make_link_down_raw("sat-P00S00", "sat-P00S01", "scenario_inject_down", t1)
        _run(orchestrator._handle_to_message(raw_ld))

        mock_publisher.publish_deviation.assert_called_once()
        mock_publisher.publish.assert_called_once()
        assert mock_push_scheduler.push_entry.call_count == 1

    def test_to_link_down_normal_no_recompute(
        self, orchestrator, mock_push_scheduler, mock_publisher
    ):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        t1 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)

        raw = _make_vis_event_raw("sat-P00S00", "sat-P00S01", True, True, t0)
        _run(orchestrator._handle_ome_message(raw))
        raw2 = _make_vis_event_raw("gs-alpha", "sat-P00S00", True, True, t1)
        _run(orchestrator._handle_ome_message(raw2))

        mock_push_scheduler.reset_mock()
        mock_publisher.reset_mock()

        raw_ld = _make_link_down_raw("sat-P00S00", "sat-P00S01", "vis_lost", t1)
        _run(orchestrator._handle_to_message(raw_ld))

        mock_publisher.publish_deviation.assert_not_called()
        mock_push_scheduler.push_entry.assert_not_called()

    def test_stop_exits_run_loop(self, orchestrator):
        """stop() causes run() to return promptly."""

        async def _test():
            async def stop_soon():
                await asyncio.sleep(0.1)
                orchestrator.stop()

            async def slow_poll(timeout=100):
                await asyncio.sleep(0.01)
                return []

            with patch("nodalpath.integration.live_orchestrator.zmq.asyncio") as mock_zmq_async:
                mock_ctx = MagicMock()
                mock_zmq_async.Context.return_value = mock_ctx
                mock_sock = MagicMock()
                mock_ctx.socket.return_value = mock_sock
                mock_sock.connect = MagicMock()
                mock_sock.setsockopt = MagicMock()
                mock_sock.close = MagicMock()
                mock_ctx.term = MagicMock()

                mock_poller = MagicMock()
                mock_zmq_async.Poller.return_value = mock_poller
                mock_poller.register = MagicMock()
                mock_poller.poll = slow_poll

                task = asyncio.create_task(orchestrator.run())
                stop_task = asyncio.create_task(stop_soon())
                await asyncio.wait_for(asyncio.gather(task, stop_task), timeout=2.0)

        asyncio.run(_test())

    def test_position_snapshot_applied(self, orchestrator):
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
        raw = _make_snapshot_raw(t0)
        _run(orchestrator._handle_ome_message(raw))
        assert len(orchestrator._builder._positions) > 0
