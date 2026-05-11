# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import nats
from nodalarc.models.events import PlaybackState, SessionEphemeris, VisibilityEvent
from nodalarc.models.link_events import LinkDecisionProvenance
from nodalarc.models.link_state import LinkStateSnapshot
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    STREAM_LINK_EVENTS,
    STREAM_OME_EVENTS,
    latency_update_subject,
    link_down_subject,
    link_state_snapshot_subject,
    link_up_subject,
    nats_url,
    ome_all_subject,
    ome_clock_subject,
    ome_visibility_subject,
    playback_state_subject,
    scenario_inject_subject,
    scheduling_checkpoint_subject,
    session_ephemeris_subject,
    substrate_latency_subject,
)
from pydantic import ValidationError

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
)
from scheduler.dispatch_planner import (
    classify_mbb_changes,
    diff_link_state,
    gs_id_for_pair,
    sat_id_for_gs_pair,
)
from scheduler.epoch_sync import EpochSyncState
from scheduler.latency_compensator import LatencyCompensation, compensate_latency
from scheduler.node_agent_batches import successful_interface_acks
from scheduler.pod_locator import PodLocationMap
from scheduler.substrate_latency import resolve_substrate_rtt_ms

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
        max_latency_age_s: float,
        compression_factor: int = 1,
        epsilon_ms: float = 100.0,
        gs_terminal_capacities: dict[str, int] | None = None,
        sat_ground_terminal_capacities: dict[str, int] | None = None,
        mbb_dispatch: bool = False,
        rtt_to_one_way_policy: Literal["half-rtt"] = "half-rtt",
    ) -> None:
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

        # Session-scoped NATS subjects
        self._subj_visibility = ome_visibility_subject(session_id)
        self._subj_clock = ome_clock_subject(session_id)
        self._subj_ome_all = ome_all_subject(session_id)
        self._subj_ephemeris = session_ephemeris_subject(session_id)
        self._subj_playback = playback_state_subject(session_id)
        self._subj_checkpoint = scheduling_checkpoint_subject(session_id)
        self._subj_link_snapshot = link_state_snapshot_subject(session_id)
        self._subj_link_up = link_up_subject(session_id)
        self._subj_link_down = link_down_subject(session_id)
        self._subj_latency = latency_update_subject(session_id)
        self._subj_substrate = substrate_latency_subject(session_id)

        # Decision engine state: what SHOULD be active (based on OME events).
        # Written ONLY by callbacks. Snapshots replace entirely. Events modify
        # incrementally. The queue carries copies to the dispatch worker.
        self._desired_links: dict[tuple[str, str], ActiveLinkInfo] = {}

        # Actuator state: what IS active (confirmed by Node Agent).
        # Written ONLY by the dispatch worker after Node Agent ACK.
        self._actual_links: dict[tuple[str, str], ActiveLinkInfo] = {}

        # Incremental active-link counters for O(1) MBB capacity checks.
        # Re-baselined from _actual_links on every LinkStateSnapshot.
        self._gs_active_count: dict[str, int] = {}
        self._sat_active_count: dict[str, int] = {}

        self._last_latencies: dict[tuple[str, str], float] = {}
        self._current_sim_time: datetime | None = None
        self._running = False
        self._last_snapshot_seq: int = 0
        self._last_snapshot_sim_time: datetime | None = None
        self._substrate_latency: dict[str, float] = {}  # "nodeA-nodeB" -> ms (legacy ConfigMap)
        self._substrate_by_ip: dict[str, float] = {}  # peer_ip -> ms (live from Node Agent)

        # Control plane state: scenario overrides. Values are reason strings.
        # Mutated only by _on_scenario_command on the main event loop.
        self._override_pairs: dict[tuple[str, str], str] = {}
        self._override_nodes: dict[str, str] = {}

        # Ground links currently in MBB teardown state (held for overlap).
        # Used by the safety check to identify teardown-expired removals.
        self._teardown_pairs: set[tuple[str, str]] = set()

        # Epoch synchronization — only active during Tier 2 seek.
        # The Scheduler starts UNSUSPENDED. It receives snapshots and
        # dispatches immediately. SUSPENDED is entered ONLY when the OME
        # publishes PlaybackState(state="seeking"), signaling a sim_time
        # discontinuity that requires fresh ephemeris + snapshot before
        # dispatch can resume safely.
        self._epoch_sync = EpochSyncState()
        self._watchdog_task: asyncio.Task | None = None
        self._scenario_sub = None

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
        effective = {
            pair: info
            for pair, info in self._desired_links.items()
            if pair not in self._override_pairs
            and pair[0] not in self._override_nodes
            and pair[1] not in self._override_nodes
        }

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

        # Load substrate latency for cross-node compensation
        self._load_substrate_latency()

        # Scenario injection — core NATS request/reply (not JetStream).
        # Single-owner per session: if Scheduler replicas go above 1, this
        # needs a NATS queue group or leader election. Today replicas=1.
        self._subj_scenario = scenario_inject_subject(self._session_id)
        self._scenario_sub = await nc.subscribe(self._subj_scenario, cb=self._on_scenario_command)
        log.debug("Scenario subscription active: %s", self._subj_scenario)

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

        # CONCURRENCY NOTE: pending_vis is mutated by _on_visibility and
        # _on_clock_tick, which both yield control at await queue.put().
        # Safe today because asyncio runs callbacks cooperatively on one
        # thread — no preemption between await points. If this ever moves
        # to a multi-threaded event loop, pending_vis must be protected
        # by an asyncio.Lock.
        pending_vis: list[VisibilityEvent] = []
        last_sim_time: datetime | None = None

        async def _on_visibility(msg):
            nonlocal last_sim_time
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

            if self._suspended:
                log.debug(
                    "Seek suspended — dropping VisibilityEvent for %s/%s", vis.node_a, vis.node_b
                )
                return

            snap_sim = vis.sim_time

            # Flush BEFORE appending the new event. When an event from a
            # new tick arrives, all events from the previous tick are
            # complete and ready to dispatch atomically. The new event
            # then starts accumulating for its own tick.
            #
            # The previous (broken) version appended first then flushed,
            # which dispatched the first event of tick T as part of tick
            # T-1's batch. Subsequent events from T accumulated alone and
            # were dispatched in the NEXT cycle. For a GS handover where
            # the OME emits two events at the same sim_time (the new sat
            # scheduled + the old sat unscheduled), this split the pair
            # across two reconciliation cycles, breaking break-before-make:
            # the new attach completed in cycle N before the old detach
            # completed in cycle N+1, and the detach brought the shared
            # GS bridge port DOWN, killing the freshly-established carrier.
            if last_sim_time is not None and snap_sim != last_sim_time:
                delta_ms = (snap_sim - last_sim_time).total_seconds() * 1000
                if delta_ms < 0:
                    raise RuntimeError(
                        "VisibilityEvent sim_time regressed outside an epoch seek: "
                        f"last={last_sim_time.isoformat()} current={snap_sim.isoformat()}"
                    )
                if delta_ms > self._epsilon_ms and pending_vis:
                    self._apply_events_to_desired(list(pending_vis))
                    intent = self._build_dispatch_intent(
                        sim_time=pending_vis[0].sim_time,
                        source="ome_event",
                    )
                    await self._dispatch_queue.put(intent)
                    pending_vis.clear()

            pending_vis.append(vis)
            last_sim_time = snap_sim

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
            if ps.state == "seeking" and ps.epoch_id > self._expected_epoch_id:
                # New seek — enter SUSPENDED state
                self._epoch_sync.begin_seek(ps.epoch_id)
                # Start watchdog
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

        async def _on_clock_tick(msg):
            nonlocal last_sim_time
            data = json.loads(msg.data)
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
            #
            # When the next vis event from a later sim_time arrives, the
            # _on_visibility flush-before-append will dispatch this tick's
            # events atomically. If no later vis events arrive, the next
            # ClockTick (with sim_time > pending_vis sim_time) will flush.
            if pending_vis and tick_sim_time is not None:
                pending_sim = pending_vis[0].sim_time
                if pending_sim < tick_sim_time:
                    self._apply_events_to_desired(list(pending_vis))
                    intent = self._build_dispatch_intent(
                        sim_time=pending_vis[0].sim_time,
                        source="ome_event",
                    )
                    await self._dispatch_queue.put(intent)
                    pending_vis.clear()
                    last_sim_time = tick_sim_time

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

            if self._suspended:
                # Buffer if matching epoch_id, discard otherwise
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

        async def _on_substrate_latency(msg):
            """Update substrate latency from live Node Agent measurements."""
            data = json.loads(msg.data)
            source = data.get("source_node", "")
            peers = data.get("peers", {})
            for peer_ip, latency_ms in peers.items():
                self._substrate_by_ip[peer_ip] = latency_ms
            if peers:
                log.debug(
                    "Substrate update from %s: %s",
                    source,
                    ", ".join(f"{ip}={ms}ms" for ip, ms in peers.items()),
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
            (self._subj_substrate, STREAM_LINK_EVENTS, DeliverPolicy.NEW, _on_substrate_latency),
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
            if owns_nc:
                await nc.close()
            log.info("Dispatcher stopped")

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
                is_gs = vis.link_type == "ground"
                if is_gs:
                    was_teardown = pair in self._teardown_pairs
                    if was_teardown:
                        gs_id = pair[0] if pair[0] in self._gs_capacities else pair[1]
                        gs_has_other = any(
                            p != pair and info.link_type == "ground"
                            for p, info in self._actual_links.items()
                            if (p[0] if p[0] in self._gs_capacities else p[1]) == gs_id
                        )
                        if not gs_has_other:
                            log.warning(
                                "MBB teardown blocked: %s has no other active "
                                "ground link — keeping link alive",
                                gs_id,
                            )
                            continue
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
        desired: dict[tuple[str, str], ActiveLinkInfo] = {}
        self._teardown_pairs.clear()

        for link in snapshot.links:
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
            sched_state = getattr(link, "scheduling_state", "active")
            if sched_state == "teardown":
                self._teardown_pairs.add(pair)
            else:
                self._teardown_pairs.discard(pair)

        # Replace _desired_links entirely — snapshot is authoritative
        self._desired_links = desired

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
        snapshots: list,
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
        intent = self._build_dispatch_intent(sim_time=sim_time, source="ome_event")
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

            await self._reconcile_links(
                intent.desired,
                nc,
                intent.sim_time,
                intent.down_reasons,
                intent.forced_bbm_pairs,
            )

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

    # ------------------------------------------------------------------
    # Reconcile-based dispatch — single path to Node Agent
    # ------------------------------------------------------------------

    def _load_substrate_latency(self) -> None:
        """Load substrate latency from ConfigMap (created by Operator)."""
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
            cm = v1.read_namespaced_config_map("nodalarc-substrate-latency", ns)
            if cm.data:
                for key, val in cm.data.items():
                    self._substrate_latency[key] = float(val)
                log.debug(
                    "Loaded substrate latency: %s",
                    ", ".join(f"{k}={v}ms" for k, v in sorted(self._substrate_latency.items())),
                )
        except kubernetes.client.rest.ApiException as exc:
            if exc.status == 404:
                log.info("No substrate latency ConfigMap — single-node deployment")
            else:
                log.warning("Failed to read substrate latency ConfigMap: %s", exc)
        except Exception as exc:
            log.warning("Substrate latency load failed: %s", exc)

    def _get_substrate_rtt_ms(self, node_a: str, node_b: str) -> float:
        """Get measured substrate RTT for a link pair in ms.

        Returns 0.0 only for LOCAL links. CROSS_NODE links require a real
        substrate measurement; otherwise dispatch fails. This prevents the
        emulator from looking healthy while silently ignoring physical
        substrate latency.
        """
        return resolve_substrate_rtt_ms(
            locator=self._loc,
            live_rtt_by_peer_ip=self._substrate_by_ip,
            configured_rtt_by_node_pair=self._substrate_latency,
            node_a=node_a,
            node_b=node_b,
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

    @staticmethod
    def _successful_interface_acks(
        *,
        result,
        requested_interfaces,
        agent_addr: str,
        operation: str,
    ) -> set[tuple[str, str, str]]:
        """Backward-compatible wrapper around the actuator ACK contract."""
        return successful_interface_acks(
            result=result,
            requested_interfaces=requested_interfaces,
            agent_addr=agent_addr,
            operation=operation,
        )

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

        diff = diff_link_state(self._actual_links, desired)
        to_remove = diff.to_remove
        to_add = diff.to_add
        to_update_latency = diff.to_update_latency

        if not diff.has_changes:
            return

        sim_iso = sim_time.isoformat()

        if not self._mbb_dispatch:
            # Two-phase BBM dispatch — sorted for deterministic replay
            if to_remove:
                removed = await self._send_batch_down(
                    to_remove, sim_iso, sim_time, nc, down_reasons
                )
                for pair in sorted(removed):
                    info = self._actual_links.pop(pair, None)
                    self._last_latencies.pop(pair, None)
                    self._decrement_active_counts(pair, info)

            if to_add:
                added = await self._send_batch_up(to_add, desired, sim_iso, sim_time, nc)
                for pair in sorted(added):
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

        # TODO(trust-gap-closure#1): Refresh authority metadata on ALL active
        # links that appear in the current desired state, not just those with
        # changed numeric values. Currently dispatch_planner.diff_link_state
        # only flags links where range_km or latency_ms changed numerically.
        # Links that are stable (same floats) never get their authority_sim_time,
        # authority_sequence, or authority_source updated — so a link active for
        # 100 ticks has provenance from tick 1. An auditor asking "when was this
        # link's geometry last confirmed?" gets a stale answer.
        #
        # Fix: after reconciliation, walk _actual_links and for every pair that
        # also appears in desired, update authority metadata from desired[pair].
        # Guard against authority regression: only update if
        # desired[pair].authority_sim_time >= actual[pair].authority_sim_time
        # (a stale retained snapshot from a pre-restart OME must not overwrite
        # newer authority — that can only happen after an explicit lineage reset).
        # The numeric diff still gates LatencyUpdate/tc dispatch — no point
        # re-applying the same delay.

        if to_update_latency:
            await self._send_authoritative_latency_updates(to_update_latency, desired, sim_time)

        if to_add or to_remove:
            added_str = ", ".join(f"{a}<->{b}" for a, b in sorted(to_add)) if to_add else ""
            removed_str = ", ".join(f"{a}<->{b}" for a, b in sorted(to_remove)) if to_remove else ""
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

    async def _send_authoritative_latency_updates(
        self,
        pairs: set[tuple[str, str]],
        desired: dict[tuple[str, str], ActiveLinkInfo],
        sim_time: datetime,
    ) -> None:
        """Apply OME-authoritative latency changes for already-active links."""
        updated = await send_authoritative_latency_updates(
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
        )
        for pair in updated:
            info = desired[pair]
            self._actual_links[pair] = info
            self._last_latencies[pair] = info.latency_ms

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
            removed = await self._send_batch_down(phase1_downs, sim_iso, sim_time, nc, down_reasons)
            failed_bbm_gs: set[str] = set()
            for pair in sorted(phase1_downs):
                gs_id = gs_id_for_pair(pair, self._gs_capacities)
                if pair in removed:
                    info = self._actual_links.pop(pair, None)
                    self._last_latencies.pop(pair, None)
                    self._decrement_active_counts(pair, info)
                elif gs_id:
                    failed_bbm_gs.add(gs_id)
        else:
            removed = set()
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
            added = await self._send_batch_up(phase2_ups, desired, sim_iso, sim_time, nc)
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
            removed3 = await self._send_batch_down(
                phase3_downs, sim_iso, sim_time, nc, down_reasons
            )
            for pair in sorted(removed3):
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
    ) -> set[tuple[str, str]]:
        """Send BatchLinkDown to Node Agents. Returns successfully removed pairs."""
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
        )

    async def _send_batch_up(
        self,
        pairs: set[tuple[str, str]],
        desired: dict[tuple[str, str], ActiveLinkInfo],
        sim_iso: str,
        sim_time: datetime,
        nc,
    ) -> set[tuple[str, str]]:
        """Send BatchLinkUp to Node Agents. Returns successfully added pairs."""
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
