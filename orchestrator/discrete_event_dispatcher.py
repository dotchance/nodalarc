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
    mi_convergence_gate_connect,
    playback_control_bind,
    to_events_bind,
    encode_message,
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_POSITION_EVENT,
    TOPIC_VISIBILITY_EVENT,
)
from orchestrator.latency_model import PositionTable
from orchestrator.timeline_reader import TimelineReader

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
        timeline_path: Path | None = None,
        ome_zmq_endpoint: str | None = None,
        interface_map: dict[tuple[str, str], tuple[str, str]] | None = None,
        bandwidth_map: dict[tuple[str, str], float] | None = None,
        override_set: set[tuple[str, str]] | None = None,
        override_lock: Any = None,
        pid_map: dict[str, int] | None = None,
        routing_protocol: str = "isis",
        db_conn: Any = None,
        dwell_s: float = 1.0,
        latency_update_interval_s: int = 10,
        use_convergence_gate: bool = True,
        max_orbits: int | None = None,
        max_idle_timeouts: int | None = None,
        area_map: dict[str, str] | None = None,
    ) -> None:
        self._timeline_path = timeline_path
        self._ome_zmq_endpoint = ome_zmq_endpoint
        self._interface_map = interface_map or {}
        self._bandwidth_map = bandwidth_map or {}
        self._override_set = override_set or set()
        self._override_lock = override_lock
        self._pid_map = pid_map or {}
        self._routing_protocol = routing_protocol
        self._db_conn = db_conn
        self._dwell_s = dwell_s
        self._latency_update_interval_s = latency_update_interval_s
        self._use_convergence_gate = use_convergence_gate
        self._max_orbits = max_orbits
        # max_idle_timeouts: exit after N consecutive reader timeouts (None=wait forever)
        # Use max_idle_timeouts=1 in tests to process a finite file then exit.
        self._max_idle_timeouts = max_idle_timeouts
        self._area_map = area_map or {}

        self._position_table = PositionTable()
        self._active_links: dict[tuple[str, str], ActiveLinkInfo] = {}
        self._last_latencies: dict[tuple[str, str], float] = {}
        self._steps_since_latency_update = 0
        self._paused: bool = False
        self._speed_factor: float = 1.0

    def run(self) -> None:
        """Run the dispatcher — ZMQ streaming or file-based depending on config."""
        if self._ome_zmq_endpoint:
            self._run_zmq()
        else:
            self._run_file()

    def _setup_zmq_output(self, ctx: zmq.Context) -> tuple:
        """Set up output ZMQ sockets (PUB, convergence, playback). Shared by both modes."""
        pub_sock = ctx.socket(zmq.PUB)
        pub_sock.setsockopt(zmq.LINGER, 0)
        pub_sock.bind(to_events_bind())
        ome_pub_sock = pub_sock  # Reuse TO PUB for position events

        conv_sock = None
        if self._use_convergence_gate:
            conv_sock = ctx.socket(zmq.REQ)
            conv_sock.setsockopt(zmq.LINGER, 0)
            conv_sock.connect(mi_convergence_gate_connect())

        playback_sock = ctx.socket(zmq.REP)
        playback_sock.setsockopt(zmq.LINGER, 0)
        playback_sock.bind(playback_control_bind())

        poller = zmq.Poller()
        poller.register(playback_sock, zmq.POLLIN)

        log.info(
            f"ZMQ PUB sockets bound: TO={to_events_bind()} "
            f"Playback={playback_control_bind()}"
        )
        return pub_sock, ome_pub_sock, conv_sock, playback_sock, poller

    def _run_zmq(self) -> None:
        """Stream events from OME ZMQ PUB socket."""
        from nodalarc.zmq_channels import decode_message

        ctx = zmq.Context()
        batch_count = 0

        try:
            pub_sock, ome_pub_sock, conv_sock, playback_sock, poller = self._setup_zmq_output(ctx)

            # Subscribe to OME events
            ome_sub = ctx.socket(zmq.SUB)
            ome_sub.connect(self._ome_zmq_endpoint)
            ome_sub.setsockopt(zmq.SUBSCRIBE, b"ClockTick")
            ome_sub.setsockopt(zmq.SUBSCRIBE, b"VisibilityEvent")
            ome_sub.setsockopt(zmq.SUBSCRIBE, b"Snapshot")
            ome_sub.setsockopt(zmq.SUBSCRIBE, b"WindowReady")
            ome_sub.setsockopt(zmq.SUBSCRIBE, b"FullStateSnapshot")

            ome_poller = zmq.Poller()
            ome_poller.register(ome_sub, zmq.POLLIN)

            log.info(f"Subscribing to OME ZMQ: {self._ome_zmq_endpoint}")
            time.sleep(3.0)  # Allow output subscribers to connect
            log.info("Dispatcher ready, waiting for OME events")

            buffer: list[dict] = []
            initialized = False

            while True:
                # Check for playback commands
                self._handle_playback_commands(poller, playback_sock)
                if self._paused:
                    time.sleep(0.1)
                    continue

                # Poll OME sub with 1s timeout
                socks = dict(ome_poller.poll(timeout=1000))
                if ome_sub not in socks:
                    # No OME data — replay trajectory at real-time pace.
                    # The trajectory is downsampled (every Nth snapshot), so
                    # each entry covers N sim-seconds. Advance one entry per
                    # N wall-seconds to match real-time orbital speed.
                    if hasattr(self, "_traj_groups") and self._traj_groups and ome_pub_sock:
                        now = time.monotonic()
                        elapsed = now - self._traj_last_wall
                        if elapsed >= self._traj_step_s:
                            # Process groups until we hit a Snapshot group (position update).
                            # VisibilityEvent-only groups are consumed instantly so link
                            # state catches up without delaying position animation.
                            while self._traj_group_idx < len(self._traj_groups):
                                gidx = self._traj_group_idx
                                group = self._traj_groups[gidx]
                                self._process_batch(group, pub_sock, conv_sock, ome_pub_sock)
                                self._traj_group_idx += 1
                                has_snapshot = any(e.get("event_type") == "Snapshot" for e in group)
                                if has_snapshot:
                                    break  # Pause until next step interval
                            # Hold at last position when trajectory exhausted
                            # (don't wrap — wait for window 2 with fresh data)
                            if self._traj_group_idx >= len(self._traj_groups):
                                self._traj_group_idx = len(self._traj_groups) - 1
                            self._traj_last_wall = now
                    continue

                raw = ome_sub.recv(zmq.NOBLOCK)
                topic, payload = decode_message(raw)
                data = json.loads(payload)

                if topic == b"FullStateSnapshot":
                    if not initialized:
                        # Cold start: bring up all visible links in a background thread
                        # so the poll loop continues receiving Snapshot events (positions).
                        import threading
                        cold_start_data = dict(data)
                        def _cold_start_bg():
                            self._cold_start_from_snapshot(
                                cold_start_data, pub_sock, conv_sock, ome_pub_sock,
                            )
                        threading.Thread(target=_cold_start_bg, daemon=True).start()
                        initialized = True
                    else:
                        self._update_state_from_snapshot(data)

                    # Pre-group trajectory by timestamp for real-time replay.
                    trajectory = data.get("position_trajectory", [])
                    if trajectory and not hasattr(self, "_traj_groups"):
                        # Group events by timestamp_s
                        from itertools import groupby
                        groups = []
                        for _, g in groupby(trajectory, key=lambda e: e.get("timestamp_s", 0)):
                            groups.append(list(g))
                        self._traj_groups = groups
                        self._traj_group_idx = 0
                        self._traj_last_wall = time.monotonic()
                        # Step interval from first two Snapshot-containing groups
                        snap_ts = [g[0].get("timestamp_s", 0) for g in groups
                                   if any(e.get("event_type") == "Snapshot" for e in g)]
                        self._traj_step_s = max(1.0, snap_ts[1] - snap_ts[0]) if len(snap_ts) >= 2 else 10.0
                        log.info(f"Trajectory loaded: {len(groups)} groups, step={self._traj_step_s:.0f}s")
                    continue

                if topic == b"WindowReady":
                    # Group buffered events by timestamp and process each group
                    # as a separate batch with dwell between them — same pacing
                    # as the file-based TimelineReader.
                    if buffer:
                        batches = _group_by_timestamp(buffer)
                        for batch in batches:
                            # Check playback between batches
                            self._handle_playback_commands(poller, playback_sock)
                            while self._paused:
                                time.sleep(0.1)
                                self._handle_playback_commands(poller, playback_sock)

                            batch_start = time.monotonic()
                            self._process_batch(batch, pub_sock, conv_sock, ome_pub_sock)
                            self._steps_since_latency_update += 1
                            batch_count += 1

                            if self._dwell_s > 0:
                                effective_dwell = self._dwell_s / self._speed_factor
                                elapsed = time.monotonic() - batch_start
                                remaining = effective_dwell - elapsed
                                if remaining > 0:
                                    time.sleep(remaining)
                    buffer = []
                    continue

                # Snapshot events arriving outside a window (periodic republish
                # from OME during inter-window sleep) should be processed immediately
                # for position updates — don't buffer them.
                if topic == b"Snapshot" and initialized:
                    self._process_batch([data], pub_sock, conv_sock, ome_pub_sock)
                    continue

                # Buffer event for current batch
                buffer.append(data)

        except KeyboardInterrupt:
            log.info("Dispatcher interrupted")
        except Exception as exc:
            log.error(f"Dispatcher crashed: {exc}", exc_info=True)
        finally:
            log.info(
                f"ZMQ dispatcher exiting: {batch_count} batches, "
                f"{len(self._active_links)} active links"
            )
            self._teardown_remaining_links(pub_sock)
            ctx.term()

    def _cold_start_from_snapshot(
        self, data: dict, pub_sock, conv_sock, ome_pub_sock,
    ) -> None:
        """Initialize link state from FullStateSnapshot — cold start only.

        FRR pods are fresh with no routing state. Treat the snapshot as
        authoritative and issue link-up for all visible/scheduled links.
        """
        sim_time = data.get("sim_time", "")
        isl_state = data.get("isl_state", {})
        gs_state_data = data.get("gs_state", {})
        log.info(f"FullStateSnapshot: {len(isl_state)} ISL pairs, {len(gs_state_data)} GS pairs, "
                 f"interface_map has {len(self._interface_map)} entries")
        # Debug: show first few ISL pairs to verify format
        for k, v in list(isl_state.items())[:3]:
            pair = tuple(k.split(":"))
            in_map = pair in self._interface_map or (min(pair), max(pair)) in self._interface_map
            log.info(f"  ISL sample: {k} -> {v}, in interface_map: {in_map}")

        link_count = 0
        # Initialize ISL links from cold start
        for pair_key, state in isl_state.items():
            if state.get("visible") and state.get("scheduled"):
                parts = pair_key.split(":")
                if len(parts) == 2:
                    node_a, node_b = parts[0], parts[1]
                    pair = (min(node_a, node_b), max(node_a, node_b))
                    if pair not in self._active_links and pair in self._interface_map:
                            # Synthesize a VisibilityEvent for _handle_link_up
                            vis = VisibilityEvent(
                                sim_time=datetime.fromisoformat(sim_time) if sim_time else datetime.now(timezone.utc),
                                node_a=pair[0], node_b=pair[1],
                                visible=True, scheduled=True,
                                range_km=0.0, elevation_deg=None,
                                terminal_type="optical",
                            )
                            self._handle_link_up(vis, pub_sock)
                            link_count += 1

        # Initialize GS links — only one satellite per GS (the scheduled one).
        # Multiple satellites may be visible to a GS but only one is scheduled
        # at a time. Processing all of them would create conflicting bridge redirects.
        gs_scheduled: dict[str, tuple[str, str]] = {}  # gs_id -> (pair_key, sat_id)
        for pair_key, state in data.get("gs_state", {}).items():
            if state.get("visible") and state.get("scheduled"):
                parts = pair_key.split(":")
                if len(parts) == 2:
                    node_a, node_b = parts[0], parts[1]
                    gs_id = node_a if node_a.startswith("gs-") else node_b
                    # Keep only the last scheduled satellite per GS (most recent)
                    gs_scheduled[gs_id] = (pair_key, node_a if not node_a.startswith("gs-") else node_b)

        for gs_id, (pair_key, sat_id) in gs_scheduled.items():
            parts = pair_key.split(":")
            node_a, node_b = min(parts[0], parts[1]), max(parts[0], parts[1])
            pair = (node_a, node_b)
            if pair not in self._active_links and pair in self._interface_map:
                vis = VisibilityEvent(
                    sim_time=datetime.fromisoformat(sim_time) if sim_time else datetime.now(timezone.utc),
                    node_a=pair[0], node_b=pair[1],
                    visible=True, scheduled=True,
                    range_km=0.0, elevation_deg=25.0,  # Default elevation for initial attach
                    terminal_type="optical",
                )
                self._handle_link_up(vis, pub_sock)
                link_count += 1
                log.info(f"Cold start GS: {gs_id} -> {sat_id}")

        log.info(f"Cold start from FullStateSnapshot: {link_count} links initialized")

    def _update_state_from_snapshot(self, data: dict) -> None:
        """Update internal state from FullStateSnapshot — no link commands.

        M3 limitation: if this snapshot differs from FRR state (e.g. after
        OME restart), the session is broken. Recovery = full session switch.
        """
        new_active: set[tuple[str, str]] = set()
        for state_dict in (data.get("isl_state", {}), data.get("gs_state", {})):
            for pair_key, state in state_dict.items():
                if state.get("visible") and state.get("scheduled"):
                    parts = pair_key.split(":")
                    if len(parts) == 2:
                        new_active.add((min(parts[0], parts[1]), max(parts[0], parts[1])))
        current = set(self._active_links.keys())
        if new_active != current:
            log.warning(
                f"FullStateSnapshot state differs from current: "
                f"{len(new_active)} active vs {len(current)} tracked. "
                f"FRR state may be inconsistent."
            )

    def _run_file(self) -> None:
        """Stream events from growing timeline file (legacy mode)."""
        ctx = zmq.Context()
        batch_count = 0

        try:
            pub_sock, ome_pub_sock, conv_sock, playback_sock, poller = self._setup_zmq_output(ctx)

            time.sleep(3.0)  # Allow subscribers to connect
            log.info("Slow joiner delay complete, starting event processing")

            reader = TimelineReader(self._timeline_path)
            idle_timeouts = 0
            while True:
                self._handle_playback_commands(poller, playback_sock)
                if self._paused:
                    time.sleep(0.1)
                    continue

                batch = reader.next_batch(timeout_s=10.0)
                if batch is None:
                    idle_timeouts += 1
                    if self._max_idle_timeouts is not None and idle_timeouts >= self._max_idle_timeouts:
                        log.info("Max idle timeouts reached, exiting")
                        break
                    log.debug("No new events, waiting for OME...")
                    continue
                idle_timeouts = 0

                batch_start = time.monotonic()
                self._process_batch(batch, pub_sock, conv_sock, ome_pub_sock)
                self._steps_since_latency_update += 1
                batch_count += 1

                if self._dwell_s > 0:
                    effective_dwell = self._dwell_s / self._speed_factor
                    elapsed = time.monotonic() - batch_start
                    remaining = effective_dwell - elapsed
                    if remaining > 0:
                        time.sleep(remaining)
        except KeyboardInterrupt:
            log.info("Dispatcher interrupted")
        except Exception as exc:
            log.error(f"Dispatcher crashed: {exc}", exc_info=True)
        finally:
            try:
                pos_count = getattr(self, '_pos_pub_count', 0)
                log.info(
                    f"Dispatcher exiting: {batch_count} batches, "
                    f"{pos_count} position events published, "
                    f"{len(self._active_links)} active links"
                )
                reader.close()
                self._teardown_remaining_links(pub_sock)
            except NameError:
                pass  # Socket setup failed before these were defined
            ctx.destroy(linger=0)

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

    def _teardown_remaining_links(self, pub_sock: zmq.Socket) -> None:
        """Detach active GS links when the dispatcher exits.

        GS links use permanent veths via bridges; detach clears tc shaping
        and removes satellite from bridge. ISL links are left as-is.
        """
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
            self._handle_link_down(fake_vis, pub_sock)

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
                # Re-publish raw Snapshot on TO port for NodalPath
                pub_sock.send(encode_message(
                    b"Snapshot", json.dumps(record["data"]).encode(),
                ))
                # Publish position event for VS-API
                if ome_pub_sock is not None:
                    if not hasattr(self, '_pos_pub_count'):
                        self._pos_pub_count = 0
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
                            "routing_area": self._area_map.get(node_id),
                            "neighbor_count": 0,
                            "isl_count": 0,
                            "gnd_count": 0,
                        })
                    position_data = {
                        "sim_time": snap.sim_time.isoformat(),
                        "positions": positions_list,
                    }
                    encoded = encode_message(
                        TOPIC_POSITION_EVENT,
                        json.dumps(position_data).encode(),
                    )
                    ome_pub_sock.send(encoded)
                    self._pos_pub_count += 1
                    if self._pos_pub_count <= 5 or self._pos_pub_count % 50 == 0:
                        log.info(
                            f"Published PositionEvent #{self._pos_pub_count} "
                            f"sim_time={position_data['sim_time']} "
                            f"nodes={len(positions_list)} bytes={len(encoded)}"
                        )
            elif record["event_type"] == "ClockTick":
                pass  # Clock ticks are informational

        # Phase 2: Process visibility events — link_downs first, then link_ups.
        # Processing downs before ups prevents transient states where a ground
        # station appears connected to multiple satellites simultaneously during
        # a terminal handoff (both events land in the same batch).
        vis_events: list[VisibilityEvent] = []
        for record in batch:
            if record["event_type"] != "VisibilityEvent":
                continue
            vis = VisibilityEvent.model_validate(record["data"])
            pair = (vis.node_a, vis.node_b)
            with self._override_lock:
                if pair in self._override_set:
                    continue
            vis_events.append(vis)

        # Sort: link_downs (not visible, or visible+unscheduled GS) before link_ups
        def _is_link_up(v: VisibilityEvent) -> bool:
            return v.visible and v.scheduled
        vis_events.sort(key=_is_link_up)

        for vis in vis_events:
            # Re-publish VisibilityEvent on TO port so NodalPath can track topology
            pub_sock.send(encode_message(
                TOPIC_VISIBILITY_EVENT, vis.model_dump_json().encode(),
            ))

            if vis.visible and vis.scheduled:
                link_event = self._handle_link_up(vis, pub_sock)
                if link_event:
                    link_events.append(link_event)
            elif not vis.visible:
                link_event = self._handle_link_down(vis, pub_sock)
                if link_event:
                    link_events.append(link_event)
            elif vis.visible and not vis.scheduled:
                # Terminal deallocated (GS handoff) — tear down the link
                # Only applies to GS links where scheduling determines connectivity.
                is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                if is_gs:
                    link_event = self._handle_link_down(vis, pub_sock)
                    if link_event:
                        link_events.append(link_event)

        # Phase 3: Latency updates on active links
        if self._steps_since_latency_update >= self._latency_update_interval_s:
            self._update_latencies(pub_sock)
            self._steps_since_latency_update = 0

        # Phase 4: Convergence gate once per batch (all link events at the same
        # sim_time, so a single convergence check after all changes suffices).
        if conv_sock and link_events:
            self._call_convergence_gate(conv_sock, link_events[-1])

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
            import time as _time
            # Retry up to 3 times — interfaces may not be visible in the
            # namespace immediately after creation (netlink propagation)
            for attempt in range(3):
                try:
                    is_gs_link = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                    if is_gs_link:
                        # Bridge attach/detach — GS gnd0 is always UP
                        gs_id = vis.node_a if vis.node_a.startswith("gs-") else vis.node_b
                        sat_id = vis.node_b if vis.node_a.startswith("gs-") else vis.node_a
                        gs_pid = self._pid_map.get(gs_id, 0)
                        sat_pid = self._pid_map.get(sat_id, 0)
                        if sat_pid:
                            link_manager.attach_to_ground_bridge(gs_id, sat_id, sat_pid)
                        # tc shaping on BOTH GS gnd0 and satellite gnd0
                        if gs_pid:
                            link_manager.apply_link_shaping(gs_pid, "gnd0", latency, bandwidth)
                        if sat_pid:
                            link_manager.apply_link_shaping(sat_pid, "gnd0", latency, bandwidth)
                        if vis.elevation_deg is not None:
                            metric = max(10, int(100 * (1 - vis.elevation_deg / 90)))
                            link_manager.set_link_metric(gs_id, "gnd0", metric, self._routing_protocol)
                            link_manager.set_link_metric(sat_id, "gnd0", metric, self._routing_protocol)
                            log.info(f"GS link {pair} elevation={vis.elevation_deg:.1f}° → metric {metric}")
                        # NodalPath: trigger NDP on both sides of the ground link
                        if self._routing_protocol == "nodalpath" and gs_pid and sat_pid:
                            gs_mac = link_manager.deterministic_mac(gs_id, "gnd0")
                            sat_mac = link_manager.deterministic_mac(sat_id, "gnd0")
                            gs_ll = link_manager.mac_to_link_local(gs_mac)
                            sat_ll = link_manager.mac_to_link_local(sat_mac)
                            link_manager.trigger_ndp_and_wait(sat_pid, "gnd0", gs_ll)
                            link_manager.trigger_ndp_and_wait(gs_pid, "gnd0", sat_ll)
                    else:
                        link_manager.set_interface_up(info.pid_a, ifaces[0])
                        link_manager.set_interface_up(info.pid_b, ifaces[1])
                        link_manager.apply_link_shaping(info.pid_a, ifaces[0], latency, bandwidth)
                        link_manager.apply_link_shaping(info.pid_b, ifaces[1], latency, bandwidth)
                        # NodalPath: trigger NDP so MPLS routes can use via inet6
                        if self._routing_protocol == "nodalpath":
                            peer_mac_b = link_manager.deterministic_mac(vis.node_b, ifaces[1])
                            peer_mac_a = link_manager.deterministic_mac(vis.node_a, ifaces[0])
                            peer_ll_a = link_manager.mac_to_link_local(peer_mac_b)
                            peer_ll_b = link_manager.mac_to_link_local(peer_mac_a)
                            link_manager.trigger_ndp_and_wait(info.pid_a, ifaces[0], peer_ll_a)
                            link_manager.trigger_ndp_and_wait(info.pid_b, ifaces[1], peer_ll_b)
                    break  # Success
                except FileNotFoundError as exc:
                    if attempt < 2:
                        log.debug(f"Retry {attempt+1}/3 for {pair}: {exc}")
                        _time.sleep(0.5)
                        continue
                    log.warning(
                        f"Link kernel setup failed for {pair} after 3 attempts: {exc} "
                        f"(pid_a={info.pid_a} iface_a={ifaces[0]} pid_b={info.pid_b} iface_b={ifaces[1]})"
                    )
                except Exception as exc:
                    log.warning(f"Link kernel setup failed for {pair}: {exc}")
                    break

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
            try:
                from orchestrator import link_manager
                is_gs_link = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                if is_gs_link:
                    gs_id = vis.node_a if vis.node_a.startswith("gs-") else vis.node_b
                    sat_id = vis.node_b if vis.node_a.startswith("gs-") else vis.node_a
                    gs_pid = self._pid_map.get(gs_id, 0)
                    sat_pid = self._pid_map.get(sat_id, 0)
                    # Remove tc shaping from BOTH sides before detach
                    if sat_pid:
                        link_manager.remove_link_shaping(sat_pid, "gnd0")
                    if gs_pid:
                        link_manager.remove_link_shaping(gs_pid, "gnd0")
                    # Detach satellite from bridge
                    if sat_pid:
                        link_manager.detach_from_ground_bridge(gs_id, sat_id, sat_pid)
                else:
                    link_manager.set_interface_down(info.pid_a, info.interface_a)
                    link_manager.set_interface_down(info.pid_b, info.interface_b)
            except Exception as exc:
                log.warning(f"Link kernel teardown failed for {pair}: {exc}")

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

            # Apply kernel change — for GS links use individual PIDs since
            # both interfaces are "gnd0" in different namespaces
            if info.pid_a and info.pid_b:
                try:
                    from orchestrator import link_manager
                    is_gs = node_a.startswith("gs-") or node_b.startswith("gs-")
                    if is_gs:
                        gs_id = node_a if node_a.startswith("gs-") else node_b
                        sat_id = node_b if node_a.startswith("gs-") else node_a
                        gs_pid = self._pid_map.get(gs_id, 0)
                        sat_pid = self._pid_map.get(sat_id, 0)
                        if gs_pid:
                            link_manager.update_delay(gs_pid, "gnd0", new_lat)
                        if sat_pid:
                            link_manager.update_delay(sat_pid, "gnd0", new_lat)
                    else:
                        link_manager.update_delay(info.pid_a, info.interface_a, new_lat)
                        link_manager.update_delay(info.pid_b, info.interface_b, new_lat)
                except Exception as exc:
                    log.warning(f"Latency update failed for {pair}: {exc}")

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
