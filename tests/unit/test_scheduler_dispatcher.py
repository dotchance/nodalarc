"""Unit tests for scheduler/dispatcher.py — the live production dispatcher.

Uses mocked NATS connection and Node Agent stubs. Feeds VisibilityEvents
through actual _dispatch_batch() and _build_desired_from_snapshot() methods.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_events import LinkUp
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)
from nodalarc.proto import node_agent_pb2
from scheduler.dispatcher import ActiveLinkInfo, Dispatcher
from scheduler.pod_locator import PodLocationMap


def _make_vis(
    node_a: str, node_b: str, visible: bool, scheduled: bool, link_type: str = "isl"
) -> VisibilityEvent:
    return VisibilityEvent(
        sim_time=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        node_a=node_a,
        node_b=node_b,
        visible=visible,
        scheduled=scheduled,
        range_km=500.0,
        latency_ms=1.6678204759907602,
        elevation_deg=45.0,
        terminal_type="optical",
        link_type=link_type,
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
        range_km=900.0 if carrier == CarrierState.UP else None,
        latency_ms=3.0 if carrier == CarrierState.UP else None,
        bandwidth_mbps=1000.0 if carrier == CarrierState.UP else None,
        link_type=link_type,
        sim_time=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_dispatcher(interface_map=None, stub_success=True):
    if interface_map is None:
        interface_map = {
            ("gs-ashburn", "sat-P00S00"): ("term0", "gnd0"),
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
    up_resp = node_agent_pb2.BatchLinkUpResponse(
        success=stub_success,
        error_message="" if stub_success else "mock failure",
        interfaces_upped=1 if stub_success else 0,
        apply_time_ms=0.0,
    )
    down_resp = node_agent_pb2.BatchLinkDownResponse(
        success=stub_success,
        error_message="" if stub_success else "mock failure",
        interfaces_downed=1 if stub_success else 0,
        apply_time_ms=0.0,
    )
    mock_stub.async_batch_link_up = AsyncMock(return_value=up_resp)
    mock_stub.async_batch_link_down = AsyncMock(return_value=down_resp)
    mock_stub.async_set_latency = AsyncMock(
        return_value=node_agent_pb2.SetLatencyResponse(success=True)
    )
    pool.get_stub.return_value = mock_stub

    d = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        session_id="test-session",
        gs_terminal_capacities={"gs-ashburn": 1},
        sat_ground_terminal_capacities={"sat-P00S00": 1},
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    d._position_table = MagicMock()
    d._position_table.compute_link_latency = MagicMock(return_value=3.0)
    d._position_table.compute_link_range = MagicMock(return_value=1500.0)
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

    def test_visibility_event_missing_latency_fails_loudly(self):
        d, _ = _make_dispatcher()
        vis = VisibilityEvent(
            sim_time=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            visible=True,
            scheduled=True,
            range_km=500.0,
            elevation_deg=45.0,
            terminal_type="optical",
            link_type="isl",
        )

        with pytest.raises(ValueError, match="missing OME-authoritative latency_ms"):
            d._apply_events_to_desired([vis])

        assert ("sat-P00S00", "sat-P00S01") not in d._desired_links

    def test_visibility_event_adds_gs_to_active_links(self):
        d, _ = _make_dispatcher()
        vis = _make_vis(
            "gs-ashburn", "sat-P00S00", visible=True, scheduled=True, link_type="ground"
        )

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("gs-ashburn", "sat-P00S00") in d._active_links

    def test_visibility_lost_removes_from_active_links(self):
        d, _ = _make_dispatcher()
        info = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._desired_links[("sat-P00S00", "sat-P00S01")] = info
        d._active_links[("sat-P00S00", "sat-P00S01")] = info

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=False, scheduled=False)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("sat-P00S00", "sat-P00S01") not in d._active_links

    def test_gs_deallocation_removes_from_active_links(self):
        d, _ = _make_dispatcher()
        info = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0)
        d._desired_links[("gs-ashburn", "sat-P00S00")] = info
        d._active_links[("gs-ashburn", "sat-P00S00")] = info

        vis = _make_vis(
            "gs-ashburn", "sat-P00S00", visible=True, scheduled=False, link_type="ground"
        )

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_isl_deallocation_does_not_remove(self):
        d, _ = _make_dispatcher()
        info = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._desired_links[("sat-P00S00", "sat-P00S01")] = info
        d._active_links[("sat-P00S00", "sat-P00S01")] = info

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=False)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestDispatcherLinkStateSnapshot:
    """Test _build_desired_from_snapshot (R-OME-009 replace-not-merge)."""

    def test_snapshot_produces_desired_without_stale_links(self):
        d, _ = _make_dispatcher()
        d._active_links[("sat-P99S99", "sat-P99S98")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(_make_link("sat-P00S00", "sat-P00S01"),),
            interval_s=5.0,
        )
        desired = d._build_desired_from_snapshot(snapshot)

        assert ("sat-P99S99", "sat-P99S98") not in desired
        assert ("sat-P00S00", "sat-P00S01") in desired

    def test_snapshot_missing_range_fails_loudly(self):
        d, _ = _make_dispatcher()
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(
                LinkState(
                    node_a="sat-P00S00",
                    node_b="sat-P00S01",
                    interface_a="isl0",
                    interface_b="isl1",
                    admin=AdminState.UP,
                    carrier=CarrierState.UP,
                    routing=RoutingState.UNKNOWN,
                    latency_ms=3.0,
                    bandwidth_mbps=1000.0,
                    link_type="isl",
                    sim_time=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ),
            interval_s=5.0,
        )

        with pytest.raises(ValueError, match="missing OME-authoritative range_km"):
            d._build_desired_from_snapshot(snapshot)

    def test_snapshot_gs_exclusion(self):
        d, _ = _make_dispatcher()
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0)

        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(),
            interval_s=5.0,
        )
        desired = d._build_desired_from_snapshot(snapshot)

        assert ("gs-ashburn", "sat-P00S00") not in desired

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
        desired = d._build_desired_from_snapshot(snapshot)

        assert desired is None
        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestDispatcherLiveDispatch:
    def test_link_up_publishes_after_node_agent_ack(self):
        d, pool = _make_dispatcher()
        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=True)
        pub = MockNats()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        stub = pool.get_stub.return_value
        assert stub.async_batch_link_up.called
        assert ("sat-P00S00", "sat-P00S01") in d._active_links
        assert d._js.publish.called
        published_subject = d._js.publish.call_args_list[0][0][0]
        assert "up" in published_subject
        payload = d._js.publish.call_args_list[0][0][1]
        event = LinkUp.model_validate(json.loads(payload.decode()))
        assert event.provenance is not None
        assert event.provenance.geometry_authority == "ome"
        assert event.provenance.range_km == vis.range_km
        assert event.provenance.orbital_one_way_ms == vis.latency_ms
        assert event.provenance.substrate_rtt_ms == 0.0
        assert event.provenance.netem_one_way_ms == vis.latency_ms

    def test_link_down_publishes_after_node_agent_ack(self):
        d, pool = _make_dispatcher()
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=False, scheduled=False)
        pub = MockNats()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        stub = pool.get_stub.return_value
        assert stub.async_batch_link_down.called
        assert ("sat-P00S00", "sat-P00S01") not in d._active_links
        assert d._js.publish.called
        published_subject = d._js.publish.call_args_list[0][0][0]
        assert "down" in published_subject

    def test_link_up_not_published_if_node_agent_exception(self):
        d, pool = _make_dispatcher()
        stub = pool.get_stub.return_value
        stub.async_batch_link_up = AsyncMock(side_effect=Exception("agent unreachable"))

        vis = _make_vis("sat-P00S00", "sat-P00S01", visible=True, scheduled=True)
        pub = MockNats()

        asyncio.run(d._dispatch_batch([vis], [], pub))

        assert ("sat-P00S00", "sat-P00S01") not in d._active_links
        link_up_msgs = [m for m in pub.messages if m[0] == "nodalarc.links.up"]
        assert len(link_up_msgs) == 0

    def test_cross_node_missing_substrate_measurement_fails_loudly(self):
        d, _ = _make_dispatcher()
        d._loc._node_of["sat-P00S00"] = "node-a"
        d._loc._node_of["sat-P00S01"] = "node-b"
        d._loc._agent_addrs["node-a"] = "agent-a"
        d._loc._agent_addrs["node-b"] = "agent-b"
        d._loc._node_ips["node-b"] = "10.0.0.2"

        with pytest.raises(ValueError, match="No substrate RTT measurement"):
            d._netem_delay_ms("sat-P00S00", "sat-P00S01", 10.0)

    def test_cross_node_substrate_rtt_is_converted_to_one_way(self):
        d, _ = _make_dispatcher()
        d._loc._node_of["sat-P00S00"] = "node-a"
        d._loc._node_of["sat-P00S01"] = "node-b"
        d._loc._agent_addrs["node-a"] = "agent-a"
        d._loc._agent_addrs["node-b"] = "agent-b"
        d._loc._node_ips["node-b"] = "10.0.0.2"
        d._substrate_by_ip["10.0.0.2"] = 4.0

        assert d._netem_delay_ms("sat-P00S00", "sat-P00S01", 10.0) == 8.0

    def test_negative_substrate_compensation_is_unrepresentable(self):
        d, _ = _make_dispatcher()
        d._loc._node_of["sat-P00S00"] = "node-a"
        d._loc._node_of["sat-P00S01"] = "node-b"
        d._loc._agent_addrs["node-a"] = "agent-a"
        d._loc._agent_addrs["node-b"] = "agent-b"
        d._loc._node_ips["node-b"] = "10.0.0.2"
        d._substrate_by_ip["10.0.0.2"] = 4.0

        with pytest.raises(ValueError, match="Unrepresentable latency"):
            d._netem_delay_ms("sat-P00S00", "sat-P00S01", 1.0)

    def test_missing_pod_placement_is_not_treated_as_local(self):
        d, _ = _make_dispatcher()
        d._loc._node_of.pop("sat-P00S01")

        with pytest.raises(ValueError, match="Missing Kubernetes node placement"):
            d._netem_delay_ms("sat-P00S00", "sat-P00S01", 10.0)

    def test_cross_node_missing_remote_ip_fails_loudly(self):
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._loc._node_of["sat-P00S00"] = "node-a"
        d._loc._node_of["sat-P00S01"] = "node-b"
        d._loc._agent_addrs["node-a"] = "agent-a"
        d._loc._agent_addrs["node-b"] = "agent-b"
        d._substrate_latency["node-a-node-b"] = 1.0
        desired = {
            pair: ActiveLinkInfo(
                "isl0",
                "isl1",
                10.0,
                1000.0,
                range_km=3000.0,
            )
        }

        with pytest.raises(RuntimeError, match="missing IP"):
            asyncio.run(
                d._send_batch_up({pair}, desired, "sim", datetime(2026, 1, 1, tzinfo=UTC), d._nc)
            )
