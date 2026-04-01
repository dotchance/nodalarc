"""Scheduler dispatch loop — OME ZMQ subscription, two-phase gRPC dispatch.

Subscribes to OME ZMQ PUB (port 5560) for VisibilityEvent, ClockTick,
Snapshot, and HeartbeatTick. Dispatches link changes as two-phase gRPC:
all BatchLinkDown ACKs before any BatchLinkUp is sent. Publishes
LinkUp/LinkDown/LatencyUpdate on port 5561.

Per streaming architecture v1.2: VisibilityEvents are paced by the OME
at their sim_time. No _pending_vis buffer. No FullStateSnapshot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import UTC, datetime

import zmq
import zmq.asyncio
from nodalarc.models.events import TimelinePositionSnapshot, VisibilityEvent
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.zmq_channels import (
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    decode_message,
    encode_message,
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
    """Two-phase topology dispatcher.

    Subscribes to OME ZMQ, windows events by sim_time, dispatches
    BatchLinkDown/Up to Node Agents, publishes TO events on port 5561.

    INVARIANT: visible=True, scheduled=False for a GS pair MUST remove the
    pair from _active_links in ALL code paths that process VisibilityEvents:
      1. _ome_catchup replay (line ~803)
      2. _dispatch_batch live (line ~281)
    _dispatch_ups only processes scheduled=True events — deallocation is
    handled by _dispatch_batch before _dispatch_ups is called.
    This bug appeared 3 times. test_ome_scheduler_contract.py verifies both
    paths. Do not remove that test.
    """

    def __init__(
        self,
        ome_endpoint: str,
        interface_map: dict[tuple[str, str], tuple[str, str]],
        bandwidth_map: dict[tuple[str, str], float],
        pod_locator: PodLocationMap,
        agent_pool: AgentPool,
        override_set: set[tuple[str, str]],
        override_lock: threading.Lock,
        compression_factor: int = 1,
        latency_update_interval_s: int = 10,
        epsilon_ms: float = 100.0,
    ) -> None:
        self._ome_endpoint = ome_endpoint
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

        # Pairs that failed dispatch and should not be retried.
        self._skip_pairs: set[tuple[str, str]] = set()

        # Dedup threshold: skip VisibilityEvents with sim_time <= this value.
        # Set from catch-up response current_sim_time.
        self._dedup_threshold: str = ""

    async def run(self, external_to_pub: object | None = None) -> None:
        """Main async dispatch loop.

        Args:
            external_to_pub: If provided, use this synchronous ZMQ PUB socket
                (created in the main thread, shared with scenario handler).
                If None, create and bind a new async PUB socket.
        """
        self._running = True
        ctx = zmq.asyncio.Context()

        if external_to_pub is not None:
            # Wrap the synchronous socket for use in the async loop.
            # ZMQ sync sockets work fine with send() from asyncio — the send
            # is non-blocking for PUB sockets (drops if no subscriber).
            to_pub = external_to_pub
            log.info("Using external TO PUB socket")
        else:
            from nodalarc.zmq_channels import to_events_bind

            to_pub = ctx.socket(zmq.PUB)
            to_pub.bind(to_events_bind())
            log.info("TO PUB bound on %s", to_events_bind())

        # The Scheduler does NOT bind the OME port (5560). The OME container
        # owns that port. VS-API subscribes to OME directly for position data.
        # The Scheduler publishes ONLY LinkUp/LinkDown/LatencyUpdate on 5561.

        # SUB to OME events per streaming architecture v1.2 Section 3.4
        ome_sub = ctx.socket(zmq.SUB)
        ome_sub.setsockopt(zmq.RECONNECT_IVL, 1000)
        ome_sub.setsockopt(zmq.RECONNECT_IVL_MAX, 10000)
        ome_sub.connect(self._ome_endpoint)
        ome_sub.setsockopt(zmq.SUBSCRIBE, b"VisibilityEvent")
        ome_sub.setsockopt(zmq.SUBSCRIBE, b"ClockTick")
        ome_sub.setsockopt(zmq.SUBSCRIBE, b"Snapshot")
        ome_sub.setsockopt(zmq.SUBSCRIBE, b"HeartbeatTick")

        await asyncio.sleep(0.5)

        # R-OME-008: catch-up — VisibilityEvents only from rolling log.
        # Catch-up is authoritative. No separate reconciliation step.
        await self._ome_catchup(to_pub)

        log.info("Scheduler dispatcher started — OME=%s", self._ome_endpoint)

        # Buffered events for epsilon-windowed batching
        pending_vis: list[VisibilityEvent] = []
        pending_snaps: list[TimelinePositionSnapshot] = []
        last_sim_time: datetime | None = None

        poller = zmq.asyncio.Poller()
        poller.register(ome_sub, zmq.POLLIN)
        last_ome_msg_time = time.monotonic()

        try:
            while self._running:
                socks = dict(await poller.poll(timeout=500))
                if ome_sub not in socks:
                    # Timeout — flush any pending batch
                    if pending_vis or pending_snaps:
                        await self._dispatch_batch(pending_vis, pending_snaps, to_pub)
                        pending_vis.clear()
                        pending_snaps.clear()
                    # 15-second watchdog: re-run catch-up
                    if time.monotonic() - last_ome_msg_time > 15.0:
                        log.warning("No OME messages for 15s — forcing reconnect + catch-up")
                        ome_sub.disconnect(self._ome_endpoint)
                        ome_sub.connect(self._ome_endpoint)
                        await self._ome_catchup(to_pub)
                        last_ome_msg_time = time.monotonic()
                    continue

                # Drain all buffered messages
                while True:
                    try:
                        raw = await ome_sub.recv(zmq.NOBLOCK)
                    except zmq.Again:
                        break

                    topic, payload = decode_message(raw)
                    data = json.loads(payload)
                    last_ome_msg_time = time.monotonic()

                    # HeartbeatTick: reset watchdog only, no other action
                    if topic == b"HeartbeatTick":
                        continue

                    # Snapshot: update position table for latency calculations
                    if topic == b"Snapshot":
                        snap = TimelinePositionSnapshot.model_validate(data)
                        self._position_table.update_from_snapshot(snap)
                        self._current_sim_time = snap.sim_time
                        continue

                    # ClockTick: trigger latency updates
                    if topic == b"ClockTick":
                        tick_sim_str = data.get("sim_time", "")
                        if tick_sim_str:
                            self._current_sim_time = datetime.fromisoformat(tick_sim_str)
                        # Flush pending batch and update latencies
                        if pending_vis or pending_snaps:
                            await self._dispatch_batch(pending_vis, pending_snaps, to_pub)
                            pending_vis.clear()
                            pending_snaps.clear()
                        self._steps_since_latency_update += 1
                        if self._steps_since_latency_update >= self._latency_interval:
                            await self._update_latencies(to_pub)
                            self._steps_since_latency_update = 0
                        continue

                    # VisibilityEvent: dedup, then apply to _active_links and dispatch
                    if topic == b"VisibilityEvent":
                        vis = VisibilityEvent.model_validate(data)
                        # Dedup: skip if already processed from catch-up
                        if self._dedup_threshold:
                            evt_st = vis.sim_time.isoformat()
                            if evt_st <= self._dedup_threshold:
                                continue
                        pending_vis.append(vis)

                        # Epsilon windowing: flush when sim_time changes
                        snap_sim = vis.sim_time
                        if last_sim_time is not None and snap_sim != last_sim_time:
                            delta_ms = abs((snap_sim - last_sim_time).total_seconds() * 1000)
                            if delta_ms > self._epsilon_ms and pending_vis:
                                await self._dispatch_batch(pending_vis, [], to_pub)
                                pending_vis.clear()
                        last_sim_time = snap_sim

        except asyncio.CancelledError:
            log.info("Dispatcher cancelled")
        finally:
            if external_to_pub is None:
                to_pub.close()
            ome_sub.close()
            ctx.term()
            log.info("Dispatcher stopped")

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Batch dispatch
    # ------------------------------------------------------------------

    async def _dispatch_batch(
        self,
        vis_events: list[VisibilityEvent],
        snapshots: list[TimelinePositionSnapshot],
        to_pub: zmq.asyncio.Socket,
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
        to_pub: zmq.asyncio.Socket,
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
            tasks.append(loop.run_in_executor(None, stub.batch_link_down, req))

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
                to_pub.send(encode_message(TOPIC_LINK_DOWN, event.model_dump_json().encode()))

    async def _dispatch_ups(
        self,
        events: list[VisibilityEvent],
        sim_time_iso: str,
        to_pub: zmq.asyncio.Socket,
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
            tasks.append(loop.run_in_executor(None, stub.batch_link_up, req))

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
                to_pub.send(encode_message(TOPIC_LINK_UP, event.model_dump_json().encode()))

    # ------------------------------------------------------------------
    # Latency updates
    # ------------------------------------------------------------------

    async def _update_latencies(self, to_pub: zmq.asyncio.Socket) -> None:
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
            to_pub.send(encode_message(TOPIC_LATENCY_UPDATE, event.model_dump_json().encode()))

        # Send to agents concurrently
        loop = asyncio.get_running_loop()
        tasks = []
        for agent_addr, entries in agent_entries.items():
            stub = self._pool.get_stub(agent_addr)
            req = node_agent_pb2.SetLatencyRequest(entries=entries)
            tasks.append(loop.run_in_executor(None, stub.set_latency, req))

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

    async def _ome_catchup(self, to_pub) -> None:
        """R-OME-008: Request VisibilityEvents from OME rolling catch-up log.

        Per streaming architecture v1.2 Section 3.4:
        - Process all VisibilityEvents in sim_time order
        - Apply to _active_links (visible+scheduled → add, !visible → remove)
        - Set _dedup_threshold from response current_sim_time
        """

        def _sync_catchup() -> dict | None:
            import zmq as _zmq
            from nodalarc.platform import get_platform_config

            catchup_addr = get_platform_config().ome_catchup_connect
            ctx = _zmq.Context()
            sock = ctx.socket(_zmq.REQ)
            sock.setsockopt(_zmq.RCVTIMEO, 20000)
            sock.setsockopt(_zmq.SNDTIMEO, 5000)
            sock.setsockopt(_zmq.LINGER, 0)
            sock.connect(catchup_addr)

            # Always request all events — the rolling log covers one orbital
            # period and is small enough to replay from the start. Using a
            # checkpoint offset can miss events if the OME restarted and its
            # current pacing position is behind the checkpoint sim_time.
            req: dict = {"request": "events_since"}

            sock.send_json(req)
            resp = sock.recv_json()
            sock.close()
            ctx.term()
            return resp

        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(None, _sync_catchup)
            if resp is None:
                return

            events = resp.get("events", [])
            current_sim = resp.get("current_sim_time", "")

            log.info(
                "OME catch-up: %d VisibilityEvents (current_sim=%s)",
                len(events),
                current_sim[:19] if current_sim else "none",
            )

            # Catch-up is authoritative — start from empty.
            # Checkpoint provides since_sim_time offset only, not state.
            self._active_links.clear()

            # Apply all VisibilityEvents in sim_time order to _active_links
            added = 0
            removed = 0
            skipped_no_iface = 0
            for evt in events:
                vis = VisibilityEvent.model_validate(evt)
                pair = (vis.node_a, vis.node_b)
                if vis.visible and vis.scheduled:
                    is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                    if is_gs:
                        iface_a, iface_b = "gnd0", "gnd0"
                    else:
                        ifaces = self._interface_map.get(pair)
                        if not ifaces:
                            skipped_no_iface += 1
                            continue
                        iface_a, iface_b = ifaces
                    self._active_links[pair] = ActiveLinkInfo(
                        interface_a=iface_a,
                        interface_b=iface_b,
                        latency_ms=3.0,
                        bandwidth_mbps=self._bandwidth_map.get(pair, 1000.0),
                    )
                    added += 1
                elif not vis.visible:
                    if pair in self._active_links:
                        removed += 1
                    self._active_links.pop(pair, None)
                elif vis.visible and not vis.scheduled:
                    is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                    if is_gs:
                        if pair in self._active_links:
                            removed += 1
                        self._active_links.pop(pair, None)
            log.info(
                "Catch-up applied: %d added, %d removed, %d skipped (no iface), "
                "interface_map=%d pairs",
                added,
                removed,
                skipped_no_iface,
                len(self._interface_map),
            )

            # Set dedup threshold
            self._dedup_threshold = current_sim

            isl_count = sum(1 for (a, _) in self._active_links if not a.startswith("gs-"))
            gs_count = sum(1 for (a, _) in self._active_links if a.startswith("gs-"))
            log.info(
                "Catch-up seeded %d active links (%d ISL, %d GS), dedup=%s",
                len(self._active_links),
                isl_count,
                gs_count,
                self._dedup_threshold[:19] if self._dedup_threshold else "none",
            )

        except Exception as exc:
            log.warning("OME catch-up failed: %s", exc)
