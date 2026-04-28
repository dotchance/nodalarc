# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""SessionContext — encapsulates all per-session state for the VS-API.

Each SessionContext owns:
  - Satellite/GS node positions, link state, recent events
  - Almanac state (NodalPath)
  - NATS subscriptions (scoped to session_id)
  - Continuous tracer
  - Playback state
  - Stale detection
  - SQLite DB path

The VS-API holds one _active_context at a time. On session switch,
the old context is stopped (subscriptions closed, state cleared) and
a new one is started. The shared NATS connection outlives all contexts.

This class is the building block for multi-tenant support:
  - Single-user: one _active_context, swapped on switch
  - Option 1 (sidecar): one context per VS-API pod
  - Option 2 (aggregator): dict[session_id, SessionContext]
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time as _time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import nats
import yaml
from nodalarc.models.session import SessionConfig
from nodalarc.models.vs_api import (
    AlmanacState,
    LinkState,
    NetworkHealth,
    NodeState,
    RecentEvent,
)
from nodalarc.nats_channels import (
    STREAM_LINK_EVENTS,
    STREAM_OPS_EVENTS,
    STREAM_SESSION_EVENTS,
    almanac_event_subject,
    latency_update_subject,
    link_down_subject,
    link_state_snapshot_subject,
    link_up_subject,
    ome_clock_subject,
    ops_subscribe_subject,
    playback_state_subject,
    session_ephemeris_subject,
)

log = logging.getLogger(__name__)

STALE_THRESHOLD_S: float = 15.0
CONVERGENCE_DWELL_S: float = 15.0
BULK_CHANGE_THRESHOLD: float = 0.10


class SessionContext:
    """Per-session state container with NATS subscription lifecycle.

    start(nc, mode) creates JetStream subscriptions on the shared NATS
    connection. stop() unsubscribes all and clears state. The context
    does NOT own or close the NATS connection.
    """

    def __init__(self, session_id: str, session_config_path: str) -> None:
        if not session_id:
            log.error("FATAL: SessionContext created with empty session_id")
            raise ValueError("session_id is required")
        if not session_config_path:
            log.error("FATAL: SessionContext created with empty session_config_path")
            raise ValueError("session_config_path is required")

        self.session_id = session_id
        self.session_file = session_config_path

        # Parse session config for metadata
        session_data = yaml.safe_load(Path(session_config_path).read_text())
        session = SessionConfig.model_validate(session_data)
        if session.routing.stack is not None:
            self.routing_stack: str = Path(session.routing.stack).name
        else:
            ext_str = (
                "-".join(session.routing.extensions) if session.routing.extensions else "plain"
            )
            self.routing_stack = f"{session.routing.protocol}-{ext_str}"
        if isinstance(session.constellation, dict):
            self.constellation_name: str = session.constellation.get("name", "custom")
        else:
            self.constellation_name = Path(session.constellation).stem

        # Load GS elevation map and beam falloff
        self.gs_elevation_map: dict[str, float] = self._load_gs_elevation_map(session)
        self.beam_falloff_exponent: float = self._load_beam_falloff_exponent(session)

        # DB path (set externally after context creation)
        self.db_path: str = ""

        # Session state — protected by state_lock
        self.state_lock = threading.Lock()
        self.nodes: dict[str, NodeState] = {}
        self.links: dict[str, LinkState] = {}
        self.recent_events: list[RecentEvent] = []
        self.network_health: NetworkHealth = NetworkHealth(
            status="no measurement",
            converging_since_ms=None,
            unreachable_flows=0,
            last_convergence_ms=None,
        )
        self.mi_active: bool = False
        self.sim_time: str = datetime.now(UTC).isoformat()

        # Playback state
        self.playback_paused: bool = False
        self.playback_speed: float = 1.0

        # Stale tracking
        self.last_clock_tick_wall_time: float = 0.0
        self.last_link_event_wall_time: float = 0.0
        self.session_ready_time: float = 0.0
        self.prev_snapshot_active_count: int = 0
        self.curr_snapshot_active_count: int = 0

        # Ephemeris cache
        self.cached_ephemeris: dict | None = None
        self.cached_ephemeris_obj: object | None = None

        # Almanac
        self.almanac_lock = threading.Lock()
        self.almanac: AlmanacState = AlmanacState()

        # Continuous tracer (set externally)
        self.continuous_tracer = None

        # Session-scoped OpsEvents (cleared on switch)
        self.session_ops_events: deque = deque(maxlen=500)

        # NATS subscription lifecycle
        self._subscriptions: list = []
        self._subscriber_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stopped = False

    def is_ready(self) -> bool:
        return self._ready.is_set()

    async def start(self, nc: nats.NATS, mode: Literal["switch", "recovery"]) -> None:
        """Start NATS subscriptions on the shared connection.

        mode="switch": DeliverPolicy.NEW — only messages published after
            subscription. Avoids stale retained snapshots from previous OME.
        mode="recovery": DeliverPolicy.LAST_PER_SUBJECT — recovers current
            state of an already-running simulation after VS-API restart.
        """
        if self._stopped:
            raise RuntimeError("Cannot start a stopped SessionContext")

        self._subscriber_task = asyncio.create_task(
            self._subscriber_loop(nc, mode),
            name=f"session-subscriber-{self.session_id}",
        )
        log.info(
            "SessionContext started: session_id=%s mode=%s",
            self.session_id,
            mode,
        )

    async def stop(self) -> None:
        """Stop all NATS subscriptions and clear state.

        Cleanup is in the subscriber task's finally block — cancelling
        the task triggers it. This method waits for cleanup to complete.
        """
        self._stopped = True
        if self._subscriber_task and not self._subscriber_task.done():
            self._subscriber_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._subscriber_task
        # Clear state after subscriptions are gone
        with self.state_lock:
            self.nodes.clear()
            self.links.clear()
            self.recent_events.clear()
        with self.almanac_lock:
            self.almanac = AlmanacState()
        self.continuous_tracer = None
        self.session_ops_events.clear()
        log.info(
            "SessionContext stopped: session_id=%s, %d subscriptions cleaned",
            self.session_id,
            len(self._subscriptions),
        )

    def is_stale(self) -> bool:
        if self.last_clock_tick_wall_time == 0.0 and self.last_link_event_wall_time == 0.0:
            return False
        latest = max(self.last_clock_tick_wall_time, self.last_link_event_wall_time)
        return (_time.monotonic() - latest) > STALE_THRESHOLD_S

    async def _subscriber_loop(self, nc: nats.NATS, mode: str) -> None:
        """Main NATS subscription loop. Cleanup in finally block."""
        from nats.js.api import DeliverPolicy

        js = nc.jetstream()
        sid = self.session_id

        state_policy = DeliverPolicy.NEW if mode == "switch" else DeliverPolicy.LAST_PER_SUBJECT

        try:
            # Subscribe to all session-scoped subjects
            self._subscriptions.append(
                await js.subscribe(
                    session_ephemeris_subject(sid),
                    stream=STREAM_SESSION_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=state_policy,
                    cb=self._on_session_ephemeris,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    playback_state_subject(sid),
                    stream=STREAM_SESSION_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=state_policy,
                    cb=self._on_playback_state,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    link_state_snapshot_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=state_policy,
                    cb=self._on_link_state_snapshot,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    link_up_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_link_up,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    link_down_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_link_down,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    latency_update_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_latency_update,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    ome_clock_subject(sid),
                    stream="NODALARC_OME",
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_clock_tick,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    almanac_event_subject(sid),
                    stream="NODALARC_MI",
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_almanac,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    ops_subscribe_subject(sid),
                    stream=STREAM_OPS_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_session_ops_event,
                )
            )

            log.info(
                "SessionContext subscribed: session_id=%s, %d subjects, policy=%s",
                sid,
                len(self._subscriptions),
                mode,
            )

            # Keep alive until cancelled
            while not self._stopped:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("SessionContext subscriber error: %s", exc)
            raise
        finally:
            # Single cleanup path — guarantees all subscriptions are closed
            for sub in self._subscriptions:
                try:
                    await sub.unsubscribe()
                except Exception as exc:
                    log.warning("Failed to unsubscribe: %s", exc)
            self._subscriptions.clear()
            log.info("SessionContext subscriptions cleaned: session_id=%s", sid)

    # ------------------------------------------------------------------
    # NATS message handlers
    # ------------------------------------------------------------------

    async def _on_session_ephemeris(self, msg) -> None:
        from nodalarc.models.events import SessionEphemeris

        eph = SessionEphemeris.model_validate_json(msg.data)
        self.cached_ephemeris_obj = eph
        eph_dict = json.loads(msg.data.decode())
        eph_dict["msg_type"] = "session_ephemeris"
        self.cached_ephemeris = eph_dict
        self._propagate_positions(eph)
        if not self._ready.is_set():
            log.info("SessionContext ephemeris received: session_id=%s", self.session_id)

    async def _on_playback_state(self, msg) -> None:
        data = json.loads(msg.data)
        state = data.get("state")
        if not state:
            log.error("Malformed PlaybackState — missing state: %s", data)
            raise ValueError("PlaybackState missing state")
        with self.state_lock:
            self.playback_paused = state == "paused"

    async def _on_clock_tick(self, msg) -> None:
        data = json.loads(msg.data)
        sim_time_str = data.get("sim_time")
        if not sim_time_str:
            log.error("Malformed ClockTick — missing sim_time: %s", data)
            raise ValueError("ClockTick missing sim_time")
        self._propagate_positions_from_time(sim_time_str)
        with self.state_lock:
            self.playback_speed = data.get("compression_ratio", 1.0)
            self.last_clock_tick_wall_time = _time.monotonic()

    async def _on_link_state_snapshot(self, msg) -> None:
        from nodalarc.models.link_state import AdminState, CarrierState, LinkStateSnapshot

        self.last_link_event_wall_time = _time.monotonic()
        try:
            snapshot = LinkStateSnapshot.model_validate_json(msg.data)
        except Exception as exc:
            log.error("FATAL: Failed to parse LinkStateSnapshot: %s", exc)
            raise

        with self.state_lock:
            self.links.clear()
            for link in snapshot.links:
                if link.admin == AdminState.UP and link.carrier == CarrierState.UP:
                    key = _link_key(link.node_a, link.node_b)
                    self.links[key] = LinkState(
                        node_a=link.node_a,
                        node_b=link.node_b,
                        state="active",
                        link_type=_derive_link_type(link.node_a, link.node_b, link.link_type),
                        link_reason="",
                        latency_ms=link.latency_ms,
                        bandwidth_mbps=link.bandwidth_mbps,
                        range_km=link.latency_ms * 299792.458 / 1000.0,
                        traffic_load_pct=None,
                        interface_a="",
                        interface_b="",
                    )
            self.prev_snapshot_active_count = self.curr_snapshot_active_count
            self.curr_snapshot_active_count = len(self.links)

        if not self._ready.is_set():
            self._ready.set()
            log.info(
                "SessionContext ready: session_id=%s, %d links",
                self.session_id,
                len(self.links),
            )

    async def _on_link_up(self, msg) -> None:
        self.last_link_event_wall_time = _time.monotonic()
        data = json.loads(msg.data)
        node_a = data.get("node_a")
        node_b = data.get("node_b")
        if not node_a or not node_b:
            log.error("Malformed LinkUp — missing node_a=%r or node_b=%r", node_a, node_b)
            raise ValueError(f"LinkUp missing required fields: node_a={node_a}, node_b={node_b}")
        for field in (
            "interface_a",
            "interface_b",
            "latency_ms",
            "bandwidth_mbps",
            "range_km",
            "reason",
        ):
            if field not in data:
                log.error("Malformed LinkUp — missing %s: %s", field, data)
                raise ValueError(f"LinkUp missing required field: {field}")
        key = _link_key(node_a, node_b)
        with self.state_lock:
            self.links[key] = LinkState(
                node_a=node_a,
                node_b=node_b,
                state="active",
                link_type=_derive_link_type(node_a, node_b, data.get("link_type", "isl")),
                link_reason=data["reason"],
                latency_ms=data["latency_ms"],
                bandwidth_mbps=data["bandwidth_mbps"],
                range_km=data["range_km"],
                traffic_load_pct=None,
                interface_a=data["interface_a"],
                interface_b=data["interface_b"],
            )
        self._notify_topology_change(node_a, node_b)
        self._add_recent_event(data, "link_up")

    async def _on_link_down(self, msg) -> None:
        self.last_link_event_wall_time = _time.monotonic()
        data = json.loads(msg.data)
        node_a = data.get("node_a")
        node_b = data.get("node_b")
        if not node_a or not node_b:
            log.error("Malformed LinkDown — missing node_a=%r or node_b=%r", node_a, node_b)
            raise ValueError(f"LinkDown missing required fields: node_a={node_a}, node_b={node_b}")
        key = _link_key(node_a, node_b)
        with self.state_lock:
            self.links.pop(key, None)
        self._notify_topology_change(node_a, node_b)
        self._add_recent_event(data, "link_down")

    async def _on_latency_update(self, msg) -> None:
        data = json.loads(msg.data)
        node_a = data.get("node_a")
        node_b = data.get("node_b")
        if not node_a or not node_b:
            log.error("Malformed LatencyUpdate — missing node_a=%r or node_b=%r", node_a, node_b)
            raise ValueError("LatencyUpdate missing required fields")
        latency_ms = data.get("latency_ms")
        range_km = data.get("range_km")
        if latency_ms is None or range_km is None:
            log.error("Malformed LatencyUpdate — missing latency_ms or range_km: %s", data)
            raise ValueError("LatencyUpdate missing latency_ms or range_km")
        key = _link_key(node_a, node_b)
        with self.state_lock:
            existing = self.links.get(key)
            if existing is not None:
                self.links[key] = existing.model_copy(
                    update={"latency_ms": latency_ms, "range_km": range_km}
                )

    async def _on_almanac(self, msg) -> None:
        data = json.loads(msg.data)
        event_type = data.get("event_type")
        if not event_type:
            log.error("Malformed AlmanacEvent — missing event_type: %s", data)
            raise ValueError("AlmanacEvent missing event_type")
        with self.almanac_lock:
            self.almanac = self.almanac.model_copy(update={"nodalpath_active": True})
            if event_type == "table_pushed":
                self.almanac = self.almanac.model_copy(
                    update={
                        "last_topology_state_id": data.get("topology_state_id"),
                        "last_push_sim_time": data.get("sim_time"),
                        "last_push_wall_time": data.get("wall_time"),
                        "nodes_succeeded": data.get("nodes_succeeded"),
                        "nodes_failed": data.get("nodes_failed"),
                    }
                )
            elif event_type == "deviation_detected":
                self.almanac = self.almanac.model_copy(
                    update={"deviation_count": self.almanac.deviation_count + 1}
                )
            elif event_type == "recomputation_triggered":
                self.almanac = self.almanac.model_copy(
                    update={"recomputation_count": self.almanac.recomputation_count + 1}
                )

    async def _on_session_ops_event(self, msg) -> None:
        try:
            self.session_ops_events.append(json.loads(msg.data))
        except Exception as exc:
            log.warning("Failed to parse session OpsEvent: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _propagate_positions(self, eph) -> None:
        """Update node positions from a SessionEphemeris object."""
        from nodalarc.models.events import EphemerisNodeFixed, EphemerisNodeKeplerian

        with self.state_lock:
            for node_id, node_data in eph.nodes.items():
                if isinstance(node_data, EphemerisNodeKeplerian):
                    self.nodes[node_id] = NodeState(
                        node_id=node_id,
                        node_type="satellite",
                        lat_deg=0.0,
                        lon_deg=0.0,
                        alt_km=node_data.altitude_km,
                        vel_x_km_s=None,
                        vel_y_km_s=None,
                        vel_z_km_s=None,
                        plane=node_data.plane,
                        slot=node_data.slot,
                        routing_area=None,
                        neighbor_count=0,
                        isl_count=0,
                        gnd_count=0,
                        prefix=None,
                        min_elevation_deg=None,
                        beam_falloff_exponent=None,
                    )
                elif isinstance(node_data, EphemerisNodeFixed):
                    self.nodes[node_id] = NodeState(
                        node_id=node_id,
                        node_type="ground_station",
                        lat_deg=node_data.lat_deg,
                        lon_deg=node_data.lon_deg,
                        alt_km=node_data.alt_km,
                        vel_x_km_s=None,
                        vel_y_km_s=None,
                        vel_z_km_s=None,
                        plane=None,
                        slot=None,
                        routing_area=None,
                        neighbor_count=0,
                        isl_count=0,
                        gnd_count=0,
                        prefix=None,
                        min_elevation_deg=self.gs_elevation_map.get(node_id),
                        beam_falloff_exponent=self.beam_falloff_exponent,
                    )

    def _propagate_positions_from_time(self, sim_time_iso: str) -> None:
        """Propagate satellite positions for a given sim_time.

        Uses the cached ephemeris to compute Keplerian propagation.
        Ground stations are static — only satellites move.
        """
        if self.cached_ephemeris_obj is None:
            return
        with self.state_lock:
            self.sim_time = sim_time_iso

    def _add_recent_event(self, event_data: dict, event_type: str) -> None:
        sim_time_raw = event_data.get("sim_time")
        if sim_time_raw is None:
            log.error("Malformed event — missing sim_time: %s", event_data)
            raise ValueError(f"Event missing sim_time: {event_type}")
        sim_time_dt = (
            datetime.fromisoformat(sim_time_raw) if isinstance(sim_time_raw, str) else sim_time_raw
        )
        node_id = event_data.get("node_id") or event_data.get("node_a")
        if not node_id:
            log.error("Malformed event — missing node_id: %s", event_data)
            raise ValueError(f"Event missing node_id: {event_type}")
        event = RecentEvent(
            sim_time=sim_time_dt,
            node_id=node_id,
            event_type=event_type,
            summary=event_data.get("reason", ""),
        )
        with self.state_lock:
            self.recent_events.append(event)

    def _notify_topology_change(self, node_a: str, node_b: str) -> None:
        if self.continuous_tracer is not None:
            self.continuous_tracer.notify_topology_change(node_a, node_b)

    def compute_convergence_state(self) -> None:
        """Update network_health based on current link counts."""
        active = self.curr_snapshot_active_count
        if self.mi_active:
            return
        now = _time.monotonic()
        if self.session_ready_time > 0 and (now - self.session_ready_time) < CONVERGENCE_DWELL_S:
            self.network_health = self.network_health.model_copy(update={"status": "stabilizing"})
            return
        total = max(active, self.prev_snapshot_active_count, 1)
        delta = abs(active - self.prev_snapshot_active_count)
        if delta / total > BULK_CHANGE_THRESHOLD:
            self.network_health = self.network_health.model_copy(update={"status": "converging"})
        else:
            self.network_health = self.network_health.model_copy(update={"status": "converged"})

    @staticmethod
    def _load_gs_elevation_map(session: SessionConfig) -> dict[str, float]:
        from nodalarc.constellation_loader import load_ground_stations
        from nodalarc.models.addressing import AddressingScheme

        try:
            gs_file = load_ground_stations(session.ground_stations)
            addressing = AddressingScheme(session.addressing)
            result: dict[str, float] = {}
            for station in gs_file.stations:
                gs_id = addressing.gs_id(station.name)
                result[gs_id] = (
                    station.min_elevation_deg or gs_file.default_min_elevation_deg or 25.0
                )
            return result
        except Exception:
            return {}

    @staticmethod
    def _load_beam_falloff_exponent(session: SessionConfig) -> float:
        try:
            if session.routing and hasattr(session.routing, "beam_falloff_exponent"):
                return session.routing.beam_falloff_exponent or 2.0
        except Exception:
            pass
        return 2.0


# ------------------------------------------------------------------
# Module-level utilities (no state, pure functions)
# ------------------------------------------------------------------


def _link_key(node_a: str, node_b: str) -> str:
    return f"{min(node_a, node_b)}:{max(node_a, node_b)}"


def _derive_link_type(node_a: str, node_b: str, raw_type: str | None = None) -> str:
    if raw_type and raw_type != "isl":
        return raw_type
    a_is_gs = node_a.startswith("gs-")
    b_is_gs = node_b.startswith("gs-")
    if a_is_gs or b_is_gs:
        return "ground"
    a_parts = node_a.replace("sat-", "").split("s")
    b_parts = node_b.replace("sat-", "").split("s")
    if len(a_parts) >= 2 and len(b_parts) >= 2:
        try:
            a_plane = int(a_parts[0].replace("p", ""))
            b_plane = int(b_parts[0].replace("p", ""))
            if a_plane == b_plane:
                return "intra_plane_isl"
            return "cross_plane_isl"
        except (ValueError, IndexError):
            pass
    return "isl"
