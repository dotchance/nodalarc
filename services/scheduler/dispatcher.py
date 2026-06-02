# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scheduler dispatch loop — event-driven architecture with async queue.

Decision callbacks consume NATS events and produce desired link state
snapshots onto an asyncio.Queue. A background dispatch worker reads from
the queue, diffs desired vs actual, and dispatches to Node Agents.

The decision callbacks NEVER block on Node Agent I/O. The dispatch worker
NEVER blocks the NATS callback pipeline. They communicate through the
queue — the native asyncio primitive for this pattern.

_reconcile_links remains the single path to the Node Agent for link state.
LinkStateSnapshot is applied as replace-not-merge.

INVARIANT: visible=True, scheduled=False for a GS pair MUST remove the
pair from desired state. test_ome_scheduler_contract.py verifies this.

TODO(trust-gap-closure#4): Structured failure telemetry. Every trust-relevant
failure (schema incompatibility, wiring timeout, unrepresentable latency,
missing OME geometry, authority freshness violation) should publish an
OpsFailureEvent to NATS nodalarc.ops.* BEFORE raising/logging. This makes
failures queryable from VS-API /api/v1/ops/events and visible to operator
dashboards. Pre-NATS-connection failures (wiring gate timeout, checkpoint
decode) must buffer to a local file and drain on first connection.

TODO(trust-gap-closure#3): Complete dispatch extraction. The dispatch planner
(dispatch_planner.py) classifies MBB changes but _reconcile_mbb still owns
phase ordering, greedy reservation, and state mutation. The planner should
return a DispatchPlan (ordered sequence of DispatchPhase objects) that the
actuator executes. The Dispatcher should orchestrate, not plan. This makes
the full BBM/MBB phase logic testable without constructing a Dispatcher.

TODO(trust-gap-closure#6): Checkpoint lineage events. When the OME starts
a new lineage (fresh start, stale checkpoint, incompatible checkpoint), it
should publish a dedicated SessionLineageReset event before the first
snapshot or tick. The Scheduler, on receiving the event, clears
_last_snapshot_seq, _actual_links, and _desired_links, and re-enters
SUSPENDED. This prevents sequence regressions from being silent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import nats
from nodalarc.models.events import OpsEvent, PlaybackState, SessionEphemeris, VisibilityEvent
from nodalarc.models.link_decisions import GroundLinkDecisionSnapshot
from nodalarc.models.link_events import LinkDecisionProvenance
from nodalarc.models.link_state import LinkStateSnapshot
from nodalarc.models.scheduler_ops import (
    ActualLinkSnapshot,
    ActuationOpsDetails,
    ActuationState,
    OperatorRepairCommand,
    OperatorRepairResponse,
    PendingActuationPair,
    SchedulerOpsCode,
)
from nodalarc.models.scheduler_ops import (
    ActuationFailureClass as OpsActuationFailureClass,
)
from nodalarc.models.scheduler_ops import (
    RecoveryStatus as OpsRecoveryStatus,
)
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    STREAM_LINK_EVENTS,
    STREAM_OME_EVENTS,
    actual_links_subject,
    actuation_state_subject,
    ground_link_decision_snapshot_subject,
    latency_update_subject,
    link_down_subject,
    link_state_snapshot_subject,
    link_up_subject,
    nats_url,
    ome_all_subject,
    ome_clock_subject,
    ome_visibility_subject,
    ops_event_subject,
    playback_state_subject,
    scenario_inject_subject,
    scheduler_repair_subject,
    scheduling_checkpoint_subject,
    session_ephemeris_subject,
)
from nodalarc.substrate.measurement_contract import RequiredSubstratePair, SubstrateMeasurement
from pydantic import ValidationError

from scheduler.actuation import (
    ActuationFailureClass,
    ActuationResult,
    GroundActuationState,
    next_verify_time,
)
from scheduler.actuation import (
    RecoveryStatus as SchedulerRecoveryStatus,
)
from scheduler.agent_pool import AgentPool
from scheduler.desired_state import (
    ActiveLinkInfo,
    desired_link_from_snapshot_link,
    desired_link_from_visibility,
)
from scheduler.dispatch_actuator import (
    send_authoritative_latency_updates,
    send_batch_down,
    send_batch_up,
    verify_ground_kernel_inventory,
)
from scheduler.dispatch_planner import (
    classify_mbb_changes,
    diff_link_state,
    gs_id_for_pair,
    sat_id_for_gs_pair,
)
from scheduler.epoch_sync import EpochSyncState
from scheduler.latency_compensator import LatencyCompensation, compensate_latency
from scheduler.pod_locator import PodLocationMap
from scheduler.substrate_latency import (
    load_substrate_status_documents,
    resolve_substrate_rtt_ms,
    validate_required_substrate_measurements,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchIntent:
    """Typed payload for the dispatch queue.

    Structurally frozen: field reassignment prevented by frozen=True.
    Dict contents are mutable by Python convention — the actuator does
    not mutate them. This is a convention boundary, not a type-level
    guarantee.

    Fields:
        desired: effective desired topology (raw desired minus overrides)
        down_reasons: reason per override-caused removal, captured at
            enqueue time. Actuator uses down_reasons.get(pair, "vis_lost").
        forced_bbm_pairs: pairs that must use BBM regardless of spare
            capacity. For ground links, forces the entire GS segment to
            BBM if any pair in the segment is forced. For ISLs, per-pair.
        sim_time: captured at enqueue time for LinkUp/LinkDown timestamps.
        source: origin for logging only — does not drive actuator behavior.
        rebaseline_counts: True for snapshot-sourced intents. The dispatch
            worker calls _rebaseline_active_counts() before reconciling.
            OR'd across drained intents to preserve the side effect.
    """

    desired: dict[tuple[str, str], ActiveLinkInfo]
    down_reasons: dict[tuple[str, str], str]
    forced_bbm_pairs: frozenset[tuple[str, str]]
    sim_time: datetime
    source: Literal["ome_event", "snapshot", "scenario", "resume"]
    rebaseline_counts: bool = False


class Dispatcher:
    """Event-driven topology dispatcher — NATS JetStream transport.

    Architecture: Decision Engine (NATS callbacks) and Actuator (dispatch
    worker) communicate through an asyncio.Queue. The decision callbacks
    compute desired state and put it on the queue. The dispatch worker
    reads desired state, diffs against actual, and dispatches to Node Agents.

    The decision callbacks NEVER await Node Agent I/O. The dispatch worker
    can take as long as it needs without blocking message ingestion.

    _actual_links: what the Node Agents have confirmed as active.
    Written ONLY by the dispatch worker after Node Agent ACK.
    Read by decision callbacks to build desired from actual + deltas.

    In asyncio single-threaded event loop, callbacks and worker never
    execute simultaneously between await points. No lock needed for
    _actual_links access.
    """

    def __init__(
        self,
        interface_map: dict[tuple[str, str], tuple[str, str]],
        bandwidth_map: dict[tuple[str, str], float],
        pod_locator: PodLocationMap,
        agent_pool: AgentPool,
        session_id: str,
        wiring_generation: str,
        max_latency_age_s: float,
        required_substrate_pairs: list[RequiredSubstratePair] | None = None,
        substrate_measurements: dict[str, SubstrateMeasurement] | None = None,
        compression_factor: int = 1,
        epsilon_ms: float = 100.0,
        gs_terminal_capacities: dict[str, int] | None = None,
        sat_ground_terminal_capacities: dict[str, int] | None = None,
        mbb_dispatch: bool = False,
        rtt_to_one_way_policy: Literal["half-rtt"] = "half-rtt",
        clean_kernel_audit_interval_s: float | None = 60.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now if now is not None else lambda: datetime.now(UTC)
        self._interface_map = interface_map
        self._bandwidth_map = bandwidth_map
        self._loc = pod_locator
        self._pool = agent_pool
        if max_latency_age_s <= 0:
            raise ValueError("max_latency_age_s must be > 0")
        self._max_latency_age_s = max_latency_age_s
        self._compression = max(1, compression_factor)
        self._epsilon_ms = epsilon_ms
        if rtt_to_one_way_policy != "half-rtt":
            raise ValueError(f"Unsupported RTT conversion policy: {rtt_to_one_way_policy!r}")
        self._rtt_to_one_way_policy = rtt_to_one_way_policy
        if clean_kernel_audit_interval_s is not None and clean_kernel_audit_interval_s <= 0:
            raise ValueError("clean_kernel_audit_interval_s must be > 0 when set")
        self._clean_kernel_audit_interval_s = clean_kernel_audit_interval_s
        self._last_clean_kernel_audit_at = self._now()
        self._recoverable_state_heartbeat_interval_s = 60.0
        self._last_recoverable_state_heartbeat_at = self._now()
        if gs_terminal_capacities is None:
            log.error("FATAL: Dispatcher created with no gs_terminal_capacities")
            raise ValueError("gs_terminal_capacities is required")
        if sat_ground_terminal_capacities is None:
            log.error("FATAL: Dispatcher created with no sat_ground_terminal_capacities")
            raise ValueError("sat_ground_terminal_capacities is required")
        self._gs_capacities = gs_terminal_capacities
        self._sat_capacities = sat_ground_terminal_capacities
        self._mbb_dispatch = mbb_dispatch
        self._session_id = session_id
        if not wiring_generation:
            raise ValueError("wiring_generation is required")
        self._wiring_generation = wiring_generation
        self._hostname = socket.gethostname()
        self._scheduler_instance_id = (
            f"{self._hostname}-{os.getpid()}-{int(self._now().timestamp() * 1000)}"
        )
        self._js = None
        self._nc = None

        # Session-scoped NATS subjects
        self._subj_visibility = ome_visibility_subject(session_id)
        self._subj_clock = ome_clock_subject(session_id)
        self._subj_ome_all = ome_all_subject(session_id)
        self._subj_ephemeris = session_ephemeris_subject(session_id)
        self._subj_playback = playback_state_subject(session_id)
        self._subj_checkpoint = scheduling_checkpoint_subject(session_id)
        self._subj_link_snapshot = link_state_snapshot_subject(session_id)
        self._subj_link_decisions = ground_link_decision_snapshot_subject(session_id)
        self._subj_link_up = link_up_subject(session_id)
        self._subj_link_down = link_down_subject(session_id)
        self._subj_latency = latency_update_subject(session_id)
        self._subj_repair = scheduler_repair_subject(session_id)

        # Decision engine state: what SHOULD be active (based on OME events).
        # Written ONLY by callbacks. Snapshots replace entirely. Events modify
        # incrementally. The queue carries copies to the dispatch worker.
        self._desired_links: dict[tuple[str, str], ActiveLinkInfo] = {}

        # Latest GroundLinkDecisionSnapshot from OME (Phase 1.3.b). Passive
        # storage in this sub-phase; Phase 1.4 reads it to construct
        # ``_ome_view`` and detect divergence from ``_desired_links``.
        # Reset to None on each Scheduler start; rebuilt from NATS
        # subscription (Direction 4: state derived from authority, not
        # local invention).
        self._latest_decision_snapshot: GroundLinkDecisionSnapshot | None = None

        # Parallel OME-view dict: the OME's stated truth for each pair
        # the OME has spoken about. Used by the C-A subset invariant
        # repro test to detect divergence from ``_desired_links``.
        #
        # Lifecycle (Phase 1.4 introduces the dict; Phase 5 promotes
        # the divergence check to a production fail-loud RuntimeError
        # and graduates ``_ome_view`` to the authoritative model
        # replacing ``_desired_links`` after the safety-net override
        # is removed):
        #   - Construction: incremental from VisibilityEvent; full
        #     replacement from LinkStateSnapshot.
        #   - Reset: cleared on Scheduler SUSPENDED → playing
        #     transition; rebuilt from the first post-resume snapshot.
        #   - Memory bound: per-pair, bounded by the constellation's
        #     pair count (configured at session start).
        #   - Multi-replica (Direction 4): each Scheduler replica
        #     computes ``_ome_view`` from its own NATS subscription.
        #     Replicas see the same events and converge to the same
        #     view. The C-A repro asserts per-replica.
        #   - Tuple shape mirrors event_diff's GroundVisibilityState
        #     and LinkStateSnapshot's gs_state for direct comparison:
        #     (visible: bool, scheduled: bool, sched_state: str).
        self._ome_view: dict[tuple[str, str], tuple[bool, bool, str]] = {}

        # Actuator state: what IS active (confirmed by Node Agent).
        # Written ONLY by the dispatch worker after Node Agent ACK.
        self._actual_links: dict[tuple[str, str], ActiveLinkInfo] = {}

        # Scheduler-owned in_flight -> faulted clock: per-pair wall-clock origin of a
        # desired-but-not-kernel-actual pair, captured at fold time. value =
        # (pending_since, epoch_id, snapshot_seq). The Scheduler owns this because it
        # owns actuation (explainability Ownership Boundaries); VS-API used to derive it
        # from when it observed a snapshot — an end-to-end measure that mismatched the
        # actuation bound and reset on VS-API restart. Published on ActualLinkSnapshot.
        self._pending_since: dict[tuple[str, str], tuple[datetime, int, int]] = {}

        # Set when a membership change is detected, cleared only after a SUCCESSFUL
        # publish. A publish that raises (swallowed at the reconcile finally so it never
        # masks the in-flight dispatch exception) leaves this True, so the next reconcile
        # re-publishes even with no further membership change — otherwise a dropped
        # convergence publish would strand a converged pair in VS-API's retained pending
        # set, where it ages past fault_after_ms and falsely renders faulted-red.
        self._actual_links_publish_dirty: bool = False

        # Incremental active-link counters for O(1) MBB capacity checks.
        # Re-baselined from _actual_links on every LinkStateSnapshot.
        self._gs_active_count: dict[str, int] = {}
        self._sat_active_count: dict[str, int] = {}

        self._last_latencies: dict[tuple[str, str], float] = {}
        self._current_sim_time: datetime | None = None
        self._running = False
        self._last_snapshot_seq: int = 0
        self._last_snapshot_sim_time: datetime | None = None
        self._required_substrate_pairs = list(required_substrate_pairs or [])
        self._substrate_by_direction: dict[str, SubstrateMeasurement] = dict(
            substrate_measurements or {}
        )
        self._last_substrate_reload: datetime | None = None
        self._dispatch_blocked_reason: str | None = None

        # Epoch id of the most recent applied LinkStateSnapshot. Used
        # together with _last_snapshot_seq and _last_snapshot_sim_time
        # to enforce the pairing contract between LinkStateSnapshot and
        # GroundLinkDecisionSnapshot (see paired_decision_snapshot below).
        self._last_snapshot_epoch_id: int = 0

        # Control plane state: scenario overrides. Values are reason strings.
        # Mutated only by _on_scenario_command on the main event loop.
        self._override_pairs: dict[tuple[str, str], str] = {}
        self._override_nodes: dict[str, str] = {}

        # Ground links currently in MBB teardown state (held for overlap).
        # Used by the safety check to identify teardown-expired removals.
        self._teardown_pairs: set[tuple[str, str]] = set()

        # Visibility batching state owned by the Scheduler epoch lifecycle.
        # Reverse seeks can legitimately make master sim_time decrease across
        # epochs; the epoch boundary resets these fields before new-epoch
        # VisibilityEvents are accepted.
        self._pending_visibility_events: list[VisibilityEvent] = []
        self._last_visibility_sim_time: datetime | None = None

        # Epoch synchronization — only active during Tier 2 seek.
        # The Scheduler starts UNSUSPENDED. It receives snapshots and
        # dispatches immediately. SUSPENDED is entered ONLY when the OME
        # publishes PlaybackState(state="seeking"), signaling a sim_time
        # discontinuity that requires fresh ephemeris + snapshot before
        # dispatch can resume safely.
        self._epoch_sync = EpochSyncState()
        self._watchdog_task: asyncio.Task | None = None
        self._scenario_sub = None
        self._repair_sub = None

        # Per-ground-station actuation truth. OME authority changes never clear
        # this state; only read-only kernel proof or explicit operator repair can.
        now_value = self._now()
        self._gs_actuation: dict[str, GroundActuationState] = {
            gs_id: GroundActuationState(gs_id=gs_id, since=now_value)
            for gs_id in sorted(self._gs_capacities)
        }
        self._gs_stale_link_infos: dict[tuple[str, str], ActiveLinkInfo] = {}
        self._pending_fold_diagnostics: list[dict] = []
        self._active_repair_tasks: set[asyncio.Task] = set()
        self._repair_original_states: dict[str, GroundActuationState] = {}
        self._max_kernel_verify_attempts = 5
        # Serializes normal reconciliation and explicit operator repair. The
        # Scheduler must never have two concurrent writers to Node Agent or
        # _actual_links for the same GS.
        self._actuation_lock = asyncio.Lock()

        # Queue: decision engine / control plane → actuator (dispatch worker)
        self._dispatch_queue: asyncio.Queue[DispatchIntent | None] = asyncio.Queue()

    # Backward compat: tests that reference _active_links
    @property
    def _active_links(self) -> dict[tuple[str, str], ActiveLinkInfo]:
        return self._actual_links

    @property
    def _suspended(self) -> bool:
        return self._epoch_sync.suspended

    @_suspended.setter
    def _suspended(self, value: bool) -> None:
        self._epoch_sync.suspended = value

    @property
    def _expected_epoch_id(self) -> int:
        return self._epoch_sync.expected_epoch_id

    @_expected_epoch_id.setter
    def _expected_epoch_id(self, value: int) -> None:
        self._epoch_sync.expected_epoch_id = value

    @property
    def _playback_playing_received(self) -> bool:
        return self._epoch_sync.playback_playing_received

    @_playback_playing_received.setter
    def _playback_playing_received(self, value: bool) -> None:
        self._epoch_sync.playback_playing_received = value

    @property
    def _epoch_deps_met(self) -> dict[str, bool]:
        return self._epoch_sync.deps_met

    @_epoch_deps_met.setter
    def _epoch_deps_met(self, value: dict[str, bool]) -> None:
        self._epoch_sync.deps_met = value

    @property
    def _buffered_snapshot(self) -> LinkStateSnapshot | None:
        return self._epoch_sync.buffered_snapshot

    @_buffered_snapshot.setter
    def _buffered_snapshot(self, value: LinkStateSnapshot | None) -> None:
        self._epoch_sync.buffered_snapshot = value

    @property
    def _stale(self) -> bool:
        return self._epoch_sync.stale

    @_stale.setter
    def _stale(self, value: bool) -> None:
        self._epoch_sync.stale = value

    def _rebaseline_active_counts(self) -> None:
        """Re-derive incremental counters from _actual_links (source of truth).

        Called on every LinkStateSnapshot to bound any drift from lost ACKs.
        O(actual_links) — runs at snapshot frequency (every 5 sim-seconds),
        not at tick frequency.
        """
        self._gs_active_count.clear()
        self._sat_active_count.clear()
        for (node_a, node_b), info in self._actual_links.items():
            if info.link_type == "ground":
                for nid in (node_a, node_b):
                    if nid in self._gs_capacities:
                        self._gs_active_count[nid] = self._gs_active_count.get(nid, 0) + 1
                    elif nid in self._sat_capacities:
                        self._sat_active_count[nid] = self._sat_active_count.get(nid, 0) + 1

    def _increment_active_counts(self, pair: tuple[str, str]) -> None:
        """O(1) increment after a successful ground LinkUp ACK."""
        info = self._actual_links.get(pair)
        if not info or info.link_type != "ground":
            return
        for nid in pair:
            if nid in self._gs_capacities:
                self._gs_active_count[nid] = self._gs_active_count.get(nid, 0) + 1
            elif nid in self._sat_capacities:
                self._sat_active_count[nid] = self._sat_active_count.get(nid, 0) + 1

    def _decrement_active_counts(
        self, pair: tuple[str, str], info: ActiveLinkInfo | None = None
    ) -> None:
        """O(1) decrement after a successful ground LinkDown ACK."""
        if info is None:
            info = self._actual_links.get(pair)
        if not info or info.link_type != "ground":
            return
        for nid in pair:
            if nid in self._gs_capacities:
                self._gs_active_count[nid] = max(0, self._gs_active_count.get(nid, 0) - 1)
            elif nid in self._sat_capacities:
                self._sat_active_count[nid] = max(0, self._sat_active_count.get(nid, 0) - 1)

    @_active_links.setter
    def _active_links(self, value: dict[tuple[str, str], ActiveLinkInfo]) -> None:
        self._actual_links = value

    def _reset_epoch_local_authority(self) -> None:
        """Clear OME-authority state that cannot cross a seek epoch boundary."""
        self._desired_links.clear()
        self._ome_view.clear()
        self._teardown_pairs.clear()
        self._latest_decision_snapshot = None
        self._pending_visibility_events.clear()
        self._last_visibility_sim_time = None
        self._last_snapshot_sim_time = None
        # The old epoch's desired-but-not-actual pairs are void: their pending_since
        # references a pre-seek desire. _actual_links survives (actuator truth); the
        # first post-seek reconcile re-stamps any still-divergent pair against the new
        # epoch, so the divergence clock never carries a stale-epoch origin.
        self._pending_since.clear()

    def _discard_pending_dispatch_intents(self) -> int:
        """Best-effort cleanup of queued old-epoch intents.

        Correctness does not rely on this queue drain. The dispatch worker's
        suspended-state guard is the invariant that prevents old-epoch intents
        from reaching the actuator; this drain only reduces wasted queue work.
        """
        discarded = 0
        while True:
            try:
                intent = self._dispatch_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if intent is None:
                self._dispatch_queue.put_nowait(None)
                break
            discarded += 1
        return discarded

    def _begin_seek_epoch(self, epoch_id: int) -> bool:
        """Enter seek suspension and reset all Scheduler-owned epoch state."""
        if not self._epoch_sync.begin_seek(epoch_id):
            return False
        self._reset_epoch_local_authority()
        self._last_snapshot_epoch_id = epoch_id
        discarded = self._discard_pending_dispatch_intents()
        if discarded:
            log.info("Discarded %d queued old-epoch dispatch intents on seek", discarded)
        return True

    async def _handle_visibility_event(self, vis: VisibilityEvent) -> None:
        """Apply one OME VisibilityEvent to the Scheduler batching state.

        This is the testable body of the NATS visibility callback. Reverse
        seeks can legitimately move master sim_time backward across epochs,
        but never inside an active epoch; _begin_seek_epoch owns that reset.
        """
        if self._suspended:
            log.debug("Seek suspended — dropping VisibilityEvent for %s/%s", vis.node_a, vis.node_b)
            return

        snap_sim = vis.sim_time

        # Flush BEFORE appending the new event. When an event from a
        # new tick arrives, all events from the previous tick are
        # complete and ready to dispatch atomically. The new event
        # then starts accumulating for its own tick.
        last_sim_time = self._last_visibility_sim_time
        pending_vis = self._pending_visibility_events
        if last_sim_time is not None and snap_sim != last_sim_time:
            delta_ms = (snap_sim - last_sim_time).total_seconds() * 1000
            if delta_ms < 0:
                raise RuntimeError(
                    "VisibilityEvent sim_time regressed outside an epoch seek: "
                    f"last={last_sim_time.isoformat()} current={snap_sim.isoformat()}"
                )
            if delta_ms > self._epsilon_ms and pending_vis:
                self._apply_events_to_desired(list(pending_vis))
                await self._assert_authority_subset_fail_loud("visibility-batch")
                await self._publish_fold_diagnostics(pending_vis[0].sim_time)
                intent = self._build_dispatch_intent(
                    sim_time=pending_vis[0].sim_time,
                    source="ome_event",
                )
                await self._dispatch_queue.put(intent)
                pending_vis.clear()

        pending_vis.append(vis)
        self._last_visibility_sim_time = snap_sim

    async def _handle_clock_tick_payload(self, data: dict) -> None:
        """Apply one OME ClockTick payload to batching/resume state."""
        tick_epoch_id = data.get("epoch_id")
        if tick_epoch_id is None:
            log.error("ClockTick missing epoch_id: %s", data)
            raise ValueError("ClockTick missing epoch_id")

        if self._suspended:
            if tick_epoch_id == self._expected_epoch_id:
                await self._try_resume_on_clock_tick(data)
            return

        tick_sim_str = data.get("sim_time")
        if not tick_sim_str:
            log.error("ClockTick missing sim_time: %s", data)
            raise ValueError("ClockTick missing sim_time")
        tick_sim_time = datetime.fromisoformat(tick_sim_str)
        self._current_sim_time = tick_sim_time

        # Flush ONLY events from sim_times strictly OLDER than this tick.
        # Events at the same sim_time as this tick may still be in flight
        # on the visibility subject (cross-subject NATS ordering is not
        # guaranteed). Flushing them now would split a tick's events
        # across two dispatch cycles, breaking break-before-make for
        # GS handovers (which emit two events at the same sim_time).
        pending_vis = self._pending_visibility_events
        if pending_vis:
            pending_sim = pending_vis[0].sim_time
            if pending_sim < tick_sim_time:
                self._apply_events_to_desired(list(pending_vis))
                await self._assert_authority_subset_fail_loud("visibility-batch")
                await self._publish_fold_diagnostics(pending_vis[0].sim_time)
                intent = self._build_dispatch_intent(
                    sim_time=pending_vis[0].sim_time,
                    source="ome_event",
                )
                await self._dispatch_queue.put(intent)
                pending_vis.clear()
                self._last_visibility_sim_time = tick_sim_time

    async def _handle_session_ephemeris(self, eph: SessionEphemeris) -> None:
        """Apply one retained/live SessionEphemeris control-plane message."""
        if eph.epoch_id == self._expected_epoch_id:
            self._epoch_sync.mark_ephemeris(eph.epoch_id)
            log.debug(
                "SessionEphemeris epoch_id=%d loaded: %d nodes",
                eph.epoch_id,
                len(eph.nodes),
            )
            await self._check_epoch_resume()
        else:
            log.debug(
                "SessionEphemeris epoch_id=%d ignored (expected %d)",
                eph.epoch_id,
                self._expected_epoch_id,
            )

    async def _handle_playback_state(self, ps: PlaybackState) -> None:
        """Apply one retained/live PlaybackState control-plane message."""
        if ps.state == "seeking" and ps.epoch_id > self._expected_epoch_id:
            # New seek — enter SUSPENDED state and clear old-epoch
            # Scheduler authority. _actual_links is actuator truth and is
            # deliberately preserved for reconciliation on resume.
            self._begin_seek_epoch(ps.epoch_id)
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()
            self._watchdog_task = asyncio.create_task(self._epoch_watchdog(ps.epoch_id))
            log.info("SUSPENDED: seeking epoch_id=%d", ps.epoch_id)
        elif ps.state == "playing" and ps.epoch_id == self._expected_epoch_id:
            self._epoch_sync.mark_playing(ps.epoch_id)
            log.debug("PlaybackState(playing, epoch_id=%d) received", ps.epoch_id)
            await self._check_epoch_resume()
        elif ps.state == "paused":
            log.debug("PlaybackState(paused, epoch_id=%d)", ps.epoch_id)

    async def _handle_link_state_snapshot(self, snapshot: LinkStateSnapshot) -> None:
        """Apply one retained/live authoritative LinkStateSnapshot."""
        if self._suspended:
            if self._epoch_sync.buffer_snapshot(snapshot):
                log.debug(
                    "Buffered LinkStateSnapshot seq=%d epoch_id=%d",
                    snapshot.snapshot_seq,
                    snapshot.epoch_id,
                )
                await self._check_epoch_resume()
            return

        desired = self._build_desired_from_snapshot(snapshot)
        if desired is not None:
            await self._assert_authority_subset_fail_loud("link-state-snapshot")
            intent = self._build_dispatch_intent(
                sim_time=snapshot.sim_time,
                source="snapshot",
                rebaseline_counts=True,
            )
            log.debug(
                "Snapshot seq=%d queued: %d links desired",
                snapshot.snapshot_seq,
                len(intent.desired),
            )
            await self._dispatch_queue.put(intent)

    def _build_dispatch_intent(
        self,
        sim_time: datetime,
        source: Literal["ome_event", "snapshot", "scenario", "resume"],
        rebaseline_counts: bool = False,
    ) -> DispatchIntent:
        """Build a DispatchIntent from current desired + override state.

        Computes effective desired (raw desired minus overrides), captures
        down_reasons and forced_bbm_pairs for override-caused removals at
        this instant. The actuator uses the returned intent without reading
        _override_pairs or _override_nodes.
        """
        effective = self._effective_desired_links()

        candidates = set(self._desired_links) | set(self._actual_links)

        down_reasons: dict[tuple[str, str], str] = {}
        forced_bbm: set[tuple[str, str]] = set()
        for pair in candidates:
            if pair not in effective:
                reason = self._override_pairs.get(pair)
                if not reason:
                    for nid in pair:
                        reason = self._override_nodes.get(nid)
                        if reason:
                            break
                if reason:
                    down_reasons[pair] = reason
                    forced_bbm.add(pair)

        return DispatchIntent(
            desired=effective,
            down_reasons=down_reasons,
            forced_bbm_pairs=frozenset(forced_bbm),
            sim_time=sim_time,
            source=source,
            rebaseline_counts=rebaseline_counts,
        )

    async def run(self, nc: nats.NATS | None = None, **_kwargs) -> None:
        """Main async dispatch loop — NATS JetStream subscription.

        Starts the dispatch worker as a background task, then subscribes
        to NATS events. Callbacks put desired state on the queue. The
        worker reconciles at its own pace.
        """
        self._running = True
        owns_nc = nc is None
        if nc is None:
            nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)

        from nodal.logging import connect as _connect_logging

        await _connect_logging(nc)

        self._nc = nc
        self._js = nc.jetstream()
        js = self._js

        # Share NATS connection with agent pool for Node Agent dispatch
        self._pool.set_nc(nc)

        log.debug("Scheduler NATS connected")

        # --- Read retained SchedulingCheckpoint for recovery context ---
        try:
            from nats.js.api import DeliverPolicy as _DP
            from nodalarc.nats_channels import (
                STREAM_SESSION_EVENTS,
            )

            ckpt_sub = await js.subscribe(
                self._subj_checkpoint,
                stream=STREAM_SESSION_EVENTS,
                ordered_consumer=True,
                deliver_policy=_DP.LAST_PER_SUBJECT,
            )
            try:
                from nodalarc.scheduling_checkpoint import decode_retained_scheduling_checkpoint

                ckpt_msg = await asyncio.wait_for(ckpt_sub.next_msg(), timeout=2.0)
                ckpt = decode_retained_scheduling_checkpoint(ckpt_msg.data)
                if ckpt is None:
                    log.info("Retained SchedulingCheckpoint is incompatible; starting fresh")
                else:
                    self._current_sim_time = ckpt.sim_time
                    log.info(
                        "Recovered SchedulingCheckpoint: sim_time=%s step=%d epoch_id=%d "
                        "snapshot_seq=%d associations=%d teardowns=%d",
                        ckpt.sim_time.isoformat(),
                        ckpt.step,
                        ckpt.epoch_id,
                        ckpt.snapshot_seq,
                        len(ckpt.associations),
                        len(ckpt.pending_teardowns),
                    )
            except TimeoutError as exc:
                log.info("No SchedulingCheckpoint retained (fresh session): %s", type(exc).__name__)
            finally:
                await ckpt_sub.unsubscribe()
        except Exception as exc:
            raise RuntimeError(
                "SchedulingCheckpoint recovery failed; refusing to start from unknown state"
            ) from exc

        # Load durable substrate measurement status for cross-node compensation.
        self._reload_substrate_status()
        await self._publish_startup_actuation_roster()
        # Recoverable kernel-actual baseline for this instance: on a fresh start
        # _actual_links is empty (rebuilt by reconcile from OME snapshots), but
        # publishing it now means VS-API recovers an authoritative "this instance
        # has these pairs up" via LAST_PER_SUBJECT even before the first membership
        # change, instead of an absent subject it must guess about.
        await self._publish_actual_links()

        # Scenario injection — core NATS request/reply (not JetStream).
        # Single-owner per session: if Scheduler replicas go above 1, this
        # needs a NATS queue group or leader election. Today replicas=1.
        self._subj_scenario = scenario_inject_subject(self._session_id)
        self._scenario_sub = await nc.subscribe(self._subj_scenario, cb=self._on_scenario_command)
        self._repair_sub = await nc.subscribe(
            self._subj_repair, cb=self._on_operator_repair_command
        )
        log.debug("Scenario subscription active: %s", self._subj_scenario)
        log.debug("Operator repair subscription active: %s", self._subj_repair)

        # Start dispatch worker BEFORE subscriptions — ready to receive work
        worker_task = asyncio.create_task(self._dispatch_worker(nc))

        # NOTE: No explicit catch-up pull. The SUSPENDED state machine is
        # the single source of startup state. The live LinkStateSnapshot
        # subscription below uses DeliverPolicy.LAST_PER_SUBJECT, which
        # delivers the JetStream-retained snapshot (MaxMsgsPerSubject=1)
        # immediately on subscribe. The _on_link_state_snapshot callback
        # buffers it until the SUSPENDED state machine resumes on the
        # first ClockTick(epoch_id=N) with all dependencies satisfied.

        # --- Callback-driven subscriptions ---
        # Callbacks compute desired state and put it on the dispatch queue.
        # They NEVER await Node Agent I/O. They return in microseconds.
        from nats.js.api import DeliverPolicy

        # CONCURRENCY NOTE: _pending_visibility_events is mutated by
        # _on_visibility and _on_clock_tick, which both yield control at
        # await queue.put(). Safe today because asyncio runs callbacks
        # cooperatively on one thread — no preemption between await points.
        # If this ever moves to a multi-threaded event loop, this batch must
        # be protected by an asyncio.Lock.

        async def _on_visibility(msg):
            data = json.loads(msg.data)
            try:
                vis = VisibilityEvent.model_validate(data)
            except ValidationError as exc:
                log.warning(
                    "Ignoring schema-incompatible retained VisibilityEvent on %s; "
                    "waiting for a current OME event: %s",
                    msg.subject,
                    exc,
                )
                return
            await self._handle_visibility_event(vis)

        async def _on_session_ephemeris(msg):
            try:
                eph = SessionEphemeris.model_validate_json(msg.data)
            except ValidationError as exc:
                log.warning(
                    "Ignoring schema-incompatible retained SessionEphemeris on %s; "
                    "waiting for OME to publish the current ephemeris: %s",
                    msg.subject,
                    exc,
                )
                return
            await self._handle_session_ephemeris(eph)

        async def _on_playback_state(msg):
            try:
                ps = PlaybackState.model_validate_json(msg.data)
            except ValidationError as exc:
                log.warning(
                    "Ignoring schema-incompatible retained PlaybackState on %s; "
                    "waiting for OME to publish current playback state: %s",
                    msg.subject,
                    exc,
                )
                return
            await self._handle_playback_state(ps)

        async def _on_clock_tick(msg):
            data = json.loads(msg.data)
            await self._handle_clock_tick_payload(data)

        async def _on_link_state_snapshot(msg):
            try:
                snapshot = LinkStateSnapshot.model_validate_json(msg.data)
            except ValidationError as exc:
                log.warning(
                    "Ignoring schema-incompatible retained LinkStateSnapshot on %s; "
                    "waiting for OME to publish current snapshot: %s",
                    msg.subject,
                    exc,
                )
                return

            await self._handle_link_state_snapshot(snapshot)

        async def _on_ground_link_decision_snapshot(msg):
            """Phase 1.3.b passive receiver — validate and store the
            decision snapshot. The Scheduler does not yet act on it.

            Phase 1.4 introduces ``_ome_view`` (the parallel divergence
            detector); Phase 5 promotes the subset invariant to a
            production ``RuntimeError`` and removes the safety-net
            override at ``dispatcher.py:835-857``.

            For now: validate the payload structure (fail-loud on any
            schema mismatch), retain the most recent snapshot for the
            ``_ome_view`` consumer in 1.4, and log a debug summary.
            Direction 4 (multi-compute-node): each Scheduler replica
            receives this independently from NATS — no replica-local
            state that does not survive failover.
            """
            try:
                decision_snapshot = GroundLinkDecisionSnapshot.model_validate_json(msg.data)
            except ValidationError as exc:
                log.warning(
                    "Ignoring schema-incompatible retained GroundLinkDecisionSnapshot on %s; "
                    "waiting for OME to publish current decision snapshot: %s",
                    msg.subject,
                    exc,
                )
                return

            self._latest_decision_snapshot = decision_snapshot
            log.debug(
                "GroundLinkDecisionSnapshot seq=%d epoch_id=%d: %d decisions, %d unscheduled",
                decision_snapshot.snapshot_seq,
                decision_snapshot.epoch_id,
                len(decision_snapshot.decisions),
                len(decision_snapshot.unscheduled_pairs),
            )

        subs = []

        subscriptions = [
            (
                self._subj_ephemeris,
                STREAM_SESSION_EVENTS,
                DeliverPolicy.LAST_PER_SUBJECT,
                _on_session_ephemeris,
            ),
            (
                self._subj_playback,
                STREAM_SESSION_EVENTS,
                DeliverPolicy.LAST_PER_SUBJECT,
                _on_playback_state,
            ),
            (
                self._subj_link_snapshot,
                STREAM_LINK_EVENTS,
                DeliverPolicy.LAST_PER_SUBJECT,
                _on_link_state_snapshot,
            ),
            (
                self._subj_link_decisions,
                STREAM_LINK_EVENTS,
                DeliverPolicy.LAST_PER_SUBJECT,
                _on_ground_link_decision_snapshot,
            ),
        ]
        for subj, stream, policy, cb in subscriptions:
            try:
                subs.append(
                    await js.subscribe(
                        subj,
                        stream=stream,
                        ordered_consumer=True,
                        deliver_policy=policy,
                        cb=cb,
                    )
                )
            except Exception as exc:
                log.error("FATAL: Failed to subscribe to %s on stream %s: %s", subj, stream, exc)
                raise

        # Single wildcard consumer for stream-sequence ordering across
        # subjects. Two separate consumers (one per subject) have
        # independent server-side push loops that can interleave:
        # ClockTick(T+1) may arrive before VisibilityEvent(T), splitting
        # a handover's paired events across dispatch cycles.
        async def _on_ome_event(msg):
            if msg.subject == self._subj_visibility:
                await _on_visibility(msg)
            elif msg.subject == self._subj_clock:
                await _on_clock_tick(msg)

        try:
            subs.append(
                await js.subscribe(
                    self._subj_ome_all,
                    stream=STREAM_OME_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=_on_ome_event,
                )
            )
        except Exception as exc:
            log.error(
                "FATAL: Failed to subscribe to %s on stream %s: %s",
                self._subj_ome_all,
                STREAM_OME_EVENTS,
                exc,
            )
            raise

        log.debug("Scheduler subscriptions active: %d", len(subs))

        # Wait for shutdown — callbacks handle all message processing
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            log.info("Dispatcher cancelled")
        finally:
            # Stop the dispatch worker
            self._running = False
            await self._dispatch_queue.put(None)  # sentinel
            await worker_task
            for sub in subs:
                try:
                    await sub.unsubscribe()
                except Exception as exc:
                    log.warning("Unsubscribe failed during shutdown: %s", exc)
            if self._scenario_sub:
                try:
                    await self._scenario_sub.unsubscribe()
                except Exception as exc:
                    log.warning("Scenario unsubscribe failed: %s", exc)
            if self._repair_sub:
                try:
                    await self._repair_sub.unsubscribe()
                except Exception as exc:
                    log.warning("Operator repair unsubscribe failed: %s", exc)
            if owns_nc:
                await nc.close()
            log.info("Dispatcher stopped")

    # ------------------------------------------------------------------
    # C-A subset invariant: _desired_links ⊆ OME's stated truth.
    #
    # Phase 1.4 introduces this as a pure-observation helper. The C-A
    # repro test calls it to assert divergence between _desired_links
    # and _ome_view when the safety-net override at lines 926-932 (in
    # _apply_events_to_desired) fires. Phase 5 promotes the check to a
    # production-time fail-loud RuntimeError in the same change set
    # that removes the override and adds the fail-loud
    # replacement-LinkUp-failure handling.
    # ------------------------------------------------------------------

    def paired_decision_snapshot(self) -> GroundLinkDecisionSnapshot | None:
        """Return the latest decision snapshot ONLY if it pairs with
        the latest applied LinkStateSnapshot — else None.

        The plan's contract claims decision and state snapshots share
        fate via the same stream + retention policy. Sharing a stream
        does NOT prove pairing: NATS delivery is async and per-subject;
        a Scheduler may receive the state snapshot for seq=N before
        the decision snapshot for seq=N (or vice versa), or one may
        be entirely missing during a stream restart.

        Consumers asking "why is this pair not scheduled?" need to
        know whether the diagnostic snapshot they hold actually
        corresponds to the state snapshot they reconciled against.
        This accessor enforces the pairing contract explicitly:
        ``(epoch_id, snapshot_seq, sim_time)`` must match exactly.
        On mismatch (or when one is missing), it returns None and the
        consumer must treat diagnostics as unavailable for the current
        state.

        Multi-replica (Direction 4): each Scheduler replica enforces
        pairing against its own observed snapshots. Replicas may pair
        at slightly different seqs during catch-up; that is correct.
        """
        ds = self._latest_decision_snapshot
        if ds is None:
            return None
        if self._last_snapshot_sim_time is None:
            return None
        if ds.snapshot_seq != self._last_snapshot_seq:
            return None
        if ds.epoch_id != self._last_snapshot_epoch_id:
            return None
        if ds.sim_time != self._last_snapshot_sim_time:
            return None
        return ds

    def authority_subset_violation(self) -> set[tuple[str, str]]:
        """Return pairs in `_desired_links` that `_ome_view` says the
        OME has NOT marked (visible AND scheduled).

        A non-empty result means the Scheduler's desired state diverges
        from the OME's stated truth — the safety-net override is the
        primary known cause today (line 926-932). Phase 5 will fail
        loud on any non-empty result.

        Pure observation. No state mutation. Multi-replica safe — each
        replica's view is derived from its own NATS subscription.
        """
        violations: set[tuple[str, str]] = set()
        for pair in self._desired_links:
            ome_state = self._ome_view.get(pair)
            if ome_state is None:
                # OME has not spoken about this pair at all — the
                # Scheduler is making it up. This is a violation.
                violations.add(pair)
                continue
            visible, scheduled, _sched_state = ome_state
            if not (visible and scheduled):
                violations.add(pair)
        return violations

    # ------------------------------------------------------------------
    # Phase 5 actuation trust helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pair_rows(
        pairs: set[tuple[str, str]] | frozenset[tuple[str, str]] | list[tuple[str, str]],
    ) -> list[list[str]]:
        return [[a, b] for a, b in sorted(pairs)]

    def _ground_gs_id(self, pair: tuple[str, str]) -> str | None:
        if pair[0] in self._gs_capacities:
            return pair[0]
        if pair[1] in self._gs_capacities:
            return pair[1]
        return None

    def _actual_ground_pairs_for_gs(self, gs_id: str) -> dict[tuple[str, str], ActiveLinkInfo]:
        return {
            pair: info
            for pair, info in self._actual_links.items()
            if info.link_type == "ground" and self._ground_gs_id(pair) == gs_id
        }

    def _desired_ground_pairs_for_gs(self, gs_id: str) -> dict[tuple[str, str], ActiveLinkInfo]:
        effective = self._effective_desired_links()
        return {
            pair: info
            for pair, info in effective.items()
            if info.link_type == "ground" and self._ground_gs_id(pair) == gs_id
        }

    def _ome_visible_scheduled_pairs_for_gs(self, gs_id: str) -> set[tuple[str, str]]:
        return {
            pair
            for pair, (visible, scheduled, _state) in self._ome_view.items()
            if visible and scheduled and self._ground_gs_id(pair) == gs_id
        }

    def _effective_desired_links(self) -> dict[tuple[str, str], ActiveLinkInfo]:
        return {
            pair: info
            for pair, info in self._desired_links.items()
            if pair not in self._override_pairs
            and pair[0] not in self._override_nodes
            and pair[1] not in self._override_nodes
        }

    def _ground_state(self, gs_id: str) -> GroundActuationState:
        state = self._gs_actuation.get(gs_id)
        if state is None:
            state = GroundActuationState(gs_id=gs_id)
            self._gs_actuation[gs_id] = state
        return state

    @staticmethod
    def _ops_failure_class(failure: ActuationFailureClass) -> OpsActuationFailureClass:
        mapping = {
            ActuationFailureClass.NONE: OpsActuationFailureClass.NONE,
            ActuationFailureClass.FENCE: OpsActuationFailureClass.FENCE,
            ActuationFailureClass.GROUND_CLEAN_FAILURE: OpsActuationFailureClass.GROUND_CLEAN_FAILURE,
            ActuationFailureClass.GROUND_KERNEL_DIRTY: OpsActuationFailureClass.GROUND_KERNEL_DIRTY,
            ActuationFailureClass.GROUND_UNKNOWN: OpsActuationFailureClass.GROUND_UNKNOWN,
            ActuationFailureClass.ISL_FAILURE: OpsActuationFailureClass.ISL_FAILURE,
        }
        return mapping[failure]

    @staticmethod
    def _ops_recovery(recovery: SchedulerRecoveryStatus) -> OpsRecoveryStatus:
        return OpsRecoveryStatus(
            verify_attempt_count=recovery.verify_attempt_count,
            last_verify_result=recovery.last_verify_result,
            next_verify_after=recovery.next_verify_after,
            verify_exhausted=recovery.verify_exhausted,
            operator_action_required=recovery.operator_action_required,
            active_intervention_id=recovery.active_intervention_id,
        )

    def _actuation_details(
        self,
        *,
        gs_id: str | None,
        operation: str,
        failure_class: ActuationFailureClass,
        affected_pairs: set[tuple[str, str]] | frozenset[tuple[str, str]] | None = None,
        result: ActuationResult | None = None,
        sim_time: datetime | None = None,
        state_before: GroundActuationState | None = None,
        state_after: GroundActuationState | None = None,
        intervention_id: str | None = None,
        reason: str | None = None,
    ) -> ActuationOpsDetails:
        affected = set(affected_pairs or set())
        before_value = state_before.state.value if state_before else "unknown"
        after_value = state_after.state.value if state_after else before_value
        recovery = state_after.recovery if state_after else SchedulerRecoveryStatus()
        return ActuationOpsDetails(
            session_id=self._session_id,
            wiring_generation=self._wiring_generation,
            scheduler_instance_id=self._scheduler_instance_id,
            hostname=self._hostname,
            sim_time=sim_time or self._current_sim_time,
            epoch_id=self._last_snapshot_epoch_id,
            snapshot_seq=self._last_snapshot_seq,
            gs_id=gs_id,
            operation=operation,
            failure_class=self._ops_failure_class(failure_class),
            affected_pairs=self._pair_rows(affected),
            desired_pairs_for_gs=self._pair_rows(set(self._desired_ground_pairs_for_gs(gs_id)))
            if gs_id
            else [],
            actual_pairs_for_gs=self._pair_rows(set(self._actual_ground_pairs_for_gs(gs_id)))
            if gs_id
            else [],
            ome_visible_scheduled_pairs_for_gs=self._pair_rows(
                self._ome_visible_scheduled_pairs_for_gs(gs_id)
            )
            if gs_id
            else [],
            node_agent_results=result.node_agent_details() if result else [],
            actuation_state_before=before_value,
            actuation_state_after=after_value,
            recovery_status=self._ops_recovery(recovery),
            intervention_id=intervention_id,
            reason=reason,
        )

    async def _publish_scheduler_ops(
        self,
        *,
        code: SchedulerOpsCode,
        message: str,
        level: str = "error",
        details: ActuationOpsDetails | dict | None = None,
    ) -> None:
        details_dict = (
            details.model_dump(mode="json") if hasattr(details, "model_dump") else details
        )
        event = OpsEvent(
            timestamp=self._now(),
            session_id=self._session_id,
            source="scheduler",
            hostname=self._hostname,
            level=level,
            code=code.value,
            message=message,
            details=details_dict,
        )
        log_level = getattr(logging, level.upper(), logging.INFO)
        log.log(log_level, "%s", message, extra={"code": code.value, "details": details_dict})
        js = getattr(self, "_js", None)
        if js is None:
            return
        await js.publish(
            ops_event_subject(self._session_id, "scheduler", code.value),
            event.model_dump_json().encode(),
        )
        # Per-GS actuation STATE is also published to a retained, replace-not-merge
        # subject (MaxMsgsPerSubject=1 on NODALARC_LINKS) so VS-API recovers the full
        # health roster via LAST_PER_SUBJECT on (re)subscribe. The ops event above is
        # the append-only audit log; this is the recoverable current state. Only the
        # per-GS state codes are retained — transient/halt codes stay log-only.
        gs_id = details_dict.get("gs_id") if isinstance(details_dict, dict) else None
        if gs_id and code in (
            SchedulerOpsCode.ACTUATION_CLEAN,
            SchedulerOpsCode.ACTUATION_BLOCKED,
            SchedulerOpsCode.KERNEL_DIRTY,
        ):
            await js.publish(
                actuation_state_subject(self._session_id, gs_id),
                event.model_dump_json().encode(),
            )

    def _actuation_state_code_for(self, state: GroundActuationState) -> SchedulerOpsCode:
        if state.state == ActuationState.KERNEL_DIRTY:
            return SchedulerOpsCode.KERNEL_DIRTY
        if state.state == ActuationState.ACTUATION_BLOCKED:
            return SchedulerOpsCode.ACTUATION_BLOCKED
        return SchedulerOpsCode.ACTUATION_CLEAN

    async def _publish_recoverable_actuation_state(
        self,
        *,
        gs_id: str,
        sim_time: datetime,
        reason: str,
    ) -> None:
        """Publish current per-GS actuation state to the retained roster subject only.

        This is not an operator event. It is the replace-not-merge recovery record
        paired with ActualLinkSnapshot so a VS-API restart does not infer health from
        an expired retained subject.
        """
        js = getattr(self, "_js", None)
        if js is None:
            return
        state = self._ground_state(gs_id)
        details = self._actuation_details(
            gs_id=gs_id,
            operation="RecoverableStateHeartbeat",
            failure_class=ActuationFailureClass.NONE,
            affected_pairs=state.affected_pairs,
            sim_time=sim_time,
            state_before=state,
            state_after=state,
            reason=reason,
        )
        code = self._actuation_state_code_for(state)
        event = OpsEvent(
            timestamp=self._now(),
            session_id=self._session_id,
            source="scheduler",
            hostname=self._hostname,
            level="debug",
            code=code.value,
            message=f"Ground station {gs_id} actuation state heartbeat: {state.state.value}",
            details=details.model_dump(mode="json"),
        )
        await js.publish(
            actuation_state_subject(self._session_id, gs_id),
            event.model_dump_json().encode(),
        )

    async def _publish_recoverable_state_heartbeat(self, *, sim_time: datetime) -> None:
        """Refresh both retained recovery subjects in lockstep.

        ActualLinkSnapshot and per-GS actuation state are consumed together by VS-API.
        Refreshing only one side can make a missing actual-kernel source look like a
        clean roster or vice versa, so the heartbeat always writes both surfaces.
        """
        await self._publish_actual_links()
        for gs_id in sorted(self._gs_capacities):
            await self._publish_recoverable_actuation_state(
                gs_id=gs_id,
                sim_time=sim_time,
                reason="recoverable state heartbeat",
            )

    async def _publish_actual_links(self) -> None:
        """Publish this instance's verified kernel-actual link set as recoverable state.

        ``_actual_links`` is what the Node Agents have CONFIRMED active (verified=true
        proof) — the "kernel actual" truth the link-explainability UX needs to tell a
        scheduled-but-unactuated pair (in_flight/faulted) from a genuinely connected one.
        LinkUp/LinkDown are DeliverPolicy.NEW and do not survive a VS-API resubscribe, so
        this retained, replace-not-merge snapshot (MaxMsgsPerSubject=1 on NODALARC_LINKS,
        recovered via LAST_PER_SUBJECT) is the only recoverable source of kernel-actual
        membership. Edge-triggered by ``_publish_actual_links_if_changed`` so a stable
        link never re-publishes and never flickers in the recovered set.
        """
        js = getattr(self, "_js", None)
        if js is None:
            return
        snapshot = ActualLinkSnapshot(
            session_id=self._session_id,
            wiring_generation=self._wiring_generation,
            scheduler_instance_id=self._scheduler_instance_id,
            hostname=self._hostname,
            sim_time=self._current_sim_time,
            epoch_id=self._last_snapshot_epoch_id,
            snapshot_seq=self._last_snapshot_seq,
            active_pairs=self._pair_rows(set(self._actual_links)),
            pending_pairs=self._pending_pair_rows(),
            emitted_at=self._now(),
        )
        await js.publish(
            actual_links_subject(self._session_id, self._scheduler_instance_id),
            snapshot.model_dump_json().encode(),
        )
        # Reached only on a successful publish; a raise above leaves the dirty flag set
        # so the next reconcile retries the publish.
        self._actual_links_publish_dirty = False

    def _pending_pair_rows(self) -> list[PendingActuationPair]:
        """The desired-but-not-kernel-actual pairs as wire records, sorted for replay."""
        return [
            PendingActuationPair(
                pair=[pair[0], pair[1]],
                pending_since=since,
                operation="BatchLinkUp",
                epoch_id=epoch_id,
                snapshot_seq=snapshot_seq,
            )
            for pair, (since, epoch_id, snapshot_seq) in sorted(self._pending_since.items())
        ]

    def _update_pending_since(self, desired: dict[tuple[str, str], ActiveLinkInfo]) -> None:
        """Maintain the in_flight -> faulted clock for desired-but-not-actual pairs.

        A pair the Scheduler desires up but ``_actual_links`` has NOT proven gets a
        wall-clock ``pending_since`` stamped ONCE — the actuation-window origin — and
        clears when the kernel proves it (enters ``_actual_links``) or it leaves desired.
        ``setdefault`` semantics preserve the first stamp across stable ticks so a stuck
        pair's age keeps growing; a converge-then-diverge handover re-stamps fresh. This
        is the divergence timing the bound measures, owned by the actuation owner.
        """
        pending = set(desired) - set(self._actual_links)
        for pair in pending:
            if pair not in self._pending_since:
                self._pending_since[pair] = (
                    self._now(),
                    self._last_snapshot_epoch_id,
                    self._last_snapshot_seq,
                )
        for pair in list(self._pending_since):
            if pair not in pending:
                del self._pending_since[pair]

    async def _publish_actual_links_if_changed(
        self,
        actual_before: frozenset[tuple[str, str]],
        pending_before: frozenset[tuple[str, str]],
    ) -> None:
        """Publish the kernel-actual + pending set only when membership changed.

        Latency-only updates (same pairs, new netem) are not membership changes and must
        not re-publish — keeps the retained set edge-triggered, not a heartbeat. The
        pending set is part of membership: a pair that diverges (enters pending) or
        converges (leaves pending) changes the retained snapshot even when ``_actual_links``
        is momentarily unchanged, so both befores gate the trigger. ``pending_since``
        timestamps never mutate once stamped, so comparing the key sets is sufficient.

        A detected change marks the publish dirty; the dirty flag also forces a publish
        when a PRIOR attempt was dropped (swallowed exception), so a lost convergence edge
        self-heals on the next reconcile instead of being lost until the next membership
        change. A stable set with no prior failure is neither changed nor dirty -> no-op.
        """
        if (
            frozenset(self._actual_links) != actual_before
            or frozenset(self._pending_since) != pending_before
        ):
            self._actual_links_publish_dirty = True
        if self._actual_links_publish_dirty:
            await self._publish_actual_links()

    async def _halt_dispatcher(
        self,
        *,
        reason: str,
        code: SchedulerOpsCode,
        details: ActuationOpsDetails | dict | None = None,
    ) -> None:
        self._dispatch_blocked_reason = reason
        self._running = False
        try:
            await self._publish_scheduler_ops(
                code=code, message=reason, level="critical", details=details
            )
        except Exception as exc:
            log.critical("FATAL: failed to publish scheduler halt OpsEvent: %s", exc)
        with suppress(Exception):
            self._dispatch_queue.put_nowait(None)
        raise RuntimeError(reason)

    async def _assert_authority_subset_fail_loud(self, location: str) -> None:
        violations = self.authority_subset_violation()
        if not violations:
            return
        details = self._actuation_details(
            gs_id=None,
            operation=location,
            failure_class=ActuationFailureClass.FENCE,
            affected_pairs=violations,
            reason="Scheduler desired state is not a subset of OME visible+scheduled authority",
        ).model_copy(update={"failure_class": OpsActuationFailureClass.AUTHORITY_INVARIANT})
        await self._halt_dispatcher(
            reason=f"C-A authority subset violation at {location}: {sorted(violations)}",
            code=SchedulerOpsCode.AUTHORITY_SUBSET_VIOLATION,
            details=details,
        )

    def _record_stale_infos_for_gs(self, gs_id: str, pairs: set[tuple[str, str]]) -> None:
        for pair in pairs:
            info = self._actual_links.get(pair) or self._desired_links.get(pair)
            if (
                info is not None
                and info.link_type == "ground"
                and self._ground_gs_id(pair) == gs_id
            ):
                self._gs_stale_link_infos[pair] = info

    def _kernel_expected_down_for_gs(
        self, gs_id: str, expected_up: dict[tuple[str, str], ActiveLinkInfo]
    ) -> dict[tuple[str, str], ActiveLinkInfo]:
        expected_down = {
            pair: info
            for pair, info in self._actual_ground_pairs_for_gs(gs_id).items()
            if pair not in expected_up
        }
        for pair, info in self._gs_stale_link_infos.items():
            if pair not in expected_up and self._ground_gs_id(pair) == gs_id:
                expected_down[pair] = info
        return expected_down

    async def _send_kernel_inventory(
        self,
        *,
        gs_id: str,
        expected_up: dict[tuple[str, str], ActiveLinkInfo],
        expected_down: dict[tuple[str, str], ActiveLinkInfo],
        sim_time: datetime,
    ) -> ActuationResult:
        return await verify_ground_kernel_inventory(
            gs_id=gs_id,
            expected_up=expected_up,
            expected_down=expected_down,
            locator=self._loc,
            pool=self._pool,
            sim_iso=sim_time.isoformat(),
            sim_time=sim_time,
            gs_capacities=self._gs_capacities,
            latency_compensation=self._latency_compensation,
            session_id=self._session_id,
            wiring_generation=self._wiring_generation,
        )

    async def _mark_gs_clean(
        self,
        *,
        gs_id: str,
        sim_time: datetime,
        intervention_id: str | None = None,
        reason: str = "read-only kernel proof matched Scheduler authority",
    ) -> None:
        before = self._ground_state(gs_id)
        after = GroundActuationState(gs_id=gs_id, since=self._now())
        self._gs_actuation[gs_id] = after
        self._gs_stale_link_infos = {
            pair: info
            for pair, info in self._gs_stale_link_infos.items()
            if self._ground_gs_id(pair) != gs_id
        }
        details = self._actuation_details(
            gs_id=gs_id,
            operation="KernelInventory",
            failure_class=ActuationFailureClass.NONE,
            sim_time=sim_time,
            state_before=before,
            state_after=after,
            intervention_id=intervention_id,
            reason=reason,
        )
        await self._publish_scheduler_ops(
            code=SchedulerOpsCode.ACTUATION_CLEAN,
            message=f"Ground station {gs_id} actuation state is clean",
            level="info",
            details=details,
        )

    async def _publish_startup_actuation_roster(self) -> None:
        """Publish a clean baseline for every configured GS at Scheduler start.

        VS-API derives actuation health from Scheduler OpsEvents. If startup
        does not declare the roster, a missing GS can look healthy by omission.
        The wiring gate is the proof boundary here: reaching this point means
        startup verified the generation's managed kernel state.
        """
        sim_time = self._current_sim_time or self._now()
        for gs_id in sorted(self._gs_capacities):
            await self._mark_gs_clean(
                gs_id=gs_id,
                sim_time=sim_time,
                reason="scheduler startup roster: wiring gate verified clean kernel state",
            )

    async def _set_gs_nonclean(
        self,
        *,
        gs_id: str,
        state_name: ActuationState,
        code: SchedulerOpsCode,
        operation: str,
        failure_class: ActuationFailureClass,
        affected_pairs: set[tuple[str, str]],
        result: ActuationResult | None,
        sim_time: datetime,
        intervention_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        before = self._ground_state(gs_id)
        self._record_stale_infos_for_gs(
            gs_id, set(affected_pairs) | set(self._actual_ground_pairs_for_gs(gs_id))
        )
        prior_attempts = before.recovery.verify_attempt_count
        recovery = SchedulerRecoveryStatus(
            verify_attempt_count=prior_attempts,
            last_verify_result=before.recovery.last_verify_result,
            next_verify_after=next_verify_time(max(1, prior_attempts + 1), now=self._now),
            verify_exhausted=False,
            operator_action_required=False,
            active_intervention_id=intervention_id,
        )
        stale = set(
            self._kernel_expected_down_for_gs(gs_id, self._desired_ground_pairs_for_gs(gs_id))
        )
        after = GroundActuationState(
            gs_id=gs_id,
            state=state_name,
            reason_code=code,
            since=self._now(),
            affected_pairs=frozenset(affected_pairs),
            stale_pairs=frozenset(stale),
            node_agent_results=tuple(result.node_agent_details()) if result else (),
            recovery=recovery,
        )
        self._gs_actuation[gs_id] = after
        details = self._actuation_details(
            gs_id=gs_id,
            operation=operation,
            failure_class=failure_class,
            affected_pairs=affected_pairs,
            result=result,
            sim_time=sim_time,
            state_before=before,
            state_after=after,
            intervention_id=intervention_id,
            reason=reason,
        )
        await self._publish_scheduler_ops(
            code=code,
            message=f"Ground station {gs_id} actuation is {state_name.value}: {operation}",
            level="error",
            details=details,
        )
        state_code = (
            SchedulerOpsCode.KERNEL_DIRTY
            if state_name == ActuationState.KERNEL_DIRTY
            else SchedulerOpsCode.ACTUATION_BLOCKED
        )
        if state_code != code:
            await self._publish_scheduler_ops(
                code=state_code,
                message=f"Ground station {gs_id} entered {state_name.value}",
                level="error",
                details=details,
            )

    def _failure_code_for_operation(self, *, operation: str, context: str) -> SchedulerOpsCode:
        if context == "replacement_up":
            return SchedulerOpsCode.REPLACEMENT_LINK_UP_FAILED
        if operation == "BatchLinkUp":
            return SchedulerOpsCode.GROUND_LINK_UP_FAILED
        if operation == "BatchLinkDown":
            return SchedulerOpsCode.GROUND_LINK_DOWN_FAILED
        if operation == "SetLatency":
            return SchedulerOpsCode.GROUND_LATENCY_UPDATE_FAILED
        return SchedulerOpsCode.KERNEL_DIRTY

    async def _handle_actuation_result(
        self,
        result: ActuationResult,
        *,
        sim_time: datetime,
        operation_context: str,
        intervention_id: str | None = None,
    ) -> None:
        if not result.has_failures:
            return
        fatal_pairs = {
            pair
            for pair, pair_result in result.pair_results.items()
            if pair in result.failed_pairs
            and (
                pair_result.failure_class
                in {ActuationFailureClass.FENCE, ActuationFailureClass.ISL_FAILURE}
                or pair_result.link_type != "ground"
            )
        }
        if result.fence_failure or fatal_pairs:
            details = self._actuation_details(
                gs_id=None,
                operation=result.operation,
                failure_class=ActuationFailureClass.FENCE
                if result.fence_failure
                else ActuationFailureClass.ISL_FAILURE,
                affected_pairs=fatal_pairs or result.failed_pairs,
                result=result,
                sim_time=sim_time,
                reason="Node Agent actuation failure cannot be degraded per-GS",
            )
            await self._halt_dispatcher(
                reason=f"Fatal actuation failure during {result.operation}: {sorted(fatal_pairs or result.failed_pairs)}",
                code=SchedulerOpsCode.ACTUATION_HALTED,
                details=details,
            )

        by_gs: dict[str, set[tuple[str, str]]] = {}
        for pair in result.failed_pairs:
            pair_result = result.pair_results[pair]
            if pair_result.link_type != "ground" or pair_result.gs_id is None:
                continue
            by_gs.setdefault(pair_result.gs_id, set()).add(pair)

        for gs_id, pairs in by_gs.items():
            failures = [result.pair_results[pair].failure_class for pair in pairs]
            if any(
                f
                in {ActuationFailureClass.GROUND_KERNEL_DIRTY, ActuationFailureClass.GROUND_UNKNOWN}
                for f in failures
            ):
                state_name = ActuationState.KERNEL_DIRTY
                failure_class = ActuationFailureClass.GROUND_KERNEL_DIRTY
            else:
                state_name = ActuationState.ACTUATION_BLOCKED
                failure_class = ActuationFailureClass.GROUND_CLEAN_FAILURE
            await self._set_gs_nonclean(
                gs_id=gs_id,
                state_name=state_name,
                code=self._failure_code_for_operation(
                    operation=result.operation, context=operation_context
                ),
                operation=result.operation,
                failure_class=failure_class,
                affected_pairs=pairs,
                result=result,
                sim_time=sim_time,
                intervention_id=intervention_id,
                reason=operation_context,
            )

    def _filter_blocked_ground_mutations(
        self,
        pairs: set[tuple[str, str]],
        *,
        operation: str,
    ) -> set[tuple[str, str]]:
        allowed: set[tuple[str, str]] = set()
        blocked: set[tuple[str, str]] = set()
        for pair in pairs:
            gs_id = self._ground_gs_id(pair)
            if gs_id is None:
                allowed.add(pair)
                continue
            if self._ground_state(gs_id).blocking_new_ground_link_up:
                blocked.add(pair)
            else:
                allowed.add(pair)
        if blocked:
            log.warning(
                "Suppressed %s for blocked ground station(s): %s",
                operation,
                sorted(blocked),
            )
        return allowed

    def _filter_ground_down_mutations(
        self,
        pairs: set[tuple[str, str]],
        *,
        operation: str,
    ) -> set[tuple[str, str]]:
        """Allow only truthful automatic ground LinkDown operations.

        actuation_blocked may still use cleanup downs because a clean failure
        means the kernel was not mutated. kernel_dirty and active repair are
        terminal for automatic mutation; only read-only verification or an
        intervention-tagged repair may touch them.
        """
        allowed: set[tuple[str, str]] = set()
        blocked: set[tuple[str, str]] = set()
        for pair in pairs:
            gs_id = self._ground_gs_id(pair)
            if gs_id is None:
                allowed.add(pair)
                continue
            state = self._ground_state(gs_id)
            if state.recovery.active_intervention_id or state.state == ActuationState.KERNEL_DIRTY:
                blocked.add(pair)
            else:
                allowed.add(pair)
        if blocked:
            log.warning(
                "Suppressed %s for kernel-dirty or repairing ground station(s): %s",
                operation,
                sorted(blocked),
            )
        return allowed

    async def _verify_gs_against_current_authority(
        self,
        *,
        gs_id: str,
        sim_time: datetime,
        intervention_id: str | None = None,
    ) -> bool:
        expected_up = self._desired_ground_pairs_for_gs(gs_id)
        expected_down = self._kernel_expected_down_for_gs(gs_id, expected_up)
        state_before = self._ground_state(gs_id)
        if not expected_up and not expected_down:
            await self._mark_gs_clean(
                gs_id=gs_id,
                sim_time=sim_time,
                intervention_id=intervention_id,
                reason="Scheduler has no known GS kernel footprint for current authority",
            )
            return True
        result = await self._send_kernel_inventory(
            gs_id=gs_id,
            expected_up=expected_up,
            expected_down=expected_down,
            sim_time=sim_time,
        )
        details = self._actuation_details(
            gs_id=gs_id,
            operation="KernelInventory",
            failure_class=ActuationFailureClass.NONE
            if not result.has_failures
            else ActuationFailureClass.GROUND_KERNEL_DIRTY,
            affected_pairs=result.failed_pairs,
            result=result,
            sim_time=sim_time,
            state_before=state_before,
            state_after=state_before,
            intervention_id=intervention_id,
            reason="read-only GS kernel verification",
        )
        await self._publish_scheduler_ops(
            code=SchedulerOpsCode.KERNEL_VERIFY_ATTEMPTED,
            message=f"KernelInventory attempted for {gs_id}",
            level="info" if not result.has_failures else "warning",
            details=details,
        )
        if not result.has_failures:
            await self._mark_gs_clean(
                gs_id=gs_id, sim_time=sim_time, intervention_id=intervention_id
            )
            return True

        attempts = state_before.recovery.verify_attempt_count + 1
        exhausted = attempts >= self._max_kernel_verify_attempts
        recovery = SchedulerRecoveryStatus(
            verify_attempt_count=attempts,
            last_verify_result="failed",
            next_verify_after=None if exhausted else next_verify_time(attempts + 1, now=self._now),
            verify_exhausted=exhausted,
            operator_action_required=exhausted,
            active_intervention_id=intervention_id,
        )
        after = GroundActuationState(
            gs_id=gs_id,
            state=ActuationState.KERNEL_DIRTY,
            reason_code=(
                SchedulerOpsCode.KERNEL_VERIFY_EXHAUSTED
                if exhausted
                else SchedulerOpsCode.KERNEL_DIRTY
            ),
            since=state_before.since,
            affected_pairs=frozenset(result.failed_pairs),
            stale_pairs=state_before.stale_pairs,
            node_agent_results=tuple(result.node_agent_details()),
            recovery=recovery,
        )
        self._gs_actuation[gs_id] = after
        if exhausted:
            exhausted_details = self._actuation_details(
                gs_id=gs_id,
                operation="KernelInventory",
                failure_class=ActuationFailureClass.GROUND_KERNEL_DIRTY,
                affected_pairs=result.failed_pairs,
                result=result,
                sim_time=sim_time,
                state_before=state_before,
                state_after=after,
                intervention_id=intervention_id,
                reason="bounded auto-verify exhausted; operator action required",
            )
            await self._publish_scheduler_ops(
                code=SchedulerOpsCode.KERNEL_VERIFY_EXHAUSTED,
                message=f"KernelInventory auto-verify exhausted for {gs_id}; operator action required",
                level="error",
                details=exhausted_details,
            )
        return False

    async def _audit_clean_ground_kernel_state(
        self,
        *,
        sim_time: datetime,
        gs_ids: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, bool]:
        """Read-only reconciliation audit for GSes currently believed clean.

        Phase 5's KernelInventory path proved dirty recovery. Phase 6 uses the same
        non-mutating proof boundary to audit the *clean* claim: every actual ground
        link the Scheduler believes the kernel has up must still be provable, and
        every tracked stale footprint must still be provably down. A mismatch does
        not self-heal and does not clear by inference; it transitions the GS to
        kernel_dirty with typed ops details so the operator sees the truth.
        """
        selected = sorted(gs_ids) if gs_ids is not None else sorted(self._gs_capacities)
        outcomes: dict[str, bool] = {}
        for gs_id in selected:
            state_before = self._ground_state(gs_id)
            if state_before.state != ActuationState.CLEAN:
                continue

            expected_up = self._actual_ground_pairs_for_gs(gs_id)
            expected_down = self._kernel_expected_down_for_gs(gs_id, expected_up)
            if not expected_up and not expected_down:
                outcomes[gs_id] = True
                continue

            result = await self._send_kernel_inventory(
                gs_id=gs_id,
                expected_up=expected_up,
                expected_down=expected_down,
                sim_time=sim_time,
            )
            details = self._actuation_details(
                gs_id=gs_id,
                operation="KernelInventoryAudit",
                failure_class=ActuationFailureClass.NONE
                if not result.has_failures
                else ActuationFailureClass.GROUND_KERNEL_DIRTY,
                affected_pairs=result.failed_pairs,
                result=result,
                sim_time=sim_time,
                state_before=state_before,
                state_after=state_before,
                reason="clean-state read-only kernel audit",
            )
            await self._publish_scheduler_ops(
                code=SchedulerOpsCode.KERNEL_VERIFY_ATTEMPTED,
                message=f"KernelInventory clean-state audit attempted for {gs_id}",
                level="debug" if not result.has_failures else "warning",
                details=details,
            )
            if result.has_failures:
                await self._set_gs_nonclean(
                    gs_id=gs_id,
                    state_name=ActuationState.KERNEL_DIRTY,
                    code=SchedulerOpsCode.KERNEL_DIRTY,
                    operation="KernelInventoryAudit",
                    failure_class=ActuationFailureClass.GROUND_KERNEL_DIRTY,
                    affected_pairs=set(result.failed_pairs),
                    result=result,
                    sim_time=sim_time,
                    reason="clean-state kernel audit mismatch",
                )
                outcomes[gs_id] = False
            else:
                outcomes[gs_id] = True
        return outcomes

    async def _run_due_kernel_verifications(self, *, sim_time: datetime) -> None:
        for gs_id, state in list(self._gs_actuation.items()):
            next_time = state.recovery.next_verify_after
            if state.state == ActuationState.CLEAN or next_time is None:
                continue
            if state.recovery.verify_exhausted or state.recovery.operator_action_required:
                continue
            if self._now() >= next_time:
                await self._verify_gs_against_current_authority(gs_id=gs_id, sim_time=sim_time)

        now = self._now()
        interval_s = self._clean_kernel_audit_interval_s
        if interval_s is not None and (now - self._last_clean_kernel_audit_at) >= timedelta(
            seconds=interval_s
        ):
            self._last_clean_kernel_audit_at = now
            await self._audit_clean_ground_kernel_state(sim_time=sim_time)

        heartbeat_interval_s = self._recoverable_state_heartbeat_interval_s
        if (now - self._last_recoverable_state_heartbeat_at) >= timedelta(
            seconds=heartbeat_interval_s
        ):
            self._last_recoverable_state_heartbeat_at = now
            await self._publish_recoverable_state_heartbeat(sim_time=sim_time)

    async def _publish_fold_diagnostics(self, sim_time: datetime) -> None:
        diagnostics = self._pending_fold_diagnostics
        self._pending_fold_diagnostics = []
        for item in diagnostics:
            gs_id = item.get("gs_id")
            pair = tuple(item.get("pair", ()))
            details = self._actuation_details(
                gs_id=gs_id,
                operation="VisibilityEventFold",
                failure_class=ActuationFailureClass.NONE,
                affected_pairs={pair} if len(pair) == 2 else set(),
                sim_time=sim_time,
                reason=item.get("reason"),
            )
            await self._publish_scheduler_ops(
                code=SchedulerOpsCode.OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR,
                message=f"OME release dropped old ground pair {pair} without a confirmed successor",
                level="warning",
                details=details,
            )

    # ------------------------------------------------------------------
    # Decision Engine: build desired state (pure computation, no I/O)
    # ------------------------------------------------------------------

    def _apply_events_to_desired(
        self,
        vis_events: list[VisibilityEvent],
    ) -> dict[tuple[str, str], ActiveLinkInfo]:
        """Apply visibility events to _desired_links and return a copy.

        Modifies _desired_links in place (incremental updates between
        snapshots). Returns a dict copy for the dispatch queue.

        _desired_links is owned by the decision engine (callbacks).
        Snapshots replace it entirely. Events modify it incrementally.
        The dispatch worker never reads _desired_links directly — it
        only receives copies via the queue.
        """
        for vis in vis_events:
            pair = (vis.node_a, vis.node_b)
            if (
                self._last_snapshot_sim_time is not None
                and vis.sim_time <= self._last_snapshot_sim_time
            ):
                log.debug(
                    "Ignoring stale VisibilityEvent for %s at %s; "
                    "LinkStateSnapshot authority is already at %s",
                    pair,
                    vis.sim_time.isoformat(),
                    self._last_snapshot_sim_time.isoformat(),
                )
                continue

            # Update _ome_view: this is OME's stated truth for the pair,
            # independent of Scheduler desired state. A desired link outside
            # this view is a production C-A violation checked after the fold.
            self._ome_view[pair] = (
                vis.visible,
                vis.scheduled,
                getattr(vis, "scheduling_state", "active"),
            )

            if vis.visible and vis.scheduled:
                sched_state = getattr(vis, "scheduling_state", "active")
                if sched_state == "teardown":
                    self._teardown_pairs.add(pair)
                else:
                    self._teardown_pairs.discard(pair)

                if pair not in self._desired_links:
                    _pair, info = desired_link_from_visibility(
                        vis,
                        interface_map=self._interface_map,
                        bandwidth_map=self._bandwidth_map,
                    )
                    self._desired_links[pair] = info
            elif not vis.visible:
                self._desired_links.pop(pair, None)
                self._teardown_pairs.discard(pair)
            elif vis.visible and not vis.scheduled:
                if vis.link_type == "ground" and pair in self._teardown_pairs:
                    gs_id = self._ground_gs_id(pair)
                    self._pending_fold_diagnostics.append(
                        {
                            "code": SchedulerOpsCode.OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR.value,
                            "pair": pair,
                            "gs_id": gs_id,
                            "reason": "OME release event marked teardown pair visible but unscheduled; Scheduler respects OME authority",
                        }
                    )
                self._desired_links.pop(pair, None)
                self._teardown_pairs.discard(pair)

        return dict(self._desired_links)

    def _build_desired_from_snapshot(
        self, snapshot: LinkStateSnapshot
    ) -> dict[tuple[str, str], ActiveLinkInfo] | None:
        """Build desired link state from a LinkStateSnapshot.

        Replaces _desired_links entirely (replace-not-merge). This is the
        authoritative full-state correction from the OME. Any drift in
        _desired_links from missed events is corrected here.

        Returns the desired dict, or None if the snapshot is stale.
        Pure computation. No I/O.
        """
        if snapshot.snapshot_seq <= self._last_snapshot_seq:
            log.debug(
                "Discarding old snapshot seq=%d (current=%d)",
                snapshot.snapshot_seq,
                self._last_snapshot_seq,
            )
            return None

        self._last_snapshot_seq = snapshot.snapshot_seq
        self._last_snapshot_sim_time = snapshot.sim_time
        self._last_snapshot_epoch_id = snapshot.epoch_id
        desired: dict[tuple[str, str], ActiveLinkInfo] = {}
        self._teardown_pairs.clear()

        # Snapshot is replace-not-merge for both _desired_links and
        # _ome_view (Phase 1.4). _ome_view captures the OME's stated
        # truth derived from the snapshot's carrier and scheduling_state
        # fields, independent of any safety-net override applied later
        # via _apply_events_to_desired. The C-A repro test compares
        # _ome_view to _desired_links to surface divergence.
        new_ome_view: dict[tuple[str, str], tuple[bool, bool, str]] = {}

        for link in snapshot.links:
            pair = (link.node_a, link.node_b)
            # Derive (visible, scheduled, sched_state) from the
            # snapshot's CarrierState + scheduling_state metadata.
            # UP → (True, True, sched_state).
            # LOWERLAYERDOWN → (True, False, sched_state).
            # DOWN → (False, False, sched_state).
            from nodalarc.models.link_state import CarrierState as _Carrier

            sched_state = getattr(link, "scheduling_state", "active") or "active"
            if link.carrier == _Carrier.UP:
                new_ome_view[pair] = (True, True, sched_state)
            elif link.carrier == _Carrier.LOWERLAYERDOWN:
                new_ome_view[pair] = (True, False, sched_state)
            else:
                new_ome_view[pair] = (False, False, sched_state)

            built = desired_link_from_snapshot_link(
                link,
                interface_map=self._interface_map,
                bandwidth_map=self._bandwidth_map,
                snapshot_sim_time=snapshot.sim_time,
                snapshot_seq=snapshot.snapshot_seq,
            )
            if built is None:
                continue
            pair, info = built
            desired[pair] = info
            if sched_state == "teardown":
                self._teardown_pairs.add(pair)
            else:
                self._teardown_pairs.discard(pair)

        # Replace _desired_links entirely — snapshot is authoritative.
        # Replace _ome_view entirely for the same reason — Phase 1.4
        # tests assert this is the OME's stated truth at snapshot time.
        self._desired_links = desired
        self._ome_view = new_ome_view

        isl = sum(1 for info in desired.values() if info.link_type == "isl")
        gs = sum(1 for info in desired.values() if info.link_type == "ground")
        log.debug(
            "LinkStateSnapshot seq=%d: %d links (%d ISL, %d GS)",
            snapshot.snapshot_seq,
            len(desired),
            isl,
            gs,
        )
        return desired

    # Backward compat: tests call _dispatch_batch directly
    async def _dispatch_batch(
        self,
        vis_events: list[VisibilityEvent],
        _snapshots: list,
        to_pub,
    ) -> None:
        """Process a batch of VisibilityEvents — builds desired and reconciles.

        This method is called directly by tests. In production, the decision
        callbacks put desired on the queue and the dispatch worker reconciles.
        Kept for backward compatibility with existing test contracts.
        """
        if not vis_events:
            return

        sim_time = vis_events[0].sim_time
        self._current_sim_time = sim_time

        self._apply_events_to_desired(vis_events)
        await self._assert_authority_subset_fail_loud("dispatch-batch")
        await self._publish_fold_diagnostics(sim_time)
        intent = self._build_dispatch_intent(sim_time=sim_time, source="ome_event")
        async with self._actuation_lock:
            await self._reconcile_links(
                intent.desired, to_pub, sim_time, intent.down_reasons, intent.forced_bbm_pairs
            )

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Dispatch Worker: background task, I/O at own pace
    # ------------------------------------------------------------------

    async def _dispatch_worker(self, nc) -> None:
        """Background task: reconcile actual state with desired state.

        Reads desired from the queue, diffs against actual, dispatches
        BatchLinkDown/Up to Node Agents, publishes LinkUp/Down events.

        Drains queue to latest desired state before reconciling — ensures
        the worker always operates on current state, not stale intermediates.

        Can take seconds (Node Agent I/O). The decision callbacks continue
        processing NATS events while this worker is busy.
        """
        log.debug("Dispatch worker started")
        while self._running:
            intent = await self._dispatch_queue.get()

            if intent is None:
                break

            # Correctness boundary for seek: no old-epoch event/snapshot/scenario
            # intent may reach the actuator while suspended. _discard_pending_
            # dispatch_intents() is only an optimization; this guard is the
            # invariant that protects kernel state during epoch replacement.
            if self._suspended and intent.source != "resume":
                log.debug("Dropping %s dispatch intent while seek-suspended", intent.source)
                continue

            # Drain queue to latest intent. rebaseline_counts is OR'd
            # across drained entries — it's a side effect that must not
            # be lost when a snapshot intent is superseded.
            rebaseline = intent.rebaseline_counts
            drained = 0
            while not self._dispatch_queue.empty():
                try:
                    next_intent = self._dispatch_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if next_intent is None:
                    intent = None
                    break
                rebaseline = rebaseline or next_intent.rebaseline_counts
                intent = next_intent
                drained += 1

            if intent is None:
                break

            if drained > 0:
                log.debug("Dispatch worker: drained %d stale entries from queue", drained)

            if rebaseline:
                self._rebaseline_active_counts()

            log.debug(
                "Dispatch worker: processing %s intent with %d links (actual has %d)",
                intent.source,
                len(intent.desired),
                len(self._actual_links),
            )

            try:
                async with self._actuation_lock:
                    await self._reconcile_links(
                        intent.desired,
                        nc,
                        intent.sim_time,
                        intent.down_reasons,
                        intent.forced_bbm_pairs,
                    )
            except Exception as exc:
                self._dispatch_blocked_reason = str(exc)
                self._running = False
                log.critical(
                    "Dispatch worker stopped for session=%s generation=%s: %s",
                    self._session_id,
                    self._wiring_generation,
                    exc,
                )
                raise

        log.debug("Dispatch worker stopped")

    # ------------------------------------------------------------------
    # Control Plane: scenario command handling
    # ------------------------------------------------------------------

    async def _on_scenario_command(self, msg) -> None:
        """Handle a scenario injection command (core NATS request/reply).

        Parses the command, mutates override state, and enqueues a
        DispatchIntent. Runs on the main event loop — no thread boundary.
        """
        import json as _json

        from scheduler.scenario_handler import (
            ClearAllOverrides,
            InjectLinkDown,
            InjectSatelliteLoss,
            ReleaseLinkOverride,
            RestoreSatellite,
            parse_scenario_command,
        )

        try:
            cmd = parse_scenario_command(msg.data)
        except ValueError as exc:
            await msg.respond(_json.dumps({"status": "error", "msg": str(exc)}).encode())
            return

        if isinstance(cmd, InjectLinkDown):
            pair = (min(cmd.node_a, cmd.node_b), max(cmd.node_a, cmd.node_b))
            self._override_pairs[pair] = cmd.reason
        elif isinstance(cmd, InjectSatelliteLoss):
            self._override_nodes[cmd.node] = "satellite_loss"
        elif isinstance(cmd, ReleaseLinkOverride):
            pair = (min(cmd.node_a, cmd.node_b), max(cmd.node_a, cmd.node_b))
            self._override_pairs.pop(pair, None)
        elif isinstance(cmd, RestoreSatellite):
            self._override_nodes.pop(cmd.node, None)
        elif isinstance(cmd, ClearAllOverrides):
            self._override_pairs.clear()
            self._override_nodes.clear()

        if self._suspended:
            await msg.respond(
                _json.dumps(
                    {"status": "accepted", "note": "scheduler suspended, will apply on resume"}
                ).encode()
            )
            return

        if self._current_sim_time is None:
            raise RuntimeError(
                "Cannot dispatch scenario override before receiving OME simulation time"
            )
        sim_time = self._current_sim_time
        intent = self._build_dispatch_intent(sim_time=sim_time, source="scenario")
        await self._dispatch_queue.put(intent)
        await msg.respond(_json.dumps({"status": "accepted"}).encode())

    async def _reject_repair(self, msg, cmd: OperatorRepairCommand | None, message: str) -> None:
        intervention_id = cmd.intervention_id if cmd else ""
        if cmd is not None:
            details = self._actuation_details(
                gs_id=cmd.gs_id,
                operation="OperatorRepair",
                failure_class=ActuationFailureClass.NONE,
                intervention_id=cmd.intervention_id,
                reason=message,
            )
            await self._publish_scheduler_ops(
                code=SchedulerOpsCode.OPERATOR_REPAIR_REJECTED,
                message=message,
                level="warning",
                details=details,
            )
        response = OperatorRepairResponse(
            status="rejected",
            intervention_id=intervention_id,
            message=message,
        )
        await msg.respond(response.model_dump_json().encode())

    async def _on_operator_repair_command(self, msg) -> None:
        try:
            cmd = OperatorRepairCommand.model_validate_json(msg.data)
        except Exception as exc:
            await msg.respond(
                OperatorRepairResponse(
                    status="rejected",
                    intervention_id="",
                    message=f"Invalid operator repair command: {exc}",
                )
                .model_dump_json()
                .encode()
            )
            return

        if cmd.session_id != self._session_id:
            await self._reject_repair(
                msg, cmd, "Repair command session_id does not match this Scheduler"
            )
            return
        if cmd.wiring_generation != self._wiring_generation:
            await self._reject_repair(msg, cmd, "Repair command wiring_generation is stale")
            return
        if cmd.scheduler_instance_id != self._scheduler_instance_id:
            await self._reject_repair(
                msg, cmd, "Repair command targets a different Scheduler instance"
            )
            return
        if cmd.gs_id not in self._gs_capacities:
            await self._reject_repair(msg, cmd, f"Unknown ground station {cmd.gs_id}")
            return
        if self._suspended:
            await self._reject_repair(
                msg, cmd, "Scheduler is seek-suspended; repair requires stable post-seek authority"
            )
            return
        state = self._ground_state(cmd.gs_id)
        if state.state == ActuationState.CLEAN:
            await self._reject_repair(msg, cmd, f"Ground station {cmd.gs_id} is already clean")
            return
        if state.recovery.active_intervention_id:
            await self._reject_repair(
                msg,
                cmd,
                f"Ground station {cmd.gs_id} already has active intervention {state.recovery.active_intervention_id}",
            )
            return

        self._repair_original_states[cmd.intervention_id] = state
        active_recovery = SchedulerRecoveryStatus(
            verify_attempt_count=0,
            last_verify_result=state.recovery.last_verify_result,
            next_verify_after=None,
            verify_exhausted=False,
            operator_action_required=False,
            active_intervention_id=cmd.intervention_id,
        )
        active_state = GroundActuationState(
            gs_id=cmd.gs_id,
            state=state.state,
            reason_code=state.reason_code,
            since=state.since,
            affected_pairs=state.affected_pairs,
            stale_pairs=state.stale_pairs,
            node_agent_results=state.node_agent_results,
            recovery=active_recovery,
        )
        self._gs_actuation[cmd.gs_id] = active_state

        details = self._actuation_details(
            gs_id=cmd.gs_id,
            operation="OperatorRepair",
            failure_class=ActuationFailureClass.NONE,
            state_before=state,
            state_after=active_state,
            intervention_id=cmd.intervention_id,
            reason=cmd.reason,
        )
        await self._publish_scheduler_ops(
            code=SchedulerOpsCode.OPERATOR_REPAIR_REQUESTED,
            message=f"Operator repair requested for {cmd.gs_id}",
            level="warning",
            details=details,
        )
        await msg.respond(
            OperatorRepairResponse(
                status="accepted",
                intervention_id=cmd.intervention_id,
                message="Repair accepted; Scheduler will reconcile GS to current authority once",
            )
            .model_dump_json()
            .encode()
        )
        task = asyncio.create_task(self._run_operator_repair(cmd))
        self._active_repair_tasks.add(task)
        task.add_done_callback(self._active_repair_tasks.discard)

    async def _run_operator_repair(self, cmd: OperatorRepairCommand) -> None:
        async with self._actuation_lock:
            await self._run_operator_repair_locked(cmd)

    async def _run_operator_repair_locked(self, cmd: OperatorRepairCommand) -> None:
        gs_id = cmd.gs_id
        sim_time = self._current_sim_time or self._now()
        # Repair tears down and rewires _actual_links; capture entry membership so
        # the recoverable kernel-actual set is republished if repair changed it —
        # on success or on a partial-then-failed repair, not just at the next tick.
        actual_before = frozenset(self._actual_links)
        pending_before = frozenset(self._pending_since)
        before = self._repair_original_states.pop(cmd.intervention_id, self._ground_state(gs_id))
        active_recovery = SchedulerRecoveryStatus(
            verify_attempt_count=0,
            last_verify_result=before.recovery.last_verify_result,
            next_verify_after=None,
            verify_exhausted=False,
            operator_action_required=False,
            active_intervention_id=cmd.intervention_id,
        )
        self._gs_actuation[gs_id] = GroundActuationState(
            gs_id=gs_id,
            state=before.state,
            reason_code=before.reason_code,
            since=before.since,
            affected_pairs=before.affected_pairs,
            stale_pairs=before.stale_pairs,
            node_agent_results=before.node_agent_results,
            recovery=active_recovery,
        )
        authority = self._desired_ground_pairs_for_gs(gs_id)
        teardown = self._actual_ground_pairs_for_gs(gs_id)
        for pair in before.stale_pairs:
            info = self._gs_stale_link_infos.get(pair)
            if info is not None and self._ground_gs_id(pair) == gs_id:
                teardown.setdefault(pair, info)
        start_details = self._actuation_details(
            gs_id=gs_id,
            operation="OperatorRepair",
            failure_class=ActuationFailureClass.NONE,
            affected_pairs=set(teardown) | set(authority),
            sim_time=sim_time,
            state_before=before,
            state_after=self._ground_state(gs_id),
            intervention_id=cmd.intervention_id,
            reason=cmd.reason,
        )
        await self._publish_scheduler_ops(
            code=SchedulerOpsCode.OPERATOR_REPAIR_STARTED,
            message=f"Operator repair started for {gs_id}",
            level="warning",
            details=start_details,
        )

        try:
            if teardown:
                down_result = await self._send_batch_down(
                    set(teardown),
                    sim_time.isoformat(),
                    sim_time,
                    self._nc,
                    dict.fromkeys(teardown, "operator_repair"),
                )
                await self._handle_actuation_result(
                    down_result,
                    sim_time=sim_time,
                    operation_context="operator_repair_down",
                    intervention_id=cmd.intervention_id,
                )
                for pair in sorted(down_result.succeeded_pairs):
                    info = self._actual_links.pop(pair, None)
                    self._last_latencies.pop(pair, None)
                    self._decrement_active_counts(pair, info)
                if set(teardown) - set(down_result.succeeded_pairs):
                    raise RuntimeError(
                        f"Repair teardown failed for {sorted(set(teardown) - set(down_result.succeeded_pairs))}"
                    )
                down_verify = await self._send_kernel_inventory(
                    gs_id=gs_id,
                    expected_up={},
                    expected_down=teardown,
                    sim_time=sim_time,
                )
                if down_verify.has_failures:
                    await self._set_gs_nonclean(
                        gs_id=gs_id,
                        state_name=ActuationState.KERNEL_DIRTY,
                        code=SchedulerOpsCode.KERNEL_DIRTY,
                        operation="OperatorRepairDownVerify",
                        failure_class=ActuationFailureClass.GROUND_KERNEL_DIRTY,
                        affected_pairs=set(down_verify.failed_pairs),
                        result=down_verify,
                        sim_time=sim_time,
                        intervention_id=cmd.intervention_id,
                        reason="repair teardown proof failed",
                    )
                    raise RuntimeError("Repair teardown proof failed")

            if authority:
                up_result = await self._send_batch_up(
                    set(authority), authority, sim_time.isoformat(), sim_time, self._nc
                )
                await self._handle_actuation_result(
                    up_result,
                    sim_time=sim_time,
                    operation_context="operator_repair_up",
                    intervention_id=cmd.intervention_id,
                )
                for pair in sorted(up_result.succeeded_pairs):
                    self._actual_links[pair] = authority[pair]
                    self._last_latencies[pair] = authority[pair].latency_ms
                    self._increment_active_counts(pair)
                if set(authority) - set(up_result.succeeded_pairs):
                    raise RuntimeError(
                        f"Repair rewire failed for {sorted(set(authority) - set(up_result.succeeded_pairs))}"
                    )

            final_down = self._kernel_expected_down_for_gs(gs_id, authority)
            final_verify = (
                await self._send_kernel_inventory(
                    gs_id=gs_id,
                    expected_up=authority,
                    expected_down=final_down,
                    sim_time=sim_time,
                )
                if authority or final_down
                else None
            )
            if final_verify is not None and final_verify.has_failures:
                await self._set_gs_nonclean(
                    gs_id=gs_id,
                    state_name=ActuationState.KERNEL_DIRTY,
                    code=SchedulerOpsCode.KERNEL_DIRTY,
                    operation="OperatorRepairFinalVerify",
                    failure_class=ActuationFailureClass.GROUND_KERNEL_DIRTY,
                    affected_pairs=set(final_verify.failed_pairs),
                    result=final_verify,
                    sim_time=sim_time,
                    intervention_id=cmd.intervention_id,
                    reason="repair final proof failed",
                )
                raise RuntimeError("Repair final proof failed")

            await self._mark_gs_clean(
                gs_id=gs_id,
                sim_time=sim_time,
                intervention_id=cmd.intervention_id,
                reason="operator repair matched current Scheduler authority",
            )
            success_details = self._actuation_details(
                gs_id=gs_id,
                operation="OperatorRepair",
                failure_class=ActuationFailureClass.NONE,
                affected_pairs=set(teardown) | set(authority),
                sim_time=sim_time,
                state_before=before,
                state_after=self._ground_state(gs_id),
                intervention_id=cmd.intervention_id,
                reason=cmd.reason,
            )
            await self._publish_scheduler_ops(
                code=SchedulerOpsCode.OPERATOR_REPAIR_SUCCEEDED,
                message=f"Operator repair succeeded for {gs_id}",
                level="info",
                details=success_details,
            )
        except Exception as exc:
            current = self._ground_state(gs_id)
            failed_recovery = SchedulerRecoveryStatus(
                verify_attempt_count=0,
                last_verify_result="operator_repair_failed",
                next_verify_after=next_verify_time(1, now=self._now),
                verify_exhausted=False,
                operator_action_required=False,
                active_intervention_id=None,
            )
            self._gs_actuation[gs_id] = GroundActuationState(
                gs_id=gs_id,
                state=ActuationState.KERNEL_DIRTY,
                reason_code=SchedulerOpsCode.OPERATOR_REPAIR_FAILED,
                since=current.since,
                affected_pairs=current.affected_pairs,
                stale_pairs=current.stale_pairs,
                node_agent_results=current.node_agent_results,
                recovery=failed_recovery,
            )
            fail_details = self._actuation_details(
                gs_id=gs_id,
                operation="OperatorRepair",
                failure_class=ActuationFailureClass.GROUND_KERNEL_DIRTY,
                affected_pairs=set(teardown) | set(authority),
                sim_time=sim_time,
                state_before=before,
                state_after=self._ground_state(gs_id),
                intervention_id=cmd.intervention_id,
                reason=str(exc),
            )
            await self._publish_scheduler_ops(
                code=SchedulerOpsCode.OPERATOR_REPAIR_FAILED,
                message=f"Operator repair failed for {gs_id}: {exc}",
                level="error",
                details=fail_details,
            )

        # Repair changed kernel-actual; recompute the divergence clock against the SAME
        # effective desired the reconcile worker uses (raw desired minus operator
        # overrides), so a deliberately-withheld pair is never stamped pending here and
        # then flashed faulted — the two writers of _pending_since must agree on "desired".
        # A repaired-up pair leaves pending; a still-down one keeps its origin.
        self._update_pending_since(self._effective_desired_links())
        await self._publish_actual_links_if_changed(actual_before, pending_before)

    # ------------------------------------------------------------------
    # Reconcile-based dispatch — single path to Node Agent
    # ------------------------------------------------------------------

    def _reload_substrate_status(self) -> None:
        """Load and validate durable substrate status documents."""
        if not self._required_substrate_pairs:
            log.debug("No required substrate pairs for this session")
            return
        try:
            import kubernetes
            import kubernetes.client
            import kubernetes.config

            try:
                kubernetes.config.load_incluster_config()
            except kubernetes.config.config_exception.ConfigException:
                kubernetes.config.load_kube_config()

            from nodalarc.platform_config import get_platform_config

            ns = get_platform_config().kubernetes_namespace
            v1 = kubernetes.client.CoreV1Api()
            documents = load_substrate_status_documents(k8s_v1=v1, namespace=ns)
            now = self._now()
            self._substrate_by_direction = validate_required_substrate_measurements(
                required_pairs=self._required_substrate_pairs,
                documents_by_source=documents,
                session_id=self._session_id,
                wiring_generation=self._wiring_generation,
                now=now,
            )
            self._last_substrate_reload = now
            log.debug(
                "Loaded substrate status: %d/%d directions",
                len(self._substrate_by_direction),
                len(self._required_substrate_pairs),
            )
        except Exception as exc:
            raise RuntimeError(f"Substrate status load failed: {exc}") from exc

    def _get_substrate_rtt_ms(self, node_a: str, node_b: str) -> float:
        """Get measured substrate RTT for a link pair in ms.

        Returns 0.0 only for LOCAL links. CROSS_NODE links require a real
        substrate measurement; otherwise dispatch fails. This prevents the
        emulator from looking healthy while silently ignoring physical
        substrate latency.
        """
        now = self._now()
        if self._required_substrate_pairs and (
            self._last_substrate_reload is None
            or (now - self._last_substrate_reload).total_seconds() >= 10.0
        ):
            self._reload_substrate_status()
        return resolve_substrate_rtt_ms(
            locator=self._loc,
            measurements_by_direction=self._substrate_by_direction,
            node_a=node_a,
            node_b=node_b,
            session_id=self._session_id,
            wiring_generation=self._wiring_generation,
            now=now,
        )

    def _latency_compensation(
        self, node_a: str, node_b: str, orbital_one_way_ms: float
    ) -> LatencyCompensation:
        """Compute auditable netem compensation from OME latency and substrate RTT."""
        substrate_rtt_ms = self._get_substrate_rtt_ms(node_a, node_b)
        try:
            return compensate_latency(
                orbital_one_way_ms=orbital_one_way_ms,
                substrate_rtt_ms=substrate_rtt_ms,
                rtt_to_one_way_policy=self._rtt_to_one_way_policy,
            )
        except ValueError as exc:
            raise ValueError(f"Unrepresentable latency for {node_a}<->{node_b}: {exc}") from exc

    def _netem_delay_ms(self, node_a: str, node_b: str, orbital_one_way_ms: float) -> float:
        """Compute netem one-way delay from OME latency and measured substrate RTT."""
        return self._latency_compensation(node_a, node_b, orbital_one_way_ms).netem_one_way_ms

    @staticmethod
    def _link_provenance(
        info: ActiveLinkInfo,
        compensation: LatencyCompensation,
        applied_sim_time: datetime,
    ) -> LinkDecisionProvenance:
        if info.range_km is None:
            raise ValueError("Cannot build link provenance without OME-authoritative range_km")
        if info.authority_sim_time is None:
            raise ValueError("Cannot build link provenance without OME authority sim_time")
        if info.authority_source is None:
            raise ValueError("Cannot build link provenance without OME authority source")
        authority_age_ms = (applied_sim_time - info.authority_sim_time).total_seconds() * 1000.0
        return LinkDecisionProvenance(
            authority_source=info.authority_source,
            authority_sim_time=info.authority_sim_time,
            authority_sequence=info.authority_sequence,
            authority_age_ms=authority_age_ms,
            range_km=info.range_km,
            orbital_one_way_ms=info.latency_ms,
            substrate_rtt_ms=compensation.substrate_rtt_ms,
            substrate_one_way_ms=compensation.substrate_one_way_ms,
            netem_one_way_ms=compensation.netem_one_way_ms,
            rtt_to_one_way_policy=compensation.rtt_to_one_way_policy,
        )

    def _validate_authority_freshness(
        self,
        pair: tuple[str, str],
        info: ActiveLinkInfo,
        dispatch_sim_time: datetime,
        *,
        operation: str,
    ) -> None:
        """Refuse actuation based on stale or missing OME geometry.

        OME is the only geometry authority. The Scheduler may carry that
        authority to Node Agent, but it must not apply a link using geometry
        whose simulation timestamp is missing, in the future, or older than
        the configured budget.
        """
        if info.authority_sim_time is None:
            raise ValueError(
                f"{operation} for {pair} has no OME authority sim_time; "
                "refusing to dispatch with untraceable geometry"
            )
        if info.authority_source is None:
            raise ValueError(
                f"{operation} for {pair} has no OME authority source; "
                "refusing to dispatch with untraceable geometry"
            )
        try:
            age_s = (dispatch_sim_time - info.authority_sim_time).total_seconds()
        except TypeError as exc:
            raise ValueError(
                f"{operation} for {pair} has incompatible simulation timestamps: "
                f"dispatch={dispatch_sim_time!r} authority={info.authority_sim_time!r}"
            ) from exc

        if age_s < -1e-9:
            raise ValueError(
                f"{operation} for {pair} uses future OME geometry: "
                f"authority_sim_time={info.authority_sim_time.isoformat()} "
                f"dispatch_sim_time={dispatch_sim_time.isoformat()}"
            )
        if age_s - self._max_latency_age_s > 1e-9:
            raise ValueError(
                f"{operation} for {pair} uses stale OME geometry: "
                f"age={age_s:.6f}s exceeds max_latency_age_s={self._max_latency_age_s:.6f}s "
                f"(authority_source={info.authority_source}, "
                f"authority_sequence={info.authority_sequence})"
            )

    def _link_locality(self, node_a: str, node_b: str) -> int | None:
        """Determine locality for a link pair. None if either pod unscheduled."""
        return self._loc.link_locality(node_a, node_b)

    async def _reconcile_links(
        self,
        desired: dict[tuple[str, str], ActiveLinkInfo],
        nc,
        sim_time: datetime,
        down_reasons: dict[tuple[str, str], str] | None = None,
        forced_bbm_pairs: frozenset[tuple[str, str]] | None = None,
    ) -> None:
        """Reconcile _actual_links toward desired state via Node Agent dispatch.

        THE SINGLE PATH TO THE NODE AGENT FOR LINK STATE.

        When mbb_dispatch is enabled, uses three-phase capacity-aware
        dispatch for ground links:
          Phase 1: BBM downs + ISL downs (frees capacity)
          Phase 2: All ups (MBB + BBM, using freed + existing spare)
          Phase 3: MBB downs (only where Phase 2 up succeeded)
        When mbb_dispatch is disabled, uses original two-phase BBM
        (all downs then all ups).
        """
        if down_reasons is None:
            down_reasons = {}
        if forced_bbm_pairs is None:
            forced_bbm_pairs = frozenset()

        # Kernel-actual membership at entry. Captured before any mutation so the
        # finally below publishes the corrected set on EVERY exit — including a phase
        # that popped/added _actual_links and then hit a fatal dispatch failure that
        # raised (via _handle_actuation_result -> _halt_dispatcher). Without the
        # finally, that raise would skip the publish and leave the retained snapshot
        # listing a torn-down pair as still up — the exact masking this fix removes.
        # Mirrors _run_operator_repair_locked, which publishes outside its try/except.
        actual_before = frozenset(self._actual_links)
        pending_before = frozenset(self._pending_since)
        try:
            await self._run_due_kernel_verifications(sim_time=sim_time)

            diff = diff_link_state(self._actual_links, desired)
            to_remove = self._filter_ground_down_mutations(
                diff.to_remove, operation="BatchLinkDown"
            )
            to_add = self._filter_blocked_ground_mutations(diff.to_add, operation="BatchLinkUp")
            to_update_latency = self._filter_blocked_ground_mutations(
                diff.to_update_latency, operation="SetLatency"
            )

            # Refresh authority metadata on all active links that appear in the
            # current desired state, BEFORE the no-change early return. The numeric
            # diff gates tc/netem re-application (no point re-applying identical
            # delay), but authority provenance must advance on every accepted snapshot
            # even when physics values are unchanged. Without this, a link stable for
            # 100 ticks retains provenance from tick 1.
            for pair, actual_info in self._actual_links.items():
                desired_info = desired.get(pair)
                if desired_info is None:
                    continue
                if desired_info.authority_sim_time is None:
                    continue
                if (
                    actual_info.authority_sim_time is not None
                    and desired_info.authority_sim_time < actual_info.authority_sim_time
                ):
                    continue
                actual_info.authority_sim_time = desired_info.authority_sim_time
                actual_info.authority_source = desired_info.authority_source
                actual_info.authority_sequence = desired_info.authority_sequence

            if not (to_remove or to_add or to_update_latency):
                return

            # Publish-before-await: stamp the in_flight->faulted clock for the pairs we are
            # about to bring up and publish it BEFORE the dispatch awaits. A slow or hung
            # BatchLinkUp / MBB phase-2 up otherwise leaves VS-API with no Scheduler-owned
            # elapsed for the whole duration of the await, and a diverged pair with no
            # elapsed reads as calm in_flight on the client — so a stuck up would never fault
            # at fault_after_ms (the exact masking the divergence clock exists to prevent).
            # Re-baseline the publish guards so the finally republishes only on a
            # dispatch-driven change (converged/torn-down pairs), not the same set twice.
            self._update_pending_since(desired)
            try:
                await self._publish_actual_links_if_changed(actual_before, pending_before)
            except Exception:
                log.exception("Failed to publish pending clock before dispatch awaits")
            actual_before = frozenset(self._actual_links)
            pending_before = frozenset(self._pending_since)

            sim_iso = sim_time.isoformat()

            if not self._mbb_dispatch:
                # Two-phase BBM dispatch — sorted for deterministic replay
                if to_remove:
                    down_result = await self._send_batch_down(
                        to_remove, sim_iso, sim_time, nc, down_reasons
                    )
                    await self._handle_actuation_result(
                        down_result, sim_time=sim_time, operation_context="bbm_down"
                    )
                    for pair in sorted(down_result.succeeded_pairs):
                        info = self._actual_links.pop(pair, None)
                        self._last_latencies.pop(pair, None)
                        self._decrement_active_counts(pair, info)

                if to_add:
                    up_result = await self._send_batch_up(to_add, desired, sim_iso, sim_time, nc)
                    await self._handle_actuation_result(
                        up_result, sim_time=sim_time, operation_context="ground_up"
                    )
                    for pair in sorted(up_result.succeeded_pairs):
                        self._actual_links[pair] = desired[pair]
                        self._last_latencies[pair] = desired[pair].latency_ms
                        self._increment_active_counts(pair)
            else:
                await self._reconcile_mbb(
                    to_remove,
                    to_add,
                    desired,
                    sim_iso,
                    sim_time,
                    nc,
                    down_reasons,
                    forced_bbm_pairs,
                )

            if to_update_latency:
                latency_result = await self._send_authoritative_latency_updates(
                    to_update_latency, desired, sim_time
                )
                await self._handle_actuation_result(
                    latency_result, sim_time=sim_time, operation_context="latency"
                )

            if to_add or to_remove:
                added_str = ", ".join(f"{a}<->{b}" for a, b in sorted(to_add)) if to_add else ""
                removed_str = (
                    ", ".join(f"{a}<->{b}" for a, b in sorted(to_remove)) if to_remove else ""
                )
                parts = []
                if to_add:
                    parts.append(f"up=[{added_str}]")
                if to_remove:
                    parts.append(f"down=[{removed_str}]")
                log.info(
                    "Link state changed [%s, active=%d]",
                    ", ".join(parts),
                    len(self._actual_links),
                )
            else:
                log.debug(
                    "Reconcile: no changes (%d active)",
                    len(self._actual_links),
                )
        finally:
            # Recompute the divergence clock against the desired we just reconciled
            # toward, then edge-publish. Both run in finally so a halt-raise still
            # records pending and publishes the corrected set. A no-op when neither the
            # actual nor the pending membership changed (a stable link never
            # re-publishes); any committed change is published even on a halt-raise. A
            # publish failure is logged, not raised — it must not mask the in-flight
            # dispatch exception (same guard idiom as _halt_dispatcher).
            self._update_pending_since(desired)
            try:
                await self._publish_actual_links_if_changed(actual_before, pending_before)
            except Exception:
                log.exception("Failed to publish kernel-actual snapshot at reconcile exit")

    async def _send_authoritative_latency_updates(
        self,
        pairs: set[tuple[str, str]],
        desired: dict[tuple[str, str], ActiveLinkInfo],
        sim_time: datetime,
    ) -> ActuationResult:
        """Apply OME-authoritative latency changes for already-active links."""
        result = await send_authoritative_latency_updates(
            pairs=pairs,
            desired=desired,
            locator=self._loc,
            pool=self._pool,
            js=self._js,
            subj_latency=self._subj_latency,
            sim_time=sim_time,
            gs_capacities=self._gs_capacities,
            latency_compensation=self._latency_compensation,
            validate_authority_freshness=self._validate_authority_freshness,
            link_provenance=self._link_provenance,
            session_id=self._session_id,
            wiring_generation=self._wiring_generation,
        )
        for pair in result.succeeded_pairs:
            info = desired[pair]
            self._actual_links[pair] = info
            self._last_latencies[pair] = info.latency_ms
        return result

    async def _reconcile_mbb(
        self,
        to_remove: set[tuple[str, str]],
        to_add: set[tuple[str, str]],
        desired: dict[tuple[str, str], ActiveLinkInfo],
        sim_iso: str,
        sim_time: datetime,
        nc,
        down_reasons: dict[tuple[str, str], str] | None = None,
        forced_bbm_pairs: frozenset[tuple[str, str]] | None = None,
    ) -> None:
        """Three-phase capacity-aware MBB dispatch for ground links.

        Phase 1: BBM downs + ISL downs (free capacity)
        Phase 2: All ups (greedy-reserved, no over-subscription)
        Phase 3: MBB downs (where Phase 2 up succeeded)

        forced_bbm_pairs escalates to GS-segment level: if ANY pair
        under a GS is forced, the entire segment goes BBM.
        """
        if down_reasons is None:
            down_reasons = {}
        if forced_bbm_pairs is None:
            forced_bbm_pairs = frozenset()

        # --- Classify changes ---
        classification = classify_mbb_changes(
            to_remove=to_remove,
            to_add=to_add,
            gs_capacities=self._gs_capacities,
            gs_active_count=self._gs_active_count,
            sat_capacities=self._sat_capacities,
            sat_active_count=self._sat_active_count,
            forced_bbm_pairs=forced_bbm_pairs,
        )
        isl_downs = classification.isl_downs
        isl_ups = classification.isl_ups
        gs_downs = classification.gs_downs
        gs_ups = classification.gs_ups
        mbb_segments = classification.mbb_segments
        bbm_segments = classification.bbm_segments

        # --- PHASE 1: Free capacity (BBM downs + ISL downs) ---
        # Sorted for deterministic dispatch ordering — two runs with different
        # PYTHONHASHSEED must produce identical link-event sequences.
        phase1_downs: set[tuple[str, str]] = set(isl_downs)
        for gs_id in sorted(bbm_segments):
            phase1_downs.update(gs_downs.get(gs_id, set()))

        if phase1_downs:
            down_result = await self._send_batch_down(
                phase1_downs, sim_iso, sim_time, nc, down_reasons
            )
            await self._handle_actuation_result(
                down_result, sim_time=sim_time, operation_context="phase1_down"
            )
            failed_bbm_gs: set[str] = set()
            for pair in sorted(phase1_downs):
                gs_id = gs_id_for_pair(pair, self._gs_capacities)
                if pair in down_result.succeeded_pairs:
                    info = self._actual_links.pop(pair, None)
                    self._last_latencies.pop(pair, None)
                    self._decrement_active_counts(pair, info)
                elif gs_id:
                    failed_bbm_gs.add(gs_id)
        else:
            down_result = None
            failed_bbm_gs = set()

        # --- INTER-PHASE: Greedy reservation for Phase 2 ups ---
        # Work with post-Phase-1 capacity (real, not projected).
        # Sorted iteration ensures greedy reservation winner is
        # deterministic when multiple pairs compete for one terminal.
        phase2_ups: set[tuple[str, str]] = set(isl_ups)

        for gs_id in sorted(mbb_segments):
            phase2_ups.update(gs_ups.get(gs_id, set()))

        for gs_id in sorted(bbm_segments):
            if gs_id in failed_bbm_gs:
                continue
            for pair in sorted(gs_ups.get(gs_id, set())):
                sat_id = sat_id_for_gs_pair(pair, self._gs_capacities)
                if sat_id is None:
                    raise ValueError(f"Ground segment {gs_id!r} includes non-ground pair {pair}")
                gs_spare = self._gs_capacities[gs_id] - self._gs_active_count.get(gs_id, 0)
                sat_spare = self._sat_capacities[sat_id] - self._sat_active_count.get(sat_id, 0)
                if gs_spare > 0 and sat_spare > 0:
                    phase2_ups.add(pair)
                else:
                    log.debug(
                        "Greedy skip: %s→%s (gs_spare=%d, sat_spare=%d)",
                        gs_id,
                        sat_id,
                        gs_spare,
                        sat_spare,
                    )

        # --- PHASE 2: All ups ---
        if phase2_ups:
            up_result = await self._send_batch_up(phase2_ups, desired, sim_iso, sim_time, nc)
            await self._handle_actuation_result(
                up_result, sim_time=sim_time, operation_context="replacement_up"
            )
            added = set(up_result.succeeded_pairs)
            for pair in sorted(added):
                self._actual_links[pair] = desired[pair]
                self._last_latencies[pair] = desired[pair].latency_ms
                self._increment_active_counts(pair)
        else:
            added = set()

        # --- PHASE 3: MBB downs (only where Phase 2 up succeeded) ---
        phase3_downs: set[tuple[str, str]] = set()
        for gs_id in sorted(mbb_segments):
            ups_for_gs = gs_ups.get(gs_id, set())
            if ups_for_gs & added:
                phase3_downs.update(gs_downs.get(gs_id, set()))

        if phase3_downs:
            down3_result = await self._send_batch_down(
                phase3_downs, sim_iso, sim_time, nc, down_reasons
            )
            await self._handle_actuation_result(
                down3_result, sim_time=sim_time, operation_context="phase3_down"
            )
            for pair in sorted(down3_result.succeeded_pairs):
                info = self._actual_links.pop(pair, None)
                self._last_latencies.pop(pair, None)
                self._decrement_active_counts(pair, info)

    async def _send_batch_down(
        self,
        pairs: set[tuple[str, str]],
        sim_iso: str,
        sim_time: datetime,
        nc,
        down_reasons: dict[tuple[str, str], str] | None = None,
    ) -> ActuationResult:
        """Send BatchLinkDown to Node Agents. Returns structured proof results."""
        if down_reasons is None:
            down_reasons = {}
        return await send_batch_down(
            pairs=pairs,
            actual_links=self._actual_links,
            locator=self._loc,
            pool=self._pool,
            js=self._js,
            subj_link_down=self._subj_link_down,
            sim_iso=sim_iso,
            sim_time=sim_time,
            down_reasons=down_reasons,
            gs_capacities=self._gs_capacities,
            session_id=self._session_id,
            wiring_generation=self._wiring_generation,
        )

    async def _send_batch_up(
        self,
        pairs: set[tuple[str, str]],
        desired: dict[tuple[str, str], ActiveLinkInfo],
        sim_iso: str,
        sim_time: datetime,
        nc,
    ) -> ActuationResult:
        """Send BatchLinkUp to Node Agents. Returns structured proof results."""
        return await send_batch_up(
            pairs=pairs,
            desired=desired,
            locator=self._loc,
            pool=self._pool,
            js=self._js,
            subj_link_up=self._subj_link_up,
            sim_iso=sim_iso,
            sim_time=sim_time,
            gs_capacities=self._gs_capacities,
            latency_compensation=self._latency_compensation,
            validate_authority_freshness=self._validate_authority_freshness,
            link_provenance=self._link_provenance,
            session_id=self._session_id,
            wiring_generation=self._wiring_generation,
        )

    # ------------------------------------------------------------------
    # Epoch synchronization state machine (PRD v0.71)
    # ------------------------------------------------------------------

    async def _check_epoch_resume(self) -> None:
        """Check if all epoch dependencies are met for resume.

        Does NOT resume — resume only happens on ClockTick. This just
        logs readiness for debugging.
        """
        if not self._suspended:
            return
        if self._epoch_sync.ready_for_clock_resume():
            log.info(
                "Epoch %d: all deps met — waiting for first ClockTick to resume",
                self._expected_epoch_id,
            )

    async def _try_resume_on_clock_tick(self, tick_data: dict) -> None:
        """Attempt to resume from SUSPENDED on a ClockTick with matching epoch_id.

        ALL 4 conditions must be true:
        1. PlaybackState(playing, N) received
        2. SessionEphemeris(N) loaded
        3. LinkStateSnapshot(N) buffered
        4. ClockTick(epoch_id=N) received (this call)
        """
        if not self._suspended:
            return

        missing = self._epoch_sync.missing_resume_dependencies()
        if missing:
            log.warning(
                "Seek resume blocked — epoch_id=%d waiting for: %s",
                self._expected_epoch_id,
                ", ".join(missing),
            )
            return

        # All conditions met — RESUME
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()

        buffered_snapshot = self._epoch_sync.resume()

        # Process the triggering ClockTick's sim_time first — needed for intent
        tick_sim_str = tick_data.get("sim_time")
        if not tick_sim_str:
            log.error("ClockTick missing sim_time on seek resume: %s", tick_data)
            raise ValueError("ClockTick missing sim_time")
        self._current_sim_time = datetime.fromisoformat(tick_sim_str)

        # Apply the buffered LinkStateSnapshot
        if buffered_snapshot:
            desired = self._build_desired_from_snapshot(buffered_snapshot)
            if desired is not None:
                await self._assert_authority_subset_fail_loud("seek-resume-snapshot")
                intent = self._build_dispatch_intent(
                    sim_time=self._current_sim_time,
                    source="resume",
                    rebaseline_counts=True,
                )
                log.info(
                    "Epoch %d resume: applying buffered snapshot seq=%d (%d links)",
                    self._expected_epoch_id,
                    buffered_snapshot.snapshot_seq,
                    len(intent.desired),
                )
                await self._dispatch_queue.put(intent)

        log.info(
            "RESUMED: epoch_id=%d sim_time=%s",
            self._expected_epoch_id,
            self._current_sim_time,
        )

    async def _epoch_watchdog(self, epoch_id: int) -> None:
        """30-second watchdog for seek epoch synchronization.

        If the seek doesn't resume within 30 seconds, the OME failed to
        publish the required epoch dependencies. Kill the process so K8s
        restarts it — a stuck Scheduler is worse than a restarted one.
        """
        try:
            await asyncio.sleep(30)
            if self._epoch_sync.mark_watchdog_timeout(epoch_id):
                deps = self._epoch_deps_met
                log.error(
                    "FATAL: seek epoch_id=%d timed out after 30s — killing process. "
                    "deps: ephemeris=%s snapshot=%s playing=%s",
                    epoch_id,
                    deps["ephemeris"],
                    deps["snapshot"],
                    self._playback_playing_received,
                )
                self._running = False
        except asyncio.CancelledError:
            pass  # Normal — watchdog cancelled on successful resume
