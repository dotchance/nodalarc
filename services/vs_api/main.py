# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
"""VS-API — Visualization State API server.

FastAPI server with WebSocket (full snapshots at ~1Hz) and REST endpoints.
Subscribes to ZMQ PUB sockets from OME, TO, and MI to maintain state.

Run: python -m vs_api.main --session <path> --db <sqlite_path> --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import sqlite3
import sys
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import nats
import yaml
from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from nodalarc.constants import LOG_FORMAT
from nodalarc.db.queries import (
    insert_snapshot,
    query_convergence_events,
    query_link_events,
    query_nearest_snapshot,
    query_probe_results,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.link_state import AdminState, CarrierState, LinkStateSnapshot
from nodalarc.models.session import SessionConfig
from nodalarc.models.vs_api import (
    LinkState,
    NetworkHealth,
    NodeState,
    RecentEvent,
    StateSnapshot,
    TracedPath,
)
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    SUBJECT_ALMANAC_EVENT,
    SUBJECT_LATENCY_UPDATE,
    SUBJECT_LINK_DOWN,
    SUBJECT_LINK_STATE_SNAPSHOT,
    SUBJECT_LINK_UP,
    SUBJECT_SNAPSHOT,
    nats_url,
)
from nodalarc.platform import get_platform_config

from vs_api.continuous_tracer import ContinuousTracer
from vs_api.introspect import VTYSH_COMMANDS, run_vtysh
from vs_api.session_manager import SessionManager

log = logging.getLogger(__name__)

# --- Authentication ---

_API_KEY: str = os.environ.get("NODAL_API_KEY", "")


def _require_api_key(request: Request) -> None:
    """FastAPI dependency: reject requests without a valid Bearer token.

    Skipped when NODAL_API_KEY is empty (local development).
    """
    if not _API_KEY:
        return
    auth = request.headers.get("Authorization", "")
    if auth == f"Bearer {_API_KEY}":
        return
    raise_unauthorized()


def raise_unauthorized() -> None:
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="Invalid or missing API key")


# --- Rate Limiting (in-memory token bucket per IP) ---

import time as _time


class _TokenBucket:
    """Simple per-IP token bucket rate limiter."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate  # tokens per second
        self._burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_time)

    def allow(self, ip: str) -> bool:
        now = _time.monotonic()
        tokens, last = self._buckets.get(ip, (float(self._burst), now))
        elapsed = now - last
        tokens = min(self._burst, tokens + elapsed * self._rate)
        if tokens >= 1.0:
            self._buckets[ip] = (tokens - 1.0, now)
            return True
        self._buckets[ip] = (tokens, now)
        return False


# Rate limiters: lazy-initialized from platform config
_rate_introspect: _TokenBucket | None = None
_rate_playback: _TokenBucket | None = None
_rate_session_switch: _TokenBucket | None = None


def _get_rate_introspect() -> _TokenBucket:
    global _rate_introspect
    if _rate_introspect is None:
        from nodalarc.platform import get_platform_config

        cfg = get_platform_config()
        r = cfg.vs_api_introspect_max_requests_per_minute
        _rate_introspect = _TokenBucket(rate=r / 60, burst=r)
    return _rate_introspect


def _get_rate_playback() -> _TokenBucket:
    global _rate_playback
    if _rate_playback is None:
        from nodalarc.platform import get_platform_config

        cfg = get_platform_config()
        r = cfg.vs_api_playback_max_requests_per_minute
        _rate_playback = _TokenBucket(rate=r / 60, burst=r)
    return _rate_playback


def _get_rate_session_switch() -> _TokenBucket:
    global _rate_session_switch
    if _rate_session_switch is None:
        from nodalarc.platform import get_platform_config

        cfg = get_platform_config()
        r = cfg.vs_api_session_switch_max_requests_per_minute
        _rate_session_switch = _TokenBucket(rate=r / 60, burst=r)
    return _rate_session_switch


def _client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate(bucket: _TokenBucket, request: Request) -> None:
    """FastAPI dependency: reject if rate limit exceeded."""
    ip = _client_ip(request)
    if not bucket.allow(ip):
        from fastapi import HTTPException

        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def _rate_limit_introspect(request: Request) -> None:
    _check_rate(_get_rate_introspect(), request)


def _rate_limit_playback(request: Request) -> None:
    _check_rate(_get_rate_playback(), request)


def _rate_limit_session_switch(request: Request) -> None:
    _check_rate(_get_rate_session_switch(), request)


# Module-level state (populated before app starts)
_state = {
    "nodes": {},  # node_id -> NodeState dict
    "links": {},  # "nodeA:nodeB" -> LinkState dict
    "recent_events": [],  # list of RecentEvent dicts (last 50)
    "active_flows": [],  # list of ActiveFlow dicts
    "network_health": {
        "status": "converged",
        "converging_since_ms": None,
        "unreachable_flows": 0,
        "last_convergence_ms": None,
    },
    "sim_time": datetime.now(UTC).isoformat(),
}
_state_lock = threading.Lock()
_db_path: str = ""
_session_file: str = ""  # Path to the active session YAML
_routing_stack: str | None = None
_constellation_name: str | None = None
_session_manager: SessionManager | None = None
_gs_elevation_map: dict[str, float] = {}  # node_id -> min_elevation_deg
_beam_falloff_exponent: float = 2.0
_playback_paused: bool = False
_playback_speed: float = 1.0

# Stale tracking per streaming architecture v1.2 Section 3.5
_STALE_THRESHOLD_S: float = 15.0
_last_snapshot_wall_time: float = 0.0  # monotonic time of last Snapshot
_last_link_event_wall_time: float = 0.0  # monotonic time of last LinkUp/Down

# Continuous tracer state
_continuous_tracer: ContinuousTracer | None = None

_almanac_state: dict = {
    "last_topology_state_id": None,
    "last_push_sim_time": None,
    "last_push_wall_time": None,
    "nodes_succeeded": 0,
    "nodes_failed": 0,
    "deviation_count": 0,
    "recomputation_count": 0,
    "nodalpath_active": False,
}
_almanac_lock = threading.Lock()


def _update_position(event_data: dict) -> None:
    """Update node positions from OME Snapshot.

    Handles two formats:
    - Per-node: top-level node_id, lat_deg, lon_deg, etc.
    - Batch (Snapshot): positions dict/list of node dicts.
    """
    with _state_lock:
        _state["sim_time"] = event_data.get("sim_time", _state["sim_time"])

        # Per-node format (PositionEvent schema)
        if "node_id" in event_data:
            import re

            node_id = event_data["node_id"]
            existing = _state["nodes"].get(node_id, {})

            # Derive plane/slot from node_id if not already set
            if "plane" not in existing or existing["plane"] is None:
                m = re.match(r"sat-P(\d+)S(\d+)", node_id)
                if m:
                    existing["plane"] = int(m.group(1))
                    existing["slot"] = int(m.group(2))
                    existing["node_type"] = "satellite"
                else:
                    existing.setdefault("node_type", "ground_station")

            existing.update(
                {
                    "node_id": node_id,
                    "lat_deg": event_data.get("lat_deg", existing.get("lat_deg", 0.0)),
                    "lon_deg": event_data.get("lon_deg", existing.get("lon_deg", 0.0)),
                    "alt_km": event_data.get("alt_km", existing.get("alt_km", 0.0)),
                    "vel_x_km_s": event_data.get("vel_x_km_s", existing.get("vel_x_km_s")),
                    "vel_y_km_s": event_data.get("vel_y_km_s", existing.get("vel_y_km_s")),
                    "vel_z_km_s": event_data.get("vel_z_km_s", existing.get("vel_z_km_s")),
                }
            )
            if node_id in _gs_elevation_map:
                existing["min_elevation_deg"] = _gs_elevation_map[node_id]
            if existing.get("node_type") == "satellite":
                existing["beam_falloff_exponent"] = _beam_falloff_exponent
            _state["nodes"][node_id] = existing
            return

        # Batch format — supports both:
        # - Snapshot dict: {"positions": {"node_id": {lat_deg, ...}}}
        # - Legacy list: {"positions": [{"node_id": "...", lat_deg, ...}]}
        raw_positions = event_data.get("positions", {})
        if isinstance(raw_positions, dict):
            items = raw_positions.items()
        else:
            items = [(n.get("node_id", ""), n) for n in raw_positions]
        for node_id, node in items:
            if not node_id:
                continue
            # Derive plane/slot from node_id (e.g. "sat-P00S05" → plane=0, slot=5)
            import re

            plane_val = node.get("plane")
            slot_val = node.get("slot")
            if plane_val is None and node_id.startswith("sat-"):
                m = re.match(r"sat-P(\d+)S(\d+)", node_id)
                if m:
                    plane_val = int(m.group(1))
                    slot_val = int(m.group(2))
            node_dict = {
                "node_id": node_id,
                "node_type": "ground_station" if node_id.startswith("gs-") else "satellite",
                "lat_deg": node.get("lat_deg", 0.0),
                "lon_deg": node.get("lon_deg", 0.0),
                "alt_km": node.get("alt_km", 0.0),
                "vel_x_km_s": node.get("vel_x_km_s"),
                "vel_y_km_s": node.get("vel_y_km_s"),
                "vel_z_km_s": node.get("vel_z_km_s"),
                "plane": plane_val,
                "slot": slot_val,
            }
            if node_id in _gs_elevation_map:
                node_dict["min_elevation_deg"] = _gs_elevation_map[node_id]
            if node_dict.get("node_type") == "satellite":
                node_dict["beam_falloff_exponent"] = _beam_falloff_exponent
            _state["nodes"][node_id] = node_dict


def _update_link_up(event_data: dict) -> None:
    """Update link state on LinkUp."""
    node_a = event_data.get("node_a", "")
    node_b = event_data.get("node_b", "")
    key = _link_key(node_a, node_b)
    with _state_lock:
        is_new = key not in _state["links"]
        _state["links"][key] = {
            "node_a": node_a,
            "node_b": node_b,
            "state": "active",
            "link_type": _derive_link_type(node_a, node_b),
            "link_reason": event_data.get("reason", ""),
            "latency_ms": event_data.get("latency_ms", 0.0),
            "bandwidth_mbps": event_data.get("bandwidth_mbps", 0.0),
            "range_km": 0.0,
            "traffic_load_pct": None,
        }
    # Only notify tracer on actual state change
    if is_new and _continuous_tracer is not None:
        _continuous_tracer.notify_topology_change(node_a, node_b)


def _update_link_down(event_data: dict) -> None:
    """Remove link state on LinkDown."""
    node_a = event_data.get("node_a", "")
    node_b = event_data.get("node_b", "")
    key = _link_key(node_a, node_b)
    with _state_lock:
        _state["links"].pop(key, None)
    # Wake continuous tracer to re-trace after convergence
    if _continuous_tracer is not None:
        _continuous_tracer.notify_topology_change(node_a, node_b)


def _update_latency(event_data: dict) -> None:
    """Update link latency."""
    key = _link_key(event_data.get("node_a", ""), event_data.get("node_b", ""))
    with _state_lock:
        if key in _state["links"]:
            _state["links"][key]["latency_ms"] = event_data.get("latency_ms", 0.0)
            _state["links"][key]["range_km"] = event_data.get("range_km", 0.0)


def _add_recent_event(event_data: dict, event_type: str) -> None:
    """Add to recent events list (cap at 50)."""
    with _state_lock:
        _state["recent_events"].append(
            {
                "sim_time": event_data.get("sim_time", datetime.now(UTC).isoformat()),
                "node_id": event_data.get("node_id", event_data.get("node_a", "")),
                "event_type": event_type,
                "summary": event_data.get("detail", event_data.get("reason", event_type)),
            }
        )
        if len(_state["recent_events"]) > 50:
            _state["recent_events"] = _state["recent_events"][-50:]


def _update_convergence(event_data: dict) -> None:
    """Update network health from convergence result."""
    with _state_lock:
        if event_data.get("converged"):
            _state["network_health"]["status"] = "converged"
            _state["network_health"]["converging_since_ms"] = None
            _state["network_health"]["last_convergence_ms"] = event_data.get("duration_ms")
        else:
            _state["network_health"]["status"] = "converging"


def _link_key(node_a: str, node_b: str) -> str:
    return f"{min(node_a, node_b)}:{max(node_a, node_b)}"


def _derive_link_type(node_a: str, node_b: str) -> str:
    """Derive link type from node IDs.

    Returns: "ground", "intra_plane_isl", or "cross_plane_isl".
    """
    if node_a.startswith("gs-") or node_b.startswith("gs-"):
        return "ground"
    # Parse plane from sat-P##S## format
    import re

    ma = re.match(r"sat-[Pp](\d+)[Ss]\d+", node_a)
    mb = re.match(r"sat-[Pp](\d+)[Ss]\d+", node_b)
    if ma and mb and ma.group(1) == mb.group(1):
        return "intra_plane_isl"
    return "cross_plane_isl"


def _update_almanac_state(event_data: dict) -> None:
    """Update almanac state from AlmanacEvent."""
    event_type = event_data.get("event_type", "")
    with _almanac_lock:
        _almanac_state["nodalpath_active"] = True
        if event_type == "table_pushed":
            _almanac_state["last_topology_state_id"] = event_data.get("topology_state_id")
            _almanac_state["last_push_sim_time"] = event_data.get("sim_time")
            _almanac_state["last_push_wall_time"] = event_data.get("wall_time")
            _almanac_state["nodes_succeeded"] = event_data.get("nodes_succeeded", 0)
            _almanac_state["nodes_failed"] = event_data.get("nodes_failed", 0)
        elif event_type == "deviation_detected":
            _almanac_state["deviation_count"] = _almanac_state.get("deviation_count", 0) + 1
        elif event_type == "recomputation_triggered":
            _almanac_state["recomputation_count"] = _almanac_state.get("recomputation_count", 0) + 1


def _is_stale() -> bool:
    """Check if position or link data is stale per streaming architecture v1.2 Section 3.5."""
    now = _time.monotonic()
    # Don't report stale until we've received at least one message of each type
    if _last_snapshot_wall_time == 0.0 or _last_link_event_wall_time == 0.0:
        return False
    return (
        now - _last_snapshot_wall_time > _STALE_THRESHOLD_S
        or now - _last_link_event_wall_time > _STALE_THRESHOLD_S
    )


def _build_snapshot() -> dict:
    """Build a StateSnapshot dict from current state."""
    with _state_lock:
        now = datetime.now(UTC)
        links = [LinkState(**l) for l in _state["links"].values()]

        # Compute isl_count / gnd_count from active links
        _isl_counts: dict[str, int] = {}
        _gnd_counts: dict[str, int] = {}
        for ldata in _state["links"].values():
            a, b = ldata["node_a"], ldata["node_b"]
            is_gnd = a.startswith("gs-") or b.startswith("gs-")
            for nid in (a, b):
                if is_gnd:
                    _gnd_counts[nid] = _gnd_counts.get(nid, 0) + 1
                else:
                    _isl_counts[nid] = _isl_counts.get(nid, 0) + 1
        nodes = []
        for n in _state["nodes"].values():
            patched = {
                **n,
                "isl_count": _isl_counts.get(n["node_id"], 0),
                "gnd_count": _gnd_counts.get(n["node_id"], 0),
            }
            nodes.append(NodeState(**patched))
        recent = [
            RecentEvent(
                sim_time=datetime.fromisoformat(e["sim_time"])
                if isinstance(e["sim_time"], str)
                else e["sim_time"],
                node_id=e["node_id"],
                event_type=e["event_type"],
                summary=e["summary"],
            )
            for e in _state["recent_events"]
        ]
        health = NetworkHealth(**_state["network_health"])

        _traced: list[TracedPath] = []
        if _continuous_tracer is not None and _continuous_tracer.active:
            tp = _continuous_tracer.traced_path
            if tp is not None:
                _traced.append(tp)

        snapshot = StateSnapshot(
            sim_time=datetime.fromisoformat(_state["sim_time"])
            if isinstance(_state["sim_time"], str)
            else _state["sim_time"],
            wall_time=now,
            schema_version=1,
            nodes=nodes,
            links=links,
            traced_paths=_traced,
            active_flows=[],
            recent_events=recent,
            network_health=health,
            routing_stack=_routing_stack,
            constellation_name=_constellation_name,
            session_status=_session_manager.status if _session_manager else None,
            session_status_detail=_session_manager.status_detail if _session_manager else None,
            playback_paused=_playback_paused,
            playback_speed=_playback_speed,
            stale=_is_stale(),
        )
        return json.loads(snapshot.model_dump_json())


# --- NATS subscriber (replaces ZMQ subscriber) ---

_nats_connection: nats.NATS | None = None


async def _nats_subscriber() -> None:
    """NATS JetStream subscriber — replaces ZMQ subscriber.

    Subscribes to:
    - nodalarc.ome.snapshot → position updates
    - nodalarc.links.state → LinkStateSnapshot (replace-not-merge)
    - nodalarc.links.up/down → individual link events
    - nodalarc.links.latency → latency updates
    - nodalarc.nodalpath.almanac → NodalPath almanac events

    Stale detection: tracks wall-clock time of last snapshot and link event.
    If no messages for 15s, data is stale (same logic as ZMQ, different transport).
    """
    global _nats_connection, _last_snapshot_wall_time, _last_link_event_wall_time

    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    _nats_connection = nc
    js = nc.jetstream()

    log.info("VS-API NATS connected to %s", nats_url())

    # --- Callback-driven subscriptions (DeliverPolicy.NEW) ---
    # NATS delivers messages to callbacks as they arrive — concurrently,
    # no sequential polling, no starvation of any subscription.
    from nats.js.api import DeliverPolicy

    msg_count = 0

    async def _on_snapshot(msg):
        global _last_snapshot_wall_time
        nonlocal msg_count
        msg_count += 1
        _update_position(json.loads(msg.data))
        _last_snapshot_wall_time = _time.monotonic()

    async def _on_link_state_snapshot(msg):
        global _last_link_event_wall_time
        nonlocal msg_count
        msg_count += 1
        _apply_link_state_snapshot(json.loads(msg.data))
        _last_link_event_wall_time = _time.monotonic()

    async def _on_link_up(msg):
        global _last_link_event_wall_time
        nonlocal msg_count
        msg_count += 1
        data = json.loads(msg.data)
        _update_link_up(data)
        _add_recent_event(data, "link_up")
        _last_link_event_wall_time = _time.monotonic()

    async def _on_link_down(msg):
        global _last_link_event_wall_time
        nonlocal msg_count
        msg_count += 1
        data = json.loads(msg.data)
        _update_link_down(data)
        _add_recent_event(data, "link_down")
        _last_link_event_wall_time = _time.monotonic()

    async def _on_latency_update(msg):
        nonlocal msg_count
        msg_count += 1
        _update_latency(json.loads(msg.data))

    async def _on_almanac(msg):
        nonlocal msg_count
        msg_count += 1
        _update_almanac_state(json.loads(msg.data))

    subs = []
    try:
        subs.append(
            await js.subscribe(
                SUBJECT_SNAPSHOT,
                stream="NODALARC_OME",
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
                cb=_on_snapshot,
            )
        )
        subs.append(
            await js.subscribe(
                SUBJECT_LINK_STATE_SNAPSHOT,
                stream="NODALARC_LINKS",
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
                cb=_on_link_state_snapshot,
            )
        )
        subs.append(
            await js.subscribe(
                SUBJECT_LINK_UP,
                stream="NODALARC_LINKS",
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
                cb=_on_link_up,
            )
        )
        subs.append(
            await js.subscribe(
                SUBJECT_LINK_DOWN,
                stream="NODALARC_LINKS",
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
                cb=_on_link_down,
            )
        )
        subs.append(
            await js.subscribe(
                SUBJECT_LATENCY_UPDATE,
                stream="NODALARC_LINKS",
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
                cb=_on_latency_update,
            )
        )
        subs.append(
            await js.subscribe(
                SUBJECT_ALMANAC_EVENT,
                stream="NODALARC_OME",
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
                cb=_on_almanac,
            )
        )
    except Exception as exc:
        log.warning("NATS subscription setup failed: %s — streams may not exist yet", exc)

    log.info("VS-API NATS subscriber started — %d callback subscriptions active", len(subs))

    # Periodic status log + wait for shutdown
    try:
        last_status_time = _time.monotonic()
        while True:
            await asyncio.sleep(10)
            now_mono = _time.monotonic()
            if now_mono - last_status_time >= 30:
                log.info("NATS subscriber status: %d msgs, stale=%s", msg_count, _is_stale())
                last_status_time = now_mono

    except asyncio.CancelledError:
        log.info("NATS subscriber cancelled after %d messages", msg_count)
    except Exception as exc:
        log.error("NATS subscriber crashed: %s", exc, exc_info=True)
    finally:
        log.info("NATS subscriber exiting (total messages: %d)", msg_count)
        for sub in subs:
            try:  # noqa: SIM105
                await sub.unsubscribe()
            except Exception:
                pass
        await nc.close()


def _apply_link_state_snapshot(data: dict) -> None:
    """Apply LinkStateSnapshot as replace-not-merge on _state["links"].

    This is the VS-API's equivalent of the Scheduler's
    _apply_link_state_snapshot(). It replaces _state["links"] entirely
    with the snapshot contents. No transition replay needed.
    """
    try:
        snapshot = LinkStateSnapshot.model_validate(data)
    except Exception as exc:
        log.warning("Failed to parse LinkStateSnapshot: %s", exc)
        return

    with _state_lock:
        _state["links"].clear()
        for link in snapshot.links:
            if link.admin == AdminState.UP and link.carrier == CarrierState.UP:
                key = _link_key(link.node_a, link.node_b)
                _state["links"][key] = {
                    "node_a": link.node_a,
                    "node_b": link.node_b,
                    "state": "active",
                    "link_type": _derive_link_type(link.node_a, link.node_b),
                    "link_reason": "",
                    "latency_ms": link.latency_ms or 0.0,
                    "bandwidth_mbps": link.bandwidth_mbps or 0.0,
                    "range_km": 0.0,
                    "traffic_load_pct": None,
                }

    isl = sum(1 for k in _state["links"] if not k.startswith("gs-"))
    gs = sum(1 for k in _state["links"] if k.startswith("gs-"))
    log.info(
        "LinkStateSnapshot applied: seq=%d, %d links (%d ISL, %d GS)",
        snapshot.snapshot_seq,
        len(_state["links"]),
        isl,
        gs,
    )


# --- FastAPI app ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start NATS subscriber and WebSocket broadcaster on startup."""
    sub_task = asyncio.create_task(_nats_subscriber())

    def _on_subscriber_done(task: asyncio.Task) -> None:
        exc = task.exception() if not task.cancelled() else None
        if exc:
            log.error("NATS subscriber task DIED with exception: %s", exc, exc_info=exc)
        elif task.cancelled():
            log.info("NATS subscriber task cancelled")
        else:
            log.warning("NATS subscriber task exited unexpectedly")

    sub_task.add_done_callback(_on_subscriber_done)

    broadcast_task = asyncio.create_task(_ws_broadcaster())

    yield

    sub_task.cancel()
    broadcast_task.cancel()


app = FastAPI(title="Nodal Arc VS-API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # NODAL_CORS_ORIGIN restricts origins in production (e.g. "https://nodal.example.com").
    # When unset, allow all origins — API key auth (C2) is the primary protection.
    allow_origins=[os.environ.get("NODAL_CORS_ORIGIN", "*")],
    allow_methods=["*"],
    allow_headers=["*"],
)

_audit_log = logging.getLogger("nodal.audit")
_MAX_BODY_BYTES = 1_048_576  # 1 MB


from starlette.types import ASGIApp, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """Add security headers to all HTTP responses. Passes WebSocket through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'self'"),
                ]
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Reject HTTP requests with bodies larger than _MAX_BODY_BYTES. Passes WebSocket through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request as StarletteRequest

        request = StarletteRequest(scope)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY_BYTES:
            response = JSONResponse(status_code=413, content={"error": "Request body too large"})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class AuditLogMiddleware:
    """Log all REST requests and failed auth attempts. Passes WebSocket through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request as StarletteRequest

        request = StarletteRequest(scope)
        ip = _client_ip(request)
        status_code = 0

        async def capture_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, capture_send)
        path = request.url.path
        method = request.method
        if status_code == 401:
            _audit_log.warning(f"AUTH_FAIL ip={ip} method={method} path={path}")
        elif path != "/api/v1/health":
            _audit_log.info(f"REQUEST ip={ip} method={method} path={path} status={status_code}")


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(AuditLogMiddleware)


@app.get("/api/v1/health")
def health_check() -> dict:
    """Unauthenticated health check for load balancers and integration tests."""
    return {
        "status": "ok",
        "session_status": _session_manager.status if _session_manager else "idle",
    }


@app.get("/api/v1/auth/token")
def get_auth_token() -> dict:
    """Return the current API key. Unauthenticated — dev-mode only."""
    return {"token": _API_KEY}


def _clear_state() -> None:
    """Reset in-memory state during session switch."""
    global _continuous_tracer
    if _continuous_tracer is not None:
        # Best-effort stop — we're in a sync context during session switch
        _continuous_tracer = None
    with _state_lock:
        _state["nodes"].clear()
        _state["links"].clear()
        _state["recent_events"].clear()
        _state["active_flows"].clear()
        _state["network_health"] = {
            "status": "converged",
            "converging_since_ms": None,
            "unreachable_flows": 0,
            "last_convergence_ms": None,
        }
        _state["sim_time"] = datetime.now(UTC).isoformat()


def _load_gs_elevation_map(session: SessionConfig) -> dict[str, float]:
    """Load per-station min_elevation_deg from ground station config."""
    from nodalarc.constellation_loader import load_ground_stations

    if isinstance(session.ground_stations, list):
        gs_file = load_ground_stations(session.ground_stations)
    else:
        gs_path = Path(session.ground_stations)
        if not gs_path.exists():
            return {}
        gs_file = load_ground_stations(gs_path)
    gs_id_tpl = session.addressing.gs_id_template
    result: dict[str, float] = {}
    for station in gs_file.stations:
        node_id = gs_id_tpl.format(name=station.name)
        elev = (
            station.min_elevation_deg
            if station.min_elevation_deg is not None
            else gs_file.default_min_elevation_deg
        )
        result[node_id] = elev
    return result


def _load_beam_falloff_exponent(session: SessionConfig) -> float:
    """Load beam_falloff_exponent from the constellation's satellite type."""
    from nodalarc.constellation_loader import load_constellation, load_satellite_type

    if isinstance(session.constellation, dict):
        config = load_constellation(session.constellation)
    else:
        constellation_path = Path(session.constellation)
        if not constellation_path.exists():
            return 2.0
        config = load_constellation(constellation_path)
    sat_type_name = getattr(config, "satellite_type", None)
    if not sat_type_name:
        return 2.0
    sat_type = load_satellite_type(sat_type_name)
    if not sat_type.ground_terminals:
        return 2.0
    return sat_type.ground_terminals[0].beam_falloff_exponent


def _update_session_globals(session_path: str, new_db_path: str) -> None:
    """Reload routing_stack, constellation_name, db_path, and session_file from new session."""
    global \
        _routing_stack, \
        _constellation_name, \
        _db_path, \
        _session_file, \
        _gs_elevation_map, \
        _beam_falloff_exponent
    _session_file = session_path
    session_data = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(session_data)
    if session.routing.stack is not None:
        _routing_stack = Path(session.routing.stack).name
    else:
        ext_str = "-".join(session.routing.extensions) if session.routing.extensions else "plain"
        _routing_stack = f"{session.routing.protocol}-{ext_str}"
    if isinstance(session.constellation, dict):
        _constellation_name = session.constellation.get("name", "custom")
    else:
        _constellation_name = Path(session.constellation).stem
    _db_path = new_db_path
    _gs_elevation_map = _load_gs_elevation_map(session)
    _beam_falloff_exponent = _load_beam_falloff_exponent(session)

    # Ensure tables exist in new DB
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(new_db_path)
    create_tables(conn)
    conn.close()


def _restore_state_from_db(db_path: str) -> bool:
    """Load the most recent snapshot from SQLite and pre-populate in-memory state.

    Returns True if state was restored, False otherwise. This is called during
    session recovery so VS-API doesn't start with empty nodes/links after a restart.
    """
    if not db_path or not Path(db_path).exists():
        return False

    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT snapshot_json FROM snapshots ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if not row:
            log.info("No snapshots in DB to restore from")
            return False

        snapshot = json.loads(row[0])

        with _state_lock:
            # Restore nodes
            for node in snapshot.get("nodes", []):
                node_id = node.get("node_id", "")
                if node_id:
                    _state["nodes"][node_id] = node

            # Restore links
            for link in snapshot.get("links", []):
                key = _link_key(link.get("node_a", ""), link.get("node_b", ""))
                _state["links"][key] = link

            # Restore recent events
            _state["recent_events"] = snapshot.get("recent_events", [])

            # Restore network health
            if "network_health" in snapshot:
                _state["network_health"] = snapshot["network_health"]

            # Restore sim time
            if "sim_time" in snapshot:
                _state["sim_time"] = snapshot["sim_time"]

        node_count = len(snapshot.get("nodes", []))
        link_count = len(snapshot.get("links", []))
        log.info(f"Restored state from DB: {node_count} nodes, {link_count} links")
        return True

    except Exception as exc:
        log.warning(f"Failed to restore state from DB: {exc}")
        return False


async def _ws_broadcaster() -> None:
    """Record StateSnapshot to SQLite every ~10 seconds for historical playback."""
    tick = 0
    while True:
        await asyncio.sleep(0.1)
        tick += 1
        if tick % 100 == 0 and _db_path:
            try:
                snapshot = _build_snapshot()
                conn = sqlite3.connect(_db_path)
                insert_snapshot(
                    conn,
                    sim_time=snapshot["sim_time"],
                    wall_time=snapshot["wall_time"],
                    snapshot_json=json.dumps(snapshot),
                )
                conn.close()
            except Exception as exc:
                log.warning(f"Failed to store snapshot: {exc}")


@app.websocket("/ws/v1/state")
async def ws_state(websocket: WebSocket) -> None:
    """WebSocket endpoint — push state snapshots at ~1Hz from this handler."""
    # Authenticate via ?token= query parameter when API key is set
    ws_ip = websocket.client.host if websocket.client else "unknown"
    if _API_KEY:
        token = websocket.query_params.get("token", "")
        if token != _API_KEY:
            _audit_log.warning(f"WS_AUTH_FAIL ip={ws_ip}")
            await websocket.close(code=4401, reason="Unauthorized")
            return
    await websocket.accept()
    _audit_log.info(f"WS_CONNECT ip={ws_ip}")

    try:
        while True:
            snapshot = _build_snapshot()
            await websocket.send_json(snapshot)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning(f"WS send error for {ws_ip}: {type(exc).__name__} {exc}")
    finally:
        _audit_log.info(f"WS_DISCONNECT ip={ws_ip}")


@app.get("/api/v1/state", dependencies=[Depends(_require_api_key)])
def get_state() -> dict:
    """Current state snapshot."""
    return _build_snapshot()


_NODALPATH_CONSOLE_URL = "http://127.0.0.1:3100/api/status"
_NODALPATH_TIMEOUT = 1.0


async def _fetch_nodalpath_status() -> dict | None:
    """Fetch the NodalPath console status snapshot.

    Returns the parsed JSON dict on success, or None if NodalPath is not reachable.
    Intentionally silent on connection errors — callers handle the None case.
    """
    try:
        async with httpx.AsyncClient(timeout=_NODALPATH_TIMEOUT) as client:
            r = await client.get(_NODALPATH_CONSOLE_URL)
            r.raise_for_status()
            return r.json()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
        return None


@app.get("/api/v1/almanac/status", dependencies=[Depends(_require_api_key)])
async def get_almanac_status() -> dict:
    """Current NodalPath almanac push status (proxied from NodalPath console)."""
    raw = await _fetch_nodalpath_status()
    if raw is None:
        return {"available": False}

    return {
        "available": True,
        "session_path": raw.get("session_path"),
        "transport": raw.get("transport"),
        "dry_run": raw.get("dry_run", False),
        "start_wall_time": raw.get("start_wall_time"),
        "nodes_in_registry": raw.get("nodes_in_registry", 0),
        "transition_count": raw.get("transition_count", 0),
        "deviation_count": raw.get("deviation_count", 0),
        "recomputation_count": raw.get("recomputation_count", 0),
        "last_topology_state_id": raw.get("last_topology_state_id"),
        "last_sim_time": raw.get("last_sim_time"),
        "recent_pushes": (raw.get("push_history") or [])[:5],
        "recent_deviations": (raw.get("deviation_history") or [])[:5],
    }


async def _fetch_nodalpath_path(params: dict) -> dict:
    """Fetch path from NodalPath console. Returns unavailable dict on failure."""
    _unavailable = {
        "reachable": False,
        "unreachable_reason": "NodalPath not available",
        "src": params.get("src", ""),
        "dst": params.get("dst", ""),
        "hops": [],
        "total_latency_ms": 0.0,
        "method": "derived",
        "sim_time": params.get("sim_time", ""),
        "topology_state_id": "",
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                "http://127.0.0.1:3100/api/v1/path",
                params=params,
            )
            if r.status_code == 200:
                return r.json()
            return _unavailable
    except Exception:
        return _unavailable


@app.get("/api/v1/path", dependencies=[Depends(_require_api_key)])
async def get_path(src: str, dst: str, sim_time: str | None = None) -> JSONResponse:
    """Unified path endpoint — proxies to NodalPath for derived paths."""
    params = {"src": src, "dst": dst}
    if sim_time is not None:
        params["sim_time"] = sim_time

    result = await _fetch_nodalpath_path(params)
    return JSONResponse(result)


@app.get("/api/v1/state/{sim_time}", dependencies=[Depends(_require_api_key)])
def get_historical_state(sim_time: str) -> dict:
    """Historical state at a specific sim_time (nearest snapshot from SQLite)."""
    if not _db_path:
        return {"error": "No database configured"}
    conn = sqlite3.connect(_db_path)
    try:
        result = query_nearest_snapshot(conn, sim_time)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "No snapshots available"})
        return json.loads(result["snapshot_json"])
    finally:
        conn.close()


@app.get("/api/v1/links", dependencies=[Depends(_require_api_key)])
def get_link_events(
    start: str = Query(None),
    end: str = Query(None),
) -> list[dict]:
    """Query link events from SQLite."""
    if not _db_path:
        return []
    conn = sqlite3.connect(_db_path)
    try:
        return query_link_events(conn, start_time=start, end_time=end)
    finally:
        conn.close()


@app.get("/api/v1/metrics/convergence", dependencies=[Depends(_require_api_key)])
def get_convergence_events(
    start: str = Query(None),
    end: str = Query(None),
) -> list[dict]:
    """Query convergence events from SQLite."""
    if not _db_path:
        return []
    conn = sqlite3.connect(_db_path)
    try:
        return query_convergence_events(conn)
    finally:
        conn.close()


@app.get("/api/v1/metrics/flows/{flow_id}", dependencies=[Depends(_require_api_key)])
def get_flow_metrics(
    flow_id: str,
    start: str = Query(None),
    end: str = Query(None),
) -> list[dict]:
    """Query probe results for a flow from SQLite."""
    if not _db_path:
        return []
    conn = sqlite3.connect(_db_path)
    try:
        return query_probe_results(conn, flow_id=flow_id, start_time=start, end_time=end)
    finally:
        conn.close()


def _live_trace_grpc(src: str, dst: str, nodes: list, links: list) -> dict | None:
    """Walk real forwarding tables on live containers via gRPC.

    Queries each node's nodalpath-fwd sidecar to read the installed
    MPLS forwarding state, then follows the label chain hop-by-hop
    from src to dst.  Returns None if gRPC is unavailable.
    """

    import grpc
    from nodalarc.platform import get_platform_config

    cfg = get_platform_config()
    grpc_port = cfg.nodalpath_fwd_grpc_port

    # Build node_id -> pod_ip map via K8s API
    node_ids = {n["node_id"] for n in nodes}
    prefix_by_node: dict[str, str] = {}
    for n in nodes:
        if n.get("prefix"):
            prefix_by_node[n["node_id"]] = n["prefix"]

    def get_pod_ip(node_id: str) -> str | None:
        try:
            import kubernetes.client
            import kubernetes.config

            try:
                kubernetes.config.load_incluster_config()
            except kubernetes.config.ConfigException:
                kubernetes.config.load_kube_config()
            v1 = kubernetes.client.CoreV1Api()
            pod = v1.read_namespaced_pod(node_id.lower(), cfg.kubernetes_namespace)
            return pod.status.pod_ip if pod.status else None
        except Exception:
            return None

    def query_fwd_table(pod_ip: str) -> tuple[list, list] | None:
        """Query a node's live forwarding table via gRPC. Returns (lsr, ler) or None."""
        try:
            from nodalpath.proto import Action, Empty
            from nodalpath.proto.forwarding_pb2_grpc import ForwardingServiceStub

            channel = grpc.insecure_channel(f"{pod_ip}:{grpc_port}")
            grpc.channel_ready_future(channel).result(timeout=3)
            stub = ForwardingServiceStub(channel)
            fwd = stub.GetForwardingTable(Empty(), timeout=3)

            action_map = {Action.SWAP: "SWAP", Action.POP: "POP", Action.PUSH: "PUSH"}
            lsr = []
            for e in fwd.lsr_entries:
                lsr.append(
                    {
                        "in_label": e.in_label,
                        "action": action_map.get(e.action, str(e.action)),
                        "out_label": e.out_label,
                        "out_interface": e.out_interface,
                    }
                )
            ler = []
            for e in fwd.ler_entries:
                ler.append(
                    {
                        "dst_prefix": e.dst_prefix,
                        "push_label": e.push_label,
                        "out_interface": e.out_interface,
                    }
                )
            channel.close()
            return lsr, ler
        except Exception:
            return None

    # Build SID -> node_id and prefix maps from the session context.
    # This loads the actual addressing scheme and ground station configs
    # so prefixes match what NodalPath installed in the forwarding tables.
    sid_to_node: dict[int, str] = {}
    try:
        from pathlib import Path as _Path

        from nodalpath.orchestrator.session_loader import load_session_context
        from nodalpath.platform import get_nodalpath_config

        np_cfg = get_nodalpath_config()
        session_path = _Path(_session_file) if _session_file else None
        if session_path and session_path.exists():
            node_reg, _, session_prefixes, _ = load_session_context(session_path)
            # Populate prefix_by_node from session context
            for nid, prefix in session_prefixes.items():
                prefix_by_node[nid] = prefix
            # Build SID -> node_id from the node registry
            for nid, node_obj in node_reg.items():
                sid_to_node[node_obj.sid] = nid
        else:
            # Fallback: compute SIDs from plane/slot
            sats = [n for n in nodes if n["node_id"].startswith("sat-")]
            max_slot = max((n.get("slot", 0) or 0) for n in sats) if sats else 0
            spp = max_slot + 1
            for n in sats:
                plane = n.get("plane", 0) or 0
                slot = n.get("slot", 0) or 0
                sid = np_cfg.satellite_sid_range_start + (plane * spp + slot) + 1
                sid_to_node[sid] = n["node_id"]
            gs_names = sorted(n["node_id"] for n in nodes if n["node_id"].startswith("gs-"))
            for gs_idx, gs_name in enumerate(gs_names):
                sid = np_cfg.ground_station_sid_range_start + gs_idx
                sid_to_node[sid] = gs_name
    except Exception as exc:
        log.warning(f"Failed to load session context for trace: {exc}")

    # Find destination prefix
    dst_prefix = prefix_by_node.get(dst)
    if not dst_prefix:
        return None

    # Step 1: Get source forwarding table
    src_ip = get_pod_ip(src)
    if not src_ip:
        return None
    src_fwd = query_fwd_table(src_ip)
    if not src_fwd:
        return None
    _src_lsr, src_ler = src_fwd

    # Find ingress rule for dst_prefix
    ingress = None
    for rule in src_ler:
        if rule["dst_prefix"] == dst_prefix:
            ingress = rule
            break
    if not ingress:
        return None

    # Build hop list
    hop_details = []
    hop_ids = [src]

    # Source node — PUSH
    hop_details.append(
        {
            "node_id": src,
            "action": "PUSH",
            "in_label": None,
            "out_label": ingress["push_label"],
            "out_interface": ingress["out_interface"],
            "latency_to_next_ms": None,
        }
    )

    current_label = ingress["push_label"]
    current_node = sid_to_node.get(current_label)
    visited = {src}
    MAX_HOPS = 20

    for _ in range(MAX_HOPS):
        if not current_node or current_node in visited:
            break
        visited.add(current_node)
        hop_ids.append(current_node)

        if current_node == dst:
            hop_details.append(
                {
                    "node_id": current_node,
                    "action": None,
                    "in_label": current_label,
                    "out_label": None,
                    "out_interface": None,
                    "latency_to_next_ms": None,
                }
            )
            break

        # Query this node's live forwarding table
        node_ip = get_pod_ip(current_node)
        if not node_ip:
            break
        fwd_result = query_fwd_table(node_ip)
        if not fwd_result:
            break
        node_lsr, node_ler = fwd_result

        # Find LSR binding for current_label
        binding = None
        for b in node_lsr:
            if b["in_label"] == current_label:
                binding = b
                break

        if not binding:
            # Maybe it's an LER ingress (dst is directly connected)
            # Check if there's a rule for dst_prefix
            for rule in node_ler:
                if rule["dst_prefix"] == dst_prefix:
                    hop_details.append(
                        {
                            "node_id": current_node,
                            "action": "PUSH",
                            "in_label": current_label,
                            "out_label": rule["push_label"],
                            "out_interface": rule["out_interface"],
                            "latency_to_next_ms": None,
                        }
                    )
                    next_node = sid_to_node.get(rule["push_label"])
                    current_label = rule["push_label"]
                    current_node = next_node
                    continue
            break

        hop_details.append(
            {
                "node_id": current_node,
                "action": binding["action"],
                "in_label": binding["in_label"],
                "out_label": binding["out_label"] if binding["action"] == "SWAP" else None,
                "out_interface": binding["out_interface"],
                "latency_to_next_ms": None,
            }
        )

        if binding["action"] == "POP":
            # Next node is the destination (PHP)
            hop_ids.append(dst)
            hop_details.append(
                {
                    "node_id": dst,
                    "action": None,
                    "in_label": None,
                    "out_label": None,
                    "out_interface": None,
                    "latency_to_next_ms": None,
                }
            )
            break
        elif binding["action"] == "SWAP":
            current_label = binding["out_label"]
            current_node = sid_to_node.get(current_label)
        else:
            break

    if len(hop_ids) < 2:
        return None

    # Add latencies from the link state
    link_latency: dict[str, float] = {}
    for l in links:
        key_fwd = f"{l['node_a']}:{l['node_b']}"
        key_rev = f"{l['node_b']}:{l['node_a']}"
        lat = l.get("latency_ms", 0)
        link_latency[key_fwd] = lat
        link_latency[key_rev] = lat

    total_latency = 0.0
    for i, hd in enumerate(hop_details):
        if i < len(hop_ids) - 1:
            key = f"{hop_ids[i]}:{hop_ids[i + 1]}"
            lat = link_latency.get(key, 0)
            hd["latency_to_next_ms"] = lat
            total_latency += lat

    return {
        "hops": hop_ids,
        "hop_details": hop_details,
        "success": True,
        "method": "live",
        "total_latency_ms": total_latency,
    }


@app.post("/api/v1/trace", dependencies=[Depends(_require_api_key)])
def trace_path(body: dict) -> dict:
    """Trace forwarding path by querying live container MPLS tables.

    Walks the real forwarding tables installed on each node's
    nodalpath-fwd gRPC sidecar, hop by hop from source to destination.
    Falls back to NodalPath CSPF if live trace is unavailable.
    """
    src = body.get("src_node", "")
    dst = body.get("dst_node", "")
    if not src or not dst:
        return {"hops": [], "error": "src_node and dst_node required"}

    # Get current snapshot for node/link info
    try:
        snap = _build_snapshot()
        nodes_list = [
            n.model_dump() if hasattr(n, "model_dump") else n for n in snap.get("nodes", [])
        ]
        links_list = [
            l.model_dump() if hasattr(l, "model_dump") else l for l in snap.get("links", [])
        ]
    except Exception:
        nodes_list = []
        links_list = []

    # Try live gRPC trace first (real forwarding tables)
    try:
        result = _live_trace_grpc(src, dst, nodes_list, links_list)
        if result:
            return result
    except Exception as exc:
        log.debug(f"Live gRPC trace failed: {exc}")

    # Fall back to NodalPath CSPF
    try:
        from nodalarc.platform import get_platform_config

        np_port = get_platform_config().nodalpath_console_http_port
        np_resp = httpx.get(
            f"http://127.0.0.1:{np_port}/api/v1/path",
            params={"src": src, "dst": dst},
            timeout=5.0,
        )
        if np_resp.status_code == 200:
            data = np_resp.json()
            if data.get("reachable") and data.get("hops"):
                hop_ids = [h["node_id"] for h in data["hops"]]
                return {
                    "hops": hop_ids,
                    "hop_details": data["hops"],
                    "success": True,
                    "method": "cspf",
                    "total_latency_ms": data.get("total_latency_ms", 0),
                }
            if data.get("reachable") is False:
                reason = data.get("unreachable_reason", "no path found")
                return {"hops": [], "success": False, "method": "cspf", "note": reason}
    except Exception as exc:
        log.debug(f"NodalPath CSPF trace failed: {exc}")

    return {"hops": [], "error": "Trace unavailable"}


# --- Continuous trace endpoints ---


def _get_sim_time_str() -> str:
    """Return current sim_time as string for the continuous tracer."""
    with _state_lock:
        return _state["sim_time"]


def _on_path_change(src: str, dst: str, old_hops: list[str], new_hops: list[str]) -> None:
    """Callback when the traced path changes — add a RecentEvent."""
    sim_time = _get_sim_time_str()
    old_str = " -> ".join(old_hops[:4])
    new_str = " -> ".join(new_hops[:4])
    if len(old_hops) > 4:
        old_str += f" ({len(old_hops)} hops)"
    if len(new_hops) > 4:
        new_str += f" ({len(new_hops)} hops)"
    _add_recent_event(
        {
            "sim_time": sim_time,
            "node_id": src,
            "detail": f"Path {src} -> {dst}: {old_str} => {new_str}",
        },
        "PATH_CHANGE",
    )


@app.post("/api/v1/trace/start", dependencies=[Depends(_require_api_key)])
async def start_continuous_trace(body: dict) -> dict:
    """Start continuous path tracing between two nodes."""
    global _continuous_tracer

    src = body.get("src_node", "")
    dst = body.get("dst_node", "")
    if not src or not dst:
        return JSONResponse(status_code=400, content={"error": "src_node and dst_node required"})

    with _state_lock:
        if src not in _state["nodes"]:
            return JSONResponse(status_code=400, content={"error": f"Unknown node: {src}"})
        if dst not in _state["nodes"]:
            return JSONResponse(status_code=400, content={"error": f"Unknown node: {dst}"})

    # Stop existing tracer
    if _continuous_tracer is not None:
        await _continuous_tracer.stop()
        _continuous_tracer = None

    # Load trace context
    try:
        tracer = _create_continuous_tracer()
    except Exception as exc:
        log.warning("Failed to create continuous tracer: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Tracer init failed: {exc}"})

    _continuous_tracer = tracer
    await tracer.start(src, dst)
    return {"ok": True, "src": src, "dst": dst}


@app.post("/api/v1/trace/stop", dependencies=[Depends(_require_api_key)])
async def stop_continuous_trace() -> dict:
    """Stop continuous path tracing."""
    global _continuous_tracer
    if _continuous_tracer is not None:
        await _continuous_tracer.stop()
        _continuous_tracer = None
    return {"ok": True}


@app.get("/api/v1/trace/status", dependencies=[Depends(_require_api_key)])
def get_trace_status() -> dict:
    """Return current continuous trace status."""
    if _continuous_tracer is None or not _continuous_tracer.active:
        return {"active": False, "src": None, "dst": None, "result": None}

    result = _continuous_tracer.latest_result
    return {
        "active": True,
        "src": _continuous_tracer.src,
        "dst": _continuous_tracer.dst,
        "result": result.model_dump(mode="json") if result else None,
    }


def _create_continuous_tracer() -> ContinuousTracer:
    """Create a ContinuousTracer from the current session context."""
    cfg = get_platform_config()

    # Load session context
    node_registry: dict = {}
    interface_map: dict = {}
    pid_map: dict = {}
    timeline_path: str | None = None
    trace_mode = "ip"

    log.info(
        "Creating continuous tracer: session_file=%s exists=%s",
        _session_file,
        Path(_session_file).exists() if _session_file else False,
    )
    if _session_file and Path(_session_file).exists():
        # Ensure NodalPath config is initialized (load_session_context needs SID ranges)
        try:
            from nodalpath.platform import get_nodalpath_config

            get_nodalpath_config()
            log.info("NodalPath config already initialized")
        except RuntimeError:
            try:
                from nodalpath.platform import init_nodalpath_config

                init_nodalpath_config(Path("configs/nodalpath.yaml"))
                log.info("Initialized NodalPath config from configs/nodalpath.yaml")
            except Exception as exc:
                log.error("Failed to init NodalPath config: %s", exc)
        try:
            from nodalpath.orchestrator.session_loader import load_session_context

            ctx = load_session_context(Path(_session_file))
            node_registry = ctx[0]
            interface_map = ctx[1]
            log.info(
                "Loaded session context: %d nodes, %d interfaces",
                len(node_registry),
                len(interface_map),
            )
        except Exception as exc:
            log.error("Failed to load session context: %s", exc, exc_info=True)

        # Read pid_map.json
        if _session_manager and _session_manager._current_data_dir:
            pid_path = Path(_session_manager._current_data_dir) / "pid_map.json"
            if pid_path.exists():
                try:
                    pid_map = json.loads(pid_path.read_text())
                except Exception as exc:
                    log.warning("Failed to read pid_map.json: %s", exc)

            # Read timeline path from session-state.json
            state_path = Path(_session_manager._current_data_dir) / "session-state.json"
            if state_path.exists():
                try:
                    state_data = json.loads(state_path.read_text())
                    timeline_path = state_data.get("timeline")
                except Exception as exc:
                    log.warning("Failed to read session-state.json: %s", exc)

        # Determine trace mode from routing stack
        if _routing_stack:
            if "isis-sr" in _routing_stack or "static-sr" in _routing_stack:
                trace_mode = "sr-uniform"
            elif _routing_stack.startswith("nodalpath"):
                trace_mode = "cspf"

    return ContinuousTracer(
        node_registry=node_registry,
        interface_map=interface_map,
        pid_map=pid_map,
        trace_mode=trace_mode,
        config=cfg,
        timeline_path=timeline_path,
        get_sim_time=_get_sim_time_str,
        on_path_change=_on_path_change,
    )


@app.post(
    "/api/v1/playback", dependencies=[Depends(_require_api_key), Depends(_rate_limit_playback)]
)
def playback_control(body: dict) -> Any:
    """Relay playback command to dispatcher via NATS request/reply."""
    import asyncio

    from nodalarc.nats_channels import SUBJECT_PLAYBACK_CONTROL

    action = body.get("action", "")
    if action not in ("pause", "resume", "set_speed", "get_status"):
        return JSONResponse(status_code=400, content={"error": "Unknown action"})

    async def _request():
        nc = _nats_connection
        if nc is None:
            return None
        resp = await nc.request(SUBJECT_PLAYBACK_CONTROL, json.dumps(body).encode(), timeout=5)
        return json.loads(resp.data)

    try:
        loop = asyncio.get_running_loop()
        result = loop.run_until_complete(_request())
    except RuntimeError:
        result = asyncio.run(_request())
    except Exception:
        return JSONResponse(status_code=504, content={"error": "Dispatcher timeout"})

    if result is None:
        return JSONResponse(status_code=503, content={"error": "NATS not connected"})

    global _playback_paused, _playback_speed
    if "paused" in result:
        _playback_paused = result["paused"]
    if "speed" in result:
        _playback_speed = result["speed"]
    return result


@app.get("/api/v1/sessions", dependencies=[Depends(_require_api_key)])
def list_sessions() -> list[dict]:
    """List available sessions with active flag."""
    if _session_manager is None:
        return []
    return _session_manager.list_sessions()


@app.post(
    "/api/v1/sessions/switch",
    response_model=None,
    dependencies=[Depends(_require_api_key), Depends(_rate_limit_session_switch)],
)
async def switch_session(body: dict):
    """Trigger async session switch. Returns immediately."""
    if _session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not initialized"})
    if _session_manager.status == "switching":
        return JSONResponse(status_code=409, content={"error": "Switch already in progress"})
    session_path = body.get("session", "")
    if not session_path:
        return JSONResponse(status_code=400, content={"error": "session field required"})
    # Rescan session directory so newly added YAML files are recognized
    _session_manager.rescan()
    valid_files = _session_manager._valid_session_files()
    if session_path not in valid_files:
        return JSONResponse(status_code=400, content={"error": "Unknown session file"})
    asyncio.create_task(_run_switch(session_path))
    return {"status": "switching"}


# --- Wizard API endpoints ---


@app.get("/api/v1/presets/constellations", dependencies=[Depends(_require_api_key)])
def list_constellation_presets() -> list[dict]:
    """Return available constellation presets for the wizard."""
    from nodalarc.session_generator import load_constellation_presets

    presets = load_constellation_presets()
    return [
        {
            "name": p.name,
            "description": p.description,
            "satellite_count": p.satellite_count,
            "constellation": p.constellation,
            "ground_stations": p.ground_stations,
        }
        for p in presets.values()
    ]


@app.get("/api/v1/presets/satellite-types", dependencies=[Depends(_require_api_key)])
def list_satellite_types() -> list[dict]:
    """Return available satellite type presets for the wizard."""
    from nodalarc.models.satellite_type import SatelliteTypeConfig

    sat_types_dir = Path("configs/satellite-types")
    results: list[dict] = []
    if not sat_types_dir.is_dir():
        return results
    for yaml_path in sorted(sat_types_dir.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text())
        data = raw.get("satellite_type", raw)
        cfg = SatelliteTypeConfig.model_validate(data)
        results.append(
            {
                "name": cfg.name,
                "description": cfg.description,
                "isl_terminals": [
                    {
                        "type": t.type,
                        "band": t.band,
                        "count": t.count,
                        "role": t.role,
                        "max_range_km": t.max_range_km,
                        "bandwidth_mbps": t.bandwidth_mbps,
                        "max_tracking_rate_deg_s": t.max_tracking_rate_deg_s,
                        "field_of_regard_deg": t.field_of_regard_deg,
                    }
                    for t in cfg.isl_terminals
                ],
                "ground_terminals": [
                    {
                        "type": t.type,
                        "band": t.band,
                        "count": t.count,
                        "bandwidth_mbps": t.bandwidth_mbps,
                    }
                    for t in cfg.ground_terminals
                ],
            }
        )
    return results


@app.get("/api/v1/presets/ground-stations", dependencies=[Depends(_require_api_key)])
def list_ground_station_sets() -> list[dict]:
    """Return available ground station sets for the wizard."""
    gs_sets_dir = Path("configs/ground-stations/sets")
    results: list[dict] = []
    if not gs_sets_dir.is_dir():
        return results
    for yaml_path in sorted(gs_sets_dir.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text())
        gs_data = raw.get("ground_station_set", raw)
        results.append(
            {
                "name": gs_data.get("name", yaml_path.stem),
                "description": gs_data.get("description", ""),
                "stations": gs_data.get("stations", []),
                "file": f"configs/ground-stations/sets/{yaml_path.name}",
            }
        )
    return results


@app.get("/api/v1/presets/ground-stations/stations", dependencies=[Depends(_require_api_key)])
def list_individual_stations() -> list[dict]:
    """Return all available individual ground stations for custom set building."""
    stations_dir = Path("configs/ground-stations/stations")
    results: list[dict] = []
    if not stations_dir.is_dir():
        return results
    for yaml_path in sorted(stations_dir.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text())
        gs = raw.get("ground_station", raw)
        results.append(
            {
                "name": gs.get("name", yaml_path.stem),
                "lat_deg": gs.get("lat_deg", 0),
                "lon_deg": gs.get("lon_deg", 0),
            }
        )
    return results


@app.get("/api/v1/wizard/extensions", dependencies=[Depends(_require_api_key)])
def wizard_extension_rules() -> dict:
    """Return protocol-extension compatibility rules for client-side validation."""
    return {
        "protocols": {
            "ospf": {"extensions": ["sr", "te", "mpls"], "constraints": {"mpls": ["te"]}},
            "isis": {"extensions": ["sr", "te", "mpls"], "constraints": {"mpls": ["te"]}},
            "nodalpath": {"extensions": [], "constraints": {}},
        },
        "area_strategies": ["flat", "stripe", "per-plane"],
    }


@app.post("/api/v1/session/generate", dependencies=[Depends(_require_api_key)])
def generate_session(body: dict) -> dict:
    """Generate a session YAML from wizard selections."""
    from nodalarc.session_generator import generate_session_yaml

    constellation = body.get("constellation", "")
    protocol = body.get("protocol", "")
    extensions = body.get("extensions", [])
    area_strategy = body.get("area_strategy", "flat")
    ground_stations = body.get("ground_stations")
    satellite_type = body.get("satellite_type")
    custom_constellation = body.get("custom_constellation")
    custom_ground_stations = body.get("custom_ground_stations")
    if not constellation or not protocol:
        return JSONResponse(
            status_code=400, content={"error": "constellation and protocol are required"}
        )
    try:
        yaml_str, warnings = generate_session_yaml(
            constellation=constellation,
            protocol=protocol,
            extensions=extensions,
            area_strategy=area_strategy,
            ground_stations=ground_stations,
            satellite_type=satellite_type,
            custom_constellation=custom_constellation,
            custom_ground_stations=custom_ground_stations,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return {"yaml": yaml_str, "warnings": warnings}


@app.post("/api/v1/session/preview-coverage", dependencies=[Depends(_require_api_key)])
async def preview_coverage(body: dict) -> dict:
    """Run OME coverage preview for the given combination.

    Accepts constellation (name or inline dict), satellite_type (name),
    and ground_stations (set name, station list, or inline dict).
    Computes visibility at 10-second steps for one orbital period.
    Returns ISL/GS coverage statistics and warnings.
    """
    from ome.coverage_preview import compute_coverage_preview

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            compute_coverage_preview,
            body.get("constellation"),
            body.get("satellite_type"),
            body.get("ground_stations"),
        )
    except Exception as exc:
        log.warning("Coverage preview failed: %s", exc)
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return result.model_dump()


@app.post("/api/v1/session/deploy", dependencies=[Depends(_require_api_key)])
async def deploy_generated_session(body: dict) -> dict:
    """Validate YAML, write to sessions dir, and trigger deploy."""
    import yaml as _yaml

    yaml_str = body.get("yaml", "")
    if not yaml_str:
        return JSONResponse(status_code=400, content={"error": "yaml field required"})
    try:
        raw = _yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": f"Invalid session YAML: {exc}"})

    # Write to sessions directory with _wizard- prefix
    from pathlib import Path

    sessions_dir = Path("configs/sessions")
    session_file = sessions_dir / f"_wizard-{session.session.name}.yaml"
    session_file.write_text(yaml_str)

    # Rescan and deploy
    if _session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not initialized"})
    if _session_manager.status == "switching":
        return JSONResponse(status_code=409, content={"error": "Switch already in progress"})
    _session_manager.rescan()
    asyncio.create_task(_run_switch(str(session_file)))
    return {"status": "switching", "session_file": str(session_file)}


@app.post("/api/v1/session/deploy-from-yaml", dependencies=[Depends(_require_api_key)])
async def deploy_from_yaml(body: dict) -> dict:
    """Deploy a self-contained session YAML (e.g., downloaded from a previous session).

    Extracts inline constellation/ground-station definitions, writes
    ephemeral files, and deploys identically to the wizard path.
    """
    from pathlib import Path

    import yaml as _yaml

    yaml_str = body.get("yaml", "")
    if not yaml_str:
        return JSONResponse(status_code=400, content={"error": "yaml field required"})
    try:
        raw = _yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": f"Invalid session YAML: {exc}"})

    # Extract inline definitions → write ephemeral files → rewrite as paths
    session_name = session.session.name
    modified = dict(raw)

    if isinstance(session.constellation, dict):
        eph_dir = Path("configs/constellations/_ephemeral")
        eph_dir.mkdir(parents=True, exist_ok=True)
        eph_path = eph_dir / f"{session_name}.yaml"
        eph_path.write_text(_yaml.dump(session.constellation, default_flow_style=False))
        modified["constellation"] = str(eph_path)

    if isinstance(session.ground_stations, dict):
        eph_dir = Path("configs/ground-stations/_ephemeral")
        eph_dir.mkdir(parents=True, exist_ok=True)
        eph_path = eph_dir / f"{session_name}.yaml"
        eph_path.write_text(_yaml.dump(session.ground_stations, default_flow_style=False))
        modified["ground_stations"] = str(eph_path)

    # Write modified session YAML (inline defs replaced with ephemeral file paths)
    sessions_dir = Path("configs/sessions")
    session_file = sessions_dir / f"_wizard-{session_name}.yaml"
    session_file.write_text(_yaml.dump(modified, default_flow_style=False))

    if _session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not initialized"})
    if _session_manager.status == "switching":
        return JSONResponse(status_code=409, content={"error": "Switch already in progress"})
    _session_manager.rescan()
    asyncio.create_task(_run_switch(str(session_file)))
    return {"status": "switching", "session_file": str(session_file)}


@app.get(
    "/api/v1/introspect/commands",
    dependencies=[Depends(_require_api_key), Depends(_rate_limit_introspect)],
)
def introspect_commands() -> list[str]:
    """Return sorted list of whitelisted vtysh commands."""
    return sorted(VTYSH_COMMANDS)


@app.post(
    "/api/v1/introspect", dependencies=[Depends(_require_api_key), Depends(_rate_limit_introspect)]
)
def introspect(body: dict) -> dict:
    """Run a whitelisted vtysh command on a node's FRR container."""
    node_id = body.get("node_id", "")
    command = body.get("command", "")
    if not node_id:
        return JSONResponse(status_code=400, content={"error": "node_id is required"})
    if command not in VTYSH_COMMANDS:
        return JSONResponse(status_code=400, content={"error": f"Command not allowed: {command}"})
    try:
        result = run_vtysh(node_id, command)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    if result.get("error") == "Command timed out":
        return JSONResponse(status_code=504, content=result)
    return result


async def _run_switch(session_path: str) -> None:
    """Run session switch in thread executor (blocking subprocess calls)."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            _session_manager.switch,
            session_path,
            _clear_state,
            _update_session_globals,
        )
    except Exception as exc:
        # Safety net: reset status if switch() raised without setting it
        if _session_manager and _session_manager.status == "switching":
            _session_manager._status = "error"
            _session_manager._status_detail = f"Unhandled: {exc}"
            log.error("_run_switch safety net caught: %s", exc)


def main() -> None:
    import uvicorn

    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)

    # Use API key from environment if set; otherwise auto-generate one
    global _API_KEY
    if not _API_KEY:
        _API_KEY = secrets.token_urlsafe(32)
        print(f"Generated API key: {_API_KEY}", file=sys.stderr)
        log.info("Auto-generated API key (set NODAL_API_KEY to use a fixed key)")
    else:
        log.info("Using API key from NODAL_API_KEY environment variable")

    parser = argparse.ArgumentParser(description="VS-API server")
    parser.add_argument("--session", default=None, help="Path to session YAML (optional)")
    parser.add_argument("--db", default=None, help="Path to SQLite database (optional)")
    parser.add_argument("--port", type=int, default=None, help="HTTP port")
    parser.add_argument(
        "--sessions-dir", default="configs/sessions", help="Directory with session YAMLs"
    )
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    args = parser.parse_args()

    from nodalarc.platform import init_platform_config

    init_platform_config(Path(args.platform_config))

    # Also init NodalPath config (needed for live gRPC trace SID lookups)
    try:
        from nodalpath.platform import init_nodalpath_config

        init_nodalpath_config(Path("configs/nodalpath.yaml"))
    except Exception:
        pass  # Non-fatal — CSPF fallback still works

    if args.port is None:
        args.port = get_platform_config().vs_api_http_port

    global \
        _db_path, \
        _session_file, \
        _routing_stack, \
        _constellation_name, \
        _session_manager, \
        _gs_elevation_map, \
        _beam_falloff_exponent

    # Initialize SessionManager
    _session_manager = SessionManager(args.sessions_dir, initial_db_path=args.db)

    if args.session and args.db:
        _db_path = args.db
        _session_file = args.session

        # Load session metadata for snapshot enrichment
        # Handle missing/empty ConfigMap mount (Operator creates session later)
        session_path = Path(args.session)
        if not session_path.is_file():
            log.info(f"Session config not found at {args.session} — starting in idle mode")
            session_data = None
        else:
            session_data = yaml.safe_load(session_path.read_text())
        if session_data:
            session = SessionConfig.model_validate(session_data)
            if session.routing.stack is not None:
                _routing_stack = Path(session.routing.stack).name
            else:
                ext_str = (
                    "-".join(session.routing.extensions) if session.routing.extensions else "plain"
                )
                _routing_stack = f"{session.routing.protocol}-{ext_str}"
            if isinstance(session.constellation, dict):
                _constellation_name = session.constellation.get("name", "custom")
            else:
                _constellation_name = Path(session.constellation).stem
            _gs_elevation_map = _load_gs_elevation_map(session)
            _beam_falloff_exponent = _load_beam_falloff_exponent(session)
            # Mark session active
            _active_path = args.session
            try:
                _state_mount = Path("/etc/nodalarc/state/session-state.json")
                if _state_mount.exists():
                    _ss = json.loads(_state_mount.read_text())
                    if _ss.get("session_config"):
                        _active_path = _ss["session_config"]
            except Exception:
                pass
            _session_manager.set_active(_active_path)
            # Resolve mounted path to matching session file in scanned list
            _loaded_name = session_data.get("session", {}).get("name", "")
            if _loaded_name:
                for _s in _session_manager._available:
                    if _s.get("name") == _loaded_name:
                        _session_manager.set_active(_s["file"])
                        break
            _session_manager._status = "ready"
        else:
            log.info("No session loaded — VS-API starting in idle mode")
            # Check if Operator has an active session (CR with phase Ready/Wiring)
            try:
                import kubernetes.client
                import kubernetes.config

                try:
                    kubernetes.config.load_incluster_config()
                except kubernetes.config.ConfigException:
                    kubernetes.config.load_kube_config()
                api = kubernetes.client.CustomObjectsApi()
                cr = api.get_namespaced_custom_object(
                    group="nodalarc.io",
                    version="v1alpha1",
                    namespace=get_platform_config().kubernetes_namespace,
                    plural="constellationspecs",
                    name="current-session",
                )
                phase = cr.get("status", {}).get("phase", "")
                if phase in ("Ready", "Wiring", "Creating"):
                    log.info(
                        f"Active ConstellationSpec CR found (phase={phase}) — setting status to ready"
                    )
                    _session_manager._status = "ready"
                    # Try to match session name from mounted config
                    _sp = Path(args.session)
                    if _sp.is_file():
                        _sd = yaml.safe_load(_sp.read_text())
                        _sn = _sd.get("session", {}).get("name", "") if _sd else ""
                        if _sn:
                            for _s in _session_manager._available:
                                if _s.get("name") == _sn:
                                    _session_manager.set_active(_s["file"])
                                    break
            except Exception:
                pass  # No CR exists — stay idle

        # Ensure tables exist
        conn = sqlite3.connect(args.db)
        create_tables(conn)
        conn.close()
        log.info(f"Started with explicit session: {args.session}")
    else:
        # No explicit session — try to recover a running session
        recovered = _session_manager.recover_session()
        if recovered:
            new_db_path = recovered.get("db_path", "")
            session_config = recovered.get("session_config", "")
            if new_db_path and session_config and Path(session_config).exists():
                _update_session_globals(session_config, new_db_path)
                # Pre-populate in-memory state from last DB snapshot
                _restore_state_from_db(new_db_path)
                log.info(f"Recovered session: {recovered.get('session_id')} (db={new_db_path})")
            else:
                log.warning(
                    f"Found live session {recovered.get('session_id')} "
                    f"but session config or db missing"
                )
        else:
            log.info("No running session found — starting idle")

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
