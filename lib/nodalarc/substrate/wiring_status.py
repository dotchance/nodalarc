# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed wiring status contract for Node Agent readiness."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest

PhaseState = Literal["pending_pid", "wiring", "ready", "failed", "dirty_kernel"]
RowState = Literal["ready", "wiring"]

# The phase clause of the workload release gate, rendered from the same closed
# vocabulary ``ready_for`` applies and shaped like ``WiringPhaseResult``: the
# phases are a list of records carrying only phase, status and error_message,
# exactly the required names, each once, each ready. What the model refuses,
# the clause refuses. The Operator embeds it in the init container's jq
# predicate.
_PHASE_RECORD_JQ = (
    '(type == "object")'
    ' and ((keys - ["error_message", "phase", "status"]) == [])'
    ' and ((.phase | type) == "string")'
    ' and (.status == "ready")'
    ' and ((has("error_message") | not) or ((.error_message | type) == "string"))'
)
READY_PHASE_JQ_CLAUSE = (
    '((.phases | type) == "array")'
    f" and ((.phases | length) == {len(REQUIRED_WIRING_PHASES)})"
    f" and all(.phases[]; {_PHASE_RECORD_JQ})"
    f" and (([.phases[].phase] | sort) == {json.dumps(sorted(REQUIRED_WIRING_PHASES))})"
)


class WiringPhaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str
    status: PhaseState
    error_message: str = ""

    @field_validator("phase")
    @classmethod
    def _known_phase(cls, value: str) -> str:
        if value not in REQUIRED_WIRING_PHASES:
            raise ValueError(f"unknown wiring phase: {value}")
        return value


class NodeWiringStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    session_id: str
    # The raw deployment-run identity, matching the pod label the Operator
    # stamps (and re-stamps on same-CR updates). Release gates compare it
    # verbatim against their own label, so a row written under a previous
    # run can never release a workload in the current one.
    session_run_id: str
    wiring_generation: str
    # The exact pod incarnation this proof was written for. pod_uid is the
    # Kubernetes pod UID; sandbox_id is the CRI sandbox; netns_id is the
    # nsfs inode of the network namespace the Node Agent actually wired.
    # A workload release gate must match its own pod UID and netns inode
    # against these, so a row from a replaced pod or a recreated sandbox
    # can never release a workload it did not wire.
    pod_uid: str
    sandbox_id: str
    netns_id: str
    status: PhaseState
    phases: list[WiringPhaseResult]
    dirty_kernel: bool = False

    @field_validator(
        "node_id",
        "session_id",
        "session_run_id",
        "wiring_generation",
        "pod_uid",
        "sandbox_id",
        "netns_id",
    )
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("wiring status identity fields must be non-empty")
        return value

    @field_validator("phases")
    @classmethod
    def _unique_phases(cls, value: list[WiringPhaseResult]) -> list[WiringPhaseResult]:
        names = [phase.phase for phase in value]
        if len(names) != len(set(names)):
            repeated = sorted({name for name in names if names.count(name) > 1})
            raise ValueError(f"wiring status phases repeated: {', '.join(repeated)}")
        return value

    def ready_for(self, manifest: WiringManifest) -> bool:
        """The one readiness rule: this row proves every phase the manifest requires.

        Session and generation must match, the row must be ready and clean,
        and the row's phases must be exactly the manifest's required phases,
        every one ready. Unknown and repeated phase names never reach here;
        they fail validation.
        """
        if self.session_id != manifest.session_id:
            return False
        if self.wiring_generation != manifest.wiring_generation:
            return False
        if self.status != "ready" or self.dirty_kernel:
            return False
        if {phase.phase for phase in self.phases} != set(manifest.required_phases):
            return False
        return all(phase.status == "ready" for phase in self.phases)


def wiring_row(
    node_id: str,
    manifest: WiringManifest,
    *,
    pod_uid: str,
    sandbox_id: str,
    netns_id: str,
    state: RowState,
) -> NodeWiringStatus:
    """A complete row for one node in one of its two whole states.

    ``ready``: every required phase proved, published after wiring succeeds.
    ``wiring``: every required phase pending, published before a destructive
    host rebuild so any earlier ready proof is invalidated, the Scheduler
    fails closed and the Operator returns the session to Wiring until every
    phase and the status write succeed again.
    """
    if state not in ("ready", "wiring"):
        raise ValueError(f"wiring_row state must be ready or wiring, got {state!r}")
    phase_state: PhaseState = "ready" if state == "ready" else "pending_pid"
    return NodeWiringStatus(
        node_id=node_id,
        session_id=manifest.session_id,
        session_run_id=manifest.session_run_id,
        wiring_generation=manifest.wiring_generation,
        pod_uid=pod_uid,
        sandbox_id=sandbox_id,
        netns_id=netns_id,
        status=state,
        phases=[
            WiringPhaseResult(phase=phase, status=phase_state) for phase in REQUIRED_WIRING_PHASES
        ],
        dirty_kernel=False,
    )


def failed_status(
    node_id: str,
    manifest: WiringManifest,
    *,
    pod_uid: str,
    sandbox_id: str,
    netns_id: str,
    phase: str,
    error_message: str,
    dirty_kernel: bool = False,
) -> NodeWiringStatus:
    phases = []
    if phase not in REQUIRED_WIRING_PHASES:
        raise ValueError(f"unknown wiring failure phase: {phase}")
    failed_index = REQUIRED_WIRING_PHASES.index(phase)
    for required in REQUIRED_WIRING_PHASES:
        phase_index = REQUIRED_WIRING_PHASES.index(required)
        if required == phase:
            phase_status: PhaseState = "dirty_kernel" if dirty_kernel else "failed"
        elif phase_index < failed_index:
            phase_status = "ready"
        else:
            phase_status = "pending_pid"
        phases.append(
            WiringPhaseResult(
                phase=required,
                status=phase_status,
                error_message=error_message if required == phase else "",
            )
        )
    return NodeWiringStatus(
        node_id=node_id,
        session_id=manifest.session_id,
        session_run_id=manifest.session_run_id,
        wiring_generation=manifest.wiring_generation,
        pod_uid=pod_uid,
        sandbox_id=sandbox_id,
        netns_id=netns_id,
        status="dirty_kernel" if dirty_kernel else "failed",
        phases=phases,
        dirty_kernel=dirty_kernel,
    )


def encode_status(status: NodeWiringStatus) -> str:
    return status.model_dump_json()


def decode_status(value: str) -> NodeWiringStatus:
    return NodeWiringStatus.model_validate(json.loads(value))


def status_configmap_data(
    statuses: dict[str, NodeWiringStatus], manifest: WiringManifest
) -> dict[str, str]:
    data = {
        "_session_id": manifest.session_id,
        "_wiring_generation": manifest.wiring_generation,
    }
    data.update({node_id: encode_status(status) for node_id, status in statuses.items()})
    return data


def failed_status_summary(
    statuses: Mapping[str, NodeWiringStatus],
    *,
    node_ids: Iterable[str] | None = None,
    limit: int = 10,
) -> str:
    """Summarize failed or dirty wiring status with the first concrete cause."""
    candidates = set(node_ids) if node_ids is not None else set(statuses)
    failed = sorted(
        node_id
        for node_id in candidates
        if (status := statuses.get(node_id)) is not None
        and (status.status in {"failed", "dirty_kernel"} or status.dirty_kernel)
    )
    if not failed:
        return ""

    displayed = ", ".join(failed[:limit])
    if len(failed) > limit:
        displayed += f" ... and {len(failed) - limit} more"

    first = failed[0]
    detail = ""
    for phase in statuses[first].phases:
        if phase.status in {"failed", "dirty_kernel"} or phase.error_message:
            reason = phase.error_message or phase.status
            detail = f"; first failure: {first} {phase.phase}: {reason}"
            break

    return f"wiring failed for nodes: {displayed}{detail}"


def parse_status_configmap(
    data: dict[str, str] | None,
) -> tuple[str, str, dict[str, NodeWiringStatus]]:
    if not data:
        return "", "", {}
    session_id = data.get("_session_id", "")
    generation = data.get("_wiring_generation", "")
    statuses: dict[str, NodeWiringStatus] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        statuses[key] = decode_status(value)
    return session_id, generation, statuses
