# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Deterministic Node Agent operation plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from node_agent.kernel_verifier import Proof

StepAction = Callable[[], None]
StepVerifier = Callable[[], Proof]


@dataclass(frozen=True, slots=True)
class OperationStep:
    """One mutation step plus its proof and optional rollback."""

    name: str
    action: StepAction
    verify: StepVerifier | None = None
    rollback: StepAction | None = None
    rollback_verify: StepVerifier | None = None
    dirty_on_failure: bool = True


@dataclass(frozen=True, slots=True)
class OperationPlan:
    """A deterministic sequence of substrate mutation steps."""

    operation_id: str
    operation_kind: str
    target: str
    steps: tuple[OperationStep, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.operation_id:
            raise ValueError("operation plan requires operation_id")
        if not self.operation_kind:
            raise ValueError("operation plan requires operation_kind")
        if not self.target:
            raise ValueError("operation plan requires target")
        if not self.steps:
            raise ValueError("operation plan requires at least one step")
