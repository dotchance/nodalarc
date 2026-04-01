"""Scheduler dispatch loop — NATS JetStream subscription, two-phase dispatch.

Subscribes to NATS JetStream for VisibilityEvent, ClockTick, Snapshot,
HeartbeatTick, and LinkStateSnapshot. Dispatches link changes as two-phase
BatchLinkDown/Up to Node Agents. Publishes LinkUp/LinkDown/LatencyUpdate
on NATS subjects.

LinkStateSnapshot (R-OME-009) is applied as replace-not-merge — all prior
_active_links state is discarded and rebuilt from the snapshot. This
eliminates window boundary accumulation, subscriber drift, and all
transition-only state bugs permanently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime

import nats
from nodalarc.models.events import TimelinePositionSnapshot, VisibilityEvent
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.models.link_state import AdminState, CarrierState, LinkStateSnapshot
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    SUBJECT_CLOCK_TICK,
    SUBJECT_LATENCY_UPDATE,
    SUBJECT_LINK_DOWN,
    SUBJECT_LINK_STATE_SNAPSHOT,
    SUBJECT_LINK_UP,
    SUBJECT_SNAPSHOT,
    SUBJECT_VISIBILITY_EVENT,
    nats_url,
)

from node_agent.proto import node_agent_pb2
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
    """Two-phase topology dispatcher — NATS JetStream transport.

    Subscribes to NATS for OME events, dispatches BatchLinkDown/Up to
    Node Agents, publishes LinkUp/LinkDown/LatencyUpdate on NATS.

    LinkStateSnapshot (R-OME-009) applied as replace-not-merge every 5
    sim-seconds. Eliminates window boundary GS accumulation permanently.

    INVARIANT: visible=True, scheduled=False for a GS pair MUST remove the
    pair from _active_links in ALL code paths that process VisibilityEvents:
      1. _apply_link_state_snapshot (replace-not-merge)
      2. _dispatch_batch live (line ~281)
    _dispatch_ups only processes scheduled=True events — deallocation is
    handled by _dispatch_batch before _dispatch_ups is called.
    This bug appeared 3 times. test_ome_scheduler_contract.py verifies both
    paths. Do not remove that test.
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
        self._active_links: dict[tuple[str, str], ActiveLinkInfo] = {}
        self._last_latencies: dict[tuple[str, str], float] = {}
        self._steps_since_latency_update = 0
        self._current_sim_time: datetime | None = None
        self._running = False
        self._last_snapshot_seq: int = 0

        # Pairs that failed dispatch and should not be retried.
        self._skip_pairs: set[tuple[str, str]] = set()

    async def run(self, nc: nats.NATS | None = None, **_kwargs) -> None:
        """Main async dispatch loop — NATS JetStream subscription.

        On startup: get latest LinkStateSnapshot from JetStream, apply as
        replace-not-merge. Then subscribe to live events.

        Args:
            nc: NATS connection. If None, connects using nats_url().
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

        # Subscribe to LinkStateSnapshot — get latest retained message
        # JetStream MaxMsgsPerSubject=1 means only the latest snapshot exists
        try:
            sub_snap = await js.subscribe(
                SUBJECT_LINK_STATE_SNAPSHOT,
                stream="NODALARC_LINKS",
                ordered_consumer=True,
            )
            # Try to get the latest snapshot (non-blocking)
            try:
                msg = await sub_snap.next_msg(timeout=5)
                snapshot = LinkStateSnapshot.model_validate_json(msg.data)
                self._apply_link_state_snapshot(snapshot)
                await self._dispatch_snapshot_delta(nc)
                log.info(
                    "Initial snapshot applied: seq=%d, %d links",
                    snapshot.snapshot_seq,
                    len(snapshot.links),
                )
            except nats.errors.TimeoutError:
                log.info("No initial LinkStateSnapshot available — waiting for OME")
        except Exception as exc:
            log.warning("LinkStateSnapshot subscription failed: %s", exc)

        # Subscribe to OME event subjects
        pending_vis: list[VisibilityEvent] = []
        last_sim_time: datetime | None = None

        async def _handle_visibility(msg):
            nonlocal last_sim_time
            data = json.loads(msg.data)
            vis = VisibilityEvent.model_validate(data)
            pending_vis.append(vis)

            snap_sim = vis.sim_time
            if last_sim_time is not None and snap_sim != last_sim_time:
                delta_ms = abs((snap_sim - last_sim_time).total_seconds() * 1000)
                if delta_ms > self._epsilon_ms and pending_vis:
                    await self._dispatch_batch(pending_vis, [], nc)
                    pending_vis.clear()
            last_sim_time = snap_sim

        async def _handle_snapshot(msg):
            data = json.loads(msg.data)
            snap = TimelinePositionSnapshot.model_validate(data)
            self._position_table.update_from_snapshot(snap)
            self._current_sim_time = snap.sim_time

        async def _handle_clock_tick(msg):
            data = json.loads(msg.data)
            tick_sim_str = data.get("sim_time", "")
            if tick_sim_str:
                self._current_sim_time = datetime.fromisoformat(tick_sim_str)
            if pending_vis:
                await self._dispatch_batch(pending_vis, [], nc)
                pending_vis.clear()
            self._steps_since_latency_update += 1
            if self._steps_since_latency_update >= self._latency_interval:
                await self._update_latencies(nc)
                self._steps_since_latency_update = 0

        async def _handle_link_state_snapshot(msg):
            snapshot = LinkStateSnapshot.model_validate_json(msg.data)
            self._apply_link_state_snapshot(snapshot)
            await self._dispatch_snapshot_delta(nc)

        # Subscribe to all subjects via JetStream ordered consumers
        subs = []
        try:
            subs.append(
                await js.subscribe(
                    SUBJECT_VISIBILITY_EVENT, stream="NODALARC_OME", ordered_consumer=True
                )
            )
            subs.append(
                await js.subscribe(SUBJECT_SNAPSHOT, stream="NODALARC_OME", ordered_consumer=True)
            )
            subs.append(
                await js.subscribe(SUBJECT_CLOCK_TICK, stream="NODALARC_OME", ordered_consumer=True)
            )
            subs.append(
                await js.subscribe(
                    SUBJECT_LINK_STATE_SNAPSHOT,
                    stream="NODALARC_LINKS",
                    ordered_consumer=True,
                )
            )
        except Exception as exc:
            log.warning("NATS subscription setup failed: %s — streams may not exist yet", exc)

        log.info("Scheduler dispatcher started — NATS subscriptions active")

        # Message processing loop
        handlers = {
            SUBJECT_VISIBILITY_EVENT: _handle_visibility,
            SUBJECT_SNAPSHOT: _handle_snapshot,
            SUBJECT_CLOCK_TICK: _handle_clock_tick,
            SUBJECT_LINK_STATE_SNAPSHOT: _handle_link_state_snapshot,
        }

        try:
            while self._running:
                got_message = False
                for sub in subs:
                    try:
                        msg = await sub.next_msg(timeout=0.5)
                        got_message = True
                        handler = handlers.get(msg.subject)
                        if handler:
                            await handler(msg)
                    except nats.errors.TimeoutError:
                        continue

                if not got_message and pending_vis:
                    await self._dispatch_batch(pending_vis, [], nc)
                    pending_vis.clear()

        except asyncio.CancelledError:
            log.info("Dispatcher cancelled")
        finally:
            for sub in subs:
                try:  # noqa: SIM105
                    await sub.unsubscribe()
                except Exception:
                    pass
            if owns_nc:
                await nc.close()
            log.info("Dispatcher stopped")

    def _apply_link_state_snapshot(self, snapshot: LinkStateSnapshot) -> None:
        """Apply LinkStateSnapshot as replace-not-merge (R-OME-009).

        Discards all prior _active_links state and rebuilds from the snapshot.
        Any subscriber applying the same snapshot arrives at identical state.
        Multi-node safe: no coordination needed between Scheduler instances.
        """
        if snapshot.snapshot_seq <= self._last_snapshot_seq:
            log.debug(
                "Discarding old snapshot seq=%d (current=%d)",
                snapshot.snapshot_seq,
                self._last_snapshot_seq,
            )
            return

        previous = dict(self._active_links)
        self._active_links.clear()

        for link in snapshot.links:
            if link.admin == AdminState.UP and link.carrier == CarrierState.UP:
                pair = (link.node_a, link.node_b)
                self._active_links[pair] = ActiveLinkInfo(
                    interface_a=link.interface_a,
                    interface_b=link.interface_b,
                    latency_ms=link.latency_ms or 3.0,
                    bandwidth_mbps=link.bandwidth_mbps or 1000.0,
                )

        self._last_snapshot_seq = snapshot.snapshot_seq

        isl = sum(1 for a, _ in self._active_links if not a.startswith("gs-"))
        gs = sum(1 for a, _ in self._active_links if a.startswith("gs-"))
        log.info(
            "LinkStateSnapshot applied: seq=%d, %d links (%d ISL, %d GS)",
            snapshot.snapshot_seq,
            len(self._active_links),
            isl,
            gs,
        )

        # Compute delta and save previous state for dispatch.
        # _dispatch_downs needs the PREVIOUS ActiveLinkInfo for removed pairs
        # (interface names, agent lookup). We must save it before it's lost.
        new_pairs = set(self._active_links.keys())
        old_pairs = set(previous.keys())
        self._snapshot_delta = (
            new_pairs - old_pairs,
            old_pairs - new_pairs,
            previous,  # previous _active_links — needed for down dispatch
        )

    async def _dispatch_snapshot_delta(self, nc) -> None:
        """Dispatch BatchLinkUp/Down to Node Agent for snapshot delta.

        Called after _apply_link_state_snapshot. Sends the kernel operations
        that make the data plane match the snapshot state.

        Critical for GS handoffs: the down dispatch needs the PREVIOUS
        ActiveLinkInfo (interface names) for removed pairs. We temporarily
        restore them into _active_links for the dispatch, then remove them.
        """
        if not hasattr(self, "_snapshot_delta") or self._snapshot_delta is None:
            return

        added_pairs, removed_pairs, previous = self._snapshot_delta
        self._snapshot_delta = None

        if not added_pairs and not removed_pairs:
            return

        sim_time = self._current_sim_time or datetime.now(UTC)
        sim_iso = sim_time.isoformat()

        # Dispatch downs first — temporarily restore removed pairs so
        # _dispatch_downs can find their interface info and agent address.
        if removed_pairs:
            for pair in removed_pairs:
                prev_info = previous.get(pair)
                if prev_info:
                    self._active_links[pair] = prev_info

            down_events = []
            for pair in removed_pairs:
                down_events.append(
                    VisibilityEvent(
                        sim_time=sim_time,
                        node_a=pair[0],
                        node_b=pair[1],
                        visible=False,
                        scheduled=False,
                        range_km=0.0,
                        elevation_deg=0.0,
                        terminal_type="optical",
                    )
                )
            await self._dispatch_downs(down_events, sim_iso, nc)

            # Clean up — _dispatch_downs pops from _active_links on success,
            # but ensure none linger if dispatch failed
            for pair in removed_pairs:
                self._active_links.pop(pair, None)

        # Dispatch ups
        if added_pairs:
            up_events = []
            for pair in added_pairs:
                info = self._active_links.get(pair)
                if not info:
                    continue
                up_events.append(
                    VisibilityEvent(
                        sim_time=sim_time,
                        node_a=pair[0],
                        node_b=pair[1],
                        visible=True,
                        scheduled=True,
                        range_km=0.0,
                        elevation_deg=0.0,
                        terminal_type="optical",
                    )
                )
            await self._dispatch_ups(up_events, sim_iso, nc)

        log.info(
            "Snapshot delta dispatched: %d up, %d down",
            len(added_pairs),
            len(removed_pairs),
        )

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Batch dispatch
    # ------------------------------------------------------------------

    async def _dispatch_batch(
        self,
        vis_events: list[VisibilityEvent],
        snapshots: list[TimelinePositionSnapshot],
        to_pub,
    ) -> None:
        """Process one epsilon-windowed batch of VisibilityEvents."""
        if not vis_events:
            return

        # Phase 2: Collect link down/up events
        sim_time = vis_events[0].sim_time
        self._current_sim_time = sim_time
        sim_time_iso = sim_time.isoformat()

        # Filter overrides and classify
        down_events: list[VisibilityEvent] = []
        up_events: list[VisibilityEvent] = []

        for vis in vis_events:
            pair = (vis.node_a, vis.node_b)
            with self._override_lock:
                if pair in self._override_set:
                    continue

            if vis.visible and vis.scheduled:
                if pair not in self._active_links:
                    up_events.append(vis)
            elif not vis.visible:
                if pair in self._active_links:
                    down_events.append(vis)
            elif vis.visible and not vis.scheduled:
                # Terminal deallocated (GS handoff)
                is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                if is_gs and pair in self._active_links:
                    down_events.append(vis)

        # Phase A: All BatchLinkDown — concurrent across agents
        if down_events:
            await self._dispatch_downs(down_events, sim_time_iso, to_pub)

        # Phase B: All BatchLinkUp — concurrent across agents
        # Only AFTER all down ACKs received.
        if up_events:
            await self._dispatch_ups(up_events, sim_time_iso, to_pub)

        # Latency updates
        self._steps_since_latency_update += 1
        if self._steps_since_latency_update >= self._latency_interval:
            await self._update_latencies(to_pub)
            self._steps_since_latency_update = 0

        # Checkpoint (fire-and-forget — don't block event loop)
        asyncio.create_task(self._write_checkpoint(sim_time_iso))

    # ------------------------------------------------------------------
    # Two-phase gRPC dispatch
    # ------------------------------------------------------------------

    async def _dispatch_downs(
        self,
        events: list[VisibilityEvent],
        sim_time_iso: str,
        to_pub,
    ) -> None:
        """Phase A: BatchLinkDown to all agents concurrently.

        Links are removed from _active_links ONLY after the Node Agent
        confirms success. If BatchLinkDown fails, links stay in
        _active_links — they are still up as far as we know.
        """
        agent_ifaces: dict[str, list[node_agent_pb2.InterfaceDown]] = {}
        # Pending removals: (pair -> (info, vis)) — committed only on success
        pending: dict[tuple[str, str], tuple[ActiveLinkInfo, VisibilityEvent]] = {}

        for vis in events:
            pair = (vis.node_a, vis.node_b)
            info = self._active_links.get(pair)
            if info is None:
                continue
            pending[pair] = (info, vis)

            is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")

            if is_gs:
                gs_id = vis.node_a if vis.node_a.startswith("gs-") else vis.node_b
                sat_id = vis.node_b if vis.node_a.startswith("gs-") else vis.node_a
                agent = self._loc.agent_addr(sat_id)
                agent_ifaces.setdefault(agent, []).append(
                    node_agent_pb2.InterfaceDown(
                        node_id=sat_id,
                        interface_name="gnd0",
                        link_type=node_agent_pb2.GROUND,
                        gs_id=gs_id,
                        sat_id=sat_id,
                    )
                )
            else:
                for nid, ifname in [
                    (vis.node_a, info.interface_a),
                    (vis.node_b, info.interface_b),
                ]:
                    agent = self._loc.agent_addr(nid)
                    agent_ifaces.setdefault(agent, []).append(
                        node_agent_pb2.InterfaceDown(
                            node_id=nid,
                            interface_name=ifname,
                            link_type=node_agent_pb2.ISL,
                        )
                    )

        # Send to all agents concurrently — simultaneity requirement
        loop = asyncio.get_running_loop()
        tasks = []
        for agent_addr, ifaces in agent_ifaces.items():
            stub = self._pool.get_stub(agent_addr)
            req = node_agent_pb2.BatchLinkDownRequest(
                batch_id=f"{sim_time_iso}-down",
                target_sim_time=sim_time_iso,
                locality=node_agent_pb2.LOCAL,
                interfaces=ifaces,
            )
            tasks.append(stub.async_batch_link_down(req))

        # Track which agents succeeded
        successful_agents: set[str] = set()
        agent_addrs_list = list(agent_ifaces.keys())
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                addr = agent_addrs_list[i]
                if isinstance(result, Exception):
                    log.warning("BatchLinkDown failed for agent %s: %s", addr, result)
                elif not result.success:
                    log.warning("BatchLinkDown partial failure: %s", result.error_message[:200])
                    successful_agents.add(addr)  # Partial = some succeeded
                else:
                    log.info(
                        "BatchLinkDown: %d downed in %.1fms",
                        result.interfaces_downed,
                        result.apply_time_ms,
                    )
                    successful_agents.add(addr)

        # Commit removals and publish TO events per-link for successful agents
        for pair, (link_info, vis) in pending.items():
            # Check if at least one node's agent succeeded
            agent_a = self._loc.agent_addr(vis.node_a)
            agent_b = self._loc.agent_addr(vis.node_b)
            if agent_a in successful_agents or agent_b in successful_agents:
                self._active_links.pop(pair, None)
                self._last_latencies.pop(pair, None)
                now = datetime.now(UTC)
                event = LinkDown(
                    sim_time=vis.sim_time,
                    wall_time=now,
                    node_a=vis.node_a,
                    node_b=vis.node_b,
                    interface_a=link_info.interface_a,
                    interface_b=link_info.interface_b,
                    reason="vis_lost",
                )
                asyncio.ensure_future(
                    to_pub.publish(SUBJECT_LINK_DOWN, event.model_dump_json().encode())
                )

    async def _dispatch_ups(
        self,
        events: list[VisibilityEvent],
        sim_time_iso: str,
        to_pub,
    ) -> None:
        """Phase B: BatchLinkUp to all agents concurrently.

        Called ONLY after all Phase A (down) ACKs are received.

        Links are added to _active_links ONLY after the Node Agent
        confirms success. If BatchLinkUp fails, the links stay out of
        _active_links so the next dispatch cycle will retry them.
        """
        agent_ifaces: dict[str, list[node_agent_pb2.InterfaceUp]] = {}
        # Pending links: added to _active_links only on success
        pending: dict[tuple[str, str], tuple[ActiveLinkInfo, VisibilityEvent]] = {}

        for vis in events:
            pair = (vis.node_a, vis.node_b)
            is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")

            # GS links use gnd0/gnd0 — not in _interface_map (which is ISL-only)
            if is_gs:
                ifaces = ("gnd0", "gnd0")
            else:
                ifaces = self._interface_map.get(pair)
                if not ifaces:
                    continue

            bandwidth = self._bandwidth_map.get(pair, 1000.0)
            latency = self._position_table.compute_link_latency(vis.node_a, vis.node_b)
            if latency is None:
                latency = 3.0

            pending[pair] = (
                ActiveLinkInfo(
                    interface_a=ifaces[0],
                    interface_b=ifaces[1],
                    latency_ms=latency,
                    bandwidth_mbps=bandwidth,
                ),
                vis,
            )

            if is_gs:
                gs_id = vis.node_a if vis.node_a.startswith("gs-") else vis.node_b
                sat_id = vis.node_b if vis.node_a.startswith("gs-") else vis.node_a
                agent = self._loc.agent_addr(sat_id)
                agent_ifaces.setdefault(agent, []).append(
                    node_agent_pb2.InterfaceUp(
                        node_id=sat_id,
                        interface_name="gnd0",
                        link_type=node_agent_pb2.GROUND,
                        latency_ms=latency,
                        bandwidth_mbps=bandwidth,
                        gs_id=gs_id,
                        sat_id=sat_id,
                    )
                )
            else:
                for nid, ifname in [
                    (vis.node_a, ifaces[0]),
                    (vis.node_b, ifaces[1]),
                ]:
                    agent = self._loc.agent_addr(nid)
                    agent_ifaces.setdefault(agent, []).append(
                        node_agent_pb2.InterfaceUp(
                            node_id=nid,
                            interface_name=ifname,
                            link_type=node_agent_pb2.ISL,
                            latency_ms=latency,
                            bandwidth_mbps=bandwidth,
                        )
                    )

        # Send to all agents concurrently
        loop = asyncio.get_running_loop()
        tasks = []
        for agent_addr, ifaces in agent_ifaces.items():
            stub = self._pool.get_stub(agent_addr)
            req = node_agent_pb2.BatchLinkUpRequest(
                batch_id=f"{sim_time_iso}-up",
                target_sim_time=sim_time_iso,
                locality=node_agent_pb2.LOCAL,
                interfaces=ifaces,
            )
            tasks.append(stub.async_batch_link_up(req))

        # Track which agents succeeded (full or partial)
        successful_agents: set[str] = set()
        agent_addrs_list = list(agent_ifaces.keys())
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                addr = agent_addrs_list[i]
                if isinstance(result, Exception):
                    log.warning("BatchLinkUp failed for agent %s: %s", addr, result)
                elif not result.success:
                    log.warning(
                        "BatchLinkUp partial failure: %d upped: %s",
                        result.interfaces_upped,
                        result.error_message[:200],
                    )
                    successful_agents.add(addr)  # Partial = some succeeded
                else:
                    log.info(
                        "BatchLinkUp: %d upped in %.1fms",
                        result.interfaces_upped,
                        result.apply_time_ms,
                    )
                    successful_agents.add(addr)

        # Commit to _active_links and publish TO events per-link for successful agents
        for pair, (info, vis) in pending.items():
            agent_a = self._loc.agent_addr(vis.node_a)
            agent_b = self._loc.agent_addr(vis.node_b)
            if agent_a in successful_agents or agent_b in successful_agents:
                self._active_links[pair] = info
                self._last_latencies[pair] = info.latency_ms
                now = datetime.now(UTC)
                event = LinkUp(
                    sim_time=vis.sim_time,
                    wall_time=now,
                    node_a=vis.node_a,
                    node_b=vis.node_b,
                    interface_a=info.interface_a,
                    interface_b=info.interface_b,
                    latency_ms=info.latency_ms,
                    bandwidth_mbps=info.bandwidth_mbps,
                    reason="vis_gained",
                )
                asyncio.ensure_future(
                    to_pub.publish(SUBJECT_LINK_UP, event.model_dump_json().encode())
                )

    # ------------------------------------------------------------------
    # Latency updates
    # ------------------------------------------------------------------

    async def _update_latencies(self, to_pub) -> None:
        """Compute and dispatch latency updates for active links.

        Substrate compensation (R-TO-002A): netem_ms = max(0, target_ms - substrate_ms).
        In M4 (single node), substrate_ms = 0.0 for all pairs.
        """
        # Scale-forward: substrate compensation present but zero in M4
        substrate_latency: dict[tuple[str, str], float] = {}

        active_set = set(self._active_links.keys())
        updates = self._position_table.get_links_needing_update(active_set, self._last_latencies)
        if not updates:
            return

        agent_entries: dict[str, list[node_agent_pb2.LatencyEntry]] = {}
        now = datetime.now(UTC)

        for node_a, node_b, new_lat, range_km in updates:
            pair = (node_a, node_b)
            info = self._active_links.get(pair)
            if not info:
                continue

            # R-TO-002A substrate compensation
            substrate_ms = substrate_latency.get(pair, 0.0)
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

            # Publish LatencyUpdate on port 5561
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
        loop = asyncio.get_running_loop()
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
        """Write sim_time to ConfigMap via merge patch. Fire-and-forget."""
        try:
            import kubernetes.client

            v1 = self._get_k8s_v1()
            from nodalarc.platform import get_platform_config

            ns = get_platform_config().kubernetes_namespace
            # Include active link pairs so reconciliation can compare
            # against Node Agent observed state without recomputing topology.
            active_pairs = sorted(f"{a}:{b}" for a, b in self._active_links)
            body = {
                "metadata": {"name": "nodalarc-scheduler-checkpoint"},
                "data": {
                    "sim_time": sim_time_iso,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "active_links": json.dumps(active_pairs),
                },
            }
            try:
                v1.patch_namespaced_config_map("nodalarc-scheduler-checkpoint", ns, body)
            except kubernetes.client.rest.ApiException as exc:
                if exc.status == 404:
                    v1.create_namespaced_config_map(
                        ns,
                        kubernetes.client.V1ConfigMap(
                            metadata=kubernetes.client.V1ObjectMeta(
                                name="nodalarc-scheduler-checkpoint"
                            ),
                            data=body["data"],
                        ),
                    )
                else:
                    raise
        except Exception as exc:
            log.warning("Checkpoint write failed (non-fatal): %s", exc)

    @staticmethod
    def read_checkpoint() -> dict | None:
        """Read checkpoint from ConfigMap. Returns {"sim_time": str} or None."""
        try:
            import kubernetes
            import kubernetes.client
            import kubernetes.config

            try:
                kubernetes.config.load_incluster_config()
            except kubernetes.config.config_exception.ConfigException:
                kubernetes.config.load_kube_config()
            v1 = kubernetes.client.CoreV1Api()
            from nodalarc.platform import get_platform_config

            ns = get_platform_config().kubernetes_namespace
            cm = v1.read_namespaced_config_map("nodalarc-scheduler-checkpoint", ns)
            return cm.data
        except kubernetes.client.rest.ApiException as exc:
            if exc.status == 404:
                return None
            raise
        except Exception as exc:
            log.warning("Checkpoint read failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Reconciliation on startup
    # ------------------------------------------------------------------
