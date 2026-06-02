# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Phase 5 Scheduler actuation trust contracts."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from nodalarc.models.scheduler_ops import (
    ActualLinkSnapshot,
    OperatorRepairCommand,
    SchedulerOpsCode,
)
from nodalarc.nats_channels import actual_links_subject, actuation_state_subject
from nodalarc.proto import node_agent_pb2
from scheduler.actuation import (
    ActuationFailureClass,
    ActuationResult,
    AgentCommandResult,
    GroundActuationState,
    GroundActuationStateName,
    PairActuationResult,
    RecoveryStatus,
    build_actuation_result,
    classify_agent_response,
)
from scheduler.dispatcher import ActiveLinkInfo

from tests.unit.test_scheduler_authority_invariant import _make_dispatcher_with_two_terminal_gs

SIM_TIME = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


def _info(interface_a: str = "term0", interface_b: str = "gnd0") -> ActiveLinkInfo:
    return ActiveLinkInfo(
        interface_a=interface_a,
        interface_b=interface_b,
        latency_ms=1.0,
        bandwidth_mbps=100.0,
        link_type="ground",
        range_km=100.0,
        authority_sim_time=SIM_TIME,
        authority_source="test",
    )


def _agent_result(
    failure: ActuationFailureClass,
    *,
    operation: str = "BatchLinkUp",
    success_acks: frozenset[tuple[str, str, str]] = frozenset(),
) -> AgentCommandResult:
    return AgentCommandResult(
        agent_addr="agent-a",
        operation=operation,
        requested=(("gs-multi", "term0"),),
        success_acks=success_acks,
        failure_class=failure,
        dirty_kernel=failure
        in {ActuationFailureClass.GROUND_KERNEL_DIRTY, ActuationFailureClass.GROUND_UNKNOWN},
        unknown_outcome=failure == ActuationFailureClass.GROUND_UNKNOWN,
        fence_failure=failure == ActuationFailureClass.FENCE,
        details={"agent_addr": "agent-a", "failure_class": failure.value},
    )


def _actuation_result(
    *,
    pair: tuple[str, str],
    link_type: str,
    gs_id: str | None,
    failure: ActuationFailureClass,
    operation: str = "BatchLinkUp",
) -> ActuationResult:
    pair_result = PairActuationResult(
        pair=pair,
        link_type=link_type,
        gs_id=gs_id,
        expected_ifaces=frozenset({("agent-a", pair[0], "term0")}),
        successful_ifaces=frozenset(),
        failure_class=failure,
    )
    return ActuationResult(
        operation=operation,
        requested_pairs=frozenset({pair}),
        succeeded_pairs=frozenset(),
        failed_pairs=frozenset({pair}),
        pair_results={pair: pair_result},
        agent_results=(_agent_result(failure, operation=operation),),
    )


def _success_result(
    *,
    pair: tuple[str, str],
    operation: str,
    link_type: str = "ground",
    gs_id: str | None = "gs-multi",
) -> ActuationResult:
    ack = ("agent-a", pair[0], "term0")
    pair_result = PairActuationResult(
        pair=pair,
        link_type=link_type,
        gs_id=gs_id,
        expected_ifaces=frozenset({ack}),
        successful_ifaces=frozenset({ack}),
        failure_class=ActuationFailureClass.NONE,
    )
    return ActuationResult(
        operation=operation,
        requested_pairs=frozenset({pair}),
        succeeded_pairs=frozenset({pair}),
        failed_pairs=frozenset(),
        pair_results={pair: pair_result},
        agent_results=(
            _agent_result(
                ActuationFailureClass.NONE,
                operation=operation,
                success_acks=frozenset({ack}),
            ),
        ),
    )


def _requested_iface(node_id: str = "gs-multi", interface_name: str = "term0"):
    return node_agent_pb2.InterfaceUp(node_id=node_id, interface_name=interface_name)


def _iface_result(
    *,
    node_id: str = "gs-multi",
    interface_name: str = "term0",
    success: bool,
    verified: bool = False,
    dirty_kernel: bool = False,
    error_code: int = node_agent_pb2.NODE_AGENT_ERROR_UNSPECIFIED,
):
    return node_agent_pb2.InterfaceResult(
        node_id=node_id,
        interface_name=interface_name,
        success=success,
        verified=verified,
        dirty_kernel=dirty_kernel,
        error_code=error_code,
        error_message="boom" if not success else "",
    )


def test_classifier_precedence_covers_dirty_unverified_unknown_clean_and_fence() -> None:
    requested = [_requested_iface()]

    fence = classify_agent_response(
        result=node_agent_pb2.BatchLinkUpResponse(
            success=False,
            dirty_kernel=True,
            error_code=node_agent_pb2.NODE_AGENT_STALE_GENERATION,
            interface_results=[
                _iface_result(
                    success=False,
                    dirty_kernel=True,
                    error_code=node_agent_pb2.NODE_AGENT_STALE_GENERATION,
                )
            ],
        ),
        requested_interfaces=requested,
        agent_addr="agent-a",
        operation="BatchLinkUp",
    )
    assert fence.failure_class == ActuationFailureClass.FENCE
    assert fence.fence_failure is True

    dirty = classify_agent_response(
        result=node_agent_pb2.BatchLinkUpResponse(
            success=False,
            dirty_kernel=True,
            interface_results=[_iface_result(success=False, dirty_kernel=True)],
        ),
        requested_interfaces=requested,
        agent_addr="agent-a",
        operation="BatchLinkUp",
    )
    assert dirty.failure_class == ActuationFailureClass.GROUND_KERNEL_DIRTY

    unverified = classify_agent_response(
        result=node_agent_pb2.BatchLinkUpResponse(
            success=True,
            interface_results=[_iface_result(success=True, verified=False)],
        ),
        requested_interfaces=requested,
        agent_addr="agent-a",
        operation="BatchLinkUp",
    )
    assert unverified.failure_class == ActuationFailureClass.GROUND_KERNEL_DIRTY
    assert unverified.dirty_kernel is True

    unknown = classify_agent_response(
        result=node_agent_pb2.BatchLinkUpResponse(
            success=True,
            interface_results=[
                _iface_result(
                    node_id="gs-other", interface_name="term9", success=True, verified=True
                )
            ],
        ),
        requested_interfaces=requested,
        agent_addr="agent-a",
        operation="BatchLinkUp",
    )
    assert unknown.failure_class == ActuationFailureClass.GROUND_UNKNOWN
    assert unknown.unknown_outcome is True
    assert unknown.dirty_kernel is True

    clean = classify_agent_response(
        result=node_agent_pb2.BatchLinkUpResponse(
            success=False,
            interface_results=[_iface_result(success=False, verified=False)],
        ),
        requested_interfaces=requested,
        agent_addr="agent-a",
        operation="BatchLinkUp",
    )
    assert clean.failure_class == ActuationFailureClass.GROUND_CLEAN_FAILURE
    assert clean.dirty_kernel is False


def test_build_actuation_result_promotes_any_isl_failure_to_halt_class() -> None:
    pair = ("sat-a", "sat-b")
    agent = AgentCommandResult(
        agent_addr="agent-a",
        operation="BatchLinkUp",
        requested=(("sat-a", "isl0"),),
        success_acks=frozenset(),
        failure_class=ActuationFailureClass.GROUND_CLEAN_FAILURE,
        dirty_kernel=False,
        unknown_outcome=False,
        fence_failure=False,
        details={},
    )

    result = build_actuation_result(
        operation="BatchLinkUp",
        requested_pairs={pair},
        pair_agent_ifaces={pair: {("agent-a", "sat-a", "isl0")}},
        pair_link_type={pair: "isl"},
        pair_gs_id={pair: None},
        agent_results=[agent],
    )

    assert result.pair_results[pair].failure_class == ActuationFailureClass.ISL_FAILURE
    assert result.failed_pairs == frozenset({pair})


def test_ground_dirty_failure_marks_only_that_gs_nonclean() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    d._running = True
    d._js.publish = AsyncMock()
    result = _actuation_result(
        pair=("gs-multi", "sat-new"),
        link_type="ground",
        gs_id="gs-multi",
        failure=ActuationFailureClass.GROUND_KERNEL_DIRTY,
    )

    asyncio.run(
        d._handle_actuation_result(result, sim_time=SIM_TIME, operation_context="replacement_up")
    )

    assert d._running is True
    state = d._gs_actuation["gs-multi"]
    assert state.state == GroundActuationStateName.KERNEL_DIRTY
    assert state.reason_code == SchedulerOpsCode.REPLACEMENT_LINK_UP_FAILED
    assert state.recovery.next_verify_after is not None


def test_isl_failure_halts_scheduler_instead_of_degrading_per_gs() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    d._running = True
    d._js.publish = AsyncMock()
    result = _actuation_result(
        pair=("sat-a", "sat-b"),
        link_type="isl",
        gs_id=None,
        failure=ActuationFailureClass.ISL_FAILURE,
    )

    with pytest.raises(RuntimeError, match="Fatal actuation failure"):
        asyncio.run(
            d._handle_actuation_result(result, sim_time=SIM_TIME, operation_context="isl_up")
        )

    assert d._running is False
    assert d._dispatch_blocked_reason.startswith("Fatal actuation failure")


def test_blocked_gs_suppresses_new_ground_up_but_not_isl() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
    )

    allowed = d._filter_blocked_ground_mutations(
        {("gs-multi", "sat-new"), ("sat-a", "sat-b")},
        operation="BatchLinkUp",
    )

    assert ("gs-multi", "sat-new") not in allowed
    assert ("sat-a", "sat-b") in allowed


def test_ground_latency_failure_degrades_per_gs_without_halting_scheduler() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    d._running = True
    d._js.publish = AsyncMock()
    result = _actuation_result(
        pair=("gs-multi", "sat-old"),
        link_type="ground",
        gs_id="gs-multi",
        failure=ActuationFailureClass.GROUND_CLEAN_FAILURE,
        operation="SetLatency",
    )

    asyncio.run(d._handle_actuation_result(result, sim_time=SIM_TIME, operation_context="latency"))

    assert d._running is True
    state = d._gs_actuation["gs-multi"]
    assert state.state == GroundActuationStateName.ACTUATION_BLOCKED
    assert state.reason_code == SchedulerOpsCode.GROUND_LATENCY_UPDATE_FAILED


def test_reconcile_does_not_auto_down_kernel_dirty_or_repairing_ground_station() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._send_batch_down = AsyncMock()
    d._js.publish = AsyncMock()

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
    )
    asyncio.run(d._reconcile_links({}, None, SIM_TIME))
    d._send_batch_down.assert_not_called()
    assert pair in d._actual_links

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.ACTUATION_BLOCKED,
        reason_code=SchedulerOpsCode.ACTUATION_BLOCKED,
        recovery=RecoveryStatus(active_intervention_id="repair-1"),
    )
    asyncio.run(d._reconcile_links({}, None, SIM_TIME))
    d._send_batch_down.assert_not_called()
    assert pair in d._actual_links


def test_reconcile_allows_cleanup_down_for_clean_actuation_blocked_ground_station() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.ACTUATION_BLOCKED,
        reason_code=SchedulerOpsCode.ACTUATION_BLOCKED,
    )
    d._send_batch_down = AsyncMock(
        return_value=_success_result(pair=pair, operation="BatchLinkDown")
    )
    d._js.publish = AsyncMock()

    asyncio.run(d._reconcile_links({}, None, SIM_TIME))

    d._send_batch_down.assert_awaited_once()
    assert pair not in d._actual_links


def _actual_link_snapshots(publish_mock: AsyncMock) -> list[ActualLinkSnapshot]:
    """Parse every ActualLinkSnapshot the dispatcher published to its .actual. subject."""
    out: list[ActualLinkSnapshot] = []
    for call in publish_mock.await_args_list:
        subject = call.args[0] if call.args else call.kwargs.get("subject")
        payload = call.args[1] if len(call.args) > 1 else call.kwargs.get("payload")
        if subject and ".actual." in subject:
            out.append(ActualLinkSnapshot.model_validate_json(payload))
    return out


def _published_ops_codes(publish_mock: AsyncMock) -> list[str]:
    codes: list[str] = []
    for call in publish_mock.await_args_list:
        subject = call.args[0] if call.args else call.kwargs.get("subject")
        payload = call.args[1] if len(call.args) > 1 else call.kwargs.get("payload")
        if not subject or ".ops." not in subject:
            continue
        data = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        code = data.get("code")
        if code:
            codes.append(code)
    return codes


def test_reconcile_publishes_recoverable_kernel_actual_on_membership_change() -> None:
    # The link-explainability UX reads kernel_up from the Scheduler's _actual_links,
    # recovered from a retained per-instance subject — LinkUp/LinkDown are NEW and do
    # not survive a VS-API resubscribe. A reconcile that changes membership must
    # publish that set, to its OWN per-instance subject, so a resubscribed VS-API can
    # tell a connected pair from a scheduled-but-unactuated one.
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._send_batch_down = AsyncMock(
        return_value=_success_result(pair=pair, operation="BatchLinkDown")
    )
    d._js.publish = AsyncMock()

    asyncio.run(d._reconcile_links({}, None, SIM_TIME))

    assert pair not in d._actual_links
    snaps = _actual_link_snapshots(d._js.publish)
    assert snaps, "expected an ActualLinkSnapshot on the retained .actual. subject"
    # Published to this instance's own keyed subject (multi-instance: union, no clobber).
    d._js.publish.assert_any_await(
        actual_links_subject(d._session_id, d._scheduler_instance_id),
        snaps[-1].model_dump_json().encode(),
    )
    latest = snaps[-1]
    assert latest.scheduler_instance_id == d._scheduler_instance_id
    assert latest.session_id == d._session_id
    # The torn-down pair is gone from the recoverable kernel-actual set.
    assert [pair[0], pair[1]] not in latest.active_pairs


def test_reconcile_does_not_republish_kernel_actual_when_membership_unchanged() -> None:
    # Edge-triggered, not a heartbeat: a no-op reconcile (desired == actual) must not
    # touch the retained kernel-actual subject, so a stable link never flickers in the
    # recovered set and the retained subject is not rewritten every tick.
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    info = _info()
    d._actual_links[pair] = info
    d._js.publish = AsyncMock()

    asyncio.run(d._reconcile_links({pair: info}, None, SIM_TIME))

    assert pair in d._actual_links
    assert _actual_link_snapshots(d._js.publish) == []


def test_recoverable_state_heartbeat_refreshes_actual_links_and_actuation_roster() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._js.publish = AsyncMock()

    asyncio.run(d._publish_recoverable_state_heartbeat(sim_time=SIM_TIME))

    subjects = [call.args[0] for call in d._js.publish.await_args_list]
    assert actual_links_subject(d._session_id, d._scheduler_instance_id) in subjects
    assert actuation_state_subject(d._session_id, "gs-multi") in subjects
    assert all(".ops." not in subject for subject in subjects)

    snaps = _actual_link_snapshots(d._js.publish)
    assert snaps[-1].active_pairs == [[pair[0], pair[1]]]
    actuation_payloads = [
        call.args[1]
        for call in d._js.publish.await_args_list
        if call.args[0] == actuation_state_subject(d._session_id, "gs-multi")
    ]
    assert actuation_payloads
    event = json.loads(actuation_payloads[-1].decode())
    assert event["code"] == "ACTUATION_CLEAN"
    assert event["level"] == "debug"
    assert event["details"]["actuation_state_after"] == "clean"


@pytest.mark.parametrize(
    ("state_name", "reason_code", "expected_code"),
    (
        (GroundActuationStateName.KERNEL_DIRTY, SchedulerOpsCode.KERNEL_DIRTY, "KERNEL_DIRTY"),
        (
            GroundActuationStateName.ACTUATION_BLOCKED,
            SchedulerOpsCode.ACTUATION_BLOCKED,
            "ACTUATION_BLOCKED",
        ),
    ),
)
def test_recoverable_state_heartbeat_refreshes_nonclean_actuation_roster(
    state_name: GroundActuationStateName,
    reason_code: SchedulerOpsCode,
    expected_code: str,
) -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=state_name,
        reason_code=reason_code,
        affected_pairs=frozenset({pair}),
        stale_pairs=frozenset({pair})
        if state_name == GroundActuationStateName.KERNEL_DIRTY
        else frozenset(),
    )
    d._js.publish = AsyncMock()

    asyncio.run(d._publish_recoverable_state_heartbeat(sim_time=SIM_TIME))

    subjects = [call.args[0] for call in d._js.publish.await_args_list]
    assert actual_links_subject(d._session_id, d._scheduler_instance_id) in subjects
    assert actuation_state_subject(d._session_id, "gs-multi") in subjects

    actuation_payloads = [
        call.args[1]
        for call in d._js.publish.await_args_list
        if call.args[0] == actuation_state_subject(d._session_id, "gs-multi")
    ]
    assert actuation_payloads
    event = json.loads(actuation_payloads[-1].decode())
    assert event["code"] == expected_code
    assert event["level"] == "debug"
    assert event["details"]["actuation_state_after"] == state_name.value
    assert event["details"]["reason"] == "recoverable state heartbeat"
    assert event["details"]["affected_pairs"] == [[pair[0], pair[1]]]


def test_recoverable_state_heartbeat_is_due_independent_of_clean_kernel_audit() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    d._clean_kernel_audit_interval_s = None
    d._recoverable_state_heartbeat_interval_s = 60.0
    d._last_recoverable_state_heartbeat_at = d._now() - timedelta(seconds=61)
    d._js.publish = AsyncMock()

    asyncio.run(d._run_due_kernel_verifications(sim_time=SIM_TIME))

    subjects = [call.args[0] for call in d._js.publish.await_args_list]
    assert actual_links_subject(d._session_id, d._scheduler_instance_id) in subjects
    assert actuation_state_subject(d._session_id, "gs-multi") in subjects


def test_reconcile_publishes_corrected_set_when_a_phase_fails_after_teardown() -> None:
    # Split-brain guard. A dispatch phase commits a membership change (tears down
    # old_pair) and a later phase raises a fatal failure (the real path:
    # _handle_actuation_result -> _halt_dispatcher -> RuntimeError). The publish is in a
    # finally, so the corrected kernel-actual set is still published before the raise
    # propagates — otherwise the retained snapshot lists the torn-down pair as up and
    # the explanation masks it as connected on a VS-API resubscribe. Without the
    # finally, no snapshot is published and this fails.
    d = _make_dispatcher_with_two_terminal_gs()
    d._mbb_dispatch = False
    old_pair = ("gs-multi", "sat-old")
    new_pair = ("gs-multi", "sat-new")
    d._actual_links[old_pair] = _info("term0")
    d._send_batch_down = AsyncMock(
        return_value=_success_result(pair=old_pair, operation="BatchLinkDown")
    )
    d._send_batch_up = AsyncMock(
        return_value=_success_result(pair=new_pair, operation="BatchLinkUp")
    )

    async def handle(result, *, sim_time, operation_context, intervention_id=None):
        # The down settles cleanly (pops old_pair); the up is fatal and raises.
        if "up" in operation_context:
            raise RuntimeError("fatal up failure mid-reconcile")

    d._handle_actuation_result = handle
    d._js.publish = AsyncMock()

    with pytest.raises(RuntimeError, match="fatal up"):
        asyncio.run(d._reconcile_links({new_pair: _info("term1")}, None, SIM_TIME))

    assert old_pair not in d._actual_links
    snaps = _actual_link_snapshots(d._js.publish)
    assert snaps, "a membership change committed before a halt-raise must still publish"
    assert [old_pair[0], old_pair[1]] not in snaps[-1].active_pairs


# --- #4: Scheduler-owned in_flight -> faulted divergence clock (pending_since) ----------

PENDING_TIME = datetime(2026, 5, 27, 12, 0, 30, tzinfo=UTC)


def _failed_up(pair: tuple[str, str]) -> ActuationResult:
    """An up that the Node Agent did NOT confirm — pair stays out of _actual_links."""
    return _actuation_result(
        pair=pair,
        link_type="ground",
        gs_id="gs-multi",
        failure=ActuationFailureClass.GROUND_CLEAN_FAILURE,
        operation="BatchLinkUp",
    )


def test_reconcile_stamps_and_publishes_pending_since_for_unactuated_desired_pair() -> None:
    # #4: the divergence clock is Scheduler-owned. A pair OME/Scheduler desire up but the
    # kernel never confirms must appear in pending_pairs with a Scheduler-stamped
    # pending_since (the actuation-window origin) plus emitted_at — VS-API derives the
    # divergence AGE from these, it does not own the timing.
    d = _make_dispatcher_with_two_terminal_gs()
    d._mbb_dispatch = False
    d._now = lambda: PENDING_TIME
    d._last_snapshot_epoch_id = 3
    d._last_snapshot_seq = 9021
    d._handle_actuation_result = AsyncMock()
    d._js.publish = AsyncMock()
    pair = ("gs-multi", "sat-new")
    d._send_batch_up = AsyncMock(return_value=_failed_up(pair))

    asyncio.run(d._reconcile_links({pair: _info("term1")}, None, SIM_TIME))

    assert pair not in d._actual_links  # kernel never confirmed it
    assert pair in d._pending_since
    latest = _actual_link_snapshots(d._js.publish)[-1]
    assert [pair[0], pair[1]] not in latest.active_pairs
    assert latest.emitted_at == PENDING_TIME
    pend = {tuple(p.pair): p for p in latest.pending_pairs}
    assert (pair[0], pair[1]) in pend
    rec = pend[(pair[0], pair[1])]
    assert rec.pending_since == PENDING_TIME
    assert rec.operation == "BatchLinkUp"
    assert rec.epoch_id == 3
    assert rec.snapshot_seq == 9021


def test_pending_since_clears_when_the_kernel_confirms_the_pair() -> None:
    # Convergence clears the clock: once the Node Agent proves the pair up it is connected,
    # not in_flight — it must leave pending_pairs and appear in active_pairs.
    d = _make_dispatcher_with_two_terminal_gs()
    d._mbb_dispatch = False
    d._handle_actuation_result = AsyncMock()
    d._js.publish = AsyncMock()
    pair = ("gs-multi", "sat-new")
    info = _info("term1")

    d._send_batch_up = AsyncMock(return_value=_failed_up(pair))
    asyncio.run(d._reconcile_links({pair: info}, None, SIM_TIME))
    assert pair in d._pending_since

    d._send_batch_up = AsyncMock(return_value=_success_result(pair=pair, operation="BatchLinkUp"))
    asyncio.run(d._reconcile_links({pair: info}, None, SIM_TIME))

    assert pair in d._actual_links
    assert pair not in d._pending_since
    latest = _actual_link_snapshots(d._js.publish)[-1]
    assert [pair[0], pair[1]] in latest.active_pairs
    assert all(tuple(p.pair) != pair for p in latest.pending_pairs)


def test_pending_since_clears_when_a_pair_leaves_desired_unactuated() -> None:
    # OME stops desiring a pair before it ever came up: not a fault, just gone. The clock
    # must clear so a never-actuated, no-longer-desired pair does not fault forever.
    d = _make_dispatcher_with_two_terminal_gs()
    d._mbb_dispatch = False
    d._handle_actuation_result = AsyncMock()
    d._js.publish = AsyncMock()
    pair = ("gs-multi", "sat-new")
    d._send_batch_up = AsyncMock(return_value=_failed_up(pair))

    asyncio.run(d._reconcile_links({pair: _info("term1")}, None, SIM_TIME))
    assert pair in d._pending_since

    asyncio.run(d._reconcile_links({}, None, SIM_TIME))
    assert pair not in d._pending_since


def test_pending_since_origin_is_stamped_once_across_stable_ticks() -> None:
    # The origin is the FIRST divergence instant, not the latest tick: a pair stuck
    # pending across ticks keeps its original pending_since so its age keeps growing.
    d = _make_dispatcher_with_two_terminal_gs()
    d._mbb_dispatch = False
    d._handle_actuation_result = AsyncMock()
    d._js.publish = AsyncMock()
    pair = ("gs-multi", "sat-new")
    info = _info("term1")
    d._send_batch_up = AsyncMock(return_value=_failed_up(pair))

    t1 = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 27, 12, 0, 5, tzinfo=UTC)
    d._now = lambda: t1
    asyncio.run(d._reconcile_links({pair: info}, None, SIM_TIME))
    d._now = lambda: t2
    asyncio.run(d._reconcile_links({pair: info}, None, SIM_TIME))

    assert d._pending_since[pair][0] == t1  # origin preserved, not advanced to t2


def test_seek_epoch_reset_clears_pending_since() -> None:
    # A seek voids the old epoch's desires; their pending_since must not survive into the
    # new epoch (it would carry a stale-epoch origin). _actual_links survives (actuator
    # truth); the first post-seek reconcile re-stamps any still-divergent pair.
    d = _make_dispatcher_with_two_terminal_gs()
    d._pending_since[("gs-multi", "sat-new")] = (SIM_TIME, 1, 5)
    d._reset_epoch_local_authority()
    assert d._pending_since == {}


def test_pending_since_excludes_operator_overridden_pairs() -> None:
    # Re-review #A: the divergence clock must be recomputed against EFFECTIVE desired (raw
    # minus operator overrides), the source the reconcile worker uses. A pair an operator
    # deliberately held down is not "in flight" — stamping it pending would flash it
    # faulted-red. Guards both writers of _pending_since against drifting onto raw desired.
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-new")
    d._desired_links = {pair: _info("term1")}
    d._override_pairs = {pair: "operator_down"}
    d._update_pending_since(d._effective_desired_links())
    assert pair not in d._pending_since
    # Sanity: lift the override and the same pair IS pending — proving the override is the
    # discriminator, not an unrelated filter.
    d._override_pairs = {}
    d._update_pending_since(d._effective_desired_links())
    assert pair in d._pending_since


def test_pending_clock_is_published_before_the_up_await() -> None:
    # Publish-before-await: a pair being brought up must have its pending_since stamped AND
    # published BEFORE the (possibly slow/hung) BatchLinkUp await, so VS-API has the
    # Scheduler-owned elapsed and can fault the pair at fault_after_ms even while the up is
    # still in flight. Without this, a diverged pair has no elapsed and reads as calm
    # in_flight on the client for the whole duration of a stuck up.
    d = _make_dispatcher_with_two_terminal_gs()
    d._mbb_dispatch = False
    d._handle_actuation_result = AsyncMock()
    d._js.publish = AsyncMock()
    pair = ("gs-multi", "sat-new")
    observed: dict = {}

    async def capturing_up(to_add, desired, sim_iso, sim_time, nc):
        # State observed at the instant the up is dispatched (i.e. before it completes).
        observed["stamped"] = pair in d._pending_since
        snaps = _actual_link_snapshots(d._js.publish)
        published_pairs = [tuple(p.pair) for s in snaps for p in s.pending_pairs]
        observed["published_pending"] = pair in published_pairs
        return _failed_up(pair)

    d._send_batch_up = capturing_up
    asyncio.run(d._reconcile_links({pair: _info("term1")}, None, SIM_TIME))

    assert observed["stamped"] is True, "pending_since must be stamped before the up await"
    assert observed["published_pending"] is True, (
        "pending clock must be published before the up await"
    )


def test_dropped_publish_self_heals_on_next_reconcile() -> None:
    # Re-review #C: a swallowed publish failure on a convergence tick must not strand the
    # converged set. The dirty flag forces a republish on the next reconcile even with no
    # further membership change, so VS-API's retained pending set cannot age into a false
    # fault for a pair the kernel actually settled.
    d = _make_dispatcher_with_two_terminal_gs()
    d._mbb_dispatch = False
    d._handle_actuation_result = AsyncMock()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._send_batch_down = AsyncMock(
        return_value=_success_result(pair=pair, operation="BatchLinkDown")
    )

    # First reconcile tears the pair down (membership change) but the publish fails.
    d._js.publish = AsyncMock(side_effect=RuntimeError("nats unavailable"))
    asyncio.run(d._reconcile_links({}, None, SIM_TIME))
    assert pair not in d._actual_links
    assert d._actual_links_publish_dirty is True  # dropped publish, marked for retry

    # Second reconcile: NO further membership change, but the dirty flag forces a republish.
    d._js.publish = AsyncMock()
    asyncio.run(d._reconcile_links({}, None, SIM_TIME))
    snaps = _actual_link_snapshots(d._js.publish)
    assert snaps, "a dropped publish must self-heal on the next reconcile"
    assert [pair[0], pair[1]] not in snaps[-1].active_pairs
    assert d._actual_links_publish_dirty is False


def test_ground_down_gate_blocks_kernel_dirty_and_repairing_but_allows_clean_cleanup() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.ACTUATION_BLOCKED,
        reason_code=SchedulerOpsCode.ACTUATION_BLOCKED,
    )
    assert pair in d._filter_ground_down_mutations({pair}, operation="BatchLinkDown")

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
    )
    assert pair not in d._filter_ground_down_mutations({pair}, operation="BatchLinkDown")

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.ACTUATION_BLOCKED,
        reason_code=SchedulerOpsCode.ACTUATION_BLOCKED,
        recovery=RecoveryStatus(active_intervention_id="repair-1"),
    )
    assert pair not in d._filter_ground_down_mutations({pair}, operation="BatchLinkDown")


def test_seek_epoch_reset_does_not_clear_dirty_actuation_state() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
    )

    d._reset_epoch_local_authority()

    assert d._gs_actuation["gs-multi"].state == GroundActuationStateName.KERNEL_DIRTY


def test_read_only_kernel_proof_clears_dirty_ground_station() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._desired_links[pair] = _info()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
        recovery=RecoveryStatus(verify_attempt_count=2),
    )
    d._send_kernel_inventory = AsyncMock(
        return_value=_success_result(pair=pair, operation="KernelInventory")
    )
    d._js.publish = AsyncMock()

    verified = asyncio.run(
        d._verify_gs_against_current_authority(gs_id="gs-multi", sim_time=SIM_TIME)
    )

    assert verified is True
    assert d._gs_actuation["gs-multi"].state == GroundActuationStateName.CLEAN
    d._send_kernel_inventory.assert_awaited_once()


def test_no_footprint_cleanup_clears_dirty_without_inventing_kernel_probe() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
    )
    d._send_kernel_inventory = AsyncMock()
    d._js.publish = AsyncMock()

    verified = asyncio.run(
        d._verify_gs_against_current_authority(gs_id="gs-multi", sim_time=SIM_TIME)
    )

    assert verified is True
    assert d._gs_actuation["gs-multi"].state == GroundActuationStateName.CLEAN
    d._send_kernel_inventory.assert_not_called()


def test_auto_verify_exhaustion_requires_operator_action_and_does_not_clear_dirty() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
        recovery=RecoveryStatus(verify_attempt_count=4),
    )
    d._send_kernel_inventory = AsyncMock(
        return_value=_actuation_result(
            pair=pair,
            link_type="ground",
            gs_id="gs-multi",
            failure=ActuationFailureClass.GROUND_KERNEL_DIRTY,
            operation="KernelInventory",
        )
    )
    d._js.publish = AsyncMock()

    verified = asyncio.run(
        d._verify_gs_against_current_authority(gs_id="gs-multi", sim_time=SIM_TIME)
    )

    state = d._gs_actuation["gs-multi"]
    assert verified is False
    assert state.state == GroundActuationStateName.KERNEL_DIRTY
    assert state.recovery.verify_exhausted is True
    assert state.recovery.operator_action_required is True
    assert state.reason_code == SchedulerOpsCode.KERNEL_VERIFY_EXHAUSTED


def test_operator_repair_reconciles_to_current_authority_and_proves_final_gs_state() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    old_pair = ("gs-multi", "sat-old")
    new_pair = ("gs-multi", "sat-new")
    d._current_sim_time = SIM_TIME
    d._actual_links[old_pair] = _info("term0")
    d._desired_links[new_pair] = _info("term1")
    dirty = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
        affected_pairs=frozenset({old_pair}),
        stale_pairs=frozenset({old_pair}),
    )
    d._gs_actuation["gs-multi"] = dirty
    d._repair_original_states["repair-1"] = dirty
    d._js.publish = AsyncMock()

    d._send_batch_down = AsyncMock(
        return_value=_success_result(pair=old_pair, operation="BatchLinkDown")
    )
    d._send_batch_up = AsyncMock(
        return_value=_success_result(pair=new_pair, operation="BatchLinkUp")
    )
    verify_calls = []

    async def verify_gs(*, gs_id, expected_up, expected_down, sim_time):
        verify_calls.append((set(expected_up), set(expected_down)))
        return ActuationResult(
            operation="KernelInventory",
            requested_pairs=frozenset(set(expected_up) | set(expected_down)),
            succeeded_pairs=frozenset(set(expected_up) | set(expected_down)),
            failed_pairs=frozenset(),
            pair_results={},
            agent_results=(_agent_result(ActuationFailureClass.NONE, operation="KernelInventory"),),
        )

    d._send_kernel_inventory = verify_gs
    cmd = OperatorRepairCommand(
        session_id=d._session_id,
        wiring_generation=d._wiring_generation,
        scheduler_instance_id=d._scheduler_instance_id,
        gs_id="gs-multi",
        reason="operator verified implementation fix",
        intervention_id="repair-1",
    )

    asyncio.run(d._run_operator_repair_locked(cmd))

    assert d._gs_actuation["gs-multi"].state == GroundActuationStateName.CLEAN
    assert old_pair not in d._actual_links
    assert new_pair in d._actual_links
    assert verify_calls[0] == (set(), {old_pair})
    assert verify_calls[-1] == ({new_pair}, set())
    # Operator repair changed membership (old_pair removed, new_pair added) — the
    # recoverable kernel-actual set must be republished so a just-repaired GS reads
    # connected on a VS-API resubscribe, not stale until the next ordinary reconcile.
    snaps = _actual_link_snapshots(d._js.publish)
    assert snaps, "operator repair that changed membership must republish kernel-actual"
    latest = snaps[-1]
    d._js.publish.assert_any_await(
        actual_links_subject(d._session_id, d._scheduler_instance_id),
        latest.model_dump_json().encode(),
    )
    assert [new_pair[0], new_pair[1]] in latest.active_pairs
    assert [old_pair[0], old_pair[1]] not in latest.active_pairs


def test_kernel_verify_due_gate_and_backoff_use_injected_clock() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    base = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    current = {"now": base}
    d._now = lambda: current["now"]
    d._desired_links[pair] = _info()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
        recovery=RecoveryStatus(next_verify_after=base),
    )
    d._send_kernel_inventory = AsyncMock(
        return_value=_actuation_result(
            pair=pair,
            link_type="ground",
            gs_id="gs-multi",
            failure=ActuationFailureClass.GROUND_KERNEL_DIRTY,
            operation="KernelInventory",
        )
    )
    d._js.publish = AsyncMock()

    current["now"] = base.replace(hour=11, minute=59, second=59)
    asyncio.run(d._run_due_kernel_verifications(sim_time=SIM_TIME))
    d._send_kernel_inventory.assert_not_called()

    current["now"] = base
    asyncio.run(d._run_due_kernel_verifications(sim_time=SIM_TIME))

    d._send_kernel_inventory.assert_awaited_once()
    state = d._gs_actuation["gs-multi"]
    assert state.recovery.verify_attempt_count == 1
    assert state.recovery.next_verify_after == base.replace(hour=12, minute=0, second=10)


def test_authority_subset_violation_halts_callback_path_and_queues_sentinel() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._running = True
    d._desired_links[pair] = _info()
    d._ome_view[pair] = (True, False, "active")
    d._js.publish = AsyncMock()

    with pytest.raises(RuntimeError, match="C-A authority subset violation"):
        asyncio.run(d._assert_authority_subset_fail_loud("unit-test"))

    assert d._running is False
    assert d._dispatch_blocked_reason.startswith("C-A authority subset violation")
    assert d._dispatch_queue.get_nowait() is None


def test_clean_kernel_audit_verifies_scheduler_actual_links_without_state_change() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._send_kernel_inventory = AsyncMock(
        return_value=_success_result(pair=pair, operation="KernelInventory")
    )
    d._js.publish = AsyncMock()

    result = asyncio.run(d._audit_clean_ground_kernel_state(sim_time=SIM_TIME, gs_ids={"gs-multi"}))

    assert result == {"gs-multi": True}
    assert d._gs_actuation["gs-multi"].state == GroundActuationStateName.CLEAN
    call = d._send_kernel_inventory.await_args.kwargs
    assert set(call["expected_up"]) == {pair}
    assert set(call["expected_down"]) == set()
    assert _published_ops_codes(d._js.publish) == ["KERNEL_VERIFY_ATTEMPTED"]


def test_clean_kernel_audit_mismatch_marks_kernel_dirty_and_preserves_failure_details() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._send_kernel_inventory = AsyncMock(
        return_value=_actuation_result(
            pair=pair,
            link_type="ground",
            gs_id="gs-multi",
            failure=ActuationFailureClass.GROUND_KERNEL_DIRTY,
            operation="KernelInventory",
        )
    )
    d._js.publish = AsyncMock()

    result = asyncio.run(d._audit_clean_ground_kernel_state(sim_time=SIM_TIME, gs_ids={"gs-multi"}))

    assert result == {"gs-multi": False}
    state = d._gs_actuation["gs-multi"]
    assert state.state == GroundActuationStateName.KERNEL_DIRTY
    assert state.reason_code == SchedulerOpsCode.KERNEL_DIRTY
    assert state.affected_pairs == frozenset({pair})
    assert state.recovery.next_verify_after is not None
    codes = _published_ops_codes(d._js.publish)
    assert codes == ["KERNEL_VERIFY_ATTEMPTED", "KERNEL_DIRTY"]


def test_clean_kernel_audit_never_clears_existing_dirty_state_by_inference() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code=SchedulerOpsCode.KERNEL_DIRTY,
    )
    d._send_kernel_inventory = AsyncMock(
        return_value=_success_result(pair=pair, operation="KernelInventory")
    )
    d._js.publish = AsyncMock()

    result = asyncio.run(d._audit_clean_ground_kernel_state(sim_time=SIM_TIME, gs_ids={"gs-multi"}))

    assert result == {}
    assert d._gs_actuation["gs-multi"].state == GroundActuationStateName.KERNEL_DIRTY
    d._send_kernel_inventory.assert_not_awaited()


def test_clean_kernel_audit_due_gate_uses_injected_clock_and_is_reproducible() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    base = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    now = {"value": base}
    d._now = lambda: now["value"]
    d._clean_kernel_audit_interval_s = 60.0
    d._last_clean_kernel_audit_at = base
    d._send_kernel_inventory = AsyncMock(
        return_value=_success_result(pair=pair, operation="KernelInventory")
    )
    d._js.publish = AsyncMock()

    now["value"] = base + timedelta(seconds=59)
    asyncio.run(d._run_due_kernel_verifications(sim_time=SIM_TIME))
    d._send_kernel_inventory.assert_not_called()

    now["value"] = base + timedelta(seconds=60)
    asyncio.run(d._run_due_kernel_verifications(sim_time=SIM_TIME))
    d._send_kernel_inventory.assert_awaited_once()
    assert _published_ops_codes(d._js.publish) == ["KERNEL_VERIFY_ATTEMPTED"]
