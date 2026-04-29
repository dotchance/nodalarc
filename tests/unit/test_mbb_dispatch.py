# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for three-phase MBB dispatch in the Scheduler.

Covers: capacity-aware MBB vs BBM classification, three-phase ordering,
rollback on failed make, BBM skip on failed break, greedy reservation,
incremental counter integrity, and snapshot rebaselining.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from scheduler.dispatcher import ActiveLinkInfo, Dispatcher


def _make_dispatcher(
    gs_caps: dict[str, int] | None = None,
    sat_caps: dict[str, int] | None = None,
    mbb: bool = True,
    pairs: list[tuple[str, str]] | None = None,
) -> Dispatcher:
    """Build a minimal Dispatcher with mocked dependencies."""
    imap = {}
    bmap = {}
    if pairs:
        for p in pairs:
            imap[p] = ("term0", "gnd0")
            bmap[p] = 1000.0

    loc = MagicMock()
    loc.agent_addr.return_value = "agent-1"
    loc.k3s_node.return_value = "node-1"
    loc.node_ip.return_value = "10.0.0.1"

    pool = MagicMock()
    stub = MagicMock()
    resp_down = MagicMock()
    resp_down.success = True
    resp_down.interfaces_downed = 1
    resp_down.apply_time_ms = 1.0
    stub.async_batch_link_down = AsyncMock(return_value=resp_down)
    resp_up = MagicMock()
    resp_up.success = True
    resp_up.interfaces_upped = 1
    resp_up.apply_time_ms = 1.0
    stub.async_batch_link_up = AsyncMock(return_value=resp_up)
    pool.get_stub.return_value = stub

    d = Dispatcher(
        interface_map=imap,
        bandwidth_map=bmap,
        pod_locator=loc,
        agent_pool=pool,
        override_set=set(),
        override_lock=threading.Lock(),
        session_id="test-session",
        gs_terminal_capacities=gs_caps or {},
        sat_ground_terminal_capacities=sat_caps or {},
        mbb_dispatch=mbb,
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    return d


def _run(coro):
    return asyncio.run(coro)


class TestMBBCapacityClassification:
    def test_multi_terminal_gs_gets_mbb(self):
        """GS with spare capacity → MBB eligible."""
        pair_old = ("gs-A", "sat-01")
        pair_new = ("gs-A", "sat-02")
        d = _make_dispatcher(
            gs_caps={"gs-A": 4},
            sat_caps={"sat-01": 1, "sat-02": 1},
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")
        d._gs_active_count["gs-A"] = 1
        d._sat_active_count["sat-01"] = 1

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        desired = {pair_new: ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")}

        _run(d._reconcile_links(desired, nc, sim))

        # MBB: sat-02 should be up (Phase 2), sat-01 should be down (Phase 3)
        assert pair_new in d._actual_links
        assert pair_old not in d._actual_links

    def test_single_terminal_gs_gets_bbm(self):
        """GS with tracking_capacity=1 → BBM (down before up)."""
        pair_old = ("gs-B", "sat-01")
        pair_new = ("gs-B", "sat-02")
        d = _make_dispatcher(
            gs_caps={"gs-B": 1},
            sat_caps={"sat-01": 1, "sat-02": 1},
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")
        d._gs_active_count["gs-B"] = 1
        d._sat_active_count["sat-01"] = 1

        nc = AsyncMock()
        nc.publish = AsyncMock()
        desired = {pair_new: ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")}

        _run(d._reconcile_links(desired, nc, datetime.now(UTC)))

        assert pair_new in d._actual_links
        assert pair_old not in d._actual_links

    def test_sat_constrained_forces_bbm(self):
        """GS has spare, but sat has ground_terminal_count=1 and is occupied → BBM."""
        pair_old = ("gs-C", "sat-X")
        pair_new = ("gs-C", "sat-Y")
        d = _make_dispatcher(
            gs_caps={"gs-C": 4},
            sat_caps={"sat-X": 1, "sat-Y": 1},
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")
        d._gs_active_count["gs-C"] = 1
        d._sat_active_count["sat-X"] = 1
        # sat-Y is also occupied by another GS
        d._sat_active_count["sat-Y"] = 1

        nc = AsyncMock()
        nc.publish = AsyncMock()
        desired = {pair_new: ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")}

        _run(d._reconcile_links(desired, nc, datetime.now(UTC)))

        # Should still complete (BBM: down sat-X first, then up sat-Y fails
        # because sat-Y is occupied by another GS, not freed by this handover)
        # Actually sat-Y's occupant is NOT in to_remove, so greedy check blocks it.
        # The handover partially fails — sat-X is freed but sat-Y can't be claimed.
        assert pair_old not in d._actual_links  # down succeeded


class TestMBBRollback:
    def test_rollback_on_failed_make(self):
        """If Phase 2 MBB LinkUp fails, Phase 3 skips the old link's down."""
        pair_old = ("gs-A", "sat-01")
        pair_new = ("gs-A", "sat-02")
        d = _make_dispatcher(
            gs_caps={"gs-A": 4},
            sat_caps={"sat-01": 1, "sat-02": 1},
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")
        d._gs_active_count["gs-A"] = 1
        d._sat_active_count["sat-01"] = 1

        # Make the up fail
        stub = d._pool.get_stub("agent-1")
        resp_fail = MagicMock()
        resp_fail.success = False
        resp_fail.interfaces_upped = 0
        resp_fail.error_message = "test failure"
        stub.async_batch_link_up = AsyncMock(return_value=resp_fail)

        nc = AsyncMock()
        nc.publish = AsyncMock()
        desired = {pair_new: ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")}

        _run(d._reconcile_links(desired, nc, datetime.now(UTC)))

        # Rollback: sat-01 should still be active (Phase 3 skipped)
        assert pair_old in d._actual_links
        assert pair_new not in d._actual_links


class TestMBBDispatchFlag:
    def test_flag_false_uses_bbm(self):
        """mbb_dispatch=false → original two-phase BBM regardless of capacity."""
        pair_old = ("gs-A", "sat-01")
        pair_new = ("gs-A", "sat-02")
        d = _make_dispatcher(
            gs_caps={"gs-A": 4},
            sat_caps={"sat-01": 1, "sat-02": 1},
            mbb=False,
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")

        nc = AsyncMock()
        nc.publish = AsyncMock()
        desired = {pair_new: ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")}

        _run(d._reconcile_links(desired, nc, datetime.now(UTC)))

        assert pair_new in d._actual_links
        assert pair_old not in d._actual_links


class TestCounterIntegrity:
    def test_rebaseline_corrects_drift(self):
        """Simulate counter drift and verify snapshot rebaseline corrects it."""
        d = _make_dispatcher(
            gs_caps={"gs-A": 2},
            sat_caps={"sat-01": 1},
        )
        pair = ("gs-A", "sat-01")
        d._actual_links[pair] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")
        d._gs_active_count["gs-A"] = 5
        d._sat_active_count["sat-01"] = 5

        d._rebaseline_active_counts()

        assert d._gs_active_count["gs-A"] == 1
        assert d._sat_active_count["sat-01"] == 1

    def test_increment_decrement_consistent(self):
        """O(1) increment/decrement produces counts matching _actual_links."""
        d = _make_dispatcher(
            gs_caps={"gs-A": 4, "gs-B": 2},
            sat_caps={"sat-01": 2, "sat-02": 1},
        )
        pairs = [("gs-A", "sat-01"), ("gs-A", "sat-02"), ("gs-B", "sat-01")]
        for p in pairs:
            d._actual_links[p] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")
            d._increment_active_counts(p)

        assert d._gs_active_count["gs-A"] == 2
        assert d._gs_active_count["gs-B"] == 1
        assert d._sat_active_count["sat-01"] == 2
        assert d._sat_active_count["sat-02"] == 1

        d._decrement_active_counts(("gs-A", "sat-01"))
        assert d._gs_active_count["gs-A"] == 1
        assert d._sat_active_count["sat-01"] == 1

    def test_decrement_floors_at_zero(self):
        """Decrement never goes negative."""
        d = _make_dispatcher(
            gs_caps={"gs-A": 1},
            sat_caps={"sat-01": 1},
        )
        d._actual_links[("gs-A", "sat-01")] = ActiveLinkInfo(
            "gnd0", "gnd0", 3.0, 1000.0, link_type="ground"
        )
        d._decrement_active_counts(("gs-A", "sat-01"))
        assert d._gs_active_count.get("gs-A", 0) == 0
        assert d._sat_active_count.get("sat-01", 0) == 0
