# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Node Agent planned operation execution."""

from __future__ import annotations

from node_agent.kernel_verifier import Proof
from node_agent.operation_executor import execute_plan
from node_agent.operation_plan import OperationPlan, OperationStep


def test_execute_plan_returns_success_only_after_step_proof() -> None:
    calls: list[str] = []

    plan = OperationPlan(
        operation_id="op-1",
        operation_kind="SetLatency",
        target="sat-a/isl0",
        steps=(
            OperationStep(
                name="mutate",
                action=lambda: calls.append("action"),
                verify=lambda: Proof.ok("verified", "evidence"),
            ),
        ),
    )

    result = execute_plan(plan)

    assert result.success is True
    assert result.dirty_kernel is False
    assert [proof.summary for proof in result.proofs] == ["verified"]
    assert calls == ["action"]


def test_execute_plan_rolls_back_completed_steps_on_later_failure() -> None:
    calls: list[str] = []

    def _fail() -> None:
        calls.append("fail")
        raise RuntimeError("boom")

    plan = OperationPlan(
        operation_id="op-2",
        operation_kind="BatchLinkUp",
        target="sat-a/isl0",
        steps=(
            OperationStep(
                name="first",
                action=lambda: calls.append("first"),
                verify=lambda: Proof.ok("first verified"),
                rollback=lambda: calls.append("rollback-first"),
                rollback_verify=lambda: Proof.ok("rollback verified"),
            ),
            OperationStep(name="second", action=_fail, dirty_on_failure=False),
        ),
    )

    result = execute_plan(plan)

    assert result.success is False
    assert result.dirty_kernel is False
    assert calls == ["first", "fail", "rollback-first"]
    assert result.rollback[0].proof is not None


def test_execute_plan_marks_dirty_when_rollback_fails() -> None:
    def _fail() -> None:
        raise RuntimeError("boom")

    def _rollback_fail() -> None:
        raise RuntimeError("rollback failed")

    plan = OperationPlan(
        operation_id="op-3",
        operation_kind="BatchLinkDown",
        target="sat-a/isl0",
        steps=(
            OperationStep(
                name="first",
                action=lambda: None,
                rollback=_rollback_fail,
            ),
            OperationStep(name="second", action=_fail),
        ),
    )

    result = execute_plan(plan)

    assert result.success is False
    assert result.dirty_kernel is True
    assert result.rollback[0].error_message == "rollback failed"


def test_execute_plan_does_not_mark_unexecuted_dirty_steps_dirty() -> None:
    def _fail_before_mutation() -> None:
        raise RuntimeError("validation failed")

    plan = OperationPlan(
        operation_id="op-4",
        operation_kind="BatchLinkUp",
        target="sat-a/isl0",
        steps=(
            OperationStep(
                name="validate",
                action=_fail_before_mutation,
                dirty_on_failure=False,
            ),
            OperationStep(
                name="mutate",
                action=lambda: None,
                dirty_on_failure=True,
            ),
        ),
    )

    result = execute_plan(plan)

    assert result.success is False
    assert result.dirty_kernel is False


def test_execute_plan_marks_failed_dirty_step_dirty() -> None:
    def _fail_mutation() -> None:
        raise RuntimeError("mutation failed")

    plan = OperationPlan(
        operation_id="op-5",
        operation_kind="BatchLinkUp",
        target="sat-a/isl0",
        steps=(
            OperationStep(
                name="mutate",
                action=_fail_mutation,
                dirty_on_failure=True,
            ),
        ),
    )

    result = execute_plan(plan)

    assert result.success is False
    assert result.dirty_kernel is True
