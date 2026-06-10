# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for three-phase MBB dispatch in the Scheduler.

Covers: capacity-aware MBB vs BBM classification, three-phase ordering,
rollback on failed make, BBM skip on failed break, greedy reservation,
incremental counter integrity, and snapshot rebaselining.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from nodalarc.proto import node_agent_pb2
from scheduler.dispatch_planner import interface_colliding_downs
from scheduler.dispatcher import ActiveLinkInfo, Dispatcher

OME_RANGE_KM = 1000.0


def _ground_info(
    authority_sim_time: datetime | None = None,
    gs_iface: str = "term0",
    sat_iface: str = "gnd0",
) -> ActiveLinkInfo:
    if authority_sim_time is None:
        authority_sim_time = datetime.now(UTC)
    return ActiveLinkInfo(
        gs_iface,
        sat_iface,
        3.0,
        1000.0,
        link_type="ground",
        range_km=OME_RANGE_KM,
        authority_sim_time=authority_sim_time,
        authority_source="test",
    )


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

    def resp_down(req):
        return node_agent_pb2.BatchLinkDownResponse(
            success=True,
            error_message="",
            interfaces_downed=len(req.interfaces),
            apply_time_ms=1.0,
            interface_results=[
                node_agent_pb2.InterfaceResult(
                    node_id=iface.node_id,
                    interface_name=iface.interface_name,
                    success=True,
                    verified=True,
                )
                for iface in req.interfaces
            ],
        )

    def resp_up(req):
        return node_agent_pb2.BatchLinkUpResponse(
            success=True,
            error_message="",
            interfaces_upped=len(req.interfaces),
            apply_time_ms=1.0,
            interface_results=[
                node_agent_pb2.InterfaceResult(
                    node_id=iface.node_id,
                    interface_name=iface.interface_name,
                    success=True,
                    verified=True,
                )
                for iface in req.interfaces
            ],
        )

    stub.async_batch_link_down = AsyncMock(side_effect=resp_down)
    stub.async_batch_link_up = AsyncMock(side_effect=resp_up)
    pool.get_stub.return_value = stub

    gs_caps = gs_caps or {}
    gs_modes = {gs: ("mbb" if mbb and cap > 1 else "bbm") for gs, cap in gs_caps.items()}
    d = Dispatcher(
        interface_map=imap,
        bandwidth_map=bmap,
        pod_locator=loc,
        agent_pool=pool,
        session_id="test-session",
        wiring_generation="sha256:" + "a" * 64,
        max_latency_age_s=60.0,
        gs_terminal_capacities=gs_caps,
        gs_handover_modes=gs_modes,
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
        d._actual_links[pair_old] = _ground_info()
        d._gs_active_count["gs-A"] = 1
        d._sat_active_count["sat-01"] = 1

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        # The successor occupies a different terminal — true MBB is possible.
        desired = {pair_new: _ground_info(authority_sim_time=sim, gs_iface="term1")}

        _run(d._reconcile_links(desired, nc, sim))

        # MBB: the successor link should be up before the old link is released.
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
        d._actual_links[pair_old] = _ground_info()
        d._gs_active_count["gs-B"] = 1
        d._sat_active_count["sat-01"] = 1

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        desired = {pair_new: _ground_info(authority_sim_time=sim)}

        _run(d._reconcile_links(desired, nc, sim))

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
        d._actual_links[pair_old] = _ground_info()
        d._gs_active_count["gs-C"] = 1
        d._sat_active_count["sat-X"] = 1
        # sat-Y is also occupied by another GS
        d._sat_active_count["sat-Y"] = 1

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        desired = {pair_new: _ground_info(authority_sim_time=sim)}

        _run(d._reconcile_links(desired, nc, sim))

        # Should still complete (BBM: down sat-X first, then up sat-Y fails
        # because sat-Y is occupied by another GS, not freed by this handover)
        # Actually sat-Y's occupant is NOT in to_remove, so greedy check blocks it.
        # The handover partially fails — sat-X is freed but sat-Y can't be claimed.
        assert pair_old not in d._actual_links  # down succeeded


class TestMBBRollback:
    def test_rollback_on_failed_make(self):
        """If successor LinkUp fails, MBB must not tear down the old link."""
        pair_old = ("gs-A", "sat-01")
        pair_new = ("gs-A", "sat-02")
        d = _make_dispatcher(
            gs_caps={"gs-A": 4},
            sat_caps={"sat-01": 1, "sat-02": 1},
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = _ground_info()
        d._gs_active_count["gs-A"] = 1
        d._sat_active_count["sat-01"] = 1

        # Make the up fail
        stub = d._pool.get_stub("agent-1")

        def resp_fail(req):
            return node_agent_pb2.BatchLinkUpResponse(
                success=False,
                error_message="test failure",
                interfaces_upped=0,
                apply_time_ms=1.0,
                interface_results=[
                    node_agent_pb2.InterfaceResult(
                        node_id=iface.node_id,
                        interface_name=iface.interface_name,
                        success=False,
                        error_message="test failure",
                    )
                    for iface in req.interfaces
                ],
            )

        stub.async_batch_link_up = AsyncMock(side_effect=resp_fail)

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        # Distinct terminal: a same-terminal successor is break-before-make
        # by necessity and the old link is legitimately released first.
        desired = {pair_new: _ground_info(authority_sim_time=sim, gs_iface="term1")}

        _run(d._reconcile_links(desired, nc, sim))

        # Rollback: sat-01 should still be active because teardown was skipped.
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
        d._actual_links[pair_old] = _ground_info()

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        desired = {pair_new: _ground_info(authority_sim_time=sim)}

        _run(d._reconcile_links(desired, nc, sim))

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
        d._actual_links[pair] = _ground_info()
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
            d._actual_links[p] = _ground_info()
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
        d._actual_links[("gs-A", "sat-01")] = _ground_info()
        d._decrement_active_counts(("gs-A", "sat-01"))
        assert d._gs_active_count.get("gs-A", 0) == 0
        assert d._sat_active_count.get("sat-01", 0) == 0


def _record_call_order(d: Dispatcher) -> list[str]:
    """Re-wrap the stub's down/up responders to record dispatch order."""
    order: list[str] = []
    stub = d._pool.get_stub("agent-1")
    original_down = stub.async_batch_link_down.side_effect
    original_up = stub.async_batch_link_up.side_effect

    def resp_down(req):
        order.append("down")
        return original_down(req)

    def resp_up(req):
        order.append("up")
        return original_up(req)

    stub.async_batch_link_down = AsyncMock(side_effect=resp_down)
    stub.async_batch_link_up = AsyncMock(side_effect=resp_up)
    return order


class TestTerminalReuseCollision:
    """A release whose kernel interface is reused by a same-pass acquire
    must dispatch before the acquire (forced break-before-make).

    Live failure this pins: the OME allocator freed denver-gw1 term0 and
    handed it to the successor link in the same tick; MBB staging upped the
    successor on term0 (proof passed), then the final-release down of the
    old link tore term0's host veth and mirred state back down — kernel
    diverged from proven bookkeeping and the GS exhausted recovery.
    """

    def test_same_terminal_handover_dispatches_down_before_up(self):
        pair_old = ("gs-A", "sat-01")
        pair_new = ("gs-A", "sat-02")
        d = _make_dispatcher(
            gs_caps={"gs-A": 4},
            sat_caps={"sat-01": 1, "sat-02": 1},
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = _ground_info()
        d._gs_active_count["gs-A"] = 1
        d._sat_active_count["sat-01"] = 1
        order = _record_call_order(d)

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        # Successor reuses term0 — spare capacity exists, but MBB is
        # physically impossible on one terminal.
        desired = {pair_new: _ground_info(authority_sim_time=sim, gs_iface="term0")}

        _run(d._reconcile_links(desired, nc, sim))

        assert order == ["down", "up"]
        assert pair_new in d._actual_links
        assert pair_old not in d._actual_links

    def test_distinct_terminal_handover_keeps_mbb_order(self):
        pair_old = ("gs-A", "sat-01")
        pair_new = ("gs-A", "sat-02")
        d = _make_dispatcher(
            gs_caps={"gs-A": 4},
            sat_caps={"sat-01": 1, "sat-02": 1},
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = _ground_info()
        d._gs_active_count["gs-A"] = 1
        d._sat_active_count["sat-01"] = 1
        order = _record_call_order(d)

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        desired = {pair_new: _ground_info(authority_sim_time=sim, gs_iface="term1")}

        _run(d._reconcile_links(desired, nc, sim))

        assert order == ["up", "down"]
        assert pair_new in d._actual_links
        assert pair_old not in d._actual_links

    def test_sat_terminal_reuse_across_stations_dispatches_down_first(self):
        """gs-A releases sat-X gnd0 while gs-B acquires it in the same pass;
        gs-A also has its own distinct-terminal acquire, so without the
        collision check its release would defer past gs-B's acquire."""
        pair_a_old = ("gs-A", "sat-X")
        pair_a_new = ("gs-A", "sat-Y")
        pair_b_new = ("gs-B", "sat-X")
        all_pairs = [pair_a_old, pair_a_new, pair_b_new]
        d = _make_dispatcher(
            gs_caps={"gs-A": 4, "gs-B": 4},
            sat_caps={"sat-X": 1, "sat-Y": 1},
            pairs=all_pairs,
        )
        d._actual_links[pair_a_old] = _ground_info(gs_iface="term0", sat_iface="gnd0")
        d._gs_active_count["gs-A"] = 1
        d._sat_active_count["sat-X"] = 1
        order = _record_call_order(d)

        nc = AsyncMock()
        nc.publish = AsyncMock()
        sim = datetime.now(UTC)
        desired = {
            pair_a_new: _ground_info(authority_sim_time=sim, gs_iface="term1", sat_iface="gnd0"),
            pair_b_new: _ground_info(authority_sim_time=sim, gs_iface="term0", sat_iface="gnd0"),
        }

        _run(d._reconcile_links(desired, nc, sim))

        assert order[0] == "down"
        assert pair_a_old not in d._actual_links
        assert pair_b_new in d._actual_links

    def test_helper_flags_only_shared_interfaces(self):
        sim = datetime.now(UTC)
        reused = ("gs-A", "sat-01")
        independent = ("gs-A", "sat-02")
        actual = {
            reused: _ground_info(authority_sim_time=sim, gs_iface="term0"),
            independent: _ground_info(authority_sim_time=sim, gs_iface="term1"),
        }
        acquire = ("gs-A", "sat-03")
        desired = {acquire: _ground_info(authority_sim_time=sim, gs_iface="term0")}

        colliding = interface_colliding_downs(
            to_remove={reused, independent},
            to_add={acquire},
            actual=actual,
            desired=desired,
        )
        assert colliding == {reused}


class TestForcedBBMSegmentEscalation:
    """forced_bbm_pairs escalates to GS-segment level in _reconcile_mbb."""

    def test_forced_segment_while_other_segments_mbb(self):
        """Forced pair on gs-A → gs-A BBM, gs-B still MBB."""
        pair_a_old = ("gs-A", "sat-01")
        pair_a_new = ("gs-A", "sat-02")
        pair_b_old = ("gs-B", "sat-03")
        pair_b_new = ("gs-B", "sat-04")
        all_pairs = [pair_a_old, pair_a_new, pair_b_old, pair_b_new]
        d = _make_dispatcher(
            gs_caps={"gs-A": 4, "gs-B": 4},
            sat_caps={"sat-01": 1, "sat-02": 1, "sat-03": 1, "sat-04": 1},
            pairs=all_pairs,
        )
        d._actual_links[pair_a_old] = _ground_info()
        d._actual_links[pair_b_old] = _ground_info()
        d._gs_active_count = {"gs-A": 1, "gs-B": 1}
        d._sat_active_count = {"sat-01": 1, "sat-03": 1}

        sim = datetime.now(UTC)
        # Successors take fresh terminals so gs-B remains genuinely MBB.
        desired = {
            pair_a_new: _ground_info(authority_sim_time=sim, gs_iface="term1"),
            pair_b_new: _ground_info(authority_sim_time=sim, gs_iface="term1"),
        }

        forced = frozenset({pair_a_old})
        down_reasons = {pair_a_old: "scenario_inject_down"}

        nc = AsyncMock()
        nc.publish = AsyncMock()

        _run(d._reconcile_links(desired, nc, sim, down_reasons, forced))

        assert pair_a_old not in d._actual_links
        assert pair_b_old not in d._actual_links
        assert pair_a_new in d._actual_links
        assert pair_b_new in d._actual_links

    def test_mixed_forced_and_normal_under_same_gs(self):
        """One forced pair + one normal pair under gs-A → entire segment BBM."""
        pair_forced = ("gs-A", "sat-01")
        pair_normal = ("gs-A", "sat-02")
        pair_new = ("gs-A", "sat-03")
        all_pairs = [pair_forced, pair_normal, pair_new]
        d = _make_dispatcher(
            gs_caps={"gs-A": 4},
            sat_caps={"sat-01": 1, "sat-02": 1, "sat-03": 1},
            pairs=all_pairs,
        )
        d._actual_links[pair_forced] = _ground_info()
        d._actual_links[pair_normal] = _ground_info()
        d._gs_active_count = {"gs-A": 2}
        d._sat_active_count = {"sat-01": 1, "sat-02": 1}

        sim = datetime.now(UTC)
        desired = {
            pair_new: _ground_info(authority_sim_time=sim),
        }

        forced = frozenset({pair_forced})
        down_reasons = {pair_forced: "scenario_inject_down"}

        nc = AsyncMock()
        nc.publish = AsyncMock()

        _run(d._reconcile_links(desired, nc, sim, down_reasons, forced))

        assert pair_forced not in d._actual_links
        assert pair_normal not in d._actual_links
        assert pair_new in d._actual_links

    def test_reason_through_mbb_path(self):
        """Override reason flows through _reconcile_mbb → _send_batch_down."""
        pair_old = ("gs-A", "sat-01")
        pair_new = ("gs-A", "sat-02")
        d = _make_dispatcher(
            gs_caps={"gs-A": 4},
            sat_caps={"sat-01": 1, "sat-02": 1},
            pairs=[pair_old, pair_new],
        )
        d._actual_links[pair_old] = _ground_info()
        d._gs_active_count = {"gs-A": 1}
        d._sat_active_count = {"sat-01": 1}

        sim = datetime.now(UTC)
        desired = {
            pair_new: _ground_info(authority_sim_time=sim),
        }

        forced = frozenset({pair_old})
        down_reasons = {pair_old: "satellite_loss"}

        nc = AsyncMock()
        nc.publish = AsyncMock()

        _run(d._reconcile_links(desired, nc, sim, down_reasons, forced))

        published_calls = d._js.publish.call_args_list
        down_calls = [c for c in published_calls if b"satellite_loss" in c[0][1]]
        assert len(down_calls) == 1
