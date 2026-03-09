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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
import zmq
import zmq.asyncio
from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from nodalarc.constants import LOG_FORMAT
from nodalarc.db.queries import (
    insert_snapshot,
    query_adapter_events,
    query_convergence_events,
    query_link_events,
    query_nearest_snapshot,
    query_probe_results,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.events import PositionEvent
from nodalarc.models.link_events import LatencyUpdate, LinkDown, LinkUp
from nodalarc.models.metrics import AdapterEvent, ConvergenceResult, ProbeResult
from nodalarc.models.session import SessionConfig
from nodalarc.models.vs_api import (
    ActiveFlow,
    LinkState,
    NetworkHealth,
    NodeState,
    RecentEvent,
    StateSnapshot,
    TracedPath,
)
from nodalarc.models.metrics import TraceRequest, TraceResponse
from nodalarc.zmq_channels import (
    MI_EVENTS_CONNECT,
    MI_TRACE_CONNECT,
    OME_EVENTS_CONNECT,
    PLAYBACK_CONTROL_CONNECT,
    TO_EVENTS_CONNECT,
    decode_message,
    TOPIC_ADAPTER_EVENT,
    TOPIC_CONVERGENCE_RESULT,
    TOPIC_LATENCY_UPDATE,
    TOPIC_LINK_DOWN,
    TOPIC_LINK_UP,
    TOPIC_POSITION_EVENT,
    TOPIC_PROBE_RESULT,
)
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

_MAX_WS_CONNECTIONS = 50


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


# Rate limiters: rate = tokens/sec, burst = max tokens
_rate_introspect = _TokenBucket(rate=10 / 60, burst=10)     # 10 req/min
_rate_playback = _TokenBucket(rate=30 / 60, burst=30)       # 30 req/min
_rate_session_switch = _TokenBucket(rate=5 / 60, burst=5)   # 5 req/min


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
    _check_rate(_rate_introspect, request)


def _rate_limit_playback(request: Request) -> None:
    _check_rate(_rate_playback, request)


def _rate_limit_session_switch(request: Request) -> None:
    _check_rate(_rate_session_switch, request)


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
    "sim_time": datetime.now(timezone.utc).isoformat(),
}
_state_lock = threading.Lock()
_db_path: str = ""
_routing_stack: str | None = None
_constellation_name: str | None = None
_ws_clients: list[WebSocket] = []
_ws_lock = asyncio.Lock()
_session_manager: SessionManager | None = None
_gs_elevation_map: dict[str, float] = {}  # node_id -> min_elevation_deg
_playback_paused: bool = False
_playback_speed: float = 1.0


def _update_position(event_data: dict) -> None:
    """Update node positions from PositionEvent."""
    with _state_lock:
        _state["sim_time"] = event_data.get("sim_time", _state["sim_time"])
        for node in event_data.get("positions", []):
            node_id = node.get("node_id", "")
            if not node_id:
                continue
            node_dict = {
                "node_id": node_id,
                "node_type": node.get("node_type", "satellite"),
                "lat_deg": node.get("lat_deg", 0.0),
                "lon_deg": node.get("lon_deg", 0.0),
                "alt_km": node.get("alt_km", 0.0),
                "vel_x_km_s": node.get("vel_x_km_s"),
                "vel_y_km_s": node.get("vel_y_km_s"),
                "vel_z_km_s": node.get("vel_z_km_s"),
                "plane": node.get("plane"),
                "slot": node.get("slot"),
                "routing_area": node.get("routing_area"),
                "neighbor_count": node.get("neighbor_count", 0),
                "isl_count": node.get("isl_count", 0),
                "gnd_count": node.get("gnd_count", 0),
                "prefix": node.get("prefix"),
            }
            if node_id in _gs_elevation_map:
                node_dict["min_elevation_deg"] = _gs_elevation_map[node_id]
            _state["nodes"][node_id] = node_dict


def _update_link_up(event_data: dict) -> None:
    """Update link state on LinkUp."""
    key = _link_key(event_data.get("node_a", ""), event_data.get("node_b", ""))
    with _state_lock:
        _state["links"][key] = {
            "node_a": event_data.get("node_a", ""),
            "node_b": event_data.get("node_b", ""),
            "state": "active",
            "link_type": event_data.get("reason", ""),
            "link_reason": event_data.get("reason", ""),
            "latency_ms": event_data.get("latency_ms", 0.0),
            "bandwidth_mbps": event_data.get("bandwidth_mbps", 0.0),
            "range_km": 0.0,
            "traffic_load_pct": None,
        }


def _update_link_down(event_data: dict) -> None:
    """Remove link state on LinkDown."""
    key = _link_key(event_data.get("node_a", ""), event_data.get("node_b", ""))
    with _state_lock:
        _state["links"].pop(key, None)


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
        _state["recent_events"].append({
            "sim_time": event_data.get("sim_time", datetime.now(timezone.utc).isoformat()),
            "node_id": event_data.get("node_id", event_data.get("node_a", "")),
            "event_type": event_type,
            "summary": event_data.get("detail", event_data.get("reason", event_type)),
        })
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


def _build_snapshot() -> dict:
    """Build a StateSnapshot dict from current state."""
    with _state_lock:
        now = datetime.now(timezone.utc)
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
            patched = {**n, "isl_count": _isl_counts.get(n["node_id"], 0),
                       "gnd_count": _gnd_counts.get(n["node_id"], 0)}
            nodes.append(NodeState(**patched))
        recent = [RecentEvent(
            sim_time=datetime.fromisoformat(e["sim_time"]) if isinstance(e["sim_time"], str) else e["sim_time"],
            node_id=e["node_id"],
            event_type=e["event_type"],
            summary=e["summary"],
        ) for e in _state["recent_events"]]
        health = NetworkHealth(**_state["network_health"])

        snapshot = StateSnapshot(
            sim_time=datetime.fromisoformat(_state["sim_time"]) if isinstance(_state["sim_time"], str) else _state["sim_time"],
            wall_time=now,
            schema_version=1,
            nodes=nodes,
            links=links,
            traced_paths=[],
            active_flows=[],
            recent_events=recent,
            network_health=health,
            routing_stack=_routing_stack,
            constellation_name=_constellation_name,
            session_status=_session_manager.status if _session_manager else None,
            session_status_detail=_session_manager.status_detail if _session_manager else None,
            playback_paused=_playback_paused,
            playback_speed=_playback_speed,
        )
        return json.loads(snapshot.model_dump_json())


# --- ZMQ subscriber (asyncio per PRD 13.2) ---

_zmq_ctx: zmq.asyncio.Context | None = None


async def _zmq_subscriber() -> None:
    """Async ZMQ subscriber: subscribe to all ZMQ PUB sockets."""
    global _zmq_ctx
    _zmq_ctx = zmq.asyncio.Context()

    ome_sub = _zmq_ctx.socket(zmq.SUB)
    ome_sub.connect(OME_EVENTS_CONNECT)
    ome_sub.setsockopt(zmq.SUBSCRIBE, b"")

    to_sub = _zmq_ctx.socket(zmq.SUB)
    to_sub.connect(TO_EVENTS_CONNECT)
    to_sub.setsockopt(zmq.SUBSCRIBE, b"")

    mi_sub = _zmq_ctx.socket(zmq.SUB)
    mi_sub.connect(MI_EVENTS_CONNECT)
    mi_sub.setsockopt(zmq.SUBSCRIBE, b"")

    poller = zmq.asyncio.Poller()
    poller.register(ome_sub, zmq.POLLIN)
    poller.register(to_sub, zmq.POLLIN)
    poller.register(mi_sub, zmq.POLLIN)

    log.info(
        "VS-API ZMQ subscriber started (asyncio) — connecting to "
        f"OME={OME_EVENTS_CONNECT} TO={TO_EVENTS_CONNECT} MI={MI_EVENTS_CONNECT}"
    )

    msg_count = 0
    last_status_time = _time.monotonic()
    try:
        while True:
            try:
                socks = dict(await poller.poll(timeout=100))
            except zmq.ZMQError as e:
                log.error(f"ZMQ poller error: {e}")
                break

            # Periodic status log (every 30s)
            now_mono = _time.monotonic()
            if now_mono - last_status_time >= 30:
                log.info(f"ZMQ subscriber status: {msg_count} messages received so far")
                last_status_time = now_mono

            for sock in [ome_sub, to_sub, mi_sub]:
                if sock not in socks:
                    continue
                raw = await sock.recv(zmq.NOBLOCK)
                try:
                    topic, payload = decode_message(raw)
                    data = json.loads(payload)
                    msg_count += 1

                    if msg_count <= 5 or msg_count % 100 == 0:
                        log.info(
                            f"ZMQ message #{msg_count}: topic={topic} "
                            f"payload_bytes={len(payload)}"
                        )

                    if topic == TOPIC_POSITION_EVENT:
                        _update_position(data)
                    elif topic == TOPIC_LINK_UP:
                        _update_link_up(data)
                        _add_recent_event(data, "link_up")
                    elif topic == TOPIC_LINK_DOWN:
                        _update_link_down(data)
                        _add_recent_event(data, "link_down")
                    elif topic == TOPIC_LATENCY_UPDATE:
                        _update_latency(data)
                    elif topic == TOPIC_CONVERGENCE_RESULT:
                        _update_convergence(data)
                        _add_recent_event(data, "convergence")
                    elif topic == TOPIC_ADAPTER_EVENT:
                        _add_recent_event(data, data.get("event_type", "adapter"))
                    elif topic == TOPIC_PROBE_RESULT:
                        pass  # Probe results don't update snapshot state directly

                except Exception as exc:
                    log.warning(f"ZMQ message processing error: {exc}")
    except asyncio.CancelledError:
        log.info(f"ZMQ subscriber cancelled after {msg_count} messages")
    except Exception as exc:
        log.error(f"ZMQ subscriber crashed: {exc}", exc_info=True)
    finally:
        log.info(f"ZMQ subscriber exiting (total messages: {msg_count})")
        ome_sub.close()
        to_sub.close()
        mi_sub.close()


# --- FastAPI app ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start ZMQ subscriber and WebSocket broadcaster on startup."""
    # Start ZMQ subscriber as asyncio task (PRD 13.2)
    sub_task = asyncio.create_task(_zmq_subscriber())

    def _on_subscriber_done(task: asyncio.Task) -> None:
        exc = task.exception() if not task.cancelled() else None
        if exc:
            log.error(f"ZMQ subscriber task DIED with exception: {exc}", exc_info=exc)
        elif task.cancelled():
            log.info("ZMQ subscriber task cancelled")
        else:
            log.warning("ZMQ subscriber task exited unexpectedly")

    sub_task.add_done_callback(_on_subscriber_done)

    # Start WebSocket broadcaster
    broadcast_task = asyncio.create_task(_ws_broadcaster())

    yield

    sub_task.cancel()
    broadcast_task.cancel()
    if _zmq_ctx is not None:
        _zmq_ctx.term()


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
        _state["sim_time"] = datetime.now(timezone.utc).isoformat()


def _load_gs_elevation_map(session: SessionConfig) -> dict[str, float]:
    """Load per-station min_elevation_deg from ground station config."""
    from ome.constellation_loader import load_ground_stations
    gs_path = Path(session.ground_stations)
    if not gs_path.exists():
        return {}
    gs_file = load_ground_stations(gs_path)
    gs_id_tpl = session.addressing.gs_id_template
    result: dict[str, float] = {}
    for station in gs_file.stations:
        node_id = gs_id_tpl.format(name=station.name)
        elev = station.min_elevation_deg if station.min_elevation_deg is not None else gs_file.default_min_elevation_deg
        result[node_id] = elev
    return result


def _update_session_globals(session_path: str, new_db_path: str) -> None:
    """Reload routing_stack, constellation_name, and db_path from new session."""
    global _routing_stack, _constellation_name, _db_path, _gs_elevation_map
    session_data = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(session_data)
    _routing_stack = Path(session.routing.stack).name
    _constellation_name = Path(session.constellation).stem
    _db_path = new_db_path
    _gs_elevation_map = _load_gs_elevation_map(session)

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
    """Broadcast StateSnapshot to WebSocket clients at ~10Hz.

    Also records snapshots to SQLite every ~10 seconds for historical playback.
    """
    tick = 0
    while True:
        await asyncio.sleep(0.1)
        snapshot = _build_snapshot()

        # Store snapshot every 100 ticks (~10 seconds) for historical playback
        tick += 1
        if tick % 100 == 0 and _db_path:
            try:
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

        async with _ws_lock:
            dead = []
            for ws in _ws_clients:
                try:
                    await ws.send_json(snapshot)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _ws_clients.remove(ws)


@app.websocket("/ws/v1/state")
async def ws_state(websocket: WebSocket) -> None:
    """WebSocket endpoint — full state snapshots at ~1Hz."""
    # Authenticate via ?token= query parameter when API key is set
    ws_ip = websocket.client.host if websocket.client else "unknown"
    if _API_KEY:
        token = websocket.query_params.get("token", "")
        if token != _API_KEY:
            _audit_log.warning(f"WS_AUTH_FAIL ip={ws_ip}")
            await websocket.close(code=4401, reason="Unauthorized")
            return
    # Enforce connection limit
    async with _ws_lock:
        if len(_ws_clients) >= _MAX_WS_CONNECTIONS:
            await websocket.close(code=1013, reason="Too many connections")
            log.warning(f"WebSocket rejected: {len(_ws_clients)} connections at limit")
            return
    await websocket.accept()
    async with _ws_lock:
        _ws_clients.append(websocket)
    _audit_log.info(f"WS_CONNECT ip={ws_ip} active={len(_ws_clients)}")

    # Send initial snapshot immediately
    try:
        snapshot = _build_snapshot()
        await websocket.send_json(snapshot)
    except Exception:
        pass

    try:
        while True:
            # Keep connection alive — client sends nothing
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            if websocket in _ws_clients:
                _ws_clients.remove(websocket)
        _audit_log.info(f"WS_DISCONNECT ip={ws_ip} active={len(_ws_clients)}")


@app.get("/api/v1/state", dependencies=[Depends(_require_api_key)])
def get_state() -> dict:
    """Current state snapshot."""
    return _build_snapshot()


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


@app.post("/api/v1/trace", dependencies=[Depends(_require_api_key)])
def trace_path(body: dict) -> dict:
    """Request path trace via ZMQ REQ to MI trace endpoint."""
    src = body.get("src_node", "")
    dst = body.get("dst_node", "")
    if not src or not dst:
        return {"hops": [], "error": "src_node and dst_node required"}

    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 10000)  # 10s timeout
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(MI_TRACE_CONNECT)

    try:
        req = TraceRequest(src_node=src, dst_node=dst)
        sock.send(req.model_dump_json().encode())
        raw = sock.recv()
        resp = TraceResponse.model_validate_json(raw)
        result = {"hops": resp.hops, "success": resp.success}
        if resp.error:
            result["error"] = resp.error
        return result
    except zmq.Again:
        return {"hops": [], "error": "MI trace endpoint timeout"}
    except Exception as exc:
        return {"hops": [], "error": str(exc)}
    finally:
        sock.close()
        ctx.term()


@app.post("/api/v1/playback", dependencies=[Depends(_require_api_key), Depends(_rate_limit_playback)])
def playback_control(body: dict) -> Any:
    """Relay playback command to dispatcher via ZMQ."""
    action = body.get("action", "")
    if action not in ("pause", "resume", "set_speed", "get_status"):
        return JSONResponse(status_code=400, content={"error": "Unknown action"})

    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 5000)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(PLAYBACK_CONTROL_CONNECT)

    try:
        sock.send(json.dumps(body).encode())
        raw = sock.recv()
        result = json.loads(raw)
        # Track playback state for inclusion in snapshots
        global _playback_paused, _playback_speed
        if "paused" in result:
            _playback_paused = result["paused"]
        if "speed" in result:
            _playback_speed = result["speed"]
        return result
    except zmq.Again:
        return JSONResponse(status_code=504, content={"error": "Dispatcher timeout"})
    finally:
        sock.close()
        ctx.term()


@app.get("/api/v1/sessions", dependencies=[Depends(_require_api_key)])
def list_sessions() -> list[dict]:
    """List available sessions with active flag."""
    if _session_manager is None:
        return []
    return _session_manager.list_sessions()


@app.post("/api/v1/sessions/switch", response_model=None, dependencies=[Depends(_require_api_key), Depends(_rate_limit_session_switch)])
async def switch_session(body: dict):
    """Trigger async session switch. Returns immediately."""
    if _session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not initialized"})
    if _session_manager.status == "switching":
        return JSONResponse(status_code=409, content={"error": "Switch already in progress"})
    session_path = body.get("session", "")
    if not session_path:
        return JSONResponse(status_code=400, content={"error": "session field required"})
    valid_files = _session_manager._valid_session_files()
    if session_path not in valid_files:
        return JSONResponse(status_code=400, content={"error": "Unknown session file"})
    asyncio.create_task(_run_switch(session_path))
    return {"status": "switching"}


@app.get("/api/v1/introspect/commands", dependencies=[Depends(_require_api_key), Depends(_rate_limit_introspect)])
def introspect_commands() -> list[str]:
    """Return sorted list of whitelisted vtysh commands."""
    return sorted(VTYSH_COMMANDS)


@app.post("/api/v1/introspect", dependencies=[Depends(_require_api_key), Depends(_rate_limit_introspect)])
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
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _session_manager.switch,
        session_path,
        _clear_state,
        _update_session_globals,
    )


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
    from nodalarc.zmq_channels import VS_API_HTTP_PORT
    parser.add_argument("--port", type=int, default=VS_API_HTTP_PORT, help="HTTP port")
    parser.add_argument("--sessions-dir", default="configs/sessions", help="Directory with session YAMLs")
    args = parser.parse_args()

    global _db_path, _routing_stack, _constellation_name, _session_manager, _gs_elevation_map

    # Initialize SessionManager
    _session_manager = SessionManager(args.sessions_dir, initial_db_path=args.db)

    if args.session and args.db:
        _db_path = args.db

        # Load session metadata for snapshot enrichment
        session_data = yaml.safe_load(Path(args.session).read_text())
        session = SessionConfig.model_validate(session_data)
        _routing_stack = Path(session.routing.stack).name
        _constellation_name = Path(session.constellation).stem
        _gs_elevation_map = _load_gs_elevation_map(session)
        _session_manager.set_active(args.session)
        _session_manager._status = "ready"

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
                log.info(
                    f"Recovered session: {recovered.get('session_id')} "
                    f"(db={new_db_path})"
                )
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
