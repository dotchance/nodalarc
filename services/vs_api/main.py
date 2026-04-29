# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""VS-API — Visualization State API server.

FastAPI server with WebSocket (full snapshots at ~1Hz) and REST endpoints.
Subscribes to NATS JetStream topics from OME and link state to maintain state.

Run: python -m vs_api.main --session <path> --db <sqlite_path> --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import secrets
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncssh
import httpx
import nats
import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from nodal.logging import configure as _configure_logging
from nodal.logging import connect as _connect_logging
from nodalarc.db.queries import (
    insert_snapshot,
    query_convergence_events,
    query_link_events,
    query_nearest_snapshot,
    query_probe_results,
)
from nodalarc.db.schema import create_tables
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
    STREAM_OPS_EVENTS,
    nats_url,
)
from nodalarc.platform_config import get_platform_config

from vs_api.continuous_tracer import ContinuousTracer
from vs_api.introspect import VTYSH_COMMANDS, run_vtysh
from vs_api.session_context import SessionContext, _link_key
from vs_api.session_manager import SessionManager
from vs_api.terminal import TerminalManager

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
        from nodalarc.platform_config import get_platform_config

        cfg = get_platform_config()
        r = cfg.vs_api_introspect_max_requests_per_minute
        _rate_introspect = _TokenBucket(rate=r / 60, burst=r)
    return _rate_introspect


def _get_rate_playback() -> _TokenBucket:
    global _rate_playback
    if _rate_playback is None:
        from nodalarc.platform_config import get_platform_config

        cfg = get_platform_config()
        r = cfg.vs_api_playback_max_requests_per_minute
        _rate_playback = _TokenBucket(rate=r / 60, burst=r)
    return _rate_playback


def _get_rate_session_switch() -> _TokenBucket:
    global _rate_session_switch
    if _rate_session_switch is None:
        from nodalarc.platform_config import get_platform_config

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


# --- Session state: owned by SessionContext ---
# All per-session state lives in _active_context. Module-level session
# globals are DELETED — any reference to the old names (_nodes, _links,
# _almanac, etc.) raises NameError at import time, forcing migration.
_active_context: SessionContext | None = None

# --- Platform state: global, outlives any session ---
_session_manager: SessionManager | None = None
_nats_connection: nats.NATS | None = None
_main_event_loop: asyncio.AbstractEventLoop | None = None
_terminal_manager = TerminalManager()
_initial_session_file: str = ""  # Set by main() before uvicorn starts

# System OpsEvents — meta-session, not cleared on switch
from collections import deque

_system_ops_events: deque = deque(maxlen=500)


async def _publish_system_ops_event(
    level: str, code: str, message: str, details: dict | None = None
) -> None:
    """Buffer a system-scoped OpsEvent for WebSocket delivery and log it.

    The logging system handles NATS publishing automatically via NatsHandler.
    This function buffers the event locally for immediate WebSocket broadcast.
    """
    import socket

    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": "_system",
        "source": "vs-api",
        "hostname": socket.gethostname(),
        "level": level,
        "code": code,
        "message": message,
        "details": details,
    }
    _system_ops_events.append(event)

    log_level = getattr(logging, level.upper(), logging.INFO)
    log.log(log_level, "%s", message, extra={"code": code, "details": details})


def _build_snapshot() -> dict | None:
    """Build a StateSnapshot dict from the active SessionContext.

    Returns None if no active context (mid-transition or no session).
    Takes a local reference to _active_context to prevent mixed-state
    reads if the context is swapped mid-tick.
    """
    ctx = _active_context
    if ctx is None:
        return None

    with ctx.state_lock:
        now = datetime.now(UTC)
        links = list(ctx.links.values())

        _isl_counts: dict[str, int] = {}
        _gnd_counts: dict[str, int] = {}
        for ldata in ctx.links.values():
            a, b = ldata.node_a, ldata.node_b
            is_gnd = ldata.link_type == "ground"
            for nid in (a, b):
                if is_gnd:
                    _gnd_counts[nid] = _gnd_counts.get(nid, 0) + 1
                else:
                    _isl_counts[nid] = _isl_counts.get(nid, 0) + 1
        nodes = []
        for n in ctx.nodes.values():
            isl_c = _isl_counts.get(n.node_id, 0)
            gnd_c = _gnd_counts.get(n.node_id, 0)
            if isl_c != n.isl_count or gnd_c != n.gnd_count:
                nodes.append(n.model_copy(update={"isl_count": isl_c, "gnd_count": gnd_c}))
            else:
                nodes.append(n)
        recent = list(ctx.recent_events)

        ctx.compute_convergence_state()
        health = ctx.network_health

        _traced: list[TracedPath] = []
        if ctx.continuous_tracer is not None and ctx.continuous_tracer.active:
            tp = ctx.continuous_tracer.traced_path
            if tp is not None:
                _traced.append(tp)

        snapshot = StateSnapshot(
            sim_time=datetime.fromisoformat(ctx.sim_time)
            if isinstance(ctx.sim_time, str)
            else ctx.sim_time,
            wall_time=now,
            schema_version=1,
            nodes=nodes,
            links=links,
            traced_paths=_traced,
            active_flows=[],
            recent_events=recent,
            network_health=health,
            routing_stack=ctx.routing_stack,
            constellation_name=ctx.constellation_name,
            session_status=_session_manager.status if _session_manager else None,
            session_status_detail=_session_manager.status_detail if _session_manager else None,
            playback_paused=ctx.playback_paused,
            playback_speed=ctx.playback_speed,
            stale=ctx.is_stale(),
        )
        result = json.loads(snapshot.model_dump_json())
        # System + session OpsEvents merged for the log panel
        all_ops = list(_system_ops_events)[-25:] + list(ctx.session_ops_events)[-25:]
        all_ops.sort(key=lambda e: e.get("timestamp", ""))
        result["ops_events"] = all_ops[-50:]
        return result


# --- NATS subscriber ---

_pending_cr_poll: bool = False
_ws_clients: set = set()  # Active WebSocket connections for instant broadcast


async def _broadcast_to_all(frame: str) -> None:
    """Push a text frame to all connected WebSocket clients."""
    for ws in list(_ws_clients):
        with contextlib.suppress(Exception):
            await ws.send_text(frame)


async def _nats_subscriber() -> None:
    """NATS connection manager and initial SessionContext bootstrap.

    Creates the shared NATS connection, waits for session config, then
    creates and starts the initial SessionContext. All session-scoped
    subscriptions are owned by the context, not this function.

    Also subscribes to wiring progress (core NATS, not session-scoped).
    """
    global _nats_connection, _active_context, _main_event_loop

    _main_event_loop = asyncio.get_running_loop()
    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    _nats_connection = nc
    await _connect_logging(nc)
    log.info("VS-API NATS connected to %s", nats_url())

    # If main() detected a CR in Wiring/Creating phase, start polling
    global _pending_cr_poll
    if _pending_cr_poll:
        _pending_cr_poll = False
        asyncio.ensure_future(_poll_cr_until_ready())

    # Wiring progress — core NATS (not session-scoped, not JetStream)
    async def _on_wiring_progress(msg):
        try:
            data = json.loads(msg.data)
            progress_msg = data.get("message", "")
            if _session_manager and progress_msg:
                _session_manager.status_detail = progress_msg
                frame = json.dumps({"msg_type": "wiring_progress", "message": progress_msg})
                for ws in list(_ws_clients):
                    with contextlib.suppress(Exception):
                        await ws.send_text(frame)
        except Exception:
            pass

    try:
        await nc.subscribe("nodalarc.agent.progress.*", cb=_on_wiring_progress)
    except Exception as exc:
        log.warning("Wiring progress subscription failed: %s", exc)

    # System OpsEvents — global, not session-scoped
    async def _on_system_ops_event(msg):
        with contextlib.suppress(Exception):
            _system_ops_events.append(json.loads(msg.data))

    try:
        js = nc.jetstream()
        from nats.js.api import DeliverPolicy

        await js.subscribe(
            "nodalarc.ops.system.>",
            stream=STREAM_OPS_EVENTS,
            ordered_consumer=True,
            deliver_policy=DeliverPolicy.NEW,
            cb=_on_system_ops_event,
        )
    except Exception as exc:
        log.warning("System OpsEvent subscription failed: %s", exc)

    # Wait for session config, then create initial SessionContext
    session_config_path = _initial_session_file
    if session_config_path:
        config_path = Path(session_config_path)
        while not config_path.is_file():
            log.info("NATS subscriber: waiting for session config at %s", config_path)
            await asyncio.sleep(5)

        try:
            from nodalarc.nats_channels import sanitize_session_id

            raw = yaml.safe_load(config_path.read_text())
            if not raw:
                log.error("FATAL: Session config is empty: %s", config_path)
                raise ValueError("Session config is empty")
            session = SessionConfig.model_validate(raw)
            session_id = sanitize_session_id(session.session.name)
        except Exception as exc:
            log.error("FATAL: Failed to read session_id from config: %s", exc)
            raise

        ctx = SessionContext(session_id, str(config_path))
        await ctx.start(nc, mode="recovery")
        _active_context = ctx
        log.info("Initial SessionContext started: session_id=%s", session_id)
        await _publish_system_ops_event(
            "info",
            "SESSION_BOOTSTRAP",
            f"VS-API started with session {session_id}",
            {"session_id": session_id, "mode": "recovery"},
        )

        asyncio.ensure_future(_poll_cr_until_ready())

    # Keep alive until cancelled
    try:
        while True:
            await asyncio.sleep(30)
            ctx = _active_context
            if ctx:
                log.info(
                    "NATS status: session_id=%s ready=%s stale=%s links=%d",
                    ctx.session_id,
                    ctx.is_ready(),
                    ctx.is_stale(),
                    len(ctx.links),
                )
    except asyncio.CancelledError:
        log.info("NATS subscriber cancelled")
    finally:
        if _active_context:
            await _active_context.stop()
            _active_context = None
        await nc.close()


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


@app.get("/api/v1/ops/events", dependencies=[Depends(_require_api_key)])
async def get_ops_events(
    source: str = Query("", description="Filter by event source (e.g. 'operator', 'scheduler')"),
    level: str = Query("", description="Filter by level (e.g. 'error', 'warning')"),
    limit: int = Query(100, ge=1, le=500, description="Max events to return"),
) -> list[dict]:
    """Return recent operational events from the NODALARC_OPS stream."""
    ctx = _active_context
    session_events = list(ctx.session_ops_events) if ctx else []
    events = list(_system_ops_events) + session_events
    if source:
        events = [e for e in events if e.get("source") == source]
    if level:
        events = [e for e in events if e.get("level") == level]
    return events[-limit:]


def _restore_state_from_db(db_path: str) -> bool:
    """Load the most recent snapshot from SQLite into the active context.

    Returns True if state was restored, False otherwise.
    """
    ctx = _active_context
    if ctx is None:
        return False
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

        with ctx.state_lock:
            for node in snapshot.get("nodes", []):
                node_id = node.get("node_id")
                if not node_id:
                    log.error("Corrupt DB snapshot — node missing node_id: %s", node)
                    raise ValueError("DB snapshot node missing node_id")
                ctx.nodes[node_id] = NodeState(**node)

            for link in snapshot.get("links", []):
                na = link.get("node_a")
                nb = link.get("node_b")
                if not na or not nb:
                    log.error("Corrupt DB snapshot — link missing node_a/node_b: %s", link)
                    raise ValueError("DB snapshot link missing node_a or node_b")
                key = _link_key(na, nb)
                ctx.links[key] = LinkState(**link)

            ctx.recent_events.clear()
            for e in snapshot.get("recent_events", []):
                sim_time_raw = e.get("sim_time")
                if sim_time_raw is None:
                    log.error("Corrupt DB snapshot — event missing sim_time: %s", e)
                    raise ValueError("DB snapshot event missing sim_time")
                sim_time_dt = (
                    datetime.fromisoformat(sim_time_raw)
                    if isinstance(sim_time_raw, str)
                    else sim_time_raw
                )
                ctx.recent_events.append(
                    RecentEvent(
                        sim_time=sim_time_dt,
                        node_id=e["node_id"],
                        event_type=e["event_type"],
                        summary=e["summary"],
                    )
                )

            if "network_health" in snapshot:
                nh = snapshot["network_health"]
                ctx.network_health = NetworkHealth(
                    status=nh["status"],
                    converging_since_ms=nh.get("converging_since_ms"),
                    unreachable_flows=nh["unreachable_flows"],
                    last_convergence_ms=nh.get("last_convergence_ms"),
                )

            if "sim_time" in snapshot:
                ctx.sim_time = snapshot["sim_time"]

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
        ctx = _active_context
        if tick % 100 == 0 and ctx and ctx.db_path:
            try:
                snapshot = _build_snapshot()
                if snapshot is None:
                    continue
                conn = sqlite3.connect(ctx.db_path)
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
    _ws_clients.add(websocket)
    _audit_log.info(f"WS_CONNECT ip={ws_ip}")

    try:
        # Send SessionEphemeris as first message if available (PRD v0.71)
        ctx = _active_context
        if ctx and ctx.cached_ephemeris:
            await websocket.send_json(ctx.cached_ephemeris)

        while True:
            snapshot = _build_snapshot()
            if snapshot is None:
                await asyncio.sleep(1.0)
                continue
            await websocket.send_json(snapshot)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning(f"WS send error for {ws_ip}: {type(exc).__name__} {exc}")
    finally:
        _ws_clients.discard(websocket)
        _audit_log.info(f"WS_DISCONNECT ip={ws_ip}")


@app.websocket("/ws/v1/terminal/{node_id}")
async def ws_terminal(websocket: WebSocket, node_id: str) -> None:
    """Persistent interactive terminal to a constellation node via SSH.

    Opens an SSH connection to the target pod's dropbear daemon and
    bidirectionally pipes data between the browser's WebSocket and the
    SSH channel. The user lands in vtysh — full FRR CLI access.

    Message protocol (JSON over WebSocket):
      Browser → VS-API: {"type": "input", "data": "show ip route\\n"}
      Browser → VS-API: {"type": "resize", "cols": 120, "rows": 40}
      VS-API → Browser: {"type": "output", "data": "Codes: K - kernel..."}
    """
    from vs_api.terminal import TerminalSession, _load_ssh_key, resolve_pod_ip

    ws_ip = websocket.client.host if websocket.client else "unknown"
    if _API_KEY:
        token = websocket.query_params.get("token", "")
        if token != _API_KEY:
            _audit_log.warning(f"WS_TERMINAL_AUTH_FAIL ip={ws_ip} node={node_id}")
            await websocket.close(code=4401, reason="Unauthorized")
            return

    # Resolve node_id to pod IP (async — runs K8s API call in thread executor
    # so it doesn't block active SSH sessions on the event loop)
    namespace = get_platform_config().kubernetes_namespace
    pod_ip = await resolve_pod_ip(node_id, namespace)
    if not pod_ip:
        await websocket.close(code=4404, reason="Node not found")
        return

    # Load SSH key (cached in memory after first call — never written to disk)
    try:
        ssh_key = _load_ssh_key(namespace)
    except RuntimeError as e:
        log.warning("Terminal key error: %s", e)
        await websocket.close(code=4503, reason=str(e))
        return

    await websocket.accept()
    _audit_log.info(f"WS_TERMINAL_CONNECT ip={ws_ip} node={node_id} pod_ip={pod_ip}")

    session = TerminalSession(pod_ip, ssh_key)
    try:
        await session.connect()
        _term_conn_id = await _terminal_manager.register(node_id, session, websocket)

        async def ws_to_ssh():
            """Forward browser input to SSH session."""
            try:
                async for msg in websocket.iter_text():
                    data = json.loads(msg)
                    msg_type = data.get("type", "")
                    if msg_type == "input":
                        await session.send(data.get("data", ""))
                    elif msg_type == "resize":
                        await session.resize(data.get("cols", 80), data.get("rows", 24))
            except WebSocketDisconnect:
                pass

        async def ssh_to_ws():
            """Forward SSH output to browser."""
            try:
                while True:
                    output = await session.read_output()
                    if output is None:
                        await asyncio.sleep(0.05)
                        continue
                    await websocket.send_json({"type": "output", "data": output})
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass

        # Run both directions concurrently; when either exits, cancel the other
        done, pending = await asyncio.wait(
            [asyncio.create_task(ws_to_ssh()), asyncio.create_task(ssh_to_ws())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except asyncssh.misc.DisconnectError as e:
        log.warning("SSH disconnect for %s: %s", node_id, e)
    except Exception as exc:
        log.warning("Terminal session error for %s: %s", node_id, exc)
    finally:
        await _terminal_manager.unregister(_term_conn_id)
        await session.close()
        _audit_log.info(f"WS_TERMINAL_DISCONNECT ip={ws_ip} node={node_id}")


@app.get(
    "/api/v1/nodes/{node_id}/config",
    dependencies=[Depends(_require_api_key)],
)
async def get_node_config(node_id: str) -> Response:
    """Download the running FRR configuration from a constellation node.

    Opens a temporary SSH session, runs 'show running-config', returns
    the output as a downloadable text file.
    """
    from vs_api.terminal import TerminalSession, _load_ssh_key, resolve_pod_ip

    namespace = get_platform_config().kubernetes_namespace
    pod_ip = await resolve_pod_ip(node_id, namespace)
    if not pod_ip:
        return JSONResponse(status_code=404, content={"error": "Node not found"})

    try:
        ssh_key = _load_ssh_key(namespace)
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})

    session = TerminalSession(pod_ip, ssh_key)
    try:
        await session.connect()
        config_text = await session.run_command("show running-config")
        return Response(
            content=config_text,
            media_type="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="{node_id}.conf"',
            },
        )
    except Exception as exc:
        log.warning("Config export error for %s: %s", node_id, exc)
        return JSONResponse(status_code=500, content={"error": f"Failed to retrieve config: {exc}"})
    finally:
        await session.close()


@app.get("/api/v1/state", dependencies=[Depends(_require_api_key)])
def get_state() -> dict:
    """Current state snapshot."""
    return _build_snapshot()


_NODALPATH_TIMEOUT = 1.0


def _nodalpath_base_url() -> str:
    from nodalarc.platform_config import get_platform_config

    cfg = get_platform_config()
    host = cfg.service_host("nodalpath")
    port = cfg.nodalpath_console_http_port
    return f"http://{host}:{port}"


async def _fetch_nodalpath_status() -> dict | None:
    """Fetch the NodalPath console status snapshot.

    Returns the parsed JSON dict on success, or None if NodalPath is not reachable.
    Intentionally silent on connection errors — callers handle the None case.
    """
    try:
        async with httpx.AsyncClient(timeout=_NODALPATH_TIMEOUT) as client:
            r = await client.get(f"{_nodalpath_base_url()}/api/status")
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
        "recent_pushes": raw["push_history"][:5],
        "recent_deviations": raw["deviation_history"][:5],
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
                f"{_nodalpath_base_url()}/api/v1/path",
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
    ctx = _active_context
    if not ctx or not ctx.db_path:
        return {"error": "No database configured"}
    conn = sqlite3.connect(ctx.db_path)
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
    ctx = _active_context
    if not ctx or not ctx.db_path:
        return []
    conn = sqlite3.connect(ctx.db_path)
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
    ctx = _active_context
    if not ctx or not ctx.db_path:
        return []
    conn = sqlite3.connect(ctx.db_path)
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
    ctx = _active_context
    if not ctx or not ctx.db_path:
        return []
    conn = sqlite3.connect(ctx.db_path)
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
    from nodalarc.platform_config import get_platform_config

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
        _ctx = _active_context
        session_path = _Path(_ctx.session_file) if _ctx else None
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
            sats = [n for n in nodes if n.get("node_type") == "satellite"]
            max_slot = max(n["slot"] for n in sats) if sats else 0
            spp = max_slot + 1
            for n in sats:
                plane = n["plane"]
                slot = n["slot"]
                sid = np_cfg.satellite_sid_range_start + (plane * spp + slot) + 1
                sid_to_node[sid] = n["node_id"]
            gs_names = sorted(n["node_id"] for n in nodes if n.get("node_type") == "ground_station")
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

    _rctx = _active_context
    _rs = _rctx.routing_stack if _rctx else None
    if not _rs or not _rs.startswith("nodalpath"):
        raise HTTPException(
            status_code=400,
            detail=f"Trace not available for routing stack '{_rs}'. "
            "Trace requires a NodalPath session (MPLS forwarding tables).",
        )

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
        np_resp = httpx.get(
            f"{_nodalpath_base_url()}/api/v1/path",
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
    ctx = _active_context
    if ctx is None:
        return datetime.now(UTC).isoformat()
    with ctx.state_lock:
        return ctx.sim_time


def _on_path_change(src: str, dst: str, old_hops: list[str], new_hops: list[str]) -> None:
    """Callback when the traced path changes — add a RecentEvent."""
    ctx = _active_context
    if ctx is None:
        return
    sim_time = _get_sim_time_str()
    old_str = " -> ".join(old_hops[:4])
    new_str = " -> ".join(new_hops[:4])
    if len(old_hops) > 4:
        old_str += f" ({len(old_hops)} hops)"
    if len(new_hops) > 4:
        new_str += f" ({len(new_hops)} hops)"
    ctx._add_recent_event(
        {
            "sim_time": sim_time,
            "node_id": src,
            "reason": f"Path {src} -> {dst}: {old_str} => {new_str}",
        },
        "PATH_CHANGE",
    )


@app.post("/api/v1/trace/start", dependencies=[Depends(_require_api_key)])
async def start_continuous_trace(body: dict) -> dict:
    """Start continuous path tracing between two nodes."""
    ctx = _active_context
    if ctx is None:
        return JSONResponse(status_code=409, content={"error": "No active session"})

    src = body.get("src_node", "")
    dst = body.get("dst_node", "")
    if not src or not dst:
        return JSONResponse(status_code=400, content={"error": "src_node and dst_node required"})

    with ctx.state_lock:
        if src not in ctx.nodes:
            return JSONResponse(status_code=400, content={"error": f"Unknown node: {src}"})
        if dst not in ctx.nodes:
            return JSONResponse(status_code=400, content={"error": f"Unknown node: {dst}"})

    if ctx.continuous_tracer is not None:
        await ctx.continuous_tracer.stop()
        ctx.continuous_tracer = None

    # Load trace context
    try:
        tracer = _create_continuous_tracer()
    except Exception as exc:
        log.warning("Failed to create continuous tracer: %s", exc)
        return JSONResponse(status_code=500, content={"error": f"Tracer init failed: {exc}"})

    ctx.continuous_tracer = tracer
    await tracer.start(src, dst)
    return {"ok": True, "src": src, "dst": dst}


@app.post("/api/v1/trace/stop", dependencies=[Depends(_require_api_key)])
async def stop_continuous_trace() -> dict:
    """Stop continuous path tracing."""
    ctx = _active_context
    if ctx is not None and ctx.continuous_tracer is not None:
        await ctx.continuous_tracer.stop()
        ctx.continuous_tracer = None
    return {"ok": True}


@app.get("/api/v1/trace/status", dependencies=[Depends(_require_api_key)])
def get_trace_status() -> dict:
    """Return current continuous trace status."""
    ctx = _active_context
    if ctx is None or ctx.continuous_tracer is None or not ctx.continuous_tracer.active:
        return {"active": False, "src": None, "dst": None, "result": None}

    result = ctx.continuous_tracer.latest_result
    return {
        "active": True,
        "src": ctx.continuous_tracer.src,
        "dst": ctx.continuous_tracer.dst,
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

    _tctx = _active_context
    _sf = _tctx.session_file if _tctx else ""
    log.info(
        "Creating continuous tracer: session_file=%s exists=%s",
        _sf,
        Path(_sf).exists() if _sf else False,
    )
    if _sf and Path(_sf).exists():
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

            ctx = load_session_context(Path(_sf))
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

        _rsctx = _active_context
        _rs2 = _rsctx.routing_stack if _rsctx else None
        if _rs2:
            if "isis-sr" in _rs2 or "static-sr" in _rs2:
                trace_mode = "sr-uniform"
            elif _rs2.startswith("nodalpath"):
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
    if action not in ("pause", "resume", "set_speed", "get_status", "seek"):
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

    ctx = _active_context
    if ctx is not None:
        if "paused" in result:
            ctx.playback_paused = result["paused"]
        if "speed" in result:
            ctx.playback_speed = result["speed"]
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
    routing_config = body.get("routing_config")
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
            routing_config=routing_config,
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
    """Run session switch — linear async chain on the main event loop.

    All pointer writes to _active_context happen here, on the main
    event loop. No executor threads, no run_coroutine_threadsafe.

    Flow:
    a. Null _active_context (broadcast loop stops)
    b. Push session_transitioning
    c. Close all terminal SSH sessions
    d. Stop old context
    e. SessionManager deploys new CR (async, polls with asyncio.sleep)
    f. Create new SessionContext, start subscriptions
    g. Wait for is_ready() with timeout
    h. Set _active_context = new context
    i. Push ephemeris + session_ready
    """
    global _active_context

    try:
        old_ctx = _active_context
        old_session = old_ctx.session_id if old_ctx else None

        await _publish_system_ops_event(
            "info",
            "SESSION_SWITCH_INITIATED",
            f"Session switch initiated: {old_session} → {Path(session_path).stem}",
            {"old_session": old_session, "new_session_path": session_path},
        )

        # (a) Null context — broadcast loop stops immediately
        _active_context = None

        # (b) Notify VF
        await _broadcast_to_all(json.dumps({"msg_type": "session_transitioning"}))

        # (c) Close all terminal SSH sessions
        await _terminal_manager.close_all("Session switched")

        # (d) Stop old context
        if old_ctx is not None:
            await old_ctx.stop()

        await _publish_system_ops_event(
            "info",
            "SESSION_TEARDOWN_COMPLETE",
            f"Old session {old_session} torn down",
        )

        # (e) Deploy new session via K8s CR (fully async)
        await _session_manager.switch(session_path)

        # (f) Create new SessionContext
        from nodalarc.nats_channels import sanitize_session_id

        session_data = yaml.safe_load(Path(session_path).read_text())
        session = SessionConfig.model_validate(session_data)
        session_id = sanitize_session_id(session.session.name)

        if _nats_connection is None:
            log.error("FATAL: No NATS connection for new SessionContext")
            raise RuntimeError("No NATS connection available")

        new_ctx = SessionContext(session_id, session_path)
        await new_ctx.start(_nats_connection, mode="switch")

        # (g) Wait for first live snapshot
        try:
            await asyncio.wait_for(new_ctx._ready.wait(), timeout=30.0)
        except TimeoutError:
            log.error("Session switch timeout — new context not ready after 30s")
            await _publish_system_ops_event(
                "error",
                "SESSION_SWITCH_TIMEOUT",
                "New session did not become ready within 30s",
                {"session_path": session_path},
            )
            await new_ctx.stop()
            await _broadcast_to_all(
                json.dumps(
                    {
                        "msg_type": "session_failed",
                        "error": "New session did not become ready within 30s",
                    }
                )
            )
            if _session_manager:
                _session_manager._status = "error"
                _session_manager.status_detail = "Session switch timeout"
            return

        # (h) Atomic pointer swap — only place _active_context is written
        _active_context = new_ctx
        from nodal.logging import set_session as _set_log_session

        _set_log_session(session_id)
        _session_manager._status = "ready"
        _session_manager.status_detail = ""

        await _publish_system_ops_event(
            "info",
            "SESSION_SWITCH_COMPLETE",
            f"Session switch complete: now running {session_id}",
            {"session_id": session_id, "links": len(new_ctx.links)},
        )

        # (i) Push ephemeris + session_ready to VF
        if new_ctx.cached_ephemeris:
            await _broadcast_to_all(json.dumps(new_ctx.cached_ephemeris))
        snapshot = _build_snapshot()
        await _broadcast_to_all(
            json.dumps(
                {
                    "msg_type": "session_ready",
                    "snapshot": snapshot,
                }
            )
        )

        log.info("Session switch complete — %s active", session_id)

    except Exception as exc:
        if _session_manager and _session_manager.status == "switching":
            _session_manager._status = "error"
            _session_manager.status_detail = f"Unhandled: {exc}"
            log.error("_run_switch caught: %s", exc)
        await _publish_system_ops_event(
            "error",
            "SESSION_SWITCH_FAILED",
            f"Session switch failed: {exc}",
            {"session_path": session_path, "error": str(exc)},
        )
        await _broadcast_to_all(
            json.dumps(
                {
                    "msg_type": "session_failed",
                    "error": str(exc),
                }
            )
        )


async def _poll_cr_until_ready() -> None:
    """Poll ConstellationSpec CR until Ready, updating session status_detail.

    Runs as a background task when VS-API starts and finds a CR in
    Wiring/Creating phase (i.e., the session was deployed via make session
    or kubectl apply, not through the wizard). Mirrors the polling in
    session_manager.switch() so the frontend sees progress messages
    regardless of deploy path.
    """
    global _active_context
    log.info("_poll_cr_until_ready: starting background CR polling task")
    import kubernetes.client
    import kubernetes.config

    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()
    api = kubernetes.client.CustomObjectsApi()
    ns = get_platform_config().kubernetes_namespace

    for _ in range(600):  # 10 minutes max
        await asyncio.sleep(1)
        try:
            cr = api.get_namespaced_custom_object(
                group="nodalarc.io",
                version="v1alpha1",
                namespace=ns,
                plural="constellationspecs",
                name="current-session",
            )
            phase = cr.get("status", {}).get("phase", "")
            message = cr.get("status", {}).get("message", "")
            # Try to load session_id on each tick — the ConfigMap appears
            if _session_manager and phase != "Wiring":
                # During Wiring, Node Agent NATS progress owns _status_detail.
                # Only update from CR for non-Wiring phases.
                _session_manager.status_detail = message or f"Phase: {phase}"
            if phase == "Ready":
                # Check if the session changed (make session deployed a different constellation)
                cr_session_yaml = cr.get("spec", {}).get("sessionYaml", "")
                if cr_session_yaml:
                    try:
                        from nodalarc.nats_channels import sanitize_session_id as _sanitize

                        cr_session = SessionConfig.model_validate(yaml.safe_load(cr_session_yaml))
                        cr_session_id = _sanitize(cr_session.session.name)
                        ctx = _active_context
                        if ctx and ctx.session_id != cr_session_id:
                            log.info(
                                "CR session_id changed: %s → %s — triggering internal switch",
                                ctx.session_id,
                                cr_session_id,
                            )
                            if _initial_session_file and Path(_initial_session_file).is_file():
                                await _run_switch(_initial_session_file)
                                return
                        elif not ctx:
                            if _initial_session_file and Path(_initial_session_file).is_file():
                                log.info("No active context but CR is Ready — bootstrapping")
                                new_ctx = SessionContext(cr_session_id, _initial_session_file)
                                nc = _nats_connection
                                if nc:
                                    await new_ctx.start(nc, mode="recovery")
                                    _active_context = new_ctx
                    except Exception as exc:
                        log.warning("Failed to check CR session_id: %s", exc)

                ctx = _active_context
                if ctx:
                    ctx.session_ready_time = _time.monotonic()
                if _session_manager:
                    _session_manager._status = "ready"
                    _session_manager.status_detail = ""
                log.info("CR reached Ready — session is now operational")
                return
            if phase == "Error":
                if _session_manager:
                    _session_manager._status = "error"
                    _session_manager.status_detail = message or "Operator reported error"
                log.error("CR reached Error during wiring: %s", message)
                return
        except Exception as exc:
            log.warning("_poll_cr_until_ready: %s", exc)

    if _session_manager:
        _session_manager._status = "error"
        _session_manager.status_detail = "Wiring timed out (10 minutes)"
    log.error("_poll_cr_until_ready timed out")


def main() -> None:
    import uvicorn

    _configure_logging("nodal.arc.vs_api", nats_level=logging.WARNING)

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

    from nodalarc.platform_config import init_platform_config

    init_platform_config(Path(args.platform_config))

    # Also init NodalPath config (needed for live gRPC trace SID lookups)
    try:
        from nodalpath.platform import init_nodalpath_config

        init_nodalpath_config(Path("configs/nodalpath.yaml"))
    except Exception:
        pass  # Non-fatal — CSPF fallback still works

    if args.port is None:
        args.port = get_platform_config().vs_api_http_port

    global _session_manager, _initial_session_file, _pending_cr_poll

    _session_manager = SessionManager(args.sessions_dir, initial_db_path=args.db)

    if args.session and args.db:
        _initial_session_file = args.session

        session_path = Path(args.session)
        if not session_path.is_file():
            log.info(f"Session config not found at {args.session} — starting in idle mode")
            session_data = None
        else:
            session_data = yaml.safe_load(session_path.read_text())
        if session_data:
            session = SessionConfig.model_validate(session_data)
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
            # Check CR phase — don't claim ready if data plane is still wiring
            _cr_phase = ""
            _cr_message = ""
            try:
                import kubernetes.client as _k8s_client
                import kubernetes.config as _k8s_config

                try:
                    _k8s_config.load_incluster_config()
                except _k8s_config.ConfigException:
                    _k8s_config.load_kube_config()
                _cr_api = _k8s_client.CustomObjectsApi()
                _cr = _cr_api.get_namespaced_custom_object(
                    group="nodalarc.io",
                    version="v1alpha1",
                    namespace=get_platform_config().kubernetes_namespace,
                    plural="constellationspecs",
                    name="current-session",
                )
                _cr_phase = _cr.get("status", {}).get("phase", "")
                _cr_message = _cr.get("status", {}).get("message", "")
            except Exception:
                pass  # CR may not exist yet — treat as ready

            if _cr_phase in ("Wiring", "Creating"):
                log.info(f"Session config loaded but CR phase={_cr_phase} — wiring in progress")
                _session_manager._status = "wiring"
                _session_manager.status_detail = _cr_message or f"Phase: {_cr_phase}"
                _pending_cr_poll = True
            else:
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
                message = cr.get("status", {}).get("message", "")
                if phase == "Ready":
                    log.info("Active ConstellationSpec CR found (phase=Ready)")
                    _session_manager._status = "ready"
                elif phase in ("Wiring", "Creating"):
                    log.info(
                        f"Active ConstellationSpec CR found (phase={phase}) — wiring in progress"
                    )
                    _session_manager._status = "wiring"
                    _session_manager.status_detail = message or f"Phase: {phase}"
                    _pending_cr_poll = True
                # Try to match session name from mounted config
                if phase in ("Ready", "Wiring", "Creating"):
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
                _initial_session_file = session_config
                log.info(
                    f"Recovered session: {recovered.get('session_id')} (config={session_config})"
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
