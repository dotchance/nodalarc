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


class WiringPhaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str
    status: PhaseState
    error_message: str = ""


class NodeWiringStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    session_id: str
    wiring_generation: str
    status: PhaseState
    phases: list[WiringPhaseResult]
    dirty_kernel: bool = False

    @field_validator("node_id", "session_id", "wiring_generation")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("wiring status identity fields must be non-empty")
        return value

    def ready_for(self, manifest: WiringManifest) -> bool:
        if self.session_id != manifest.session_id:
            return False
        if self.wiring_generation != manifest.wiring_generation:
            return False
        if self.status != "ready" or self.dirty_kernel:
            return False
        phase_map = {phase.phase: phase for phase in self.phases}
        for required in manifest.required_phases:
            phase = phase_map.get(required)
            if phase is None or phase.status != "ready":
                return False
        return True


def ready_status(node_id: str, manifest: WiringManifest) -> NodeWiringStatus:
    return NodeWiringStatus(
        node_id=node_id,
        session_id=manifest.session_id,
        wiring_generation=manifest.wiring_generation,
        status="ready",
        phases=[WiringPhaseResult(phase=phase, status="ready") for phase in REQUIRED_WIRING_PHASES],
        dirty_kernel=False,
    )


def failed_status(
    node_id: str,
    manifest: WiringManifest,
    *,
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
        wiring_generation=manifest.wiring_generation,
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
