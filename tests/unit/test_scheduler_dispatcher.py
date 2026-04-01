"""Unit tests for scheduler/dispatcher.py — the live production dispatcher.

Uses the same mock pattern as test_ome_scheduler_contract.py: minimal
Dispatcher with mocked Node Agent stubs, feeding VisibilityEvents through
actual _dispatch_batch() and _ome_catchup() methods.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from nodalarc.models.events import VisibilityEvent

from node_agent.proto import node_agent_pb2
from scheduler.dispatcher import ActiveLinkInfo, Dispatcher
from scheduler.pod_locator import PodLocationMap


def _make_vis(node_a: str, node_b: str, visible: bool, scheduled: bool) -> VisibilityEvent:
    return VisibilityEvent(
        sim_time=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        node_a=node_a,
        node_b=node_b,
        visible=visible,
        scheduled=scheduled,
        range_km=500.0,
        elevation_deg=45.0,
        terminal_type="optical",
    )


def _make_dispatcher(interface_map=None, stub_success=True):
    """Construct a minimal Dispatcher with mocked Node Agent."""
    if interface_map is None:
        interface_map = {
            ("gs-ashburn", "sat-P00S00"): ("gnd0", "gnd0"),
            ("sat-P00S00", "sat-P00S01"): ("isl0", "isl1"),
        }
    bandwidth_map = {k: 1000.0 for k in interface_map}

    loc = PodLocationMap()
    for pair in interface_map:
        for nid in pair:
            loc._node_of[nid] = "nodal"
    loc._agent_addrs["nodal"] = "127.0.0.1:50100"

    pool = MagicMock()
    mock_stub = MagicMock()
    mock_stub.batch_link_up.return_value = node_agent_pb2.BatchLinkUpResponse(
        success=stub_success,
        error_message="" if stub_success else "mock failure",
        interfaces_upped=1 if stub_success else 0,
        apply_time_ms=0.0,
    )
    mock_stub.batch_link_down.return_value = node_agent_pb2.BatchLinkDownResponse(
        success=stub_success,
        error_message="" if stub_success else "mock failure",
        interfaces_downed=1 if stub_success else 0,
        apply_time_ms=0.0,
    )
    pool.get_stub.return_value = mock_stub

    d = Dispatcher(
        ome_endpoint="tcp://127.0.0.1:5560",
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        override_set=set(),
        override_lock=threading.Lock(),
    )
    return d, pool


class MockPub:
    """Mock ZMQ PUB socket — records sent messages."""

    def __init__(self):
        self.messages = []

    def send(self, data):
        self.messages.append(data)


# ---------------------------------------------------------------------------
# TestDispatcherActiveLinks
# ---------------------------------------------------------------------------


class TestDispatcherActiveLinks:
    def test_visibility_event_adds_isl_to_active_links(self):
        d, _ = _make_dispatcher()
        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=True)

        asyncio.run(d._dispatch_batch([vis], [], MockPub()))

        assert ("sat-P00S00", "sat-P00S01") in d._active_links

    def test_visibility_event_adds_gs_to_active_links(self):
        d, _ = _make_dispatcher()
        vis = _make_vis("gs-ashburn", "sat-P00S00", visible=True, scheduled=True)

        asyncio.run(d._dispatch_batch([vis], [], MockPub()))

        assert ("gs-ashburn", "sat-P00S00") in d._active_links

    def test_visibility_lost_removes_from_active_links(self):
        d, _ = _make_dispatcher()
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=False, scheduled=False)

        asyncio.run(d._dispatch_batch([vis], [], MockPub()))

        assert ("sat-P00S00", "sat-P00S01") not in d._active_links

    def test_gs_deallocation_removes_from_active_links(self):
        d, _ = _make_dispatcher()
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo("gnd0", "gnd0", 3.0, 1000.0)

        vis = _make_vis("gs-ashburn", "sat-P00S00", visible=True, scheduled=False)

        asyncio.run(d._dispatch_batch([vis], [], MockPub()))

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_isl_deallocation_does_not_remove(self):
        d, _ = _make_dispatcher()
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=False)

        asyncio.run(d._dispatch_batch([vis], [], MockPub()))

        # ISL visible+unscheduled does NOT remove — only GS
        assert ("sat-P00S00", "sat-P00S01") in d._active_links


# ---------------------------------------------------------------------------
# TestDispatcherCatchupReplay
# ---------------------------------------------------------------------------


class TestDispatcherCatchupReplay:
    def _run_catchup(self, d, events_dicts):
        catchup_response = {
            "events": events_dicts,
            "current_sim_time": "2026-01-01T00:00:30Z",
        }

        async def _run():
            with patch("nodalarc.platform.get_platform_config") as mock_cfg:
                mock_cfg.return_value.ome_catchup_connect = "tcp://127.0.0.1:5568"
                with patch("zmq.Context") as mock_zmq_ctx:
                    mock_sock = MagicMock()
                    mock_sock.recv_json.return_value = catchup_response
                    mock_zmq_ctx.return_value.socket.return_value = mock_sock
                    await d._ome_catchup(MockPub())

        asyncio.run(_run())

    def test_catchup_clears_active_links_before_applying(self):
        d, _ = _make_dispatcher()
        # Pre-seed a stale link
        d._active_links[("sat-P99S99", "sat-P99S98")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        self._run_catchup(
            d,
            [
                _make_vis("sat-P00S00", "sat-P00S01", True, True).model_dump(mode="json"),
            ],
        )

        # Stale link must be gone (catch-up clears before applying)
        assert ("sat-P99S99", "sat-P99S98") not in d._active_links
        # New link from catch-up must be present
        assert ("sat-P00S00", "sat-P00S01") in d._active_links

    def test_catchup_gs_deallocation_removes_pair(self):
        d, _ = _make_dispatcher()

        self._run_catchup(
            d,
            [
                _make_vis("gs-ashburn", "sat-P00S00", True, True).model_dump(mode="json"),
                _make_vis("gs-ashburn", "sat-P00S00", True, False).model_dump(mode="json"),
            ],
        )

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_catchup_handles_window_boundary_synthetic_events(self):
        """Synthetic boundary events (visible=True, scheduled=False with
        range_km=0.0) must be handled the same as real events."""
        d, _ = _make_dispatcher()

        # Simulate: real allocation, then synthetic boundary deallocation
        self._run_catchup(
            d,
            [
                _make_vis("gs-ashburn", "sat-P00S00", True, True).model_dump(mode="json"),
                # Synthetic boundary event — range_km=0.0, elevation_deg=0.0
                VisibilityEvent(
                    sim_time=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
                    node_a="gs-ashburn",
                    node_b="sat-P00S00",
                    visible=True,
                    scheduled=False,
                    range_km=0.0,
                    elevation_deg=0.0,
                    terminal_type="optical",
                ).model_dump(mode="json"),
            ],
        )

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links


# ---------------------------------------------------------------------------
# TestDispatcherLiveDispatch
# ---------------------------------------------------------------------------


class TestDispatcherLiveDispatch:
    def test_link_up_publishes_after_node_agent_ack(self):
        d, pool = _make_dispatcher()
        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=True)
        pub = MockPub()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        # Node Agent stub was called
        stub = pool.get_stub.return_value
        assert stub.batch_link_up.called
        # Link is in active_links
        assert ("sat-P00S00", "sat-P00S01") in d._active_links
        # LinkUp was published on PUB socket
        assert len(pub.messages) > 0
        assert b"LinkUp" in pub.messages[0]

    def test_link_down_publishes_after_node_agent_ack(self):
        d, pool = _make_dispatcher()
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=False, scheduled=False)
        pub = MockPub()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        # Node Agent stub was called
        stub = pool.get_stub.return_value
        assert stub.batch_link_down.called
        # Link is NOT in active_links
        assert ("sat-P00S00", "sat-P00S01") not in d._active_links
        # LinkDown was published on PUB socket
        assert len(pub.messages) > 0
        assert b"LinkDown" in pub.messages[0]

    def test_link_up_not_published_if_node_agent_exception(self):
        """If the Node Agent raises an exception (unreachable), the link
        must NOT be added to _active_links and no LinkUp is published."""
        d, pool = _make_dispatcher()
        # Make the stub raise an exception (agent unreachable)
        stub = pool.get_stub.return_value
        stub.batch_link_up.side_effect = Exception("agent unreachable")

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=True)
        pub = MockPub()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        # Link must NOT be in active_links — agent was unreachable
        assert ("sat-P00S00", "sat-P00S01") not in d._active_links
        # No LinkUp published
        link_up_msgs = [m for m in pub.messages if b"LinkUp" in m]
        assert len(link_up_msgs) == 0
