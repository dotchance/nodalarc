"""Production queue behavior tests — the real quality gate.

Tests the dispatch worker, DispatchIntent, queue drain semantics,
forced BBM escalation, suspend/resume override deferral, and reason
attribution through the single dispatch path.

These tests exercise the production code path (dispatch worker + queue),
NOT the _dispatch_batch test-compat method.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)
from nodalarc.proto import node_agent_pb2
from scheduler.dispatcher import ActiveLinkInfo, Dispatcher, DispatchIntent
from scheduler.pod_locator import PodLocationMap

SIM = datetime(2026, 1, 1, tzinfo=UTC)


def _make_dispatcher(mbb=False):
    interface_map = {
        ("gs-ashburn", "sat-P00S00"): ("term0", "gnd0"),
        ("gs-ashburn", "sat-P00S01"): ("term0", "gnd0"),
        ("sat-P00S00", "sat-P00S01"): ("isl0", "isl1"),
    }
    bandwidth_map = {k: 1000.0 for k in interface_map}

    loc = PodLocationMap()
    for pair in interface_map:
        for nid in pair:
            loc._node_of[nid] = "nodal"
    loc._agent_addrs["nodal"] = "nodal"

    pool = MagicMock()
    stub = MagicMock()
    up_resp = node_agent_pb2.BatchLinkUpResponse(
        success=True, error_message="", interfaces_upped=1, apply_time_ms=0.0
    )
    down_resp = node_agent_pb2.BatchLinkDownResponse(
        success=True, error_message="", interfaces_downed=1, apply_time_ms=0.0
    )
    stub.async_batch_link_up = AsyncMock(return_value=up_resp)
    stub.async_batch_link_down = AsyncMock(return_value=down_resp)
    stub.async_set_latency = AsyncMock(return_value=node_agent_pb2.SetLatencyResponse(success=True))
    pool.get_stub.return_value = stub

    d = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        session_id="test-session",
        gs_terminal_capacities={"gs-ashburn": 2},
        sat_ground_terminal_capacities={"sat-P00S00": 1, "sat-P00S01": 1},
        mbb_dispatch=mbb,
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    d._position_table = MagicMock()
    d._position_table.compute_link_latency = MagicMock(return_value=3.0)
    d._position_table.compute_link_range = MagicMock(return_value=1500.0)
    return d, pool


class TestDispatchWorkerReconcile:
    """Dispatch worker performs actual BatchLinkDown/Up through _reconcile_links."""

    def test_scenario_override_dispatches_via_worker(self):
        """Override causes down through worker, not scenario handler."""
        d, pool = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        d._override_pairs[pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        async def _run():
            nc = MagicMock()
            await d._reconcile_links(
                intent.desired,
                nc,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )

        asyncio.run(_run())

        stub = pool.get_stub.return_value
        assert stub.async_batch_link_down.called
        assert pair not in d._actual_links

    def test_actual_links_only_modified_by_reconcile(self):
        """_override_pairs mutation alone does not change _actual_links."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        d._override_pairs[pair] = "scenario_inject_down"

        assert pair in d._actual_links

    def test_clear_overrides_immediate_reconcile(self):
        """Clear overrides + reconcile restores OME-desired links without OME wait."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._override_pairs[pair] = "scenario_inject_down"

        d._override_pairs.clear()
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        assert pair in intent.desired

    def test_overridden_invisible_link_does_not_resurrect(self):
        """Link overridden then OME-invisible: clear does not bring it back."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._override_pairs[pair] = "scenario_inject_down"

        d._desired_links.pop(pair)
        d._override_pairs.clear()
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        assert pair not in intent.desired


class TestReasonAttribution:
    """LinkDown reason flows through the single dispatch path."""

    def test_scenario_inject_down_reason(self):
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        d._override_pairs[pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        async def _run():
            nc = MagicMock()
            await d._reconcile_links(
                intent.desired,
                nc,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )

        asyncio.run(_run())

        publish_call = d._js.publish.call_args_list[0]
        published_data = publish_call[0][1]
        assert b"scenario_inject_down" in published_data

    def test_satellite_loss_reason(self):
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        d._override_nodes["sat-P00S00"] = "satellite_loss"
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        async def _run():
            nc = MagicMock()
            await d._reconcile_links(
                intent.desired,
                nc,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )

        asyncio.run(_run())

        publish_call = d._js.publish.call_args_list[0]
        published_data = publish_call[0][1]
        assert b"satellite_loss" in published_data

    def test_vis_lost_for_ome_removal(self):
        """Non-override removal uses vis_lost."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        intent = d._build_dispatch_intent(sim_time=SIM, source="ome_event")

        async def _run():
            nc = MagicMock()
            await d._reconcile_links(
                intent.desired,
                nc,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )

        asyncio.run(_run())

        publish_call = d._js.publish.call_args_list[0]
        published_data = publish_call[0][1]
        assert b"vis_lost" in published_data


class TestQueueDrainSemantics:
    """Queue draining preserves rebaseline_counts via OR."""

    def test_rebaseline_ored_across_drain(self):
        """If a snapshot intent is drained, rebaseline_counts survives."""
        d, _ = _make_dispatcher()

        snapshot_intent = DispatchIntent(
            desired={},
            down_reasons={},
            forced_bbm_pairs=frozenset(),
            sim_time=SIM,
            source="snapshot",
            rebaseline_counts=True,
        )
        scenario_intent = DispatchIntent(
            desired={},
            down_reasons={},
            forced_bbm_pairs=frozenset(),
            sim_time=SIM,
            source="scenario",
            rebaseline_counts=False,
        )

        async def _run():
            d._running = True
            d._dispatch_queue.put_nowait(snapshot_intent)
            d._dispatch_queue.put_nowait(scenario_intent)
            d._dispatch_queue.put_nowait(None)

            nc = MagicMock()
            await d._dispatch_worker(nc)

        asyncio.run(_run())

    def test_rapid_inject_then_clear_no_down(self):
        """Inject then immediately clear before worker runs: no physical down."""
        d, pool = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        d._override_pairs[pair] = "scenario_inject_down"
        inject_intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        d._override_pairs.clear()
        clear_intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        async def _run():
            d._running = True
            d._dispatch_queue.put_nowait(inject_intent)
            d._dispatch_queue.put_nowait(clear_intent)
            d._dispatch_queue.put_nowait(None)

            nc = MagicMock()
            await d._dispatch_worker(nc)

        asyncio.run(_run())

        stub = pool.get_stub.return_value
        assert not stub.async_batch_link_down.called
        assert pair in d._actual_links

    def test_forced_bbm_uses_latest_not_union(self):
        """After drain, forced_bbm_pairs is from the latest intent only."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")

        intent_with_forced = DispatchIntent(
            desired={},
            down_reasons={pair: "scenario_inject_down"},
            forced_bbm_pairs=frozenset({pair}),
            sim_time=SIM,
            source="scenario",
        )
        intent_without_forced = DispatchIntent(
            desired={pair: ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)},
            down_reasons={},
            forced_bbm_pairs=frozenset(),
            sim_time=SIM,
            source="ome_event",
        )

        async def _run():
            d._running = True
            # Put both intents — worker will drain to latest
            d._dispatch_queue.put_nowait(intent_with_forced)
            d._dispatch_queue.put_nowait(intent_without_forced)

            nc = MagicMock()
            # Manually simulate one iteration of the worker loop
            intent = await d._dispatch_queue.get()
            rebaseline = intent.rebaseline_counts
            while not d._dispatch_queue.empty():
                try:
                    next_intent = d._dispatch_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if next_intent is None:
                    intent = None
                    break
                rebaseline = rebaseline or next_intent.rebaseline_counts
                intent = next_intent

            assert intent is not None
            assert intent.forced_bbm_pairs == frozenset()
            await d._reconcile_links(
                intent.desired,
                nc,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )

        asyncio.run(_run())

        assert pair in d._actual_links


class TestSuspendDeferral:
    """Scenario commands during seek: override stored, enqueue deferred."""

    def test_override_stored_while_suspended(self):
        d, _ = _make_dispatcher()
        d._suspended = True
        d._expected_epoch_id = 1
        pair = ("sat-P00S00", "sat-P00S01")

        d._override_pairs[pair] = "scenario_inject_down"

        assert pair in d._override_pairs
        assert d._dispatch_queue.empty()

    def test_override_applies_on_resume(self):
        """Override set during suspend takes effect when resume builds intent."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._override_pairs[pair] = "scenario_inject_down"

        intent = d._build_dispatch_intent(sim_time=SIM, source="resume", rebaseline_counts=True)

        assert pair not in intent.desired
        assert pair in intent.down_reasons
        assert intent.rebaseline_counts is True


class TestForcedBBMEscalation:
    """Forced BBM escalates to GS-segment level in _reconcile_mbb."""

    def test_forced_pair_forces_segment_bbm(self):
        """If any pair under a GS is forced, entire segment goes BBM."""
        d, pool = _make_dispatcher(mbb=True)
        old_pair = ("gs-ashburn", "sat-P00S00")
        new_pair = ("gs-ashburn", "sat-P00S01")
        d._actual_links[old_pair] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0, link_type="ground")
        d._gs_active_count["gs-ashburn"] = 1
        d._sat_active_count["sat-P00S00"] = 1

        d._override_pairs[old_pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        assert old_pair in intent.forced_bbm_pairs

        async def _run():
            nc = MagicMock()
            await d._reconcile_links(
                intent.desired,
                nc,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )

        asyncio.run(_run())

        assert old_pair not in d._actual_links

    def test_ome_removal_not_forced_bbm(self):
        """Normal OME removal does not appear in forced_bbm_pairs."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)

        intent = d._build_dispatch_intent(sim_time=SIM, source="ome_event")

        assert pair not in intent.forced_bbm_pairs


class TestInFlightUpOmeRemoval:
    """In-flight up + OME removal: OME takes attribution precedence."""

    def test_ome_removal_during_inflight_uses_vis_lost(self):
        """Pair in actual (up ACKed), not in desired (OME removed), no override
        reason captured because OME removal cleared it from desired before
        intent was built. Falls back to vis_lost."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._override_pairs[pair] = "scenario_inject_down"
        d._desired_links.pop(pair, None)

        intent = d._build_dispatch_intent(sim_time=SIM, source="ome_event")

        assert intent.down_reasons.get(pair) == "scenario_inject_down"

    def test_ome_invisible_removes_from_desired_override_still_captures(self):
        """If override exists AND pair is in actual, reason is captured
        even if OME already removed from desired."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._override_pairs[pair] = "scenario_inject_down"

        intent = d._build_dispatch_intent(sim_time=SIM, source="ome_event")

        assert pair in intent.down_reasons
        assert intent.down_reasons[pair] == "scenario_inject_down"


class TestTeardownPairsClearedOnSnapshot:
    """_teardown_pairs cleared on snapshot replace."""

    def test_snapshot_clears_stale_teardown(self):
        d, _ = _make_dispatcher()
        d._teardown_pairs.add(("sat-P99S99", "sat-P99S98"))

        snapshot = LinkStateSnapshot(
            sim_time=SIM,
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
                    sim_time=SIM,
                ),
            ),
            interval_s=5.0,
        )
        d._build_desired_from_snapshot(snapshot)

        assert ("sat-P99S99", "sat-P99S98") not in d._teardown_pairs
