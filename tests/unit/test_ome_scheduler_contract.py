"""B.3A Contract test: GS deallocation consistency across Scheduler code paths.

Tests the ACTUAL Dispatcher code — no logic extraction, no duplication.
Constructs a minimal Dispatcher with mocked Node Agent (AgentPool.get_stub
returns a mock that returns success), feeds VisibilityEvents through the
real _ome_catchup replay and _dispatch_batch paths, and asserts _active_links.

Covers PRD B.3A requirement: visible=True/scheduled=False for a GS pair
must remove the pair from _active_links in both code paths.
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


def _make_dispatcher(interface_map=None) -> Dispatcher:
    """Construct a minimal Dispatcher with no ZMQ connections."""
    if interface_map is None:
        interface_map = {
            ("gs-ashburn", "sat-P00S00"): ("gnd0", "gnd0"),
            ("sat-P00S00", "sat-P00S01"): ("isl0", "isl1"),
        }
    bandwidth_map = {k: 1000.0 for k in interface_map}

    loc = PodLocationMap()
    # Populate node_of so agent_addr returns something
    for pair in interface_map:
        for nid in pair:
            loc._node_of[nid] = "nodal"
    loc._agent_addrs["nodal"] = "127.0.0.1:50100"

    pool = MagicMock()
    # Mock stub returns success for batch_link_up and batch_link_down
    mock_stub = MagicMock()
    mock_stub.batch_link_up.return_value = node_agent_pb2.BatchLinkUpResponse(
        success=True,
        error_message="",
        interfaces_upped=1,
        apply_time_ms=0.0,
    )
    mock_stub.batch_link_down.return_value = node_agent_pb2.BatchLinkDownResponse(
        success=True,
        error_message="",
        interfaces_downed=1,
        apply_time_ms=0.0,
    )
    pool.get_stub.return_value = mock_stub

    d = Dispatcher(
        ome_endpoint="tcp://127.0.0.1:5560",  # never connected
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        override_set=set(),
        override_lock=threading.Lock(),
    )
    return d


class MockPub:
    """Mock ZMQ PUB socket — records sent messages."""

    def __init__(self):
        self.messages = []

    def send(self, data):
        self.messages.append(data)


class TestGsDeallocationCatchupReplay:
    """Test _ome_catchup replay handles visible=True/scheduled=False for GS."""

    def test_gs_pair_removed_after_deallocation(self):
        d = _make_dispatcher()

        # Simulate what _ome_catchup does: replay events into _active_links
        # by calling the actual catch-up method with a mocked OME response.
        catchup_response = {
            "events": [
                _make_vis("gs-ashburn", "sat-P00S00", True, True).model_dump(mode="json"),
                _make_vis("gs-ashburn", "sat-P00S00", True, False).model_dump(mode="json"),
            ],
            "current_sim_time": "2026-01-01T00:00:30Z",
        }

        with patch.object(d, "_ome_catchup", wraps=d._ome_catchup):
            # Call the replay logic directly by patching the ZMQ call
            async def _run():
                mock_pub = MockPub()
                # Patch the sync catchup to return our crafted response
                with patch("nodalarc.platform.get_platform_config") as mock_cfg:
                    mock_cfg.return_value.ome_catchup_connect = "tcp://127.0.0.1:5568"
                    with patch("zmq.Context") as mock_zmq_ctx:
                        mock_sock = MagicMock()
                        mock_sock.recv_json.return_value = catchup_response
                        mock_zmq_ctx.return_value.socket.return_value = mock_sock
                        await d._ome_catchup(mock_pub)

            asyncio.run(_run())

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_gs_handoff_keeps_new_satellite(self):
        d = _make_dispatcher(
            {
                ("gs-ashburn", "sat-P00S00"): ("gnd0", "gnd0"),
                ("gs-ashburn", "sat-P00S01"): ("gnd0", "gnd0"),
            }
        )

        catchup_response = {
            "events": [
                _make_vis("gs-ashburn", "sat-P00S00", True, True).model_dump(mode="json"),
                _make_vis("gs-ashburn", "sat-P00S01", True, True).model_dump(mode="json"),
                _make_vis("gs-ashburn", "sat-P00S00", True, False).model_dump(mode="json"),
            ],
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

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links
        assert ("gs-ashburn", "sat-P00S01") in d._active_links


class TestGsDeallocationDispatchBatch:
    """Test _dispatch_batch handles visible=True/scheduled=False for GS."""

    def test_gs_pair_removed_via_dispatch_batch(self):
        d = _make_dispatcher()
        # Pre-seed the link as active
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo(
            "gnd0",
            "gnd0",
            3.0,
            1000.0,
        )

        vis = _make_vis("gs-ashburn", "sat-P00S00", True, False)

        async def _run():
            mock_pub = MockPub()
            await d._dispatch_batch([vis], [], mock_pub)

        asyncio.run(_run())

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_isl_deallocation_not_removed(self):
        d = _make_dispatcher()
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo(
            "isl0",
            "isl1",
            3.0,
            1000.0,
        )

        vis = _make_vis("sat-P00S00", "sat-P00S01", True, False)

        async def _run():
            await d._dispatch_batch([vis], [], MockPub())

        asyncio.run(_run())

        # ISL visible+unscheduled does NOT remove — only GS
        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestGsDeallocationConsistency:
    """Both paths produce identical _active_links for identical input."""

    def test_catchup_and_dispatch_agree(self):
        events = [
            _make_vis("gs-ashburn", "sat-P00S00", True, True),
            _make_vis("gs-ashburn", "sat-P00S00", True, False),
        ]
        pair = ("gs-ashburn", "sat-P00S00")

        # Path 1: catch-up replay
        d1 = _make_dispatcher()
        catchup_response = {
            "events": [e.model_dump(mode="json") for e in events],
            "current_sim_time": "2026-01-01T00:00:30Z",
        }

        async def _run_catchup():
            with patch("nodalarc.platform.get_platform_config") as mock_cfg:
                mock_cfg.return_value.ome_catchup_connect = "tcp://127.0.0.1:5568"
                with patch("zmq.Context") as mock_zmq_ctx:
                    mock_sock = MagicMock()
                    mock_sock.recv_json.return_value = catchup_response
                    mock_zmq_ctx.return_value.socket.return_value = mock_sock
                    await d1._ome_catchup(MockPub())

        asyncio.run(_run_catchup())

        # Path 2: _dispatch_batch (feed events sequentially)
        d2 = _make_dispatcher()

        async def _run_dispatch():
            mock_pub = MockPub()
            # First event: link up
            await d2._dispatch_batch([events[0]], [], mock_pub)
            # Second event: deallocation
            await d2._dispatch_batch([events[1]], [], mock_pub)

        asyncio.run(_run_dispatch())

        # Both must agree
        assert pair not in d1._active_links, "catch-up replay did not remove GS pair"
        assert pair not in d2._active_links, "_dispatch_batch did not remove GS pair"
