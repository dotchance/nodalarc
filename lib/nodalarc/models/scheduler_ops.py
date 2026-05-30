# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler operational contracts for actuation trust.

These models are the typed producer-side shape for Scheduler OpsEvent details,
VS-API actuation health, and explicit operator repair commands. OpsEvent keeps a
free-form ``details`` dict for wire compatibility; Scheduler code constructs
that dict from these models so Phase 5 failure states are not ad hoc strings.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ActuationState = Literal["clean", "actuation_blocked", "kernel_dirty", "unknown"]
InstanceHealthStatus = Literal["clean", "degraded", "dirty", "unknown"]


class SchedulerOpsCode(StrEnum):
    REPLACEMENT_LINK_UP_FAILED = "REPLACEMENT_LINK_UP_FAILED"
    GROUND_LINK_UP_FAILED = "GROUND_LINK_UP_FAILED"
    GROUND_LINK_DOWN_FAILED = "GROUND_LINK_DOWN_FAILED"
    GROUND_LATENCY_UPDATE_FAILED = "GROUND_LATENCY_UPDATE_FAILED"
    OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR = "OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR"
    ACTUATION_BLOCKED = "ACTUATION_BLOCKED"
    ACTUATION_CLEAN = "ACTUATION_CLEAN"
    KERNEL_DIRTY = "KERNEL_DIRTY"
    KERNEL_VERIFY_ATTEMPTED = "KERNEL_VERIFY_ATTEMPTED"
    KERNEL_VERIFY_EXHAUSTED = "KERNEL_VERIFY_EXHAUSTED"
    AUTHORITY_SUBSET_VIOLATION = "AUTHORITY_SUBSET_VIOLATION"
    ACTUATION_HALTED = "ACTUATION_HALTED"
    OPERATOR_REPAIR_REQUESTED = "OPERATOR_REPAIR_REQUESTED"
    OPERATOR_REPAIR_STARTED = "OPERATOR_REPAIR_STARTED"
    OPERATOR_REPAIR_SUCCEEDED = "OPERATOR_REPAIR_SUCCEEDED"
    OPERATOR_REPAIR_FAILED = "OPERATOR_REPAIR_FAILED"
    OPERATOR_REPAIR_REJECTED = "OPERATOR_REPAIR_REJECTED"


class ActuationFailureClass(StrEnum):
    NONE = "none"
    AUTHORITY_INVARIANT = "authority_invariant"
    OME_CONTRACT = "ome_contract"
    FENCE = "fence"
    GROUND_CLEAN_FAILURE = "ground_clean_failure"
    GROUND_KERNEL_DIRTY = "ground_kernel_dirty"
    GROUND_UNKNOWN = "ground_unknown"
    ISL_FAILURE = "isl_failure"
    OPS_PUBLISH_FAILURE = "ops_publish_failure"


class RecoveryStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    verify_attempt_count: int = 0
    last_verify_result: str | None = None
    next_verify_after: datetime | None = None
    verify_exhausted: bool = False
    operator_action_required: bool = False
    active_intervention_id: str | None = None


class ActuationOpsDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    wiring_generation: str
    scheduler_instance_id: str
    hostname: str
    sim_time: datetime | None = None
    epoch_id: int | None = None
    snapshot_seq: int | None = None
    gs_id: str | None = None
    operation: str
    failure_class: ActuationFailureClass
    affected_pairs: list[list[str]] = Field(default_factory=list)
    successor_pair: list[str] | None = None
    old_pair: list[str] | None = None
    desired_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    actual_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    ome_visible_scheduled_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    node_agent_results: list[dict[str, Any]] = Field(default_factory=list)
    actuation_state_before: ActuationState = "unknown"
    actuation_state_after: ActuationState = "unknown"
    recovery_status: RecoveryStatus = Field(default_factory=RecoveryStatus)
    intervention_id: str | None = None
    reason: str | None = None


class PendingActuationPair(BaseModel):
    """One pair the Scheduler desires up but the kernel has not yet proven active.

    The Scheduler OWNS this timing. ``pending_since`` is the Scheduler's wall-clock
    instant (UTC) the pair entered the effective-desired set without a verified
    ``_actual_links`` entry — the origin of the actuation window the in_flight ->
    faulted bound measures. It is NOT VS-API's snapshot-observation time and NOT OME
    visibility time (both of which mismatched the bound and re-clocked on VS-API
    restart). Rides the retained ``ActualLinkSnapshot`` so VS-API RECOVERS the
    divergence clock on resubscribe and only ever derives elapsed from it, never
    re-stamps it. ``operation`` is the pending direction (``BatchLinkUp``);
    ``epoch_id``/``snapshot_seq`` are the snapshot identity at fold time, for
    provenance and stale-epoch discard.
    """

    model_config = ConfigDict(frozen=True)

    pair: list[str]
    pending_since: datetime
    operation: str
    epoch_id: int | None = None
    snapshot_seq: int | None = None


class ActualLinkSnapshot(BaseModel):
    """One Scheduler instance's verified kernel-actual link set, recoverable.

    ``active_pairs`` is ``_actual_links`` — the pairs the Node Agents have
    CONFIRMED active (verified=true proof), i.e. "kernel actual", NOT OME's
    desired/visible model. Published replace-not-merge to a per-instance retained
    subject (``actual_links_subject``) whenever membership changes, so VS-API
    recovers which pairs the kernel actually has up on (re)subscribe — the only
    recoverable kernel-actual source, since LinkUp/LinkDown are NEW-delivered.
    Each pair is an ordered ``[node_a, node_b]`` list. ``scheduler_instance_id``
    keys the retained subject so a restart does not clobber a dead predecessor's
    message; the consumer tracks the current owner (single-owner-per-session).

    ``pending_pairs`` carries the Scheduler-owned in_flight -> faulted clock for every
    desired-but-not-kernel-actual pair (see ``PendingActuationPair``); it rides the same
    retained snapshot so the divergence timing recovers atomically with the actual set
    and never split-brains across two messages. ``emitted_at`` is the Scheduler's
    wall-clock publish instant: VS-API combines it with each pair's ``pending_since`` (a
    same-clock delta) and its own receive-to-now delta to compute divergence age WITHOUT
    cross-pod clock skew — ``pending_since`` stays the load-bearing origin.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    wiring_generation: str
    scheduler_instance_id: str
    hostname: str
    sim_time: datetime | None = None
    epoch_id: int | None = None
    snapshot_seq: int | None = None
    active_pairs: list[list[str]] = Field(default_factory=list)
    pending_pairs: list[PendingActuationPair] = Field(default_factory=list)
    emitted_at: datetime | None = None


class ActuationNotice(BaseModel):
    model_config = ConfigDict(frozen=True)

    gs_id: str
    actuation_state: ActuationState
    reason_code: str
    message: str
    since: datetime
    blocking_new_ground_link_up: bool
    affected_pairs: list[list[str]] = Field(default_factory=list)
    desired_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    actual_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    ome_visible_scheduled_pairs_for_gs: list[list[str]] = Field(default_factory=list)
    recovery_status: RecoveryStatus = Field(default_factory=RecoveryStatus)
    last_event: dict[str, Any] = Field(default_factory=dict)


class ActuationHealthGroundStation(BaseModel):
    model_config = ConfigDict(frozen=True)

    gs_id: str
    actuation_state: ActuationState
    since: datetime | None = None
    reason_code: str | None = None
    blocking_new_ground_link_up: bool
    recovery_status: RecoveryStatus = Field(default_factory=RecoveryStatus)
    last_event: dict[str, Any] = Field(default_factory=dict)


class ActuationHealthInstance(BaseModel):
    model_config = ConfigDict(frozen=True)

    scheduler_instance_id: str
    hostname: str
    status: InstanceHealthStatus
    ground_stations: list[ActuationHealthGroundStation]


class ActuationHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    wiring_generation: str
    scheduler_instances: list[ActuationHealthInstance]


class OperatorRepairCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    wiring_generation: str
    scheduler_instance_id: str
    gs_id: str
    reason: str
    intervention_id: str


class OperatorRepairResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["accepted", "rejected", "error"]
    intervention_id: str
    message: str


class OperatorInterventionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    intervention_id: str
    session_id: str
    wiring_generation: str
    scheduler_instance_id: str
    hostname: str
    gs_id: str
    reason: str
    status: str
    requested_at: datetime
    updated_at: datetime
    repair_authority_pairs: list[list[str]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
