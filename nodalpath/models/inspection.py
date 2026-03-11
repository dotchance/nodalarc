"""Data models for node inspection / feedback loop."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class BindingDiffKind(enum.Enum):
    MISSING = "missing"
    EXTRA = "extra"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class BindingDiff:
    in_label: int
    kind: BindingDiffKind
    planned_action: str | None = None
    planned_out_label: int | None = None
    planned_out_interface: str | None = None
    observed_action: str | None = None
    observed_out_label: int | None = None
    observed_out_interface: str | None = None


@dataclass(frozen=True)
class IngressDiff:
    dst_prefix: str
    kind: BindingDiffKind
    planned_push_label: int | None = None
    planned_out_interface: str | None = None
    observed_push_label: int | None = None
    observed_out_interface: str | None = None


@dataclass(frozen=True)
class NodeInspectionResult:
    node_id: str
    reachable: bool
    status_topology_state_id: str | None = None
    status_total_entries: int | None = None
    binding_diffs: list[BindingDiff] = field(default_factory=list)
    ingress_diffs: list[IngressDiff] = field(default_factory=list)
    error_message: str | None = None

    @property
    def has_deviation(self) -> bool:
        return bool(self.binding_diffs or self.ingress_diffs)


@dataclass
class InspectionRun:
    run_id: str
    trigger: str  # "push_verify" | "link_event" | "heartbeat" | "operator"
    topology_state_id: str
    started_at: datetime
    completed_at: datetime | None = None
    node_results: list[NodeInspectionResult] = field(default_factory=list)

    @property
    def nodes_inspected(self) -> int:
        return len(self.node_results)

    @property
    def nodes_reachable(self) -> int:
        return sum(1 for r in self.node_results if r.reachable)

    @property
    def nodes_with_deviations(self) -> int:
        return sum(1 for r in self.node_results if r.has_deviation)

    @property
    def nodes_unreachable(self) -> int:
        return sum(1 for r in self.node_results if not r.reachable)
