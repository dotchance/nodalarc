"""B.3A Contract test: GS deallocation consistency across Scheduler code paths.

Tests the ACTUAL Dispatcher code — no logic extraction, no duplication.
Constructs a minimal Dispatcher with mocked Node Agent, feeds events through
real _build_desired_from_snapshot() and _dispatch_batch() paths, asserts
desired/active state.

Covers PRD B.3A requirement: GS links must not accumulate. Two paths:
  1. _build_desired_from_snapshot (replace-not-merge from R-OME-009)
  2. _dispatch_batch (live VisibilityEvent → _reconcile_links)
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from nodalarc.models.events import VisibilityEvent
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


def _make_link_state(
    node_a: str,
    node_b: str,
    admin: AdminState = AdminState.UP,
    carrier: CarrierState = CarrierState.UP,
    link_type: str = "isl",
) -> LinkState:
    return LinkState(
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0" if link_type == "isl" else "gnd0",
        interface_b="isl1" if link_type == "isl" else "gnd0",
        admin=admin,
        carrier=carrier,
        routing=RoutingState.UNKNOWN,
        latency_ms=3.0 if carrier == CarrierState.UP else None,
        bandwidth_mbps=1000.0 if carrier == CarrierState.UP else None,
        link_type=link_type,
        sim_time=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_dispatcher(interface_map=None):
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
    mock_stub.async_batch_link_up = AsyncMock(
        return_value=node_agent_pb2.BatchLinkUpResponse(
            success=True,
            error_message="",
            interfaces_upped=1,
            apply_time_ms=0.0,
        )
    )
    mock_stub.async_batch_link_down = AsyncMock(
        return_value=node_agent_pb2.BatchLinkDownResponse(
            success=True,
            error_message="",
            interfaces_downed=1,
            apply_time_ms=0.0,
        )
    )
    mock_stub.async_set_latency = AsyncMock(
        return_value=node_agent_pb2.SetLatencyResponse(success=True)
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
    return d


class MockNats:
    """Mock NATS connection — records published messages."""

    def __init__(self):
        self.messages = []

    async def publish(self, subject, data):
        self.messages.append((subject, data))


class TestGsDeallocationSnapshot:
    """Test _build_desired_from_snapshot excludes GS links not in snapshot."""

    def test_snapshot_removes_stale_gs_link(self):
        d = _make_dispatcher()
        # Pre-seed a GS link
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo(
            "gnd0",
            "gnd0",
            3.0,
            1000.0,
        )

        # Snapshot with NO GS links — replaces everything
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(_make_link_state("sat-P00S00", "sat-P00S01"),),
            interval_s=5.0,
        )
        desired = d._build_desired_from_snapshot(snapshot)

        assert ("gs-ashburn", "sat-P00S00") not in desired
        assert ("sat-P00S00", "sat-P00S01") in desired

    def test_snapshot_handoff_keeps_new_satellite(self):
        d = _make_dispatcher(
            {
                ("gs-ashburn", "sat-P00S00"): ("gnd0", "gnd0"),
                ("gs-ashburn", "sat-P00S01"): ("gnd0", "gnd0"),
                ("sat-P00S00", "sat-P00S01"): ("isl0", "isl1"),
            }
        )
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo(
            "gnd0",
            "gnd0",
            3.0,
            1000.0,
        )

        # Snapshot with different GS satellite
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(_make_link_state("gs-ashburn", "sat-P00S01", link_type="ground"),),
            interval_s=5.0,
        )
        desired = d._build_desired_from_snapshot(snapshot)

        assert ("gs-ashburn", "sat-P00S00") not in desired
        assert ("gs-ashburn", "sat-P00S01") in desired

    def test_old_snapshot_seq_discarded(self):
        d = _make_dispatcher()
        d._last_snapshot_seq = 10

        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=5,  # older than current
            links=(),
            interval_s=5.0,
        )
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo(
            "isl0",
            "isl1",
            3.0,
            1000.0,
        )
        desired = d._build_desired_from_snapshot(snapshot)

        # Old snapshot ignored — returns None, active_links unchanged
        assert desired is None
        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestGsDeallocationDispatchBatch:
    """Test _dispatch_batch handles visible=True/scheduled=False for GS."""

    def test_gs_pair_removed_via_dispatch_batch(self):
        d = _make_dispatcher()
        info = ActiveLinkInfo("gnd0", "gnd0", 3.0, 1000.0)
        d._desired_links[("gs-ashburn", "sat-P00S00")] = info
        d._active_links[("gs-ashburn", "sat-P00S00")] = info

        vis = _make_vis("gs-ashburn", "sat-P00S00", True, False)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_isl_deallocation_not_removed(self):
        d = _make_dispatcher()
        info = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._desired_links[("sat-P00S00", "sat-P00S01")] = info
        d._active_links[("sat-P00S00", "sat-P00S01")] = info

        vis = _make_vis("sat-P00S00", "sat-P00S01", True, False)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestGsDeallocationConsistency:
    """Both paths produce identical _active_links for identical scenarios."""

    def test_snapshot_and_dispatch_agree_on_gs_removal(self):
        pair = ("gs-ashburn", "sat-P00S00")

        # Path 1: snapshot with no GS link — desired dict excludes pair
        d1 = _make_dispatcher()
        d1._active_links[pair] = ActiveLinkInfo("gnd0", "gnd0", 3.0, 1000.0)
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(),
            interval_s=5.0,
        )
        desired = d1._build_desired_from_snapshot(snapshot)

        # Path 2: dispatch batch with deallocation event → _reconcile_links removes
        d2 = _make_dispatcher()
        info = ActiveLinkInfo("gnd0", "gnd0", 3.0, 1000.0)
        d2._desired_links[pair] = info
        d2._active_links[pair] = info
        vis = _make_vis("gs-ashburn", "sat-P00S00", True, False)
        asyncio.run(d2._dispatch_batch([vis], [], MockNats()))

        # Both must agree
        assert pair not in desired, "snapshot desired did not exclude GS pair"
        assert pair not in d2._active_links, "_dispatch_batch did not remove GS pair"
