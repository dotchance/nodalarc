# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Phase 5 Scheduler actuation trust contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from nodalarc.models.scheduler_ops import OperatorRepairCommand
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
    assert state.reason_code == "REPLACEMENT_LINK_UP_FAILED"
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
        reason_code="KERNEL_DIRTY",
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
    assert state.reason_code == "GROUND_LATENCY_UPDATE_FAILED"


def test_reconcile_does_not_auto_down_kernel_dirty_or_repairing_ground_station() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._send_batch_down = AsyncMock()
    d._js.publish = AsyncMock()

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code="KERNEL_DIRTY",
    )
    asyncio.run(d._reconcile_links({}, None, SIM_TIME))
    d._send_batch_down.assert_not_called()
    assert pair in d._actual_links

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.ACTUATION_BLOCKED,
        reason_code="ACTUATION_BLOCKED",
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
        reason_code="ACTUATION_BLOCKED",
    )
    d._send_batch_down = AsyncMock(
        return_value=_success_result(pair=pair, operation="BatchLinkDown")
    )
    d._js.publish = AsyncMock()

    asyncio.run(d._reconcile_links({}, None, SIM_TIME))

    d._send_batch_down.assert_awaited_once()
    assert pair not in d._actual_links


def test_ground_down_gate_blocks_kernel_dirty_and_repairing_but_allows_clean_cleanup() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.ACTUATION_BLOCKED,
        reason_code="ACTUATION_BLOCKED",
    )
    assert pair in d._filter_ground_down_mutations({pair}, operation="BatchLinkDown")

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code="KERNEL_DIRTY",
    )
    assert pair not in d._filter_ground_down_mutations({pair}, operation="BatchLinkDown")

    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.ACTUATION_BLOCKED,
        reason_code="ACTUATION_BLOCKED",
        recovery=RecoveryStatus(active_intervention_id="repair-1"),
    )
    assert pair not in d._filter_ground_down_mutations({pair}, operation="BatchLinkDown")


def test_seek_epoch_reset_does_not_clear_dirty_actuation_state() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code="KERNEL_DIRTY",
    )

    d._reset_epoch_local_authority()

    assert d._gs_actuation["gs-multi"].state == GroundActuationStateName.KERNEL_DIRTY


def test_auto_verify_exhaustion_requires_operator_action_and_does_not_clear_dirty() -> None:
    d = _make_dispatcher_with_two_terminal_gs()
    pair = ("gs-multi", "sat-old")
    d._actual_links[pair] = _info()
    d._gs_actuation["gs-multi"] = GroundActuationState(
        gs_id="gs-multi",
        state=GroundActuationStateName.KERNEL_DIRTY,
        reason_code="KERNEL_DIRTY",
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
    assert state.reason_code == "KERNEL_VERIFY_EXHAUSTED"


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
        reason_code="KERNEL_DIRTY",
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
