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
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime

import nats
from nodalarc.models.events import PlaybackState, SessionEphemeris, VisibilityEvent
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.models.link_state import AdminState, CarrierState, LinkStateSnapshot
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    STREAM_SESSION_EVENTS,
    SUBJECT_CLOCK_TICK,
    SUBJECT_LATENCY_UPDATE,
    SUBJECT_LINK_DOWN,
    SUBJECT_LINK_STATE_SNAPSHOT,
    SUBJECT_LINK_UP,
    SUBJECT_PLAYBACK_STATE,
    SUBJECT_SESSION_EPHEMERIS,
    SUBJECT_SUBSTRATE_LATENCY,
    SUBJECT_VISIBILITY_EVENT,
    nats_url,
)
from nodalarc.proto import node_agent_pb2

from scheduler.agent_pool import AgentPool
from scheduler.latency_model import PositionTable
from scheduler.pod_locator import PodLocationMap

log = logging.getLogger(__name__)


class ActiveLinkInfo:
    """Mutable internal state for an active link (migrated from realtime_dispatcher)."""

    __slots__ = ("interface_a", "interface_b", "latency_ms", "bandwidth_mbps")

    def __init__(
        self,
        interface_a: str,
        interface_b: str,
        latency_ms: float,
        bandwidth_mbps: float,
    ) -> None:
        self.interface_a = interface_a
        self.interface_b = interface_b
        self.latency_ms = latency_ms
        self.bandwidth_mbps = bandwidth_mbps


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
        override_set: set[tuple[str, str]],
        override_lock: threading.Lock,
        compression_factor: int = 1,
        latency_update_interval_s: int = 10,
        epsilon_ms: float = 100.0,
        # Legacy — kept for test compatibility, ignored at runtime
        ome_endpoint: str = "",
    ) -> None:
        self._interface_map = interface_map
        self._bandwidth_map = bandwidth_map
        self._loc = pod_locator
        self._pool = agent_pool
        self._override_set = override_set
        self._override_lock = override_lock
        self._compression = max(1, compression_factor)
        self._latency_interval = latency_update_interval_s
        self._epsilon_ms = epsilon_ms

        self._position_table = PositionTable()

        # Decision engine state: what SHOULD be active (based on OME events).
        # Written ONLY by callbacks. Snapshots replace entirely. Events modify
        # incrementally. The queue carries copies to the dispatch worker.
        self._desired_links: dict[tuple[str, str], ActiveLinkInfo] = {}

        # Actuator state: what IS active (confirmed by Node Agent).
        # Written ONLY by the dispatch worker after Node Agent ACK.
        self._actual_links: dict[tuple[str, str], ActiveLinkInfo] = {}

        self._last_latencies: dict[tuple[str, str], float] = {}
        self._steps_since_latency_update = 0
        self._latency_update_pending = False
        self._current_sim_time: datetime | None = None
        self._running = False
        self._last_snapshot_seq: int = 0
        self._substrate_latency: dict[str, float] = {}  # "nodeA-nodeB" -> ms (legacy ConfigMap)
        self._substrate_by_ip: dict[str, float] = {}  # peer_ip -> ms (live from Node Agent)

        # Pairs that failed dispatch and should not be retried.
        self._skip_pairs: set[tuple[str, str]] = set()

        # Epoch synchronization state machine (PRD v0.71)
        self._suspended = True  # Start SUSPENDED — wait for epoch_id=0 deps
        self._expected_epoch_id = 0
        self._playback_playing_received = False
        self._epoch_deps_met = {"ephemeris": False, "snapshot": False}
        self._buffered_snapshot: LinkStateSnapshot | None = None
        self._stale = False
        self._watchdog_task: asyncio.Task | None = None

        # Queue: decision callbacks → dispatch worker
        self._dispatch_queue: asyncio.Queue[dict[tuple[str, str], ActiveLinkInfo] | None] = (
            asyncio.Queue()
        )

    # Backward compat: tests that reference _active_links
    @property
    def _active_links(self) -> dict[tuple[str, str], ActiveLinkInfo]:
        return self._actual_links

    @_active_links.setter
    def _active_links(self, value: dict[tuple[str, str], ActiveLinkInfo]) -> None:
        self._actual_links = value

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

        self._nc = nc
        js = nc.jetstream()

        # Share NATS connection with agent pool for Node Agent dispatch
        self._pool.set_nc(nc)

        log.info("Scheduler NATS connected")

        # Load substrate latency for cross-node compensation
        self._load_substrate_latency()

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

        pending_vis: list[VisibilityEvent] = []
        last_sim_time: datetime | None = None

        async def _on_visibility(msg):
            nonlocal last_sim_time
            data = json.loads(msg.data)
            vis = VisibilityEvent.model_validate(data)

            # While SUSPENDED: discard the event entirely. The buffered
            # LinkStateSnapshot at resume is the authoritative full state
            # for that epoch. Events arriving during suspension are either:
            #   - already incorporated into the buffered snapshot (older
            #     sim_time), or
            #   - from the new epoch but pre-resume (not safe to apply
            #     until ephemeris and snapshot for the new epoch are loaded)
            # In either case, dropping them is correct — the next live
            # snapshot or live event after resume will reconcile any drift.
            if self._suspended:
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
                delta_ms = abs((snap_sim - last_sim_time).total_seconds() * 1000)
                if delta_ms > self._epsilon_ms and pending_vis:
                    desired = self._apply_events_to_desired(list(pending_vis))
                    await self._dispatch_queue.put(desired)
                    pending_vis.clear()

            pending_vis.append(vis)
            last_sim_time = snap_sim

        async def _on_session_ephemeris(msg):
            eph = SessionEphemeris.model_validate_json(msg.data)
            if eph.epoch_id == self._expected_epoch_id:
                self._position_table.load_ephemeris(eph)
                self._epoch_deps_met["ephemeris"] = True
                log.info(
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
            ps = PlaybackState.model_validate_json(msg.data)
            if ps.state == "seeking" and ps.epoch_id > self._expected_epoch_id:
                # New seek — enter SUSPENDED state
                self._suspended = True
                self._stale = False
                self._expected_epoch_id = ps.epoch_id
                self._playback_playing_received = False
                self._epoch_deps_met = {"ephemeris": False, "snapshot": False}
                self._buffered_snapshot = None
                self._latency_update_pending = False
                self._steps_since_latency_update = 0
                # Start watchdog
                if self._watchdog_task and not self._watchdog_task.done():
                    self._watchdog_task.cancel()
                self._watchdog_task = asyncio.create_task(self._epoch_watchdog(ps.epoch_id))
                log.info("SUSPENDED: seeking epoch_id=%d", ps.epoch_id)
            elif ps.state == "playing" and ps.epoch_id == self._expected_epoch_id:
                self._playback_playing_received = True
                log.info("PlaybackState(playing, epoch_id=%d) received", ps.epoch_id)
                await self._check_epoch_resume()
            elif ps.state == "paused":
                log.info("PlaybackState(paused, epoch_id=%d)", ps.epoch_id)

        async def _on_clock_tick(msg):
            nonlocal last_sim_time
            data = json.loads(msg.data)
            tick_epoch_id = data.get("epoch_id", 0)

            # Drop ClockTick while SUSPENDED or wrong epoch
            if self._suspended:
                if tick_epoch_id == self._expected_epoch_id:
                    # This is the resume trigger — check all conditions
                    await self._try_resume_on_clock_tick(data)
                return

            tick_sim_str = data.get("sim_time", "")
            tick_sim_time: datetime | None = None
            if tick_sim_str:
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
                    desired = self._apply_events_to_desired(list(pending_vis))
                    await self._dispatch_queue.put(desired)
                    pending_vis.clear()
                    last_sim_time = tick_sim_time

            self._steps_since_latency_update += 1
            if self._steps_since_latency_update >= self._latency_interval:
                self._latency_update_pending = True
                self._steps_since_latency_update = 0

        async def _on_link_state_snapshot(msg):
            snapshot = LinkStateSnapshot.model_validate_json(msg.data)

            if self._suspended:
                # Buffer if matching epoch_id, discard otherwise
                if snapshot.epoch_id == self._expected_epoch_id:
                    self._buffered_snapshot = snapshot
                    self._epoch_deps_met["snapshot"] = True
                    log.info(
                        "Buffered LinkStateSnapshot seq=%d epoch_id=%d",
                        snapshot.snapshot_seq,
                        snapshot.epoch_id,
                    )
                    await self._check_epoch_resume()
                return

            desired = self._build_desired_from_snapshot(snapshot)
            if desired is not None:
                log.info(
                    "Snapshot seq=%d queued: %d links desired",
                    snapshot.snapshot_seq,
                    len(desired),
                )
                await self._dispatch_queue.put(desired)

        async def _on_substrate_latency(msg):
            """Update substrate latency from live Node Agent measurements."""
            data = json.loads(msg.data)
            source = data.get("source_node", "")
            peers = data.get("peers", {})
            for peer_ip, latency_ms in peers.items():
                self._substrate_by_ip[peer_ip] = latency_ms
            if peers:
                log.info(
                    "Substrate update from %s: %s",
                    source,
                    ", ".join(f"{ip}={ms}ms" for ip, ms in peers.items()),
                )

        subs = []
        try:
            # NODALARC_SESSION stream — SessionEphemeris and PlaybackState
            # MaxMsgsPerSubject=1 ensures late-joiner gets current truth
            subs.append(
                await js.subscribe(
                    SUBJECT_SESSION_EPHEMERIS,
                    stream=STREAM_SESSION_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
                    cb=_on_session_ephemeris,
                )
            )
            subs.append(
                await js.subscribe(
                    SUBJECT_PLAYBACK_STATE,
                    stream=STREAM_SESSION_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
                    cb=_on_playback_state,
                )
            )
            # NODALARC_OME stream — VisibilityEvent and ClockTick
            subs.append(
                await js.subscribe(
                    SUBJECT_VISIBILITY_EVENT,
                    stream="NODALARC_OME",
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=_on_visibility,
                )
            )
            subs.append(
                await js.subscribe(
                    SUBJECT_CLOCK_TICK,
                    stream="NODALARC_OME",
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=_on_clock_tick,
                )
            )
            # NODALARC_LINKS stream — LinkStateSnapshot and SubstrateLatency
            # LAST_PER_SUBJECT delivers the retained snapshot
            # (MaxMsgsPerSubject=1 on NODALARC_LINKS) immediately on subscribe,
            # then continues with new snapshots as OME publishes them.
            # The buffered snapshot is the sole startup catch-up path.
            subs.append(
                await js.subscribe(
                    SUBJECT_LINK_STATE_SNAPSHOT,
                    stream="NODALARC_LINKS",
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
                    cb=_on_link_state_snapshot,
                )
            )
            subs.append(
                await js.subscribe(
                    SUBJECT_SUBSTRATE_LATENCY,
                    stream="NODALARC_LINKS",
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=_on_substrate_latency,
                )
            )
        except Exception as exc:
            log.warning("NATS subscription setup failed: %s — streams may not exist yet", exc)

        log.info("Scheduler dispatcher started — %d callback subscriptions active", len(subs))

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
                try:  # noqa: SIM105
                    await sub.unsubscribe()
                except Exception:
                    pass
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
            with self._override_lock:
                if pair in self._override_set:
                    continue

            if vis.visible and vis.scheduled:
                if pair not in self._desired_links:
                    is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                    if is_gs:
                        ifaces = ("gnd0", "gnd0")
                    else:
                        ifaces = self._interface_map.get(pair)
                        if not ifaces:
                            continue

                    bandwidth = self._bandwidth_map.get(pair)
                    if bandwidth is None:
                        log.warning(
                            "No bandwidth configured for pair %s — "
                            "skipping LinkUp (check satellite/GS terminal config)",
                            pair,
                        )
                        continue
                    sim_unix = vis.sim_time.timestamp() if vis.sim_time else 0.0
                    latency = self._position_table.compute_link_latency(
                        vis.node_a, vis.node_b, sim_unix
                    )
                    if latency is None:
                        latency = 3.0

                    self._desired_links[pair] = ActiveLinkInfo(
                        interface_a=ifaces[0],
                        interface_b=ifaces[1],
                        latency_ms=latency,
                        bandwidth_mbps=bandwidth,
                    )
            elif not vis.visible:
                self._desired_links.pop(pair, None)
            elif vis.visible and not vis.scheduled:
                # Terminal deallocated (GS handoff) — INVARIANT
                is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                if is_gs:
                    self._desired_links.pop(pair, None)

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
        desired: dict[tuple[str, str], ActiveLinkInfo] = {}

        for link in snapshot.links:
            if link.admin == AdminState.UP and link.carrier == CarrierState.UP:
                pair = (link.node_a, link.node_b)
                latency = link.latency_ms
                if latency is None:
                    sim_unix = snapshot.sim_time.timestamp() if snapshot.sim_time else 0.0
                    latency = self._position_table.compute_link_latency(
                        link.node_a, link.node_b, sim_unix
                    )
                if latency is None:
                    latency = 3.0

                is_gs = link.node_a.startswith("gs-") or link.node_b.startswith("gs-")
                if is_gs:
                    ifaces = ("gnd0", "gnd0")
                else:
                    ifaces = self._interface_map.get(pair)
                    if not ifaces:
                        continue

                # Prefer the pre-computed, config-derived bandwidth (authoritative).
                # Fall back to the snapshot's value only if the config didn't
                # resolve (e.g., missing terminal data). Skip the link entirely
                # if neither source yields a positive bandwidth — a silent
                # default would emulate the wrong link rate.
                bandwidth = self._bandwidth_map.get(pair)
                if bandwidth is None and link.bandwidth_mbps:
                    bandwidth = link.bandwidth_mbps
                if bandwidth is None or bandwidth <= 0:
                    log.warning(
                        "No bandwidth for pair %s (config_map=missing, snapshot=%s) "
                        "— skipping in snapshot reconciliation",
                        pair,
                        link.bandwidth_mbps,
                    )
                    continue
                desired[pair] = ActiveLinkInfo(
                    interface_a=ifaces[0],
                    interface_b=ifaces[1],
                    latency_ms=latency,
                    bandwidth_mbps=bandwidth,
                )

        # Replace _desired_links entirely — snapshot is authoritative
        self._desired_links = desired

        isl = sum(1 for a, _ in desired if not a.startswith("gs-"))
        gs = sum(1 for a, _ in desired if a.startswith("gs-"))
        log.info(
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

        desired = self._apply_events_to_desired(vis_events)
        await self._reconcile_links(desired, to_pub, sim_time)

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
        log.info("Dispatch worker started")
        while self._running:
            # Block until work arrives (event-driven, no polling)
            desired = await self._dispatch_queue.get()

            if desired is None:
                break  # Shutdown sentinel

            # Drain queue to latest — discard stale intermediates
            drained = 0
            while not self._dispatch_queue.empty():
                try:
                    next_desired = self._dispatch_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if next_desired is None:
                    desired = None
                    break
                desired = next_desired
                drained += 1

            if desired is None:
                break

            if drained > 0:
                log.info("Dispatch worker: drained %d stale entries from queue", drained)

            sim_time = self._current_sim_time or datetime.now(UTC)
            log.info(
                "Dispatch worker: processing desired with %d links (actual has %d)",
                len(desired),
                len(self._actual_links),
            )

            # Reconcile desired vs actual
            await self._reconcile_links(desired, nc, sim_time)

            # Latency updates (queued by _on_clock_tick)
            if self._latency_update_pending:
                await self._update_latencies(nc)
                self._latency_update_pending = False

        log.info("Dispatch worker stopped")

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

            from nodalarc.platform import get_platform_config

            ns = get_platform_config().kubernetes_namespace
            v1 = kubernetes.client.CoreV1Api()
            cm = v1.read_namespaced_config_map("nodalarc-substrate-latency", ns)
            if cm.data:
                for key, val in cm.data.items():
                    self._substrate_latency[key] = float(val)
                log.info(
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

    def _get_substrate_ms(self, node_a: str, node_b: str) -> float:
        """Get substrate latency for a link pair in ms.

        Prefers live measurements from Node Agent (by peer IP).
        Falls back to ConfigMap value (by node name pair).
        Returns 0.0 for LOCAL links (same node).
        """
        k3s_a = self._loc.k3s_node(node_a)
        k3s_b = self._loc.k3s_node(node_b)
        if not k3s_a or not k3s_b or k3s_a == k3s_b:
            return 0.0
        # Prefer live measurement (by IP) from Node Agent
        ip_b = self._loc.node_ip(k3s_b)
        if ip_b and ip_b in self._substrate_by_ip:
            return self._substrate_by_ip[ip_b]
        # Fallback to ConfigMap measurement (by node name pair)
        key = f"{k3s_a}-{k3s_b}"
        return self._substrate_latency.get(key, 0.0)

    def _link_locality(self, node_a: str, node_b: str) -> int:
        """Determine locality for a link pair."""
        k3s_a = self._loc.k3s_node(node_a)
        k3s_b = self._loc.k3s_node(node_b)
        if k3s_a and k3s_b and k3s_a != k3s_b:
            return node_agent_pb2.CROSS_NODE
        return node_agent_pb2.LOCAL

    async def _reconcile_links(
        self,
        desired: dict[tuple[str, str], ActiveLinkInfo],
        nc,
        sim_time: datetime,
    ) -> None:
        """Reconcile _actual_links toward desired state via Node Agent dispatch.

        THE SINGLE PATH TO THE NODE AGENT FOR LINK STATE.

        Computes delta (desired vs actual), dispatches BatchLinkDown for
        removed pairs (Phase A), then BatchLinkUp for added pairs (Phase B).
        Updates _actual_links only for successfully dispatched changes.
        """
        current_pairs = set(self._actual_links.keys())
        desired_pairs = set(desired.keys())

        to_remove = current_pairs - desired_pairs
        to_add = desired_pairs - current_pairs

        if not to_remove and not to_add:
            return

        sim_iso = sim_time.isoformat()

        # Phase A: BatchLinkDown — downs first, always
        if to_remove:
            removed = await self._send_batch_down(to_remove, sim_iso, sim_time, nc)
            for pair in removed:
                self._actual_links.pop(pair, None)
                self._last_latencies.pop(pair, None)

        # Phase B: BatchLinkUp — only after all Phase A ACKs
        if to_add:
            added = await self._send_batch_up(to_add, desired, sim_iso, sim_time, nc)
            for pair in added:
                self._actual_links[pair] = desired[pair]
                self._last_latencies[pair] = desired[pair].latency_ms

        log.info(
            "Reconcile: +%d/-%d links (%d active)",
            len(to_add),
            len(to_remove),
            len(self._actual_links),
        )

        # Checkpoint (fire-and-forget)
        asyncio.create_task(self._write_checkpoint(sim_iso))

    async def _send_batch_down(
        self,
        pairs: set[tuple[str, str]],
        sim_iso: str,
        sim_time: datetime,
        nc,
    ) -> set[tuple[str, str]]:
        """Send BatchLinkDown to Node Agents. Returns successfully removed pairs."""
        agent_ifaces: dict[str, list[node_agent_pb2.InterfaceDown]] = {}
        pair_agents: dict[tuple[str, str], set[str]] = {}

        for pair in pairs:
            info = self._actual_links.get(pair)
            if info is None:
                continue

            node_a, node_b = pair
            locality = self._link_locality(node_a, node_b)
            is_gs = node_a.startswith("gs-") or node_b.startswith("gs-")

            if is_gs:
                gs_id = node_a if node_a.startswith("gs-") else node_b
                sat_id = node_b if node_a.startswith("gs-") else node_a
                vni = 0
                if locality == node_agent_pb2.CROSS_NODE:
                    from nodalarc.vxlan import compute_vni

                    vni = compute_vni(gs_id, sat_id, "gnd0", "gnd0")

                if locality == node_agent_pb2.LOCAL:
                    targets = [(sat_id, self._loc.agent_addr(sat_id))]
                else:
                    targets = [
                        (sat_id, self._loc.agent_addr(sat_id)),
                        (gs_id, self._loc.agent_addr(gs_id)),
                    ]

                for nid, agent in targets:
                    agent_ifaces.setdefault(agent, []).append(
                        node_agent_pb2.InterfaceDown(
                            node_id=nid,
                            interface_name="gnd0",
                            link_type=node_agent_pb2.GROUND,
                            gs_id=gs_id,
                            sat_id=sat_id,
                            locality=locality,
                            remote_node_ip="",
                            vni=vni,
                        )
                    )
                    pair_agents.setdefault(pair, set()).add(agent)
            else:
                vni = 0
                if locality == node_agent_pb2.CROSS_NODE:
                    from nodalarc.vxlan import compute_vni

                    vni = compute_vni(node_a, node_b, info.interface_a, info.interface_b)

                for nid, ifname, peer_nid, peer_ifname in [
                    (node_a, info.interface_a, node_b, info.interface_b),
                    (node_b, info.interface_b, node_a, info.interface_a),
                ]:
                    agent = self._loc.agent_addr(nid)
                    agent_ifaces.setdefault(agent, []).append(
                        node_agent_pb2.InterfaceDown(
                            node_id=nid,
                            interface_name=ifname,
                            link_type=node_agent_pb2.ISL,
                            locality=locality,
                            vni=vni,
                            peer_node_id=peer_nid,
                            peer_interface_name=peer_ifname,
                        )
                    )
                    pair_agents.setdefault(pair, set()).add(agent)

        successful_agents: set[str] = set()
        agent_addrs = list(agent_ifaces.keys())
        if agent_addrs:
            tasks = []
            for addr in agent_addrs:
                stub = self._pool.get_stub(addr)
                req = node_agent_pb2.BatchLinkDownRequest(
                    batch_id=f"{sim_iso}-down",
                    target_sim_time=sim_iso,
                    interfaces=agent_ifaces[addr],
                )
                tasks.append(stub.async_batch_link_down(req))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                addr = agent_addrs[i]
                if isinstance(result, Exception):
                    log.warning("BatchLinkDown failed for agent %s: %s", addr, result)
                elif not result.success:
                    log.warning("BatchLinkDown partial: %s", result.error_message[:200])
                    if result.interfaces_downed > 0:
                        successful_agents.add(addr)
                else:
                    log.info(
                        "BatchLinkDown: %d downed in %.1fms",
                        result.interfaces_downed,
                        result.apply_time_ms,
                    )
                    successful_agents.add(addr)

        removed: set[tuple[str, str]] = set()
        now = datetime.now(UTC)
        for pair in pairs:
            agents = pair_agents.get(pair, set())
            # ALL agents must succeed (see _send_batch_up for rationale)
            if agents and agents <= successful_agents:
                removed.add(pair)
                info = self._actual_links.get(pair)
                if info:
                    event = LinkDown(
                        sim_time=sim_time,
                        wall_time=now,
                        node_a=pair[0],
                        node_b=pair[1],
                        interface_a=info.interface_a,
                        interface_b=info.interface_b,
                        reason="vis_lost",
                    )
                    asyncio.ensure_future(
                        nc.publish(
                            SUBJECT_LINK_DOWN,
                            event.model_dump_json().encode(),
                        )
                    )

        return removed

    async def _send_batch_up(
        self,
        pairs: set[tuple[str, str]],
        desired: dict[tuple[str, str], ActiveLinkInfo],
        sim_iso: str,
        sim_time: datetime,
        nc,
    ) -> set[tuple[str, str]]:
        """Send BatchLinkUp to Node Agents. Returns successfully added pairs."""
        agent_ifaces: dict[str, list[node_agent_pb2.InterfaceUp]] = {}
        pair_agents: dict[tuple[str, str], set[str]] = {}

        for pair in pairs:
            info = desired.get(pair)
            if info is None:
                continue

            node_a, node_b = pair
            locality = self._link_locality(node_a, node_b)
            is_gs = node_a.startswith("gs-") or node_b.startswith("gs-")

            substrate_ms = self._get_substrate_ms(node_a, node_b)
            netem_ms = max(0.0, info.latency_ms - substrate_ms)
            if substrate_ms > 0 and netem_ms == 0.0:
                log.warning(
                    "Substrate latency %.1fms exceeds orbital %.1fms for %s<->%s — "
                    "emulated latency will be higher than physical reality",
                    substrate_ms,
                    info.latency_ms,
                    node_a,
                    node_b,
                )

            if is_gs:
                gs_id = node_a if node_a.startswith("gs-") else node_b
                sat_id = node_b if node_a.startswith("gs-") else node_a
                vni = 0
                if locality == node_agent_pb2.CROSS_NODE:
                    from nodalarc.vxlan import compute_vni

                    vni = compute_vni(gs_id, sat_id, "gnd0", "gnd0")

                if locality == node_agent_pb2.LOCAL:
                    targets = [(sat_id, self._loc.agent_addr(sat_id), "")]
                else:
                    targets = []
                    for nid, peer_nid in [(sat_id, gs_id), (gs_id, sat_id)]:
                        peer_k3s = self._loc.k3s_node(peer_nid)
                        remote_ip = self._loc.node_ip(peer_k3s)
                        targets.append((nid, self._loc.agent_addr(nid), remote_ip))

                for nid, agent, remote_ip in targets:
                    agent_ifaces.setdefault(agent, []).append(
                        node_agent_pb2.InterfaceUp(
                            node_id=nid,
                            interface_name="gnd0",
                            link_type=node_agent_pb2.GROUND,
                            latency_ms=netem_ms,
                            bandwidth_mbps=info.bandwidth_mbps,
                            gs_id=gs_id,
                            sat_id=sat_id,
                            locality=locality,
                            remote_node_ip=remote_ip,
                            vni=vni,
                        )
                    )
                    pair_agents.setdefault(pair, set()).add(agent)
            else:
                vni = 0
                if locality == node_agent_pb2.CROSS_NODE:
                    from nodalarc.vxlan import compute_vni

                    vni = compute_vni(node_a, node_b, info.interface_a, info.interface_b)

                for nid, ifname, peer_nid, peer_ifname in [
                    (node_a, info.interface_a, node_b, info.interface_b),
                    (node_b, info.interface_b, node_a, info.interface_a),
                ]:
                    agent = self._loc.agent_addr(nid)
                    remote_ip = ""
                    if locality == node_agent_pb2.CROSS_NODE:
                        peer_k3s = self._loc.k3s_node(peer_nid)
                        remote_ip = self._loc.node_ip(peer_k3s)
                    agent_ifaces.setdefault(agent, []).append(
                        node_agent_pb2.InterfaceUp(
                            node_id=nid,
                            interface_name=ifname,
                            link_type=node_agent_pb2.ISL,
                            latency_ms=netem_ms,
                            bandwidth_mbps=info.bandwidth_mbps,
                            locality=locality,
                            remote_node_ip=remote_ip,
                            vni=vni,
                            peer_node_id=peer_nid,
                            peer_interface_name=peer_ifname,
                        )
                    )
                    pair_agents.setdefault(pair, set()).add(agent)

        successful_agents: set[str] = set()
        agent_addrs = list(agent_ifaces.keys())
        if agent_addrs:
            tasks = []
            for addr in agent_addrs:
                stub = self._pool.get_stub(addr)
                req = node_agent_pb2.BatchLinkUpRequest(
                    batch_id=f"{sim_iso}-up",
                    target_sim_time=sim_iso,
                    interfaces=agent_ifaces[addr],
                )
                tasks.append(stub.async_batch_link_up(req))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                addr = agent_addrs[i]
                if isinstance(result, Exception):
                    log.warning("BatchLinkUp failed for agent %s: %s", addr, result)
                elif not result.success:
                    log.warning(
                        "BatchLinkUp partial: %d upped: %s",
                        result.interfaces_upped,
                        result.error_message[:200],
                    )
                    if result.interfaces_upped > 0:
                        successful_agents.add(addr)
                else:
                    log.info(
                        "BatchLinkUp: %d upped in %.1fms",
                        result.interfaces_upped,
                        result.apply_time_ms,
                    )
                    successful_agents.add(addr)

        added: set[tuple[str, str]] = set()
        now = datetime.now(UTC)
        for pair in pairs:
            agents = pair_agents.get(pair, set())
            # ALL agents for this pair must succeed. For LOCAL links (1 agent),
            # this is the same as ANY. For CROSS_NODE links (2 agents), BOTH
            # must succeed — a half-wired VXLAN tunnel does not forward traffic.
            if agents and agents <= successful_agents:
                added.add(pair)
                info = desired[pair]
                sim_unix = sim_time.timestamp() if sim_time else 0.0
                range_km = self._position_table.compute_link_range(pair[0], pair[1], sim_unix)
                event = LinkUp(
                    sim_time=sim_time,
                    wall_time=now,
                    node_a=pair[0],
                    node_b=pair[1],
                    interface_a=info.interface_a,
                    interface_b=info.interface_b,
                    latency_ms=info.latency_ms,
                    bandwidth_mbps=info.bandwidth_mbps,
                    range_km=range_km or 0.0,
                    reason="vis_gained",
                )
                asyncio.ensure_future(nc.publish(SUBJECT_LINK_UP, event.model_dump_json().encode()))

        return added

    # ------------------------------------------------------------------
    # Latency updates
    # ------------------------------------------------------------------

    async def _update_latencies(self, to_pub) -> None:
        """Compute and dispatch latency updates for active links.

        Substrate compensation: netem_ms = max(0, target_ms - substrate_ms).
        """

        active_set = set(self._actual_links.keys())
        sim_time_unix = self._current_sim_time.timestamp() if self._current_sim_time else 0.0
        updates = self._position_table.get_links_needing_update(
            active_set, self._last_latencies, sim_time_unix=sim_time_unix
        )
        if not updates:
            return

        agent_entries: dict[str, list[node_agent_pb2.LatencyEntry]] = {}
        now = datetime.now(UTC)

        for node_a, node_b, new_lat, range_km in updates:
            pair = (node_a, node_b)
            info = self._actual_links.get(pair)
            if not info:
                continue

            substrate_ms = self._get_substrate_ms(node_a, node_b)
            netem_ms = max(0.0, new_lat - substrate_ms)

            info.latency_ms = new_lat
            self._last_latencies[pair] = new_lat

            is_gs = node_a.startswith("gs-") or node_b.startswith("gs-")

            if is_gs:
                gs_id = node_a if node_a.startswith("gs-") else node_b
                sat_id = node_b if node_a.startswith("gs-") else node_a
                agent = self._loc.agent_addr(sat_id)
                agent_entries.setdefault(agent, []).append(
                    node_agent_pb2.LatencyEntry(
                        node_id=sat_id,
                        interface_name="gnd0",
                        latency_ms=netem_ms,
                        link_type=node_agent_pb2.GROUND,
                        gs_id=gs_id,
                        sat_id=sat_id,
                    )
                )
            else:
                for nid, ifname in [
                    (node_a, info.interface_a),
                    (node_b, info.interface_b),
                ]:
                    agent = self._loc.agent_addr(nid)
                    agent_entries.setdefault(agent, []).append(
                        node_agent_pb2.LatencyEntry(
                            node_id=nid,
                            interface_name=ifname,
                            latency_ms=netem_ms,
                            link_type=node_agent_pb2.ISL,
                        )
                    )

            event = LatencyUpdate(
                sim_time=now,
                wall_time=now,
                node_a=node_a,
                node_b=node_b,
                latency_ms=new_lat,
                range_km=range_km,
            )
            asyncio.ensure_future(
                to_pub.publish(SUBJECT_LATENCY_UPDATE, event.model_dump_json().encode())
            )

        # Send to agents concurrently
        tasks = []
        for agent_addr, entries in agent_entries.items():
            stub = self._pool.get_stub(agent_addr)
            req = node_agent_pb2.SetLatencyRequest(entries=entries)
            tasks.append(stub.async_set_latency(req))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    _k8s_v1 = None  # Cached K8s API client

    def _get_k8s_v1(self):
        """Get or create cached K8s CoreV1Api client."""
        if self._k8s_v1 is None:
            import kubernetes
            import kubernetes.client
            import kubernetes.config

            try:
                kubernetes.config.load_incluster_config()
            except kubernetes.config.config_exception.ConfigException:
                kubernetes.config.load_kube_config()
            self._k8s_v1 = kubernetes.client.CoreV1Api()
        return self._k8s_v1

    async def _write_checkpoint(self, sim_time_iso: str) -> None:
        """Write Scheduler checkpoint to K8s ConfigMap (non-blocking)."""
        try:
            import kubernetes.client

            v1 = self._get_k8s_v1()
            from nodalarc.platform import get_platform_config

            ns = get_platform_config().kubernetes_namespace
            body = kubernetes.client.V1ConfigMap(
                metadata=kubernetes.client.V1ObjectMeta(name="nodalarc-scheduler-checkpoint"),
                data={
                    "sim_time": sim_time_iso,
                    "active_links": str(len(self._actual_links)),
                },
            )
            try:
                v1.patch_namespaced_config_map("nodalarc-scheduler-checkpoint", ns, body)
            except kubernetes.client.rest.ApiException as exc:
                if exc.status == 404:
                    v1.create_namespaced_config_map(ns, body)
                else:
                    raise
        except Exception:
            pass  # Checkpoint failure is non-fatal

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
        deps = self._epoch_deps_met
        if deps["ephemeris"] and deps["snapshot"] and self._playback_playing_received:
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

        if not self._playback_playing_received:
            return
        if not self._epoch_deps_met["ephemeris"]:
            return
        if not self._epoch_deps_met["snapshot"]:
            return

        # All conditions met — RESUME
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()

        self._suspended = False
        self._stale = False

        # Apply the buffered LinkStateSnapshot
        if self._buffered_snapshot:
            desired = self._build_desired_from_snapshot(self._buffered_snapshot)
            if desired is not None:
                log.info(
                    "Epoch %d resume: applying buffered snapshot seq=%d (%d links)",
                    self._expected_epoch_id,
                    self._buffered_snapshot.snapshot_seq,
                    len(desired),
                )
                await self._dispatch_queue.put(desired)
            self._buffered_snapshot = None

        # Process the triggering ClockTick normally
        tick_sim_str = tick_data.get("sim_time", "")
        if tick_sim_str:
            self._current_sim_time = datetime.fromisoformat(tick_sim_str)

        log.info(
            "RESUMED: epoch_id=%d sim_time=%s",
            self._expected_epoch_id,
            self._current_sim_time,
        )

    async def _epoch_watchdog(self, epoch_id: int) -> None:
        """30-second watchdog for epoch synchronization.

        If the epoch doesn't resume within 30 seconds, log STALE/ERROR.
        Do NOT crash. Wait for operator intervention (new seek clears stale).
        """
        try:
            await asyncio.sleep(30)
            if self._suspended and self._expected_epoch_id == epoch_id:
                self._stale = True
                deps = self._epoch_deps_met
                log.error(
                    "STALE/ERROR: seek epoch_id=%d timed out after 30s. "
                    "deps: ephemeris=%s snapshot=%s playing=%s",
                    epoch_id,
                    deps["ephemeris"],
                    deps["snapshot"],
                    self._playback_playing_received,
                )
        except asyncio.CancelledError:
            pass  # Normal — watchdog cancelled on successful resume
