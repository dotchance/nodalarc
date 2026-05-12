# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Execute Node Agent operation plans with proof and rollback evidence."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from node_agent.kernel_verifier import Proof
from node_agent.operation_plan import OperationPlan, OperationStep

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StepExecution:
    step_name: str
    proof: Proof | None = None
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class OperationExecutionResult:
    success: bool
    dirty_kernel: bool
    error_message: str = ""
    proofs: tuple[Proof, ...] = field(default_factory=tuple)
    executed: tuple[StepExecution, ...] = field(default_factory=tuple)
    rollback: tuple[StepExecution, ...] = field(default_factory=tuple)


def _verify_step(step: OperationStep) -> Proof | None:
    if step.verify is None:
        return None
    proof = step.verify()
    if not proof.verified:
        raise RuntimeError(proof.summary)
    return proof


def execute_plan(plan: OperationPlan) -> OperationExecutionResult:
    """Execute a deterministic operation plan.

    If a step action or verifier fails, completed steps with rollback hooks are
    rolled back in reverse order. Rollback failures or missing rollback for a
    dirty step produce ``dirty_kernel=True``.
    """
    completed: list[OperationStep] = []
    executed: list[StepExecution] = []
    proofs: list[Proof] = []
    failed_step: OperationStep | None = None
    try:
        for step in plan.steps:
            failed_step = step
            step.action()
            proof = _verify_step(step)
            if proof is not None:
                proofs.append(proof)
            completed.append(step)
            executed.append(StepExecution(step_name=step.name, proof=proof))
        return OperationExecutionResult(
            success=True,
            dirty_kernel=False,
            proofs=tuple(proofs),
            executed=tuple(executed),
        )
    except Exception as exc:
        error_message = f"{plan.operation_kind} step failed for {plan.target}: {exc}"
        log.warning("%s", error_message)
        rollback_results: list[StepExecution] = []
        dirty = bool(failed_step and failed_step.dirty_on_failure)
        for step in reversed(completed):
            if step.rollback is None:
                dirty = dirty or step.dirty_on_failure
                continue
            try:
                step.rollback()
                proof = step.rollback_verify() if step.rollback_verify else None
                if proof is not None and not proof.verified:
                    dirty = True
                    rollback_results.append(
                        StepExecution(
                            step_name=step.name,
                            proof=proof,
                            error_message=proof.summary,
                        )
                    )
                else:
                    rollback_results.append(StepExecution(step_name=step.name, proof=proof))
            except Exception as rollback_exc:
                dirty = True
                rollback_results.append(
                    StepExecution(step_name=step.name, error_message=str(rollback_exc))
                )
        return OperationExecutionResult(
            success=False,
            dirty_kernel=dirty,
            error_message=error_message,
            proofs=tuple(proofs),
            executed=tuple(executed),
            rollback=tuple(rollback_results),
        )
