"""Discrete-Event dispatcher — load timeline, process events sequentially.

Loads a pre-computed timeline from JSON Lines, processes events in order,
calls link_manager for state changes, latency_model for position updates,
and MI convergence gate for each batch.

Does NOT subscribe to OME ZeroMQ, run probes, or evaluate convergence.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import zmq

from nodalarc.models.events import (
    ClockTick,
    TimelinePositionSnapshot,
    VisibilityEvent,
)
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.models.metrics import ConvergenceRequest, ConvergenceResult
from nodalarc.zmq_channels import (
    MI_CONVERGENCE_GATE_CONNECT,
    OME_EVENTS_BIND,
    TO_EVENTS_BIND,
    encode_message,
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_POSITION_EVENT,
)
from orchestrator.latency_model import PositionTable

log = logging.getLogger(__name__)


class ActiveLinkInfo:
    """Mutable internal state for an active link."""

    __slots__ = (
        "interface_a", "interface_b", "latency_ms",
        "bandwidth_mbps", "pid_a", "pid_b",
    )

    def __init__(
        self,
        interface_a: str,
        interface_b: str,
        latency_ms: float,
        bandwidth_mbps: float,
        pid_a: int = 0,
        pid_b: int = 0,
    ) -> None:
        self.interface_a = interface_a
        self.interface_b = interface_b
        self.latency_ms = latency_ms
        self.bandwidth_mbps = bandwidth_mbps
        self.pid_a = pid_a
        self.pid_b = pid_b


def _load_timeline(path: Path) -> list[dict[str, Any]]:
    """Load timeline from JSON Lines file."""
    events: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def _group_by_timestamp(
    events: list[dict[str, Any]],
    epsilon_s: float = 0.1,
) -> list[list[dict[str, Any]]]:
    """Group events into batches by timestamp (within epsilon)."""
    if not events:
        return []
    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = [events[0]]
    current_ts = events[0]["timestamp_s"]
    for event in events[1:]:
        if abs(event["timestamp_s"] - current_ts) < epsilon_s:
            current_batch.append(event)
        else:
            batches.append(current_batch)
            current_batch = [event]
            current_ts = event["timestamp_s"]
    if current_batch:
        batches.append(current_batch)
    return batches


class DiscreteEventDispatcher:
    """Process a pre-computed timeline in discrete-event mode."""

    def __init__(
        self,
        timeline_path: Path,
        interface_map: dict[tuple[str, str], tuple[str, str]],
        bandwidth_map: dict[tuple[str, str], float],
        override_set: set[tuple[str, str]],
        override_lock: Any,
        pid_map: dict[str, int] | None = None,
        db_conn: Any = None,
        dwell_s: float = 1.0,
        latency_update_interval_s: int = 10,
        use_convergence_gate: bool = True,
    ) -> None:
        self._timeline_path = timeline_path
        self._interface_map = interface_map
        self._bandwidth_map = bandwidth_map
        self._override_set = override_set
        self._override_lock = override_lock
        self._pid_map = pid_map or {}
        self._db_conn = db_conn
        self._dwell_s = dwell_s
        self._latency_update_interval_s = latency_update_interval_s
        self._use_convergence_gate = use_convergence_gate

        self._position_table = PositionTable()
        self._active_links: dict[tuple[str, str], ActiveLinkInfo] = {}
        self._last_latencies: dict[tuple[str, str], float] = {}
        self._steps_since_latency_update = 0

    def run(self) -> None:
        """Execute the full timeline."""
        events = _load_timeline(self._timeline_path)
        batches = _group_by_timestamp(events)
        log.info(f"Loaded timeline: {len(events)} events in {len(batches)} batches")

        # Set up ZeroMQ
        ctx = zmq.Context()
        pub_sock = ctx.socket(zmq.PUB)
        pub_sock.bind(TO_EVENTS_BIND)

        # OME PUB socket for position events (VS-API subscribes to this)
        ome_pub_sock = ctx.socket(zmq.PUB)
        ome_pub_sock.bind(OME_EVENTS_BIND)

        conv_sock = None
        if self._use_convergence_gate:
            conv_sock = ctx.socket(zmq.REQ)
            conv_sock.connect(MI_CONVERGENCE_GATE_CONNECT)

        # Allow subscribers time to connect (ZMQ slow joiner)
        time.sleep(0.5)

        try:
            orbit = 0
            while True:
                orbit += 1
                for batch_idx, batch in enumerate(batches):
                    self._process_batch(batch, pub_sock, conv_sock, ome_pub_sock)
                    self._steps_since_latency_update += 1

                    if batch_idx < len(batches) - 1:
                        time.sleep(self._dwell_s)
                log.info(
                    f"Timeline orbit {orbit} complete: "
                    f"{len(self._active_links)} active links, looping"
                )
        except KeyboardInterrupt:
            log.info("Dispatcher interrupted")
        finally:
            pub_sock.close()
            ome_pub_sock.close()
            if conv_sock:
                conv_sock.close()
            ctx.term()

    def _process_batch(
        self,
        batch: list[dict[str, Any]],
        pub_sock: zmq.Socket,
        conv_sock: zmq.Socket | None,
        ome_pub_sock: zmq.Socket | None = None,
    ) -> None:
        """Process a batch of events at the same timestamp."""
        link_events: list[LinkUp | LinkDown] = []

        # Phase 1: Process snapshots first (position updates)
        for record in batch:
            if record["event_type"] == "Snapshot":
                snap = TimelinePositionSnapshot.model_validate(record["data"])
                self._position_table.update_from_snapshot(snap)
                # Publish position event for VS-API
                if ome_pub_sock is not None:
                    positions_list = []
                    for node_id, pos in snap.positions.items():
                        node_type = "ground_station" if node_id.startswith("gs-") else "satellite"
                        plane = None
                        slot = None
                        if node_type == "satellite":
                            # Parse plane/slot from node_id: sat-P00S05
                            parts = node_id.replace("sat-P", "").split("S")
                            if len(parts) == 2:
                                plane = int(parts[0])
                                slot = int(parts[1])
                        positions_list.append({
                            "node_id": node_id,
                            "node_type": node_type,
                            "lat_deg": pos.lat_deg,
                            "lon_deg": pos.lon_deg,
                            "alt_km": pos.alt_km,
                            "vel_x_km_s": pos.vel_x_km_s,
                            "vel_y_km_s": pos.vel_y_km_s,
                            "vel_z_km_s": pos.vel_z_km_s,
                            "plane": plane,
                            "slot": slot,
                            "routing_area": None,
                            "neighbor_count": 0,
                            "isl_count": 0,
                            "gnd_count": 0,
                        })
                    position_data = {
                        "sim_time": snap.sim_time.isoformat(),
                        "positions": positions_list,
                    }
                    ome_pub_sock.send(encode_message(
                        TOPIC_POSITION_EVENT,
                        json.dumps(position_data).encode(),
                    ))
            elif record["event_type"] == "ClockTick":
                pass  # Clock ticks are informational

        # Phase 2: Process visibility events
        for record in batch:
            if record["event_type"] != "VisibilityEvent":
                continue
            vis = VisibilityEvent.model_validate(record["data"])
            pair = (vis.node_a, vis.node_b)

            # Check override set
            with self._override_lock:
                if pair in self._override_set:
                    continue

            if vis.visible and vis.scheduled:
                link_event = self._handle_link_up(vis, pub_sock)
                if link_event:
                    link_events.append(link_event)
            elif not vis.visible:
                link_event = self._handle_link_down(vis, pub_sock)
                if link_event:
                    link_events.append(link_event)

        # Phase 3: Latency updates on active links
        if self._steps_since_latency_update >= self._latency_update_interval_s:
            self._update_latencies(pub_sock)
            self._steps_since_latency_update = 0

        # Phase 4: Convergence gate for each link event
        if conv_sock and link_events:
            for le in link_events:
                self._call_convergence_gate(conv_sock, le)

    def _handle_link_up(
        self,
        vis: VisibilityEvent,
        pub_sock: zmq.Socket,
    ) -> LinkUp | None:
        """Handle a visibility gained event → bring link up."""
        pair = (vis.node_a, vis.node_b)
        if pair in self._active_links:
            return None  # Already up

        ifaces = self._interface_map.get(pair)
        if not ifaces:
            log.warning(f"No interface mapping for {pair}")
            return None

        bandwidth = self._bandwidth_map.get(pair, 1000.0)
        latency = self._position_table.compute_link_latency(vis.node_a, vis.node_b)
        if latency is None:
            latency = 3.0  # Default fallback

        # Record active link
        self._active_links[pair] = ActiveLinkInfo(
            interface_a=ifaces[0],
            interface_b=ifaces[1],
            latency_ms=latency,
            bandwidth_mbps=bandwidth,
            pid_a=self._pid_map.get(vis.node_a, 0),
            pid_b=self._pid_map.get(vis.node_b, 0),
        )
        self._last_latencies[pair] = latency

        # Apply kernel changes if we have PIDs
        info = self._active_links[pair]
        if info.pid_a and info.pid_b:
            from orchestrator import link_manager
            # GS links need dynamic veth creation (no pre-wired pairs)
            is_gs_link = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
            if is_gs_link:
                link_manager.create_veth_pair(
                    info.pid_a, info.pid_b, ifaces[0], ifaces[1],
                    node_id_a=vis.node_a, node_id_b=vis.node_b,
                )
                link_manager.enable_mpls_input(info.pid_a, ifaces[0])
                link_manager.enable_mpls_input(info.pid_b, ifaces[1])
            link_manager.set_interface_up(info.pid_a, ifaces[0])
            link_manager.set_interface_up(info.pid_b, ifaces[1])
            link_manager.apply_link_shaping(info.pid_a, ifaces[0], latency, bandwidth)
            link_manager.apply_link_shaping(info.pid_b, ifaces[1], latency, bandwidth)

        now = datetime.now(timezone.utc)
        event = LinkUp(
            sim_time=vis.sim_time,
            wall_time=now,
            node_a=vis.node_a,
            node_b=vis.node_b,
            interface_a=ifaces[0],
            interface_b=ifaces[1],
            latency_ms=latency,
            bandwidth_mbps=bandwidth,
            reason="vis_gained",
        )
        pub_sock.send(encode_message(TOPIC_LINK_UP, event.model_dump_json().encode()))
        self._record_link_event(event)
        return event

    def _handle_link_down(
        self,
        vis: VisibilityEvent,
        pub_sock: zmq.Socket,
    ) -> LinkDown | None:
        """Handle a visibility lost event → bring link down."""
        pair = (vis.node_a, vis.node_b)
        info = self._active_links.pop(pair, None)
        if info is None:
            return None  # Wasn't up

        # Apply kernel changes if we have PIDs
        if info.pid_a and info.pid_b:
            from orchestrator import link_manager
            is_gs_link = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
            if is_gs_link:
                # Destroy dynamic veth — deleting one end removes both + qdiscs
                link_manager.destroy_veth_pair(info.pid_a, info.interface_a)
            else:
                link_manager.set_interface_down(info.pid_a, info.interface_a)
                link_manager.set_interface_down(info.pid_b, info.interface_b)

        self._last_latencies.pop(pair, None)

        now = datetime.now(timezone.utc)
        event = LinkDown(
            sim_time=vis.sim_time,
            wall_time=now,
            node_a=vis.node_a,
            node_b=vis.node_b,
            interface_a=info.interface_a,
            interface_b=info.interface_b,
            reason="vis_lost",
        )
        pub_sock.send(encode_message(TOPIC_LINK_DOWN, event.model_dump_json().encode()))
        self._record_link_event(event)
        return event

    def _update_latencies(self, pub_sock: zmq.Socket) -> None:
        """Recompute and apply latency updates for all active links."""
        active_set = set(self._active_links.keys())
        updates = self._position_table.get_links_needing_update(
            active_set, self._last_latencies,
        )
        now = datetime.now(timezone.utc)
        for node_a, node_b, new_lat, range_km in updates:
            pair = (node_a, node_b)
            info = self._active_links.get(pair)
            if not info:
                continue

            # Apply kernel change
            if info.pid_a and info.pid_b:
                from orchestrator import link_manager
                link_manager.update_delay(info.pid_a, info.interface_a, new_lat)
                link_manager.update_delay(info.pid_b, info.interface_b, new_lat)

            info.latency_ms = new_lat
            self._last_latencies[pair] = new_lat

            # Get sim_time from position table's last known time
            event = LatencyUpdate(
                sim_time=now,
                wall_time=now,
                node_a=node_a,
                node_b=node_b,
                latency_ms=new_lat,
                range_km=range_km,
            )
            pub_sock.send(encode_message(
                TOPIC_LATENCY_UPDATE, event.model_dump_json().encode(),
            ))

    def _call_convergence_gate(
        self,
        conv_sock: zmq.Socket,
        link_event: LinkUp | LinkDown,
    ) -> None:
        """Send convergence request to MI gate and wait for response."""
        event_id = str(uuid.uuid4())
        req = ConvergenceRequest(
            event_id=event_id,
            link_event=link_event,
        )
        conv_sock.send(req.model_dump_json().encode())
        raw_reply = conv_sock.recv()
        result = ConvergenceResult.model_validate_json(raw_reply)
        log.info(
            f"Convergence: event={event_id} converged={result.converged} "
            f"duration={result.duration_ms}ms"
        )
        self._record_convergence(result)

    def _record_link_event(self, event: LinkUp | LinkDown) -> None:
        """Record a link event in SQLite."""
        if self._db_conn is None:
            return
        from nodalarc.db.queries import insert_link_event
        insert_link_event(self._db_conn, event)

    def _record_convergence(self, result: ConvergenceResult) -> None:
        """Record a convergence result in SQLite."""
        if self._db_conn is None:
            return
        from nodalarc.db.queries import insert_convergence_event
        insert_convergence_event(self._db_conn, result)
