"""Production queue behavior tests — the real quality gate.

Tests the dispatch worker, DispatchIntent, queue drain semantics,
forced BBM escalation, suspend/resume override deferral, reason
attribution, and _on_scenario_command callback.

These tests exercise the production code path (dispatch worker + queue),
NOT the _dispatch_batch test-compat method.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
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
OME_RANGE_KM = 1000.0


def _isl_info() -> ActiveLinkInfo:
    return ActiveLinkInfo(
        "isl0",
        "isl1",
        3.0,
        1000.0,
        link_type="isl",
        range_km=OME_RANGE_KM,
        authority_sim_time=SIM,
        authority_source="test",
    )


def _ground_info() -> ActiveLinkInfo:
    return ActiveLinkInfo(
        "term0",
        "gnd0",
        3.0,
        1000.0,
        link_type="ground",
        range_km=OME_RANGE_KM,
        authority_sim_time=SIM,
        authority_source="test",
    )


def _make_dispatcher(mbb=False):
    interface_map = {
        ("gs-ashburn", "sat-P00S00"): ("term0", "gnd0"),
        ("gs-ashburn", "sat-P00S01"): ("term0", "gnd0"),
        ("sat-P00S00", "sat-P00S01"): ("isl0", "isl1"),
    }
    bandwidth_map = dict.fromkeys(interface_map, 1000.0)

    loc = PodLocationMap()
    for pair in interface_map:
        for nid in pair:
            loc._node_of[nid] = "nodal"
    loc._agent_addrs["nodal"] = "nodal"

    pool = MagicMock()
    stub = MagicMock()

    def up_resp(req):
        return node_agent_pb2.BatchLinkUpResponse(
            success=True,
            error_message="",
            interfaces_upped=len(req.interfaces),
            apply_time_ms=0.0,
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

    def down_resp(req):
        return node_agent_pb2.BatchLinkDownResponse(
            success=True,
            error_message="",
            interfaces_downed=len(req.interfaces),
            apply_time_ms=0.0,
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

    stub.async_batch_link_up = AsyncMock(side_effect=up_resp)
    stub.async_batch_link_down = AsyncMock(side_effect=down_resp)
    stub.async_set_latency = AsyncMock(return_value=node_agent_pb2.SetLatencyResponse(success=True))
    pool.get_stub.return_value = stub

    d = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        session_id="test-session",
        wiring_generation="sha256:" + "a" * 64,
        max_latency_age_s=1.0,
        gs_terminal_capacities={"gs-ashburn": 2},
        sat_ground_terminal_capacities={"sat-P00S00": 1, "sat-P00S01": 1},
        mbb_dispatch=mbb,
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    return d, pool


async def _run_worker_with_intents(d, intents: list[DispatchIntent], nc=None):
    """Run the dispatch worker processing the given intents then stopping.

    Uses a task + sleep pattern: enqueue intents, let the worker process
    them, then stop the worker cleanly. This exercises the real worker
    loop including queue drain semantics.
    """
    if nc is None:
        nc = MagicMock()
    d._running = True
    for intent in intents:
        await d._dispatch_queue.put(intent)

    async def _stop_after_processing():
        while not d._dispatch_queue.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        d._running = False
        await d._dispatch_queue.put(None)

    worker = asyncio.create_task(d._dispatch_worker(nc))
    stopper = asyncio.create_task(_stop_after_processing())
    await asyncio.gather(worker, stopper)


class TestDispatchWorkerReconcile:
    """Dispatch worker processes queued intents through _reconcile_links."""

    def test_worker_processes_queued_intent_and_dispatches_down(self):
        """Worker dequeues intent, calls _reconcile_links, Node Agent gets BatchLinkDown."""
        d, pool = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()
        d._actual_links[pair] = _isl_info()

        d._override_pairs[pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        asyncio.run(_run_worker_with_intents(d, [intent]))

        stub = pool.get_stub.return_value
        assert stub.async_batch_link_down.called
        assert pair not in d._actual_links

    def test_worker_processes_queued_intent_and_dispatches_up(self):
        """Worker dequeues intent with new desired pair, calls BatchLinkUp."""
        d, pool = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()

        intent = d._build_dispatch_intent(sim_time=SIM, source="ome_event")

        asyncio.run(_run_worker_with_intents(d, [intent]))

        stub = pool.get_stub.return_value
        assert stub.async_batch_link_up.called
        assert pair in d._actual_links

    def test_actual_links_only_modified_by_reconcile(self):
        """_override_pairs mutation alone does not change _actual_links."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = _isl_info()

        d._override_pairs[pair] = "scenario_inject_down"

        assert pair in d._actual_links

    def test_clear_overrides_immediate_reconcile(self):
        """Clear overrides + reconcile restores OME-desired links without OME wait."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()
        d._override_pairs[pair] = "scenario_inject_down"

        d._override_pairs.clear()
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        assert pair in intent.desired

    def test_overridden_invisible_link_does_not_resurrect(self):
        """Link overridden then OME-invisible: clear does not bring it back."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()
        d._override_pairs[pair] = "scenario_inject_down"

        d._desired_links.pop(pair)
        d._override_pairs.clear()
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        assert pair not in intent.desired


class TestReasonAttribution:
    """LinkDown reason flows through the single dispatch path via worker."""

    def test_scenario_inject_down_reason_through_worker(self):
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()
        d._actual_links[pair] = _isl_info()

        d._override_pairs[pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        asyncio.run(_run_worker_with_intents(d, [intent]))

        publish_call = d._js.publish.call_args_list[0]
        published_data = publish_call[0][1]
        assert b"scenario_inject_down" in published_data

    def test_satellite_loss_reason_through_worker(self):
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()
        d._actual_links[pair] = _isl_info()

        d._override_nodes["sat-P00S00"] = "satellite_loss"
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        asyncio.run(_run_worker_with_intents(d, [intent]))

        publish_call = d._js.publish.call_args_list[0]
        published_data = publish_call[0][1]
        assert b"satellite_loss" in published_data

    def test_vis_lost_for_ome_removal_through_worker(self):
        """Non-override removal uses vis_lost."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = _isl_info()

        intent = d._build_dispatch_intent(sim_time=SIM, source="ome_event")

        asyncio.run(_run_worker_with_intents(d, [intent]))

        publish_call = d._js.publish.call_args_list[0]
        published_data = publish_call[0][1]
        assert b"vis_lost" in published_data


class TestQueueDrainSemantics:
    """Queue draining preserves rebaseline_counts via OR."""

    def test_rebaseline_ored_corrects_counters(self):
        """Snapshot intent drained by a later OME intent: counters still rebaselined."""
        d, _ = _make_dispatcher()
        pair = ("gs-ashburn", "sat-P00S00")
        d._actual_links[pair] = _ground_info()
        d._gs_active_count["gs-ashburn"] = 99
        d._sat_active_count["sat-P00S00"] = 99

        snapshot_intent = DispatchIntent(
            desired={pair: _ground_info()},
            down_reasons={},
            forced_bbm_pairs=frozenset(),
            sim_time=SIM,
            source="snapshot",
            rebaseline_counts=True,
        )
        ome_intent = DispatchIntent(
            desired={pair: _ground_info()},
            down_reasons={},
            forced_bbm_pairs=frozenset(),
            sim_time=SIM,
            source="ome_event",
            rebaseline_counts=False,
        )

        asyncio.run(_run_worker_with_intents(d, [snapshot_intent, ome_intent]))

        assert d._gs_active_count["gs-ashburn"] == 1
        assert d._sat_active_count["sat-P00S00"] == 1

    def test_rapid_inject_then_clear_no_down(self):
        """Inject then immediately clear before worker runs: no physical down."""
        d, pool = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()
        d._actual_links[pair] = _isl_info()

        d._override_pairs[pair] = "scenario_inject_down"
        inject_intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        d._override_pairs.clear()
        clear_intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        asyncio.run(_run_worker_with_intents(d, [inject_intent, clear_intent]))

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
            desired={pair: _isl_info()},
            down_reasons={},
            forced_bbm_pairs=frozenset(),
            sim_time=SIM,
            source="ome_event",
        )

        asyncio.run(_run_worker_with_intents(d, [intent_with_forced, intent_without_forced]))

        assert pair in d._actual_links


class TestOnScenarioCommand:
    """_on_scenario_command: parse → normalize → mutate → enqueue/suspend → respond."""

    def _make_msg(self, data: dict) -> MagicMock:
        msg = MagicMock()
        msg.data = json.dumps(data).encode()
        msg.respond = AsyncMock()
        return msg

    def test_inject_link_down_mutates_and_enqueues(self):
        d, _ = _make_dispatcher()
        d._current_sim_time = SIM
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()

        msg = self._make_msg(
            {"action": "inject_link_down", "node_a": "sat-P00S01", "node_b": "sat-P00S00"}
        )

        asyncio.run(d._on_scenario_command(msg))

        canonical = ("sat-P00S00", "sat-P00S01")
        assert canonical in d._override_pairs
        assert d._override_pairs[canonical] == "scenario_inject_down"
        assert not d._dispatch_queue.empty()
        msg.respond.assert_called_once()
        resp = json.loads(msg.respond.call_args[0][0])
        assert resp["status"] == "accepted"

    def test_inject_satellite_loss_mutates_override_nodes(self):
        d, _ = _make_dispatcher()
        d._current_sim_time = SIM

        msg = self._make_msg({"action": "inject_satellite_loss", "node": "sat-P00S00"})
        asyncio.run(d._on_scenario_command(msg))

        assert "sat-P00S00" in d._override_nodes
        assert d._override_nodes["sat-P00S00"] == "satellite_loss"

    def test_release_link_override_removes_pair(self):
        d, _ = _make_dispatcher()
        d._current_sim_time = SIM
        pair = ("sat-P00S00", "sat-P00S01")
        d._override_pairs[pair] = "scenario_inject_down"

        msg = self._make_msg(
            {"action": "inject_link_up", "node_a": "sat-P00S00", "node_b": "sat-P00S01"}
        )
        asyncio.run(d._on_scenario_command(msg))

        assert pair not in d._override_pairs

    def test_restore_satellite_removes_node(self):
        d, _ = _make_dispatcher()
        d._current_sim_time = SIM
        d._override_nodes["sat-P00S00"] = "satellite_loss"

        msg = self._make_msg({"action": "restore_satellite", "node": "sat-P00S00"})
        asyncio.run(d._on_scenario_command(msg))

        assert "sat-P00S00" not in d._override_nodes

    def test_clear_overrides_clears_both_dicts(self):
        d, _ = _make_dispatcher()
        d._current_sim_time = SIM
        d._override_pairs[("sat-P00S00", "sat-P00S01")] = "scenario_inject_down"
        d._override_nodes["sat-P00S00"] = "satellite_loss"

        msg = self._make_msg({"action": "clear_overrides"})
        asyncio.run(d._on_scenario_command(msg))

        assert len(d._override_pairs) == 0
        assert len(d._override_nodes) == 0

    def test_suspended_stores_override_but_no_enqueue(self):
        d, _ = _make_dispatcher()
        d._suspended = True
        d._expected_epoch_id = 1
        pair = ("sat-P00S00", "sat-P00S01")

        msg = self._make_msg(
            {"action": "inject_link_down", "node_a": "sat-P00S00", "node_b": "sat-P00S01"}
        )
        asyncio.run(d._on_scenario_command(msg))

        assert pair in d._override_pairs
        assert d._dispatch_queue.empty()
        resp = json.loads(msg.respond.call_args[0][0])
        assert resp["status"] == "accepted"
        assert "suspended" in resp.get("note", "")

    def test_malformed_command_returns_error(self):
        d, _ = _make_dispatcher()
        d._current_sim_time = SIM

        msg = self._make_msg({"action": "bogus_action"})
        asyncio.run(d._on_scenario_command(msg))

        resp = json.loads(msg.respond.call_args[0][0])
        assert resp["status"] == "error"
        assert d._dispatch_queue.empty()

    def test_unknown_scenario_node_rejected_without_mutation_or_enqueue(self):
        d, _ = _make_dispatcher()
        d._current_sim_time = SIM

        msg = self._make_msg(
            {
                "action": "inject_link_down",
                "node_a": "space-sat-p99s99",
                "node_b": "sat-P00S00",
            }
        )
        asyncio.run(d._on_scenario_command(msg))

        resp = json.loads(msg.respond.call_args[0][0])
        assert resp["status"] == "error"
        assert "space-sat-p99s99" in resp["msg"]
        assert d._override_pairs == {}
        assert d._override_nodes == {}
        assert d._dispatch_queue.empty()

    def test_command_without_ome_sim_time_fails_loudly(self):
        d, _ = _make_dispatcher()

        msg = self._make_msg(
            {"action": "inject_link_down", "node_a": "sat-P00S00", "node_b": "sat-P00S01"}
        )

        with pytest.raises(RuntimeError, match="before receiving OME simulation time"):
            asyncio.run(d._on_scenario_command(msg))

        assert d._dispatch_queue.empty()
        assert d._override_pairs == {}
        assert d._override_nodes == {}

    def test_pair_normalization(self):
        """Pairs are normalized to (min, max) regardless of command order."""
        d, _ = _make_dispatcher()
        d._current_sim_time = SIM

        msg = self._make_msg(
            {"action": "inject_link_down", "node_a": "sat-P00S01", "node_b": "sat-P00S00"}
        )
        asyncio.run(d._on_scenario_command(msg))

        assert ("sat-P00S00", "sat-P00S01") in d._override_pairs
        assert ("sat-P00S01", "sat-P00S00") not in d._override_pairs


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
        d._desired_links[pair] = _isl_info()
        d._override_pairs[pair] = "scenario_inject_down"

        intent = d._build_dispatch_intent(sim_time=SIM, source="resume", rebaseline_counts=True)

        assert pair not in intent.desired
        assert pair in intent.down_reasons
        assert intent.rebaseline_counts is True


class TestForcedBBMEscalation:
    """Forced BBM escalates to GS-segment level in _reconcile_mbb."""

    def test_forced_pair_forces_segment_bbm_through_worker(self):
        """Worker processes intent with forced pair → GS segment goes BBM."""
        d, pool = _make_dispatcher(mbb=True)
        old_pair = ("gs-ashburn", "sat-P00S00")
        d._actual_links[old_pair] = _ground_info()
        d._gs_active_count["gs-ashburn"] = 1
        d._sat_active_count["sat-P00S00"] = 1

        d._override_pairs[old_pair] = "scenario_inject_down"
        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        assert old_pair in intent.forced_bbm_pairs

        asyncio.run(_run_worker_with_intents(d, [intent]))

        assert old_pair not in d._actual_links
        stub = pool.get_stub.return_value
        assert stub.async_batch_link_down.called

    def test_ome_removal_not_forced_bbm(self):
        """Normal OME removal does not appear in forced_bbm_pairs."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = _isl_info()

        intent = d._build_dispatch_intent(sim_time=SIM, source="ome_event")

        assert pair not in intent.forced_bbm_pairs


class TestInFlightUpOmeRemoval:
    """In-flight up + OME removal: OME takes attribution precedence."""

    def test_override_reason_captured_from_actual(self):
        """Pair in actual + override → reason captured even if not in desired."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._actual_links[pair] = _isl_info()
        d._override_pairs[pair] = "scenario_inject_down"

        intent = d._build_dispatch_intent(sim_time=SIM, source="ome_event")

        assert pair in intent.down_reasons
        assert intent.down_reasons[pair] == "scenario_inject_down"

    def test_override_reason_captured_from_desired(self):
        """Pair in desired (not yet in actual) + override → reason captured."""
        d, _ = _make_dispatcher()
        pair = ("sat-P00S00", "sat-P00S01")
        d._desired_links[pair] = _isl_info()
        d._override_pairs[pair] = "scenario_inject_down"

        intent = d._build_dispatch_intent(sim_time=SIM, source="scenario")

        assert pair in intent.down_reasons


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
                    range_km=OME_RANGE_KM,
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
