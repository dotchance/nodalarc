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
    PLAYBACK_CONTROL_BIND,
    TO_EVENTS_BIND,
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_POSITION_EVENT,
    encode_message,
)
from orchestrator.discrete_event_dispatcher import ActiveLinkInfo
from orchestrator.latency_model import PositionTable
from orchestrator.timeline_reader import TimelineReader

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
        self._paused: bool = False
        self._speed_factor: float = 1.0

    def run(self) -> None:
        """Stream events paced at wall-clock x compression from growing timeline."""
        if self._timeline_path is None:
            raise ValueError("timeline_path is required for run()")

        log.info(f"RT dispatcher starting, compression={self._compression_factor}x")

        ctx = zmq.Context()

        # Publish TO events (link up/down/latency)
        pub_sock = ctx.socket(zmq.PUB)
        pub_sock.bind(TO_EVENTS_BIND)

        # Publish position events on OME port (VS-API subscribes here)
        ome_pub_sock = ctx.socket(zmq.PUB)
        ome_pub_sock.bind(OME_EVENTS_BIND)

        # Playback control REP socket
        playback_sock = ctx.socket(zmq.REP)
        playback_sock.bind(PLAYBACK_CONTROL_BIND)

        poller = zmq.Poller()
        poller.register(playback_sock, zmq.POLLIN)

        # Allow subscribers time to connect (ZMQ slow joiner)
        time.sleep(0.5)

        reader = TimelineReader(self._timeline_path)
        session_start_wall = time.monotonic()

        try:
            while True:
                # Poll for playback commands (non-blocking)
                self._handle_playback_commands(poller, playback_sock)

                if self._paused:
                    time.sleep(0.1)
                    continue

                batch = reader.next_batch(timeout_s=10.0)
                if batch is None:
                    log.debug("Waiting for OME window...")
                    continue

                # Pace: wall_target relative to session start
                batch_ts = batch[0]["timestamp_s"]
                effective_compression = self._compression_factor * self._speed_factor
                wall_target = session_start_wall + (batch_ts / effective_compression)
                wall_now = time.monotonic()
                if wall_target > wall_now:
                    time.sleep(wall_target - wall_now)

                self._process_batch(batch, pub_sock, ome_pub_sock)
                self._steps_since_latency_update += 1
        except KeyboardInterrupt:
            log.info("RT dispatcher shutting down")
        finally:
            reader.close()
            self._teardown_remaining_links(pub_sock)
            pub_sock.close()
            ome_pub_sock.close()
            playback_sock.close()
            ctx.term()

    def _handle_playback_commands(
        self, poller: zmq.Poller, playback_sock: zmq.Socket,
    ) -> None:
        """Poll for and handle playback control commands."""
        socks = dict(poller.poll(timeout=0))
        if playback_sock not in socks:
            return
        raw = playback_sock.recv()
        try:
            cmd = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            playback_sock.send(json.dumps({"error": "invalid json"}).encode())
            return

        action = cmd.get("action", "")
        if action == "pause":
            self._paused = True
            playback_sock.send(json.dumps({"status": "ok", "paused": True}).encode())
        elif action == "resume":
            self._paused = False
            playback_sock.send(json.dumps({"status": "ok", "paused": False}).encode())
        elif action == "set_speed":
            factor = max(0.1, min(100.0, float(cmd.get("factor", 1.0))))
            self._speed_factor = factor
            playback_sock.send(json.dumps({"status": "ok", "speed": factor}).encode())
        elif action == "get_status":
            playback_sock.send(json.dumps({
                "paused": self._paused, "speed": self._speed_factor,
            }).encode())
        else:
            playback_sock.send(json.dumps({"error": "unknown action"}).encode())

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
            elif vis.visible and not vis.scheduled:
                # Terminal deallocated (GS handoff) — tear down GS links
                is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                if is_gs:
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
            try:
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
            except Exception as exc:
                log.warning(f"Link kernel setup failed for {pair}: {exc}")

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
            try:
                from orchestrator import link_manager
                is_gs_link = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                if is_gs_link:
                    link_manager.destroy_veth_pair(info.pid_a, info.interface_a)
                else:
                    link_manager.set_interface_down(info.pid_a, info.interface_a)
                    link_manager.set_interface_down(info.pid_b, info.interface_b)
            except Exception as exc:
                log.warning(f"Link kernel teardown failed for {pair}: {exc}")

        self._last_latencies.pop(pair, None)
        now = datetime.now(timezone.utc)
        event = LinkDown(
            sim_time=vis.sim_time, wall_time=now,
            node_a=vis.node_a, node_b=vis.node_b,
            interface_a=info.interface_a, interface_b=info.interface_b,
            reason="vis_lost",
        )
        pub_sock.send(encode_message(TOPIC_LINK_DOWN, event.model_dump_json().encode()))

    def _teardown_remaining_links(self, pub_sock: zmq.Socket) -> None:
        """Tear down active GS links when the dispatcher exits."""
        gs_pairs = [
            pair for pair in self._active_links
            if pair[0].startswith("gs-") or pair[1].startswith("gs-")
        ]
        if not gs_pairs:
            return
        log.info(f"Tearing down {len(gs_pairs)} remaining GS links")
        for pair in gs_pairs:
            fake_vis = VisibilityEvent(
                sim_time=datetime.now(timezone.utc),
                node_a=pair[0],
                node_b=pair[1],
                visible=False,
                scheduled=False,
                range_km=0.0,
                elevation_deg=None,
                terminal_type="optical",
            )
            self._link_down(fake_vis, pub_sock)

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
                try:
                    from orchestrator import link_manager
                    link_manager.update_delay(info.pid_a, info.interface_a, new_lat)
                    link_manager.update_delay(info.pid_b, info.interface_b, new_lat)
                except Exception as exc:
                    log.warning(f"Latency update failed for {pair}: {exc}")

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
