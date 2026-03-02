"""Real-Time dispatcher — read timeline, pace at wall-clock speed.

Reads a pre-computed timeline from JSON Lines (same as DE dispatcher),
but paces event batches at wall-clock speed using the session's
compression_factor. Publishes on both OME (port 5560) and TO (port 5561)
so VS-API receives position updates and link events.

Loops the timeline continuously (one orbit per pass).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import zmq

from nodalarc.models.events import (
    TimelinePositionSnapshot,
    VisibilityEvent,
)
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.zmq_channels import (
    OME_EVENTS_BIND,
    TO_EVENTS_BIND,
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_POSITION_EVENT,
    encode_message,
)
from orchestrator.discrete_event_dispatcher import ActiveLinkInfo, _group_by_timestamp, _load_timeline
from orchestrator.latency_model import PositionTable

log = logging.getLogger(__name__)


class RealtimeDispatcher:
    """Process a pre-computed timeline paced at wall-clock speed."""

    def __init__(
        self,
        interface_map: dict[tuple[str, str], tuple[str, str]],
        bandwidth_map: dict[tuple[str, str], float],
        override_set: set[tuple[str, str]],
        override_lock: Any,
        pid_map: dict[str, int] | None = None,
        latency_update_interval_s: int = 10,
        compression_factor: int = 1,
        timeline_path: Path | None = None,
    ) -> None:
        self._timeline_path = timeline_path
        self._interface_map = interface_map
        self._bandwidth_map = bandwidth_map
        self._override_set = override_set
        self._override_lock = override_lock
        self._pid_map = pid_map or {}
        self._latency_update_interval_s = latency_update_interval_s
        self._compression_factor = max(1, compression_factor)

        self._position_table = PositionTable()
        self._active_links: dict[tuple[str, str], ActiveLinkInfo] = {}
        self._last_latencies: dict[tuple[str, str], float] = {}
        self._steps_since_latency_update = 0

    def run(self) -> None:
        """Load timeline and dispatch batches paced at wall-clock speed."""
        if self._timeline_path is None:
            raise ValueError("timeline_path is required for run()")
        events = _load_timeline(self._timeline_path)
        batches = _group_by_timestamp(events)
        log.info(
            f"RT dispatcher loaded: {len(events)} events in {len(batches)} batches, "
            f"compression={self._compression_factor}x"
        )

        ctx = zmq.Context()

        # Publish TO events (link up/down/latency)
        pub_sock = ctx.socket(zmq.PUB)
        pub_sock.bind(TO_EVENTS_BIND)

        # Publish position events on OME port (VS-API subscribes here)
        ome_pub_sock = ctx.socket(zmq.PUB)
        ome_pub_sock.bind(OME_EVENTS_BIND)

        # Allow subscribers time to connect (ZMQ slow joiner)
        time.sleep(0.5)

        try:
            orbit = 0
            while True:
                orbit += 1
                wall_start = time.monotonic()
                sim_start_s = batches[0][0]["timestamp_s"] if batches else 0.0

                for batch_idx, batch in enumerate(batches):
                    # Pace: compute wall-clock time this batch should fire
                    batch_sim_s = batch[0]["timestamp_s"]
                    sim_elapsed_s = batch_sim_s - sim_start_s
                    wall_target = wall_start + (sim_elapsed_s / self._compression_factor)
                    wall_now = time.monotonic()
                    if wall_target > wall_now:
                        time.sleep(wall_target - wall_now)

                    self._process_batch(batch, pub_sock, ome_pub_sock)
                    self._steps_since_latency_update += 1

                log.info(
                    f"RT timeline orbit {orbit} complete: "
                    f"{len(self._active_links)} active links, looping"
                )
        except KeyboardInterrupt:
            log.info("RT dispatcher shutting down")
        finally:
            pub_sock.close()
            ome_pub_sock.close()
            ctx.term()

    def _process_batch(
        self,
        batch: list[dict[str, Any]],
        pub_sock: zmq.Socket,
        ome_pub_sock: zmq.Socket,
    ) -> None:
        """Process a batch of events at the same timestamp."""
        # Phase 1: Snapshots (position updates)
        for record in batch:
            if record["event_type"] == "Snapshot":
                snap = TimelinePositionSnapshot.model_validate(record["data"])
                self._position_table.update_from_snapshot(snap)
                # Publish position event for VS-API
                positions_list = []
                for node_id, pos in snap.positions.items():
                    node_type = "ground_station" if node_id.startswith("gs-") else "satellite"
                    plane = None
                    slot = None
                    if node_type == "satellite":
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

        # Phase 2: Visibility events
        for record in batch:
            if record["event_type"] != "VisibilityEvent":
                continue
            vis = VisibilityEvent.model_validate(record["data"])
            pair = (vis.node_a, vis.node_b)

            with self._override_lock:
                if pair in self._override_set:
                    continue

            if vis.visible and vis.scheduled:
                self._link_up(vis, pub_sock)
            elif not vis.visible:
                self._link_down(vis, pub_sock)

        # Phase 3: Latency updates
        if self._steps_since_latency_update >= self._latency_update_interval_s:
            self._update_latencies(pub_sock)
            self._steps_since_latency_update = 0

    def _handle_visibility(self, vis: VisibilityEvent, pub_sock: zmq.Socket) -> None:
        """Process a single visibility event."""
        pair = (vis.node_a, vis.node_b)
        with self._override_lock:
            if pair in self._override_set:
                return
        if vis.visible and vis.scheduled:
            self._link_up(vis, pub_sock)
        elif not vis.visible:
            self._link_down(vis, pub_sock)

    def _link_up(self, vis: VisibilityEvent, pub_sock: zmq.Socket) -> None:
        pair = (vis.node_a, vis.node_b)
        if pair in self._active_links:
            return

        ifaces = self._interface_map.get(pair)
        if not ifaces:
            return

        bandwidth = self._bandwidth_map.get(pair, 1000.0)
        latency = self._position_table.compute_link_latency(vis.node_a, vis.node_b)
        if latency is None:
            latency = 3.0

        self._active_links[pair] = ActiveLinkInfo(
            interface_a=ifaces[0], interface_b=ifaces[1],
            latency_ms=latency, bandwidth_mbps=bandwidth,
            pid_a=self._pid_map.get(vis.node_a, 0),
            pid_b=self._pid_map.get(vis.node_b, 0),
        )
        self._last_latencies[pair] = latency

        info = self._active_links[pair]
        if info.pid_a and info.pid_b:
            from orchestrator import link_manager
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
            sim_time=vis.sim_time, wall_time=now,
            node_a=vis.node_a, node_b=vis.node_b,
            interface_a=ifaces[0], interface_b=ifaces[1],
            latency_ms=latency, bandwidth_mbps=bandwidth,
            reason="vis_gained",
        )
        pub_sock.send(encode_message(TOPIC_LINK_UP, event.model_dump_json().encode()))

    def _link_down(self, vis: VisibilityEvent, pub_sock: zmq.Socket) -> None:
        pair = (vis.node_a, vis.node_b)
        info = self._active_links.pop(pair, None)
        if info is None:
            return

        if info.pid_a and info.pid_b:
            from orchestrator import link_manager
            is_gs_link = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
            if is_gs_link:
                link_manager.destroy_veth_pair(info.pid_a, info.interface_a)
            else:
                link_manager.set_interface_down(info.pid_a, info.interface_a)
                link_manager.set_interface_down(info.pid_b, info.interface_b)

        self._last_latencies.pop(pair, None)
        now = datetime.now(timezone.utc)
        event = LinkDown(
            sim_time=vis.sim_time, wall_time=now,
            node_a=vis.node_a, node_b=vis.node_b,
            interface_a=info.interface_a, interface_b=info.interface_b,
            reason="vis_lost",
        )
        pub_sock.send(encode_message(TOPIC_LINK_DOWN, event.model_dump_json().encode()))

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

            if info.pid_a and info.pid_b:
                from orchestrator import link_manager
                link_manager.update_delay(info.pid_a, info.interface_a, new_lat)
                link_manager.update_delay(info.pid_b, info.interface_b, new_lat)

            info.latency_ms = new_lat
            self._last_latencies[pair] = new_lat

            event = LatencyUpdate(
                sim_time=now, wall_time=now,
                node_a=node_a, node_b=node_b,
                latency_ms=new_lat, range_km=range_km,
            )
            pub_sock.send(encode_message(
                TOPIC_LATENCY_UPDATE, event.model_dump_json().encode(),
            ))
