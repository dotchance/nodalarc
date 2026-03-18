"""Live orchestrator — drives NodalPath from ZMQ event streams."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nodalpath.console.state import ConsoleState
    from nodalpath.integration.node_inspector import NodeInspector
    from nodalpath.orchestrator.link_state_store import LinkStateStore

import zmq
import zmq.asyncio

from nodalarc.models.events import TimelinePositionSnapshot, VisibilityEvent
from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.zmq_channels import decode_message
from nodalpath.engine.almanac_builder import compute_almanac_entry
from nodalpath.integration.deviation import DeviationDetector
from nodalpath.integration.zmq_publisher import AlmanacPublisher
from nodalpath.models.almanac_event import AlmanacEvent
from nodalpath.models.topology import TopologyNode
from nodalpath.orchestrator.almanac_store import AlmanacStore
from nodalpath.orchestrator.snapshot_builder import SnapshotBuilder
from nodalpath.orchestrator.transition_detector import has_transition
from nodalpath.push.push_scheduler import PushScheduler

log = logging.getLogger(__name__)


class LiveOrchestrator:
    """Drives NodalPath in live mode from ZMQ event streams.

    Subscribes to OME events (VisibilityEvent, TimelinePositionSnapshot) to
    track topology. Subscribes to TO events (LinkDown) for deviation detection.
    Publishes AlmanacEvent records for VS-API consumption.
    """

    def __init__(
        self,
        node_registry: dict[str, TopologyNode],
        interface_map: dict[tuple[str, str], tuple[str, str]],
        prefix_map: dict[str, list[str]],
        bandwidth_map: dict[tuple[str, str], float] | None,
        push_scheduler: PushScheduler,
        publisher: AlmanacPublisher,
        ome_connect: str,
        to_connect: str,
        console_state: ConsoleState | None = None,
        link_state_store: LinkStateStore | None = None,
        node_inspector: NodeInspector | None = None,
        inspection_on_push: bool = True,
        inspection_on_link_event: bool = True,
        inspection_heartbeat_interval_s: int = 0,
        static_edges: list | None = None,
    ) -> None:
        self._builder = SnapshotBuilder(node_registry, interface_map, bandwidth_map, static_edges=static_edges)
        self._node_registry = node_registry
        self._interface_map = interface_map
        self._store = AlmanacStore()
        self._prefix_map = prefix_map
        self._push_scheduler = push_scheduler
        self._publisher = publisher
        self._ome_connect = ome_connect
        self._to_connect = to_connect
        self._deviation_detector = DeviationDetector(self._store)
        self._prev_link_set: frozenset[tuple[str, str]] = frozenset()
        self._current_sim_time: datetime | None = None
        self._transition_count = 0
        self._running = False
        self._console_state = console_state
        self._link_state_store = link_state_store
        self._inspector = node_inspector
        self._inspection_on_push = inspection_on_push
        self._inspection_on_link_event = inspection_on_link_event
        self._inspection_heartbeat_interval_s = inspection_heartbeat_interval_s

    @property
    def link_state_store(self) -> LinkStateStore | None:
        return self._link_state_store

    @property
    def transition_count(self) -> int:
        return self._transition_count

    @property
    def almanac_store(self) -> AlmanacStore:
        return self._store

    @property
    def snapshot_builder(self) -> SnapshotBuilder:
        return self._builder

    async def run(self) -> None:
        """Main async loop. Runs until stop() is called."""
        self._running = True
        ctx = zmq.asyncio.Context()

        ome_sub = ctx.socket(zmq.SUB)
        ome_sub.connect(self._ome_connect)
        ome_sub.setsockopt(zmq.SUBSCRIBE, b"VisibilityEvent")
        ome_sub.setsockopt(zmq.SUBSCRIBE, b"Snapshot")
        ome_sub.setsockopt(zmq.SUBSCRIBE, b"FullStateSnapshot")

        to_sub = ctx.socket(zmq.SUB)
        to_sub.connect(self._to_connect)
        to_sub.setsockopt(zmq.SUBSCRIBE, b"LinkDown")
        to_sub.setsockopt(zmq.SUBSCRIBE, b"LinkUp")
        to_sub.setsockopt(zmq.SUBSCRIBE, b"VisibilityEvent")
        to_sub.setsockopt(zmq.SUBSCRIBE, b"Snapshot")

        poller = zmq.asyncio.Poller()
        poller.register(ome_sub, zmq.POLLIN)
        poller.register(to_sub, zmq.POLLIN)

        log.info(
            "LiveOrchestrator started — OME=%s TO=%s",
            self._ome_connect, self._to_connect,
        )

        # Seed active link state from VS-API to catch links established before
        # this subscriber connected (e.g. when running in a container that
        # starts after the orchestrator has already dispatched initial events).
        await self._seed_from_vsapi()

        if self._inspector is not None and self._inspection_heartbeat_interval_s > 0:
            asyncio.create_task(
                self._inspector.heartbeat_loop(self._inspection_heartbeat_interval_s),
            )

        _poll_count = 0
        try:
            while self._running:
                try:
                    socks = dict(await poller.poll(timeout=1000))
                except zmq.ZMQError as exc:
                    log.error("ZMQ poller error: %s", exc)
                    break

                _poll_count += 1
                if _poll_count % 30 == 1:
                    log.info(
                        "Poll #%d: %d sockets ready, active_links=%d, transitions=%d",
                        _poll_count, len(socks),
                        len(self._builder._active_links), self._transition_count,
                    )

                # Drain ALL buffered messages per socket (not just one)
                if ome_sub in socks:
                    while True:
                        try:
                            raw = await ome_sub.recv(zmq.NOBLOCK)
                            await self._handle_ome_message(raw)
                        except zmq.Again:
                            break

                if to_sub in socks:
                    while True:
                        try:
                            raw = await to_sub.recv(zmq.NOBLOCK)
                            await self._handle_to_message(raw)
                        except zmq.Again:
                            break

                # Check for manual recompute request from console
                if self._console_state is not None and self._console_state.consume_recompute_request():
                    if self._current_sim_time is not None:
                        log.info("Manual recompute requested via console")
                        await self._recompute(self._current_sim_time.isoformat())

        except asyncio.CancelledError:
            log.info("LiveOrchestrator cancelled")
        finally:
            ome_sub.close()
            to_sub.close()
            ctx.term()
            log.info(
                "LiveOrchestrator stopped (%d transitions, %d deviations)",
                self._transition_count, self._deviation_detector.deviation_count,
            )

    async def _seed_from_vsapi(self) -> None:
        """Seed active link state from VS-API to catch links established before connect."""
        try:
            from nodalarc.platform import get_platform_config
            cfg = get_platform_config()
            import os
            api_key = os.environ.get("NODAL_API_KEY", "")
            # VS-API runs on the host — use per-service host override if available
            vs_api_host = cfg.zmq_connect_host_for("vs-api")
            url = f"http://{vs_api_host}:{cfg.vs_api_http_port}/api/v1/state"
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            import httpx
            for attempt in range(10):
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(url, headers=headers, timeout=5.0)
                    if resp.status_code == 200:
                        state = resp.json()
                        links = state.get("links", [])
                        seeded = 0
                        sim_time = state.get("sim_time")
                        for link in links:
                            if link.get("state") != "active":
                                continue
                            pair = (min(link["node_a"], link["node_b"]), max(link["node_a"], link["node_b"]))
                            if pair not in self._builder._active_links:
                                self._builder._active_links[pair] = link.get("range_km", 0.0)
                                seeded += 1
                        if sim_time:
                            self._current_sim_time = datetime.fromisoformat(sim_time)
                        log.info("Seeded %d active links from VS-API (%d total)", seeded, len(self._builder._active_links))
                        if seeded > 0:
                            await self._check_transition(sim_time or datetime.now(timezone.utc).isoformat())
                        return
                    elif resp.status_code == 401:
                        # Try fetching the API key from the token endpoint
                        try:
                            token_url = f"http://{vs_api_host}:{cfg.vs_api_http_port}/api/v1/auth/token"
                            async with httpx.AsyncClient() as client:
                                token_resp = await client.get(token_url, timeout=5.0)
                            if token_resp.status_code == 200:
                                api_key = token_resp.json().get("token", "")
                                headers["Authorization"] = f"Bearer {api_key}"
                                continue
                        except Exception:
                            pass
                    log.warning("VS-API seed attempt %d: HTTP %d", attempt + 1, resp.status_code)
                except Exception as exc:
                    log.debug("VS-API seed attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(2)
            log.warning("Could not seed from VS-API after 10 attempts — starting with empty link state")
        except Exception as exc:
            log.warning("VS-API seed failed: %s", exc)

    def stop(self) -> None:
        self._running = False

    async def _handle_ome_message(self, raw: bytes) -> None:
        """Process one OME message.

        Handles both wrapped format (from OME ZMQ direct: {timestamp_s, event_type, data: {...}})
        and unwrapped format (from orchestrator re-publish: the model directly).
        """
        try:
            topic, payload = decode_message(raw)
            data = json.loads(payload)

            # Unwrap if OME sends wrapped events (has "data" key with nested model)
            inner = data.get("data", data) if isinstance(data, dict) and "event_type" in data else data

            if topic == b"FullStateSnapshot":
                await self._handle_full_state_snapshot(data)

            elif topic == b"VisibilityEvent":
                event = VisibilityEvent.model_validate(inner)
                if self._current_sim_time is not None and event.sim_time != self._current_sim_time:
                    await self._check_transition(self._current_sim_time.isoformat())
                self._current_sim_time = event.sim_time
                self._builder.apply_link_event(event)

            elif topic == b"Snapshot":
                snapshot = TimelinePositionSnapshot.model_validate(inner)
                self._builder.apply_position_record(snapshot)
                # Trigger transition check on sim_time change from Snapshots too
                if self._current_sim_time is not None and snapshot.sim_time != self._current_sim_time:
                    await self._check_transition(self._current_sim_time.isoformat())
                self._current_sim_time = snapshot.sim_time

        except Exception as exc:
            log.warning("OME message processing error: %s", exc, exc_info=True)

    async def _handle_full_state_snapshot(self, data: dict) -> None:
        """Initialize link state from OME FullStateSnapshot (slow joiner catchup).

        ZMQ Slow Joiner Problem
        -----------------------
        ZMQ PUB/SUB has a well-known "slow joiner" race: when a SUB socket
        connects to a PUB, there is a brief window where messages published
        by the PUB are lost because the SUB's subscription has not yet
        propagated. In our architecture, the OME publishes VisibilityEvent
        messages as a stream, and NodalPath subscribes to reconstruct link
        state. If NodalPath starts (or restarts) after the OME has already
        published the initial window of events, those events are gone -- the
        SUB never receives them and NodalPath's link state is empty.

        FullStateSnapshot solves this by providing a complete point-in-time
        snapshot of all link states (ISL and ground). The OME publishes
        FullStateSnapshot periodically (every 30 seconds) and at the start
        of each simulation window. When NodalPath receives one, it
        replaces its entire active_links set with the snapshot contents,
        then triggers a transition check to compute and push forwarding
        tables for the current topology.

        Belt and Suspenders: VS-API Seed
        ---------------------------------
        In addition to FullStateSnapshot, the `_seed_from_vsapi()` method
        (called at startup) fetches the current link state from the VS-API
        HTTP endpoint. This is a second line of defense: if the first
        FullStateSnapshot is delayed or the OME has not published one yet,
        the VS-API seed provides the initial state. Both mechanisms write
        to the same `_builder._active_links` dict, so whichever arrives
        first populates the state and the other is a no-op (or a
        refinement). The VS-API seed is best-effort (10 retries, then
        gives up) because FullStateSnapshot is the authoritative source.

        range_km Extraction
        -------------------
        The FullStateSnapshot includes `range_km` for each link pair.
        This is extracted and stored in `_builder._active_links` (which
        maps link pairs to their range in km) because NodalPath uses
        range_km to compute accurate one-way light-propagation latency
        for the MPLS forwarding model. Without range_km, the latency
        computation would fall back to a default or zero, producing
        incorrect tc netem delay values on the veth pairs. Storing
        range_km at the link level (rather than fetching it later)
        ensures the latency is available immediately when the forwarding
        table is computed.
        """
        sim_time = data.get("sim_time", datetime.now(timezone.utc).isoformat())

        link_count = 0
        for state_key in ("isl_state", "gs_state"):
            for pair_key, state in data.get(state_key, {}).items():
                parts = pair_key.split(":")
                if len(parts) != 2:
                    continue
                node_a, node_b = parts[0], parts[1]
                # Ensure canonical ordering
                if node_a > node_b:
                    node_a, node_b = node_b, node_a
                pair = (node_a, node_b)
                visible = state.get("visible", False)
                scheduled = state.get("scheduled", False)

                range_km = state.get("range_km", 0.0)

                if visible and scheduled:
                    self._builder._active_links[pair] = range_km
                    link_count += 1
                else:
                    self._builder._active_links.pop(pair, None)

                self._builder._all_links[pair] = (visible, scheduled, range_km)

        self._current_sim_time = datetime.fromisoformat(sim_time)

        log.info(
            "FullStateSnapshot: %d active links initialized at %s",
            link_count, sim_time,
        )

        # Trigger transition check — computes initial paths and pushes tables
        await self._check_transition(sim_time)

    async def _handle_to_message(self, raw: bytes) -> None:
        """Process one TO message — deviations + re-published OME events."""
        try:
            topic, payload = decode_message(raw)
            data = json.loads(payload)

            # Handle VisibilityEvent and Snapshot re-published by orchestrator
            if topic == b"VisibilityEvent" or topic == b"Snapshot":
                await self._handle_ome_message(raw)
                return

            if topic == b"LinkDown":
                event = LinkDown.model_validate(data)
                is_deviation = self._deviation_detector.check_link_down(event)
                if is_deviation:
                    sim_time_iso = event.sim_time.isoformat()
                    entry = self._store.get_entry_at(sim_time_iso)
                    self._publisher.publish_deviation(
                        sim_time=event.sim_time,
                        topology_state_id=entry.topology_state_id if entry else "unknown",
                        node_a=event.node_a,
                        node_b=event.node_b,
                        reason=event.reason,
                    )
                    if self._console_state is not None:
                        self._console_state.record_deviation(
                            sim_time=event.sim_time.isoformat(),
                            topology_state_id=entry.topology_state_id if entry else "unknown",
                            node_a=event.node_a,
                            node_b=event.node_b,
                            reason=event.reason,
                        )
                    if self._inspector is not None and self._inspection_on_link_event:
                        state_id = entry.topology_state_id if entry else self._inspector._last_pushed_state_id
                        asyncio.create_task(self._inspector.trigger_link_event(state_id))
                    await self._recompute(event.sim_time.isoformat())

            elif topic == b"LinkUp":
                event = LinkUp.model_validate(data)
                if self._deviation_detector.check_link_up(event):
                    await self._recompute(event.sim_time.isoformat())

        except Exception as exc:
            log.warning("TO message processing error: %s", exc)

    async def _check_transition(self, sim_time_iso: str) -> None:
        """Check for topology transition and compute almanac entry if changed."""
        curr = self._builder.active_link_set
        if not has_transition(self._prev_link_set, curr):
            return

        snapshot = self._builder.build_snapshot(sim_time_iso)
        entry = compute_almanac_entry(
            snapshot, self._prefix_map,
            node_registry=self._node_registry,
            interface_map=self._interface_map,
        )
        self._store.store(entry)
        self._transition_count += 1

        log.info(
            "Transition at %s: %d active links, %d forwarding tables",
            sim_time_iso, len(curr), len(entry.forwarding_tables),
        )

        self._publisher.publish_path_computed(
            sim_time=datetime.fromisoformat(sim_time_iso),
            topology_state_id=entry.topology_state_id,
        )

        prev_entry = self._store.entries[-2] if len(self._store.entries) > 1 else None
        loop = asyncio.get_running_loop()
        push_result = await loop.run_in_executor(
            None, self._push_scheduler.push_entry, entry, prev_entry,
        )

        self._publisher.publish_table_pushed(
            sim_time=datetime.fromisoformat(sim_time_iso),
            topology_state_id=entry.topology_state_id,
            nodes_attempted=push_result.nodes_attempted,
            nodes_succeeded=push_result.nodes_succeeded,
            nodes_failed=push_result.nodes_failed,
            push_duration_ms=push_result.push_duration_ms,
        )

        if self._inspector is not None and self._inspection_on_push:
            self._inspector.record_push(entry.topology_state_id, entry.forwarding_tables)
            asyncio.create_task(self._inspector.trigger_push_verify(entry.topology_state_id))

        self._prev_link_set = curr

        if self._console_state is not None:
            self._console_state.record_transition(
                sim_time=sim_time_iso,
                topology_state_id=entry.topology_state_id,
                active_link_count=len(curr),
                forwarding_table_count=len(entry.forwarding_tables),
            )
            self._console_state.record_push_result(push_result)

            # Build console-format topology dict for the frontend graph
            # Only count active links for neighbor counts
            fls = self._builder.full_link_state
            isl_counts: dict[str, int] = {}
            gnd_counts: dict[str, int] = {}
            for edge in snapshot.edges:
                pair = (edge.src_node_id, edge.dst_node_id)
                vis, sched, _ = fls.get(pair, (False, False, 0.0))
                if not (vis and sched):
                    continue  # Only count active links
                for nid in (edge.src_node_id, edge.dst_node_id):
                    if edge.link_type == "isl":
                        isl_counts[nid] = isl_counts.get(nid, 0) + 1
                    else:
                        gnd_counts[nid] = gnd_counts.get(nid, 0) + 1

            nodes_payload = []
            for node in snapshot.nodes:
                ic = isl_counts.get(node.node_id, 0)
                gc = gnd_counts.get(node.node_id, 0)
                nodes_payload.append({
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "plane": node.plane,
                    "slot": node.slot,
                    "routing_area": getattr(node, "routing_area", None),
                    "neighbor_count": ic + gc,
                    "isl_count": ic,
                    "gnd_count": gc,
                    "prefix": ", ".join(self._prefix_map.get(node.node_id, [])),
                })

            # Use full_link_state to determine live link status instead
            # of hardcoding all edges as "active".
            fls = self._builder.full_link_state
            links_payload = []
            for edge in snapshot.edges:
                pair = (edge.src_node_id, edge.dst_node_id)
                vis, sched, _range = fls.get(pair, (False, False, 0.0))
                if vis and sched:
                    state = "active"
                elif vis and not sched:
                    state = "visible_unscheduled"
                else:
                    state = "inactive"
                links_payload.append({
                    "node_a": edge.src_node_id,
                    "node_b": edge.dst_node_id,
                    "state": state,
                    "link_type": edge.link_type,
                })

            self._console_state.record_topology_snapshot({
                "topology_state_id": entry.topology_state_id,
                "sim_time": sim_time_iso,
                "nodes": nodes_payload,
                "links": links_payload,
            })

        if self._link_state_store is not None:
            self._link_state_store.store(
                topology_state_id=entry.topology_state_id,
                full_link_state=self._builder.full_link_state,
                sim_time=sim_time_iso,
                is_future=False,
            )

    async def _recompute(self, sim_time_iso: str) -> None:
        """Force recomputation at current topology state (used after deviation)."""
        snapshot = self._builder.build_snapshot(sim_time_iso)
        entry = compute_almanac_entry(
            snapshot, self._prefix_map,
            node_registry=self._node_registry,
            interface_map=self._interface_map,
        )
        self._store.store(entry)

        if self._console_state is not None:
            self._console_state.record_recomputation()

        log.info(
            "Recomputation at %s after deviation: %d forwarding tables",
            sim_time_iso, len(entry.forwarding_tables),
        )

        self._publisher.publish(AlmanacEvent(
            event_type="recomputation_triggered",
            sim_time=datetime.fromisoformat(sim_time_iso),
            wall_time=datetime.now(timezone.utc),
            topology_state_id=entry.topology_state_id,
        ))

        loop = asyncio.get_running_loop()
        push_result = await loop.run_in_executor(
            None, self._push_scheduler.push_entry, entry, None,
        )

        if self._console_state is not None:
            self._console_state.record_push_result(push_result)

        self._publisher.publish_table_pushed(
            sim_time=datetime.fromisoformat(sim_time_iso),
            topology_state_id=entry.topology_state_id,
            nodes_attempted=push_result.nodes_attempted,
            nodes_succeeded=push_result.nodes_succeeded,
            nodes_failed=push_result.nodes_failed,
            push_duration_ms=push_result.push_duration_ms,
        )

        if self._inspector is not None and self._inspection_on_push:
            self._inspector.record_push(entry.topology_state_id, entry.forwarding_tables)
            asyncio.create_task(self._inspector.trigger_push_verify(entry.topology_state_id))
