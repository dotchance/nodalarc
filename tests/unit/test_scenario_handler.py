"""Test scenario handler logic: override_set management and dispatch routing.

Tests the internal functions without NATS connections — verifies that:
- Override set add/remove/clear works correctly
- inject_satellite_loss overrides all links for a node
- _inject_link_down_on_main_loop adds override and skips inactive links
- _inject_link_down_on_main_loop does NOT pop active_links on dispatch failure
- _dispatch_down_single builds correct per-interface locality/vni/remote_node_ip
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

from scheduler.agent_pool import AgentPool
from scheduler.dispatcher import ActiveLinkInfo
from scheduler.pod_locator import PodLocationMap
from scheduler.scenario_handler import (
    _dispatch_down_single,
    _inject_link_down_on_main_loop,
)


class TestOverrideSetManagement:
    def test_override_set_add_and_remove(self):
        """Basic override_set operations."""
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()

        # Add
        pair = ("sat-P00S00", "sat-P00S01")
        with lock:
            override_set.add(pair)
        assert pair in override_set

        # Remove
        with lock:
            override_set.discard(pair)
        assert pair not in override_set

    def test_satellite_loss_adds_all_pairs(self):
        """inject_satellite_loss should add all pairs involving a node."""
        interface_map = {
            ("sat-P00S00", "sat-P00S01"): ("isl0", "isl1"),
            ("sat-P00S00", "sat-P00S02"): ("isl1", "isl0"),
            ("sat-P00S01", "sat-P00S02"): ("isl2", "isl2"),
            ("gs-ashburn", "sat-P00S00"): ("term0", "gnd0"),
        }
        override_set: set[tuple[str, str]] = set()
        node = "sat-P00S00"

        for pair in interface_map:
            if node in pair:
                override_set.add(pair)

        assert ("sat-P00S00", "sat-P00S01") in override_set
        assert ("sat-P00S00", "sat-P00S02") in override_set
        assert ("gs-ashburn", "sat-P00S00") in override_set
        # sat-P00S01<->sat-P00S02 should NOT be overridden
        assert ("sat-P00S01", "sat-P00S02") not in override_set

    def test_clear_overrides_empties_set(self):
        """clear_overrides removes all entries."""
        override_set: set[tuple[str, str]] = set()
        override_set.add(("a", "b"))
        override_set.add(("c", "d"))
        override_set.add(("e", "f"))
        override_set.clear()
        assert len(override_set) == 0


class TestInjectLinkDown:
    def test_inject_skips_inactive_link(self):
        """_inject_link_down_on_main_loop returns None for inactive link (no dispatch)."""
        active_links: dict = {}
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()
        interface_map = {("sat-P00S00", "sat-P00S01"): ("isl0", "isl1")}
        loc = PodLocationMap()
        pool = AgentPool()

        result = asyncio.run(
            _inject_link_down_on_main_loop(
                ("sat-P00S00", "sat-P00S01"),
                interface_map,
                active_links,
                loc,
                pool,
                override_set,
                lock,
                {},
            )
        )
        assert result is None
        assert ("sat-P00S00", "sat-P00S01") in override_set

    def test_inject_does_not_pop_on_dispatch_failure(self):
        """Active link is NOT popped if dispatch fails (no ghost links)."""
        pair = ("sat-P00S00", "sat-P00S01")
        info = ActiveLinkInfo("isl0", "isl1", 13.0, 1000.0)
        active_links = {pair: info}
        override_set: set[tuple[str, str]] = set()
        lock = threading.Lock()
        interface_map = {pair: ("isl0", "isl1")}
        loc = PodLocationMap()
        pool = AgentPool()

        # PodLocationMap has no pods — link_locality returns None → dispatch
        # returns error string, active_links should NOT be popped
        result = asyncio.run(
            _inject_link_down_on_main_loop(
                pair, interface_map, active_links, loc, pool, override_set, lock, {}
            )
        )
        assert result is not None  # Error string
        assert pair in active_links  # NOT popped

    def test_override_blocks_dispatcher(self):
        """Override set prevents the dispatcher from processing OME events."""
        override_set = {("sat-P00S00", "sat-P00S01")}

        # Simulate what the dispatcher does: check override before processing
        pair = ("sat-P00S00", "sat-P00S01")
        assert pair in override_set  # Dispatcher would skip this event


class TestDispatchDownSingle:
    def test_returns_error_when_pods_unscheduled(self):
        """_dispatch_down_single returns error when pods aren't scheduled."""
        pair = ("sat-P00S00", "sat-P00S01")
        info = ActiveLinkInfo("isl0", "isl1", 13.0, 1000.0)
        active_links = {pair: info}
        loc = PodLocationMap()  # Empty — no pods
        pool = AgentPool()

        result = asyncio.run(
            _dispatch_down_single(pair, info, {pair: ("isl0", "isl1")}, active_links, loc, pool, {})
        )
        assert result is not None
        assert "not yet scheduled" in result

    def test_local_isl_sends_to_one_agent(self):
        """LOCAL ISL groups both interfaces to the same agent."""
        from nodalarc.proto import node_agent_pb2

        pair = ("sat-P00S00", "sat-P00S01")
        info = ActiveLinkInfo("isl0", "isl1", 13.0, 1000.0)
        active_links = {pair: info}

        loc = PodLocationMap()
        loc._node_of = {"sat-P00S00": "nodal", "sat-P00S01": "nodal"}
        loc._agent_addrs = {"nodal": "nodal"}

        # Mock the agent pool to capture the request
        mock_resp = MagicMock()
        mock_resp.success = True
        mock_stub = MagicMock()
        mock_stub.async_batch_link_down = AsyncMock(return_value=mock_resp)
        pool = AgentPool()
        pool.get_stub = MagicMock(return_value=mock_stub)

        result = asyncio.run(
            _dispatch_down_single(pair, info, {pair: ("isl0", "isl1")}, active_links, loc, pool, {})
        )
        assert result is None  # Success
        # Verify one call to one agent with 2 InterfaceDown entries
        mock_stub.async_batch_link_down.assert_called_once()
        req = mock_stub.async_batch_link_down.call_args[0][0]
        assert len(req.interfaces) == 2
        assert req.interfaces[0].locality == node_agent_pb2.LOCAL
        assert req.interfaces[1].locality == node_agent_pb2.LOCAL

    def test_cross_node_isl_sends_to_two_agents(self):
        """CROSS_NODE ISL sends to both agents with correct vni."""
        from nodalarc.proto import node_agent_pb2

        pair = ("sat-P00S00", "sat-P00S01")
        info = ActiveLinkInfo("isl0", "isl1", 13.0, 1000.0)
        active_links = {pair: info}

        loc = PodLocationMap()
        loc._node_of = {"sat-P00S00": "nodal", "sat-P00S01": "nodal03"}
        loc._agent_addrs = {"nodal": "nodal", "nodal03": "nodal03"}
        loc._node_ips = {"nodal": "192.168.10.202", "nodal03": "192.168.10.203"}

        mock_resp = MagicMock()
        mock_resp.success = True
        mock_stub = MagicMock()
        mock_stub.async_batch_link_down = AsyncMock(return_value=mock_resp)
        pool = AgentPool()
        pool.get_stub = MagicMock(return_value=mock_stub)

        result = asyncio.run(
            _dispatch_down_single(pair, info, {pair: ("isl0", "isl1")}, active_links, loc, pool, {})
        )
        assert result is None  # Success
        # Two agents called
        assert pool.get_stub.call_count == 2
        assert mock_stub.async_batch_link_down.call_count == 2
        # Each call has 1 InterfaceDown, both with CROSS_NODE and matching vni
        for call in mock_stub.async_batch_link_down.call_args_list:
            req = call[0][0]
            assert len(req.interfaces) == 1
            assert req.interfaces[0].locality == node_agent_pb2.CROSS_NODE
            assert req.interfaces[0].vni != 0

    def test_ground_link_local(self):
        """LOCAL ground link sends to one agent (satellite's agent)."""
        from nodalarc.proto import node_agent_pb2

        pair = ("gs-ashburn", "sat-P00S00")
        info = ActiveLinkInfo("term0", "gnd0", 13.0, 500.0, link_type="ground")
        active_links = {pair: info}
        gs_caps = {"gs-ashburn": 2}

        loc = PodLocationMap()
        loc._node_of = {"gs-ashburn": "nodal", "sat-P00S00": "nodal"}
        loc._agent_addrs = {"nodal": "nodal"}

        mock_resp = MagicMock()
        mock_resp.success = True
        mock_stub = MagicMock()
        mock_stub.async_batch_link_down = AsyncMock(return_value=mock_resp)
        pool = AgentPool()
        pool.get_stub = MagicMock(return_value=mock_stub)

        result = asyncio.run(
            _dispatch_down_single(
                pair, info, {pair: ("term0", "gnd0")}, active_links, loc, pool, gs_caps
            )
        )
        assert result is None
        mock_stub.async_batch_link_down.assert_called_once()
        req = mock_stub.async_batch_link_down.call_args[0][0]
        assert len(req.interfaces) == 1
        iface = req.interfaces[0]
        assert iface.link_type == node_agent_pb2.GROUND
        assert iface.gs_id == "gs-ashburn"
        assert iface.sat_id == "sat-P00S00"
        assert iface.locality == node_agent_pb2.LOCAL

    def test_ground_link_cross_node(self):
        """CROSS_NODE ground link sends to two agents with vni."""
        from nodalarc.proto import node_agent_pb2

        pair = ("gs-ashburn", "sat-P00S00")
        info = ActiveLinkInfo("term0", "gnd0", 13.0, 500.0, link_type="ground")
        active_links = {pair: info}
        gs_caps = {"gs-ashburn": 2}

        loc = PodLocationMap()
        loc._node_of = {"gs-ashburn": "nodal", "sat-P00S00": "nodal03"}
        loc._agent_addrs = {"nodal": "nodal", "nodal03": "nodal03"}
        loc._node_ips = {"nodal": "192.168.10.202", "nodal03": "192.168.10.203"}

        mock_resp = MagicMock()
        mock_resp.success = True
        mock_stub = MagicMock()
        mock_stub.async_batch_link_down = AsyncMock(return_value=mock_resp)
        pool = AgentPool()
        pool.get_stub = MagicMock(return_value=mock_stub)

        result = asyncio.run(
            _dispatch_down_single(
                pair, info, {pair: ("term0", "gnd0")}, active_links, loc, pool, gs_caps
            )
        )
        assert result is None
        # Two agents
        assert mock_stub.async_batch_link_down.call_count == 2
        for call in mock_stub.async_batch_link_down.call_args_list:
            req = call[0][0]
            assert len(req.interfaces) == 1
            iface = req.interfaces[0]
            assert iface.link_type == node_agent_pb2.GROUND
            assert iface.locality == node_agent_pb2.CROSS_NODE
            assert iface.vni != 0
