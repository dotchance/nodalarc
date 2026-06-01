# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Phase 6 local foundation proof tests.

These tests cover the deterministic/local parts of Phase 6. They do not claim
C-J zero-loss acceptance; that remains a real-pod run with retained evidence.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from time import perf_counter
from unittest.mock import AsyncMock, MagicMock

from nodalarc.models.events import VisibilityEvent
from scheduler.actuation import (
    ActuationFailureClass,
    ActuationResult,
    AgentCommandResult,
    PairActuationResult,
)
from scheduler.dispatcher import ActiveLinkInfo, Dispatcher
from scheduler.pod_locator import PodLocationMap

BASE = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
OLD = ("gs-multi", "sat-old")
NEW = ("gs-multi", "sat-new")
ACK_TO_ACTUAL_REFERENCE_SLA_MS = 1200.0


def _info(pair: tuple[str, str]) -> ActiveLinkInfo:
    return ActiveLinkInfo(
        interface_a="term0" if pair == OLD else "term1",
        interface_b="gnd0",
        latency_ms=1.0,
        bandwidth_mbps=100.0,
        link_type="ground",
        range_km=100.0,
        authority_sim_time=BASE,
        authority_source="phase6-test",
    )


def _dispatcher(*, now=None) -> Dispatcher:
    interface_map = {
        OLD: ("term0", "gnd0"),
        NEW: ("term1", "gnd0"),
    }
    loc = PodLocationMap()
    for pair in interface_map:
        for node_id in pair:
            loc._node_of[node_id] = "node-a"
    loc._agent_addrs["node-a"] = "127.0.0.1:50100"
    pool = MagicMock()
    d = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=dict.fromkeys(interface_map, 100.0),
        pod_locator=loc,
        agent_pool=pool,
        session_id="phase6-proof",
        wiring_generation="sha256:" + "6" * 64,
        max_latency_age_s=1.0,
        gs_terminal_capacities={"gs-multi": 2},
        sat_ground_terminal_capacities={"sat-old": 1, "sat-new": 1},
        clean_kernel_audit_interval_s=60.0,
        now=now,
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    return d


def _vis(
    pair: tuple[str, str],
    *,
    sim_time: datetime,
    visible: bool,
    scheduled: bool,
    reject_reason: str,
    unscheduled_reason: str | None = None,
) -> VisibilityEvent:
    return VisibilityEvent(
        sim_time=sim_time,
        node_a=pair[0],
        node_b=pair[1],
        visible=visible,
        scheduled=scheduled,
        range_km=100.0,
        latency_ms=1.0,
        elevation_deg=50.0 if visible else 0.0,
        terminal_type="rf",
        link_type="ground",
        gs_terminal_index=0 if visible and scheduled else None,
        sat_terminal_index=0 if visible and scheduled else None,
        scheduling_state="active",
        visibility_reject_reason=reject_reason,
        unscheduled_reason=unscheduled_reason,
    )


def _success(pair: tuple[str, str], *, operation: str) -> ActuationResult:
    ack = ("agent-a", pair[0], "term0" if pair == OLD else "term1")
    pair_result = PairActuationResult(
        pair=pair,
        link_type="ground",
        gs_id="gs-multi",
        expected_ifaces=frozenset({ack}),
        successful_ifaces=frozenset({ack}),
        failure_class=ActuationFailureClass.NONE,
    )
    agent = AgentCommandResult(
        agent_addr="agent-a",
        operation=operation,
        requested=((pair[0], ack[2]),),
        success_acks=frozenset({ack}),
        failure_class=ActuationFailureClass.NONE,
        dirty_kernel=False,
        unknown_outcome=False,
        fence_failure=False,
        details={"operation": operation, "pair": list(pair)},
    )
    return ActuationResult(
        operation=operation,
        requested_pairs=frozenset({pair}),
        succeeded_pairs=frozenset({pair}),
        failed_pairs=frozenset(),
        pair_results={pair: pair_result},
        agent_results=(agent,),
    )


def test_p6_d_los_loss_queues_linkdown_intent_at_the_loss_tick() -> None:
    """C-D sim-tick bound: LOS loss becomes a LinkDown intent for that tick.

    This drives the production visibility/clock batching handlers. The assertion is
    not based on a sleep: the down intent is queued when the next clock tick closes
    the loss tick, and its sim_time is exactly the loss event time.
    """
    d = _dispatcher()
    t0 = BASE
    t1 = BASE + timedelta(seconds=2)

    async def drive() -> None:
        await d._handle_visibility_event(
            _vis(OLD, sim_time=t0, visible=True, scheduled=True, reject_reason="ok")
        )
        await d._handle_clock_tick_payload(
            {"sim_time": (t0 + timedelta(seconds=1)).isoformat(), "epoch_id": 0}
        )
        up_intent = d._dispatch_queue.get_nowait()
        assert OLD in up_intent.desired

        await d._handle_visibility_event(
            _vis(
                OLD,
                sim_time=t1,
                visible=False,
                scheduled=False,
                reject_reason="range_exceeded",
            )
        )
        await d._handle_clock_tick_payload(
            {"sim_time": (t1 + timedelta(seconds=1)).isoformat(), "epoch_id": 0}
        )

    asyncio.run(drive())

    down_intent = d._dispatch_queue.get_nowait()
    assert down_intent.source == "ome_event"
    assert down_intent.sim_time == t1
    assert OLD not in down_intent.desired
    assert (down_intent.sim_time - t1).total_seconds() == 0.0


def test_p6_d_linkdown_ack_is_awaited_before_actual_links_pop() -> None:
    """C-D wall-clock SLA: ACK is the boundary for `_actual_links` removal."""
    d = _dispatcher()
    d._actual_links[OLD] = _info(OLD)
    observed: dict[str, bool] = {}

    async def down(pairs, sim_iso, sim_time, nc, down_reasons):
        assert pairs == {OLD}
        observed["actual_still_present_while_rpc_inflight"] = OLD in d._actual_links
        return _success(OLD, operation="BatchLinkDown")

    d._send_batch_down = down
    start = perf_counter()
    asyncio.run(d._reconcile_links({}, None, BASE))
    elapsed_ms = (perf_counter() - start) * 1000.0

    assert observed["actual_still_present_while_rpc_inflight"] is True
    assert OLD not in d._actual_links
    assert elapsed_ms < ACK_TO_ACTUAL_REFERENCE_SLA_MS


def _drain_dispatch_queue(
    d: Dispatcher, ops: list[tuple[str, tuple[tuple[str, str], ...]]]
) -> None:
    while True:
        try:
            intent = d._dispatch_queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        asyncio.run(
            d._reconcile_links(
                intent.desired,
                None,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )
        )


def _run_fixed_replay(*, now_base: datetime) -> dict[str, object]:
    now = {"value": now_base}
    d = _dispatcher(now=lambda: now["value"])
    ops: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    async def up(pairs, desired, sim_iso, sim_time, nc):
        ordered = tuple(sorted(pairs))
        ops.append(("up", ordered))
        pair = ordered[0]
        return _success(pair, operation="BatchLinkUp")

    async def down(pairs, sim_iso, sim_time, nc, down_reasons):
        ordered = tuple(sorted(pairs))
        ops.append(("down", ordered))
        pair = ordered[0]
        return _success(pair, operation="BatchLinkDown")

    d._send_batch_up = up
    d._send_batch_down = down

    async def drive_inputs() -> None:
        await d._handle_visibility_event(
            _vis(OLD, sim_time=BASE, visible=True, scheduled=True, reject_reason="ok")
        )
        await d._handle_clock_tick_payload(
            {"sim_time": (BASE + timedelta(seconds=1)).isoformat(), "epoch_id": 0}
        )

    asyncio.run(drive_inputs())
    _drain_dispatch_queue(d, ops)

    async def drive_handover() -> None:
        t2 = BASE + timedelta(seconds=2)
        await d._handle_visibility_event(
            _vis(OLD, sim_time=t2, visible=False, scheduled=False, reject_reason="range_exceeded")
        )
        await d._handle_visibility_event(
            _vis(NEW, sim_time=t2, visible=True, scheduled=True, reject_reason="ok")
        )
        await d._handle_clock_tick_payload(
            {"sim_time": (t2 + timedelta(seconds=1)).isoformat(), "epoch_id": 0}
        )

    now["value"] = now_base + timedelta(seconds=30)
    asyncio.run(drive_handover())
    _drain_dispatch_queue(d, ops)

    return {
        "desired": tuple(sorted(d._desired_links)),
        "actual": tuple(sorted(d._actual_links)),
        "ops": tuple(ops),
        "pending": tuple(sorted(d._pending_since)),
        "verify_count": 0,
    }


def test_p6_h_fixed_scheduler_replay_is_wall_clock_independent() -> None:
    """C-H layer 1: identical inputs replay to the same state and dispatch order.

    The two runs use different injected wall-clock bases. Equality here proves the
    scheduling/dispatch sequence is a pure function of the captured control-plane
    inputs plus ACK script, not the process wall clock.
    """
    first = _run_fixed_replay(now_base=datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC))
    second = _run_fixed_replay(now_base=datetime(2031, 1, 1, 0, 0, 0, tzinfo=UTC))

    assert first == second
    assert first["ops"] == (
        ("up", (OLD,)),
        ("down", (OLD,)),
        ("up", (NEW,)),
    )
    assert first["actual"] == (NEW,)
    assert first["desired"] == (NEW,)
