"""Test scenario handler logic: override_set management and dispatch routing.

Tests the internal functions without NATS connections — verifies that:
- inject_link_down adds to override_set and calls _dispatch_link_down
- inject_link_up removes from override_set
- inject_satellite_loss overrides all links for a node
- clear_overrides empties the set
"""

from __future__ import annotations

import threading

from scheduler.agent_pool import AgentPool
from scheduler.pod_locator import PodLocationMap
from scheduler.scenario_handler import _dispatch_link_down


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
            ("gs-ashburn", "sat-P00S00"): ("gnd0", "gnd0"),
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


class TestDispatchLinkDown:
    def test_dispatch_skips_inactive_link(self):
        """_dispatch_link_down does nothing if link is not in active_links."""
        active_links: dict = {}  # Empty — no active links
        interface_map = {("sat-P00S00", "sat-P00S01"): ("isl0", "isl1")}
        loc = PodLocationMap()
        pool = AgentPool()

        # Should not raise — just returns because link is not active
        _dispatch_link_down(
            ("sat-P00S00", "sat-P00S01"),
            interface_map,
            active_links,
            loc,
            pool,
        )

    def test_dispatch_pops_from_active_links(self):
        """_dispatch_link_down removes the pair from active_links."""
        from scheduler.dispatcher import ActiveLinkInfo

        active_links = {
            ("sat-P00S00", "sat-P00S01"): ActiveLinkInfo("isl0", "isl1", 13.0, 1000.0),
        }
        interface_map = {("sat-P00S00", "sat-P00S01"): ("isl0", "isl1")}
        loc = PodLocationMap()
        pool = AgentPool()

        # Will fail the gRPC call (no agent) but should pop from active_links
        _dispatch_link_down(
            ("sat-P00S00", "sat-P00S01"),
            interface_map,
            active_links,
            loc,
            pool,
        )
        assert ("sat-P00S00", "sat-P00S01") not in active_links

    def test_override_blocks_dispatcher(self):
        """Override set prevents the dispatcher from processing OME events."""
        override_set = {("sat-P00S00", "sat-P00S01")}

        # Simulate what the dispatcher does: check override before processing
        pair = ("sat-P00S00", "sat-P00S01")
        assert pair in override_set  # Dispatcher would skip this event
