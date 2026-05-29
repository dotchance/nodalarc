# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler actuation result contracts for Phase 5."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from nodalarc.proto import node_agent_pb2

LinkPair = tuple[str, str]
InterfaceAck = tuple[str, str, str]


class ActuationFailureClass(StrEnum):
    NONE = "none"
    FENCE = "fence"
    GROUND_CLEAN_FAILURE = "ground_clean_failure"
    GROUND_KERNEL_DIRTY = "ground_kernel_dirty"
    GROUND_UNKNOWN = "ground_unknown"
    ISL_FAILURE = "isl_failure"


class GroundActuationStateName(StrEnum):
    CLEAN = "clean"
    ACTUATION_BLOCKED = "actuation_blocked"
    KERNEL_DIRTY = "kernel_dirty"


@dataclass(frozen=True, slots=True)
class RecoveryStatus:
    verify_attempt_count: int = 0
    last_verify_result: str | None = None
    next_verify_after: datetime | None = None
    verify_exhausted: bool = False
    operator_action_required: bool = False
    active_intervention_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verify_attempt_count": self.verify_attempt_count,
            "last_verify_result": self.last_verify_result,
            "next_verify_after": self.next_verify_after.isoformat()
            if self.next_verify_after
            else None,
            "verify_exhausted": self.verify_exhausted,
            "operator_action_required": self.operator_action_required,
            "active_intervention_id": self.active_intervention_id,
        }


@dataclass(frozen=True, slots=True)
class GroundActuationState:
    gs_id: str
    state: GroundActuationStateName = GroundActuationStateName.CLEAN
    reason_code: str = "ACTUATION_CLEAN"
    since: datetime = field(default_factory=lambda: datetime.now(UTC))
    affected_pairs: frozenset[LinkPair] = frozenset()
    stale_pairs: frozenset[LinkPair] = frozenset()
    node_agent_results: tuple[dict[str, Any], ...] = ()
    recovery: RecoveryStatus = field(default_factory=RecoveryStatus)

    @property
    def blocking_new_ground_link_up(self) -> bool:
        return self.state != GroundActuationStateName.CLEAN

    def to_notice(self) -> dict[str, Any]:
        return {
            "gs_id": self.gs_id,
            "actuation_state": self.state.value,
            "since": self.since.isoformat(),
            "reason_code": self.reason_code,
            "blocking_new_ground_link_up": self.blocking_new_ground_link_up,
            "affected_pairs": [list(pair) for pair in sorted(self.affected_pairs)],
            "stale_pairs": [list(pair) for pair in sorted(self.stale_pairs)],
            "recovery_status": self.recovery.to_dict(),
            "last_event": self.node_agent_results[-1] if self.node_agent_results else {},
        }


@dataclass(frozen=True, slots=True)
class AgentCommandResult:
    agent_addr: str
    operation: str
    requested: tuple[tuple[str, str], ...]
    success_acks: frozenset[InterfaceAck]
    failure_class: ActuationFailureClass
    dirty_kernel: bool
    unknown_outcome: bool
    fence_failure: bool
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PairActuationResult:
    pair: LinkPair
    link_type: str
    gs_id: str | None
    expected_ifaces: frozenset[InterfaceAck]
    successful_ifaces: frozenset[InterfaceAck]
    failure_class: ActuationFailureClass

    @property
    def success(self) -> bool:
        return bool(self.expected_ifaces) and self.expected_ifaces <= self.successful_ifaces


@dataclass(frozen=True, slots=True)
class ActuationResult:
    operation: str
    requested_pairs: frozenset[LinkPair]
    succeeded_pairs: frozenset[LinkPair]
    failed_pairs: frozenset[LinkPair]
    pair_results: Mapping[LinkPair, PairActuationResult]
    agent_results: tuple[AgentCommandResult, ...]

    @property
    def dirty_kernel(self) -> bool:
        return any(result.dirty_kernel for result in self.agent_results)

    @property
    def unknown_outcome(self) -> bool:
        return any(result.unknown_outcome for result in self.agent_results)

    @property
    def fence_failure(self) -> bool:
        return any(result.fence_failure for result in self.agent_results)

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_pairs) or any(
            result.failure_class != ActuationFailureClass.NONE for result in self.agent_results
        )

    def node_agent_details(self) -> list[dict[str, Any]]:
        return [result.details for result in self.agent_results]


FENCE_CODES = {
    node_agent_pb2.NODE_AGENT_STALE_SESSION,
    node_agent_pb2.NODE_AGENT_STALE_GENERATION,
    node_agent_pb2.NODE_AGENT_INVALID_ENVELOPE,
}


def _code_name(code: int) -> str:
    try:
        return node_agent_pb2.NodeAgentErrorCode.Name(code)
    except ValueError:
        return str(code)


def _interface_results(result) -> list:
    if hasattr(result, "interface_results"):
        return list(result.interface_results)
    if hasattr(result, "entry_results"):
        return list(result.entry_results)
    return []


def classify_agent_response(
    *,
    result,
    requested_interfaces: Iterable,
    agent_addr: str,
    operation: str,
) -> AgentCommandResult:
    requested = tuple((iface.node_id, iface.interface_name) for iface in requested_interfaces)
    requested_set = set(requested)
    entries = _interface_results(result)
    returned_set = {(entry.node_id, entry.interface_name) for entry in entries}
    aggregate_code = getattr(result, "error_code", node_agent_pb2.NODE_AGENT_ERROR_UNSPECIFIED)
    entry_codes = {entry.error_code for entry in entries}
    fence_failure = aggregate_code in FENCE_CODES or bool(entry_codes & FENCE_CODES)
    dirty_kernel = bool(getattr(result, "dirty_kernel", False)) or any(
        getattr(entry, "dirty_kernel", False) for entry in entries
    )
    all_interface_success = all(bool(entry.success) for entry in entries)
    aggregate_success = bool(getattr(result, "success", False))
    shape_ok = requested_set == returned_set and aggregate_success == all_interface_success
    unverified_success = any(entry.success and not entry.verified for entry in entries)

    if fence_failure:
        failure_class = ActuationFailureClass.FENCE
        unknown = False
    elif dirty_kernel or unverified_success:
        failure_class = ActuationFailureClass.GROUND_KERNEL_DIRTY
        unknown = False
        dirty_kernel = True
    elif not shape_ok:
        failure_class = ActuationFailureClass.GROUND_UNKNOWN
        unknown = True
        dirty_kernel = True
    elif aggregate_success:
        failure_class = ActuationFailureClass.NONE
        unknown = False
    else:
        failure_class = ActuationFailureClass.GROUND_CLEAN_FAILURE
        unknown = False

    success_acks = frozenset(
        (agent_addr, entry.node_id, entry.interface_name)
        for entry in entries
        if entry.success and entry.verified and not entry.dirty_kernel
    )
    details = {
        "agent_addr": agent_addr,
        "operation": operation,
        "success": aggregate_success,
        "error_code": _code_name(aggregate_code),
        "error_message": getattr(result, "error_message", ""),
        "dirty_kernel": dirty_kernel,
        "unknown_outcome": unknown,
        "fence_failure": fence_failure,
        "requested": [list(item) for item in sorted(requested_set)],
        "returned": [list(item) for item in sorted(returned_set)],
        "interface_results": [
            {
                "node_id": entry.node_id,
                "interface_name": entry.interface_name,
                "success": bool(entry.success),
                "error_code": _code_name(entry.error_code),
                "error_message": entry.error_message,
                "verified": bool(entry.verified),
                "dirty_kernel": bool(entry.dirty_kernel),
                "proof_summary": getattr(entry, "proof_summary", ""),
                "proof_evidence": list(getattr(entry, "proof_evidence", [])),
            }
            for entry in entries
        ],
    }
    return AgentCommandResult(
        agent_addr=agent_addr,
        operation=operation,
        requested=tuple(sorted(requested_set)),
        success_acks=success_acks,
        failure_class=failure_class,
        dirty_kernel=dirty_kernel,
        unknown_outcome=unknown,
        fence_failure=fence_failure,
        details=details,
    )


def classify_agent_exception(
    *,
    exc: Exception,
    requested_interfaces: Iterable,
    agent_addr: str,
    operation: str,
) -> AgentCommandResult:
    requested_set = {(iface.node_id, iface.interface_name) for iface in requested_interfaces}
    details = {
        "agent_addr": agent_addr,
        "operation": operation,
        "success": False,
        "error_code": "TRANSPORT_OR_TIMEOUT",
        "error_message": str(exc),
        "dirty_kernel": True,
        "unknown_outcome": True,
        "fence_failure": False,
        "requested": [list(item) for item in sorted(requested_set)],
        "returned": [],
        "interface_results": [],
    }
    return AgentCommandResult(
        agent_addr=agent_addr,
        operation=operation,
        requested=tuple(sorted(requested_set)),
        success_acks=frozenset(),
        failure_class=ActuationFailureClass.GROUND_UNKNOWN,
        dirty_kernel=True,
        unknown_outcome=True,
        fence_failure=False,
        details=details,
    )


def build_actuation_result(
    *,
    operation: str,
    requested_pairs: Iterable[LinkPair],
    pair_agent_ifaces: Mapping[LinkPair, set[InterfaceAck]],
    pair_link_type: Mapping[LinkPair, str],
    pair_gs_id: Mapping[LinkPair, str | None],
    agent_results: Iterable[AgentCommandResult],
) -> ActuationResult:
    agent_tuple = tuple(agent_results)
    successful_ifaces = (
        frozenset().union(*(r.success_acks for r in agent_tuple)) if agent_tuple else frozenset()
    )
    pair_results: dict[LinkPair, PairActuationResult] = {}
    succeeded: set[LinkPair] = set()
    failed: set[LinkPair] = set()
    for pair in requested_pairs:
        expected = frozenset(pair_agent_ifaces.get(pair, set()))
        involved = [
            r
            for r in agent_tuple
            if any(ack in expected for ack in r.success_acks)
            or any((r.agent_addr, node, iface) in expected for node, iface in r.requested)
        ]
        failure_class = _pair_failure_class(involved, pair_link_type.get(pair, ""))
        result = PairActuationResult(
            pair=pair,
            link_type=pair_link_type.get(pair, ""),
            gs_id=pair_gs_id.get(pair),
            expected_ifaces=expected,
            successful_ifaces=frozenset(ack for ack in successful_ifaces if ack in expected),
            failure_class=failure_class,
        )
        pair_results[pair] = result
        if result.success:
            succeeded.add(pair)
        else:
            failed.add(pair)
    return ActuationResult(
        operation=operation,
        requested_pairs=frozenset(requested_pairs),
        succeeded_pairs=frozenset(succeeded),
        failed_pairs=frozenset(failed),
        pair_results=pair_results,
        agent_results=agent_tuple,
    )


def _pair_failure_class(
    agent_results: list[AgentCommandResult], link_type: str
) -> ActuationFailureClass:
    if any(r.failure_class == ActuationFailureClass.FENCE for r in agent_results):
        return ActuationFailureClass.FENCE
    if link_type == "isl" and any(
        r.failure_class != ActuationFailureClass.NONE for r in agent_results
    ):
        return ActuationFailureClass.ISL_FAILURE
    if not agent_results:
        return (
            ActuationFailureClass.ISL_FAILURE
            if link_type == "isl"
            else ActuationFailureClass.GROUND_UNKNOWN
        )
    if any(r.failure_class == ActuationFailureClass.GROUND_KERNEL_DIRTY for r in agent_results):
        return ActuationFailureClass.GROUND_KERNEL_DIRTY
    if any(r.failure_class == ActuationFailureClass.GROUND_UNKNOWN for r in agent_results):
        return ActuationFailureClass.GROUND_UNKNOWN
    if any(r.failure_class == ActuationFailureClass.GROUND_CLEAN_FAILURE for r in agent_results):
        return ActuationFailureClass.GROUND_CLEAN_FAILURE
    return ActuationFailureClass.NONE


def next_verify_time(
    attempt_count: int,
    *,
    now: Callable[[], datetime] | None = None,
) -> datetime:
    delay = min(300, 5 * (2 ** max(0, attempt_count - 1)))
    base = now() if now is not None else datetime.now(UTC)
    return base + timedelta(seconds=delay)
