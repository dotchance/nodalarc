"""Unit tests for scheduler/dispatcher.py — the live production dispatcher.

Uses mocked NATS connection and Node Agent stubs. Feeds VisibilityEvents
through actual _dispatch_batch() and _apply_link_state_snapshot() methods.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock

from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)

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


def _make_link(
    node_a: str,
    node_b: str,
    link_type: str = "isl",
    carrier: CarrierState = CarrierState.UP,
) -> LinkState:
    return LinkState(
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0" if link_type == "isl" else "gnd0",
        interface_b="isl1" if link_type == "isl" else "gnd0",
        admin=AdminState.UP,
        carrier=carrier,
        routing=RoutingState.UNKNOWN,
        latency_ms=3.0 if carrier == CarrierState.UP else None,
        bandwidth_mbps=1000.0 if carrier == CarrierState.UP else None,
        link_type=link_type,
        sim_time=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_dispatcher(interface_map=None, stub_success=True):
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
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        override_set=set(),
        override_lock=threading.Lock(),
    )
    return d, pool


class MockNats:
    """Mock NATS connection — records published messages."""

    def __init__(self):
        self.messages = []

    async def publish(self, subject, data):
        self.messages.append((subject, data))


class TestDispatcherActiveLinks:
    def test_visibility_event_adds_isl_to_active_links(self):
        d, _ = _make_dispatcher()
        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=True)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("sat-P00S00", "sat-P00S01") in d._active_links

    def test_visibility_event_adds_gs_to_active_links(self):
        d, _ = _make_dispatcher()
        vis = _make_vis("gs-ashburn", "sat-P00S00", visible=True, scheduled=True)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("gs-ashburn", "sat-P00S00") in d._active_links

    def test_visibility_lost_removes_from_active_links(self):
        d, _ = _make_dispatcher()
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=False, scheduled=False)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("sat-P00S00", "sat-P00S01") not in d._active_links

    def test_gs_deallocation_removes_from_active_links(self):
        d, _ = _make_dispatcher()
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo("gnd0", "gnd0", 3.0, 1000.0)

        vis = _make_vis("gs-ashburn", "sat-P00S00", visible=True, scheduled=False)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_isl_deallocation_does_not_remove(self):
        d, _ = _make_dispatcher()
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=False)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestDispatcherLinkStateSnapshot:
    """Test _apply_link_state_snapshot (R-OME-009 replace-not-merge)."""

    def test_snapshot_clears_active_links_before_applying(self):
        d, _ = _make_dispatcher()
        d._active_links[("sat-P99S99", "sat-P99S98")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(_make_link("sat-P00S00", "sat-P00S01"),),
            interval_s=5.0,
        )
        d._apply_link_state_snapshot(snapshot)

        assert ("sat-P99S99", "sat-P99S98") not in d._active_links
        assert ("sat-P00S00", "sat-P00S01") in d._active_links

    def test_snapshot_gs_removal(self):
        d, _ = _make_dispatcher()
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo("gnd0", "gnd0", 3.0, 1000.0)

        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(),
            interval_s=5.0,
        )
        d._apply_link_state_snapshot(snapshot)

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_snapshot_seq_monotonicity(self):
        d, _ = _make_dispatcher()
        d._last_snapshot_seq = 10
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=5,
            links=(),
            interval_s=5.0,
        )
        d._apply_link_state_snapshot(snapshot)

        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestDispatcherLiveDispatch:
    def test_link_up_publishes_after_node_agent_ack(self):
        d, pool = _make_dispatcher()
        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=True)
        pub = MockNats()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        stub = pool.get_stub.return_value
        assert stub.batch_link_up.called
        assert ("sat-P00S00", "sat-P00S01") in d._active_links
        assert len(pub.messages) > 0
        assert pub.messages[0][0] == "nodalarc.links.up"

    def test_link_down_publishes_after_node_agent_ack(self):
        d, pool = _make_dispatcher()
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=False, scheduled=False)
        pub = MockNats()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        stub = pool.get_stub.return_value
        assert stub.batch_link_down.called
        assert ("sat-P00S00", "sat-P00S01") not in d._active_links
        assert len(pub.messages) > 0
        assert pub.messages[0][0] == "nodalarc.links.down"

    def test_link_up_not_published_if_node_agent_exception(self):
        d, pool = _make_dispatcher()
        stub = pool.get_stub.return_value
        stub.batch_link_up.side_effect = Exception("agent unreachable")

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=True)
        pub = MockNats()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        assert ("sat-P00S00", "sat-P00S01") not in d._active_links
        link_up_msgs = [m for m in pub.messages if m[0] == "nodalarc.links.up"]
        assert len(link_up_msgs) == 0
