# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Lifecycle-aware classification of fatal actuation failures.

A fatal actuation failure has two distinct causes demanding opposite
responses: the lifecycle authority moved past this scheduler instance
(the routine progress of a session switch — fence rejections after the
manifest flips, missing-node ISL failures while old pods are torn
down — end quietly, exit clean), or the failure happened while this
instance IS the current world (a real fault — the fatal halt stands).
These tests pin both directions, both authority sources
(ConstellationSpec sessionRunId and wiring-manifest identity), and the
fail-loud edges: an unreadable authority or an unwired reader must
never be interpreted as supersession.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from nodalarc.models.scheduler_ops import ActuationFailureClass, SchedulerOpsCode
from scheduler.actuation import ActuationResult, AgentCommandResult, PairActuationResult
from scheduler.dispatcher import Dispatcher, DispatcherSuperseded
from scheduler.pod_locator import PodLocationMap

OWN_SESSION = "run-old"
OWN_GENERATION = "sha256:" + "a" * 64
NEW_GENERATION = "sha256:" + "b" * 64
SIM_TIME = datetime(2026, 1, 1, tzinfo=UTC)

CURRENT_AUTHORITY = (OWN_SESSION, (OWN_SESSION, OWN_GENERATION))


def _make_dispatcher(read_lifecycle_identity=None) -> Dispatcher:
    interface_map = {("sat-a", "sat-b"): ("isl0", "isl1")}
    loc = PodLocationMap()
    for node_id in ("sat-a", "sat-b"):
        loc._node_of[node_id] = "nodal"
    loc._agent_addrs["nodal"] = "127.0.0.1:50100"
    dispatcher = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=dict.fromkeys(interface_map, 1000.0),
        pod_locator=loc,
        agent_pool=MagicMock(),
        session_id=OWN_SESSION,
        wiring_generation=OWN_GENERATION,
        max_latency_age_s=1.0,
        gs_terminal_capacities={},
        gs_handover_modes={},
        sat_ground_terminal_capacities={},
        read_lifecycle_identity=read_lifecycle_identity,
    )
    dispatcher._js = AsyncMock()
    dispatcher._nc = MagicMock()
    dispatcher._publish_scheduler_ops = AsyncMock()
    return dispatcher


def _fatal_result(failure_class: ActuationFailureClass, operation: str = "SetLatency"):
    """An ISL actuation failure of the given class (FENCE or ISL_FAILURE)."""
    pair = ("sat-a", "sat-b")
    agent = AgentCommandResult(
        agent_addr="127.0.0.1:50100",
        operation=operation,
        requested=(("sat-a", "isl0"),),
        success_acks=frozenset(),
        failure_class=failure_class,
        dirty_kernel=False,
        unknown_outcome=False,
        fence_failure=failure_class == ActuationFailureClass.FENCE,
        details={"error_code": "NODE_AGENT_STALE_GENERATION"},
    )
    pair_result = PairActuationResult(
        pair=pair,
        link_type="isl",
        gs_id=None,
        expected_ifaces=frozenset({("127.0.0.1:50100", "sat-a", "isl0")}),
        successful_ifaces=frozenset(),
        failure_class=failure_class,
    )
    return ActuationResult(
        operation=operation,
        requested_pairs=frozenset({pair}),
        succeeded_pairs=frozenset(),
        failed_pairs=frozenset({pair}),
        pair_results={pair: pair_result},
        agent_results=(agent,),
    )


def _handle(dispatcher: Dispatcher, result: ActuationResult) -> None:
    asyncio.run(
        dispatcher._handle_actuation_result(result, sim_time=SIM_TIME, operation_context="latency")
    )


def _assert_clean_supersession(dispatcher: Dispatcher) -> None:
    assert dispatcher._running is False
    assert "Superseded" in dispatcher._dispatch_blocked_reason
    (call,) = dispatcher._publish_scheduler_ops.await_args_list
    assert call.kwargs["code"] == SchedulerOpsCode.SCHEDULER_SUPERSEDED
    assert call.kwargs["level"] == "info"


def test_fence_superseded_by_new_manifest_generation_exits_cleanly():
    dispatcher = _make_dispatcher(lambda: (OWN_SESSION, (OWN_SESSION, NEW_GENERATION)))

    with pytest.raises(DispatcherSuperseded):
        _handle(dispatcher, _fatal_result(ActuationFailureClass.FENCE))

    _assert_clean_supersession(dispatcher)


def test_fence_superseded_by_new_cr_session_exits_cleanly():
    """The CR sessionRunId flips before the manifest does — it alone proves
    supersession even while the manifest still names this session."""
    dispatcher = _make_dispatcher(lambda: ("run-new", (OWN_SESSION, OWN_GENERATION)))

    with pytest.raises(DispatcherSuperseded):
        _handle(dispatcher, _fatal_result(ActuationFailureClass.FENCE))


def test_isl_teardown_failure_while_superseded_exits_cleanly():
    """The live-observed switch-window class: old session pods being torn
    down produce missing-node ISL failures with no fence code, while the
    CR already names the next session."""
    dispatcher = _make_dispatcher(lambda: ("run-new", (OWN_SESSION, OWN_GENERATION)))

    with pytest.raises(DispatcherSuperseded):
        _handle(dispatcher, _fatal_result(ActuationFailureClass.ISL_FAILURE))

    _assert_clean_supersession(dispatcher)


def test_absent_authority_means_superseded():
    """CR and manifest both deleted = teardown = this instance is over."""
    dispatcher = _make_dispatcher(lambda: (None, None))

    with pytest.raises(DispatcherSuperseded):
        _handle(dispatcher, _fatal_result(ActuationFailureClass.FENCE))


def test_fatal_failure_while_current_stays_fatal():
    """Authority fully matches this instance: the failure is real."""
    dispatcher = _make_dispatcher(lambda: CURRENT_AUTHORITY)

    for failure_class in (ActuationFailureClass.FENCE, ActuationFailureClass.ISL_FAILURE):
        dispatcher = _make_dispatcher(lambda: CURRENT_AUTHORITY)
        with pytest.raises(RuntimeError, match="Fatal actuation failure"):
            _handle(dispatcher, _fatal_result(failure_class))
        codes = [c.kwargs["code"] for c in dispatcher._publish_scheduler_ops.await_args_list]
        assert SchedulerOpsCode.ACTUATION_HALTED in codes
        assert SchedulerOpsCode.SCHEDULER_SUPERSEDED not in codes


def test_unreadable_authority_stays_fatal():
    """Supersession must be proven; a failed read never downgrades the halt."""

    def _broken_reader():
        raise ConnectionError("apiserver unreachable")

    dispatcher = _make_dispatcher(_broken_reader)

    with pytest.raises(RuntimeError, match="Fatal actuation failure"):
        _handle(dispatcher, _fatal_result(ActuationFailureClass.FENCE))


def test_no_reader_stays_fatal():
    """Without a wired authority reader every fatal failure stays fatal."""
    dispatcher = _make_dispatcher(None)

    with pytest.raises(RuntimeError, match="Fatal actuation failure"):
        _handle(dispatcher, _fatal_result(ActuationFailureClass.FENCE))
