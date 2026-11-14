# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Scheduler operational contracts for actuation trust.

These models are the typed producer-side shape for Scheduler OpsEvent details
and explicit operator repair commands. OpsEvent keeps a free-form ``details``
dict for wire compatibility; Scheduler code constructs that dict from these
models so actuation-failure states are not ad hoc strings. VS-API's actuation
health payload has its own contract in ``nodalarc.models.vs_api``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, TypeIs, get_args

from pydantic import BaseModel, ConfigDict, Field


class ActuationState(StrEnum):
    CLEAN = "clean"
    ACTUATION_BLOCKED = "actuation_blocked"
    KERNEL_DIRTY = "kernel_dirty"
    UNKNOWN = "unknown"


# The roster states in which a node's actuation is not proven.
type FailingActuationState = Literal[ActuationState.KERNEL_DIRTY, ActuationState.ACTUATION_BLOCKED]
_FAILING_ACTUATION_STATES = frozenset(get_args(FailingActuationState.__value__))


def is_failing_actuation(state: ActuationState) -> TypeIs[FailingActuationState]:
    """Whether a roster state fails the node's actuation proof."""
    return state in _FAILING_ACTUATION_STATES


def parse_actuation_state(value: object) -> ActuationState:
    """Parse operator-facing actuation state without inventing a clean default."""
    if isinstance(value, ActuationState):
        return value
    if isinstance(value, str):
        try:
            return ActuationState(value)
        except ValueError:
            return ActuationState.UNKNOWN
    return ActuationState.UNKNOWN


class SchedulerOpsCode(StrEnum):
    REPLACEMENT_LINK_UP_FAILED = "REPLACEMENT_LINK_UP_FAILED"
    LINK_UP_FAILED = "LINK_UP_FAILED"
    LINK_DOWN_FAILED = "LINK_DOWN_FAILED"
    LATENCY_UPDATE_FAILED = "LATENCY_UPDATE_FAILED"
    OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR = "OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR"
    ACTUATION_BLOCKED = "ACTUATION_BLOCKED"
    ACTUATION_CLEAN = "ACTUATION_CLEAN"
    KERNEL_DIRTY = "KERNEL_DIRTY"
    KERNEL_VERIFY_ATTEMPTED = "KERNEL_VERIFY_ATTEMPTED"
    KERNEL_VERIFY_EXHAUSTED = "KERNEL_VERIFY_EXHAUSTED"
    AUTHORITY_SUBSET_VIOLATION = "AUTHORITY_SUBSET_VIOLATION"
    ACTUATION_HALTED = "ACTUATION_HALTED"
    SCHEDULER_SUPERSEDED = "SCHEDULER_SUPERSEDED"
    OPERATOR_REPAIR_REQUESTED = "OPERATOR_REPAIR_REQUESTED"
    OPERATOR_REPAIR_STARTED = "OPERATOR_REPAIR_STARTED"
    OPERATOR_REPAIR_SUCCEEDED = "OPERATOR_REPAIR_SUCCEEDED"
    OPERATOR_REPAIR_FAILED = "OPERATOR_REPAIR_FAILED"
    OPERATOR_REPAIR_REJECTED = "OPERATOR_REPAIR_REJECTED"
    AGENT_DEGRADED = "AGENT_DEGRADED"
    AGENT_RECOVERED = "AGENT_RECOVERED"


class ActuationFailureClass(StrEnum):
    NONE = "none"
    AUTHORITY_INVARIANT = "authority_invariant"
    FENCE = "fence"
    # The Node Agent answered a clean failure: it changed nothing.
    CLEAN_FAILURE = "clean_failure"
    # The Node Agent reported dirty kernel state, or a success it did not prove.
    KERNEL_DIRTY = "kernel_dirty"
    # The reply does not match the request, so what the kernel holds is not known.
    UNKNOWN_OUTCOME = "unknown_outcome"
    # The prover could not be reached at all (no responders, transport
    # timeout). For a mutating command this still implies conservative
    # unknown or dirty handling; for a read-only proof it is evidence of
    # nothing: "could not observe" is never reported as observed divergence.
    AGENT_UNREACHABLE = "agent_unreachable"


class RecoveryStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    verify_attempt_count: int = 0
    last_verify_result: str | None = None
    next_verify_after: datetime | None = None
    verify_exhausted: bool = False
    operator_action_required: bool = False
    active_intervention_id: str | None = None


class LinkFootprint(BaseModel):
    """Where a link sits in the kernel: its two nodes in pair order and the interface of each."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair: list[str] = Field(min_length=2, max_length=2)
    interfaces: list[str] = Field(min_length=2, max_length=2)


class CommandedLink(LinkFootprint):
    """A link the Node Agents proved up, with the delay commanded on each direction.

    ``netem_ms`` is (a_to_b, b_to_a) in pair order, the substrate compensation already taken
    off: what the kernel's delay queues were told to hold. A successor Scheduler rebuilds its
    record of the link from these values, and KernelInventory proves the kernel against them
    until the successor commands the link's next delays.
    """

    netem_ms: list[float] = Field(min_length=2, max_length=2)


class ActuationOpsDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    wiring_generation: str
    scheduler_instance_id: str
    hostname: str
    sim_time: datetime | None = None
    epoch_id: int | None = None
    snapshot_seq: int | None = None
    # The node whose actuation state the event is about; None on an event about no one node.
    node_id: str | None = None
    operation: str
    failure_class: ActuationFailureClass
    affected_pairs: list[list[str]] = Field(default_factory=list)
    successor_pair: list[str] | None = None
    old_pair: list[str] | None = None
    # The node's links: those it chooses, as the Scheduler desires them and as the Node
    # Agents proved them.
    desired_pairs: list[list[str]] = Field(default_factory=list)
    actual_pairs: list[list[str]] = Field(default_factory=list)
    # The node's links that may still hold kernel state and must be down (its stale
    # footprint), so a successor Scheduler proves and repairs the same footprint.
    stale_links: list[LinkFootprint] = Field(default_factory=list)
    ome_visible_scheduled_pairs: list[list[str]] = Field(default_factory=list)
    node_agent_results: list[dict[str, Any]] = Field(default_factory=list)
    actuation_state_before: ActuationState = ActuationState.UNKNOWN
    actuation_state_after: ActuationState = ActuationState.UNKNOWN
    recovery_status: RecoveryStatus = Field(default_factory=RecoveryStatus)
    intervention_id: str | None = None
    reason: str | None = None
    # Operator-facing guidance: what to do about this event, in words. Set
    # whenever the event represents a state needing (or explicitly NOT
    # needing) human action.
    remediation: str | None = None


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


class DrainingLinkEnd(BaseModel):
    """One end of a draining contact that still receives: the packets its peer sent before
    admission stopped keep arriving until ``receives_until`` (Scheduler wall clock, UTC), the
    proven stop plus the light time of the direction the end receives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    interface: str
    receives_until: datetime


class DrainingContact(BaseModel):
    """A long-delay contact whose transmitters stopped admitting and whose ends still receive.

    ``stopped_at`` is when the Scheduler had the Node Agents' proof that both transmitters
    stopped (Scheduler wall clock, UTC). ``ends`` lists the ends not yet closed; the contact
    leaves the snapshot when the last one closes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair: list[str]
    # The link's interfaces, in pair order.
    interfaces: list[str]
    contact_id: str
    stopped_at: datetime
    ends: list[DrainingLinkEnd] = Field(min_length=1)


class ActualLinkSnapshot(BaseModel):
    """One Scheduler instance's verified kernel-actual link set, recoverable.

    ``active_links`` is ``_actual_links`` — the links the Node Agents have
    CONFIRMED active (verified=true proof), i.e. "kernel actual", NOT OME's
    desired/visible model. Published replace-not-merge to a per-instance retained
    subject (``actual_links_subject``) whenever membership changes, so VS-API
    recovers which pairs the kernel actually has up on (re)subscribe — the only
    recoverable kernel-actual source, since LinkUp/LinkDown are NEW-delivered.
    ``scheduler_instance_id`` keys the retained subject so a restart does not
    clobber a dead predecessor's message. ``writer_epoch`` is the writer Lease
    epoch the instance commanded at; ``current_actual_links`` picks the current
    snapshot by it. A restarted Scheduler takes its predecessor's links from the
    current snapshot before it commands anything.

    ``wired_sandboxes`` names, for every session node, the CRI sandbox of the pod the Node
    Agent wired, as the instance's wiring gate proved it. The links in ``active_links`` live in
    those sandboxes' network namespaces. A pod replaced under the same wiring generation is
    wired anew from nothing, so a successor takes over only what sits in sandboxes its own
    wiring gate still proves.

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
    writer_epoch: int
    hostname: str
    sim_time: datetime | None = None
    epoch_id: int | None = None
    snapshot_seq: int | None = None
    active_links: list[CommandedLink]
    wired_sandboxes: dict[str, str]
    pending_pairs: list[PendingActuationPair] = Field(default_factory=list)
    # Long-delay contacts that stopped admitting and still deliver; never in ``active_links``.
    draining_contacts: list[DrainingContact] = Field(default_factory=list)
    emitted_at: datetime


def current_actual_links(
    snapshots: Iterable[ActualLinkSnapshot], *, session_id: str, wiring_generation: str
) -> ActualLinkSnapshot | None:
    """The snapshot of the Scheduler that last commanded a session's wiring generation.

    Each Scheduler instance retains one snapshot on its own subject, at the writer epoch it
    commanded with. The writer Lease gives each instance that takes it a higher epoch than the
    one before, so the snapshot at the highest epoch is the last commander's. Arrival order and
    pod clocks play no part. None when no instance of this generation published one. Two
    snapshots at one epoch break the Lease's guarantee and raise.
    """
    matching = [
        snapshot
        for snapshot in snapshots
        if snapshot.session_id == session_id and snapshot.wiring_generation == wiring_generation
    ]
    if not matching:
        return None
    top = max(snapshot.writer_epoch for snapshot in matching)
    current = [snapshot for snapshot in matching if snapshot.writer_epoch == top]
    if len(current) > 1:
        raise ValueError(
            f"session {session_id} generation {wiring_generation}: Scheduler instances "
            f"{sorted(s.scheduler_instance_id for s in current)} all published at writer epoch "
            f"{top}; the writer Lease gives each instance its own epoch"
        )
    return current[0]


class OperatorRepairRequest(BaseModel):
    """What an operator sends to repair the links one node chooses. VS-API fills the
    Scheduler instance and wiring generation from the node's latest actuation state
    when the request leaves them out, then sends an OperatorRepairCommand."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    scheduler_instance_id: str | None = None
    wiring_generation: str | None = None
    intervention_id: str | None = None


class OperatorRepairCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    wiring_generation: str
    scheduler_instance_id: str
    node_id: str
    reason: str
    intervention_id: str


class OperatorRepairResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["accepted", "rejected"]
    intervention_id: str
    message: str
