# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from nodalarc.catalog_paths import (
    CatalogPathError,
    CatalogRoots,
    config_value_for,
    generated_file_path,
    generated_file_stem,
    resolve_constellation_reference,
    resolve_site_set_reference,
    validate_station_names,
    write_text_exclusive,
)
from nodalarc.db.queries import (
    insert_snapshot,
    query_convergence_events,
    query_link_events,
    query_nearest_snapshot,
    query_probe_results,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.catalog import validate_catalog_document
from nodalarc.models.resolved_session import ResolvedSession, SourceContext
from nodalarc.models.scheduler_ops import OperatorRepairCommand
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
    STREAM_DEBUG_EVENTS,
    STREAM_OPS_EVENTS,
    debug_ctrl_subject,
    nats_url,
    sanitize_session_id,
    scheduler_repair_subject,
)
from nodalarc.platform_config import get_platform_config
from nodalarc.project_info import project_attribution, project_version
from nodalarc.resolve_session import resolve_session_with_assets
from yaml import YAMLError

from vs_api.continuous_tracer import ContinuousTracer
from vs_api.introspect import VTYSH_COMMANDS, run_vtysh
from vs_api.session_context import SessionContext, _link_key
from vs_api.session_manager import SessionManager
from vs_api.terminal import TerminalManager

log = logging.getLogger(__name__)

_CATALOG_ROOTS = CatalogRoots.from_catalog_root(Path("catalog/nodalarc"))


def _generated_sessions_dir() -> Path:
    """Return the runtime write root for wizard/upload sessions."""
    return Path(get_platform_config().session_data_root) / "generated-sessions"


def _catalog_ref_for_path(path: Path) -> str:
    rel = path.resolve(strict=True).relative_to(_CATALOG_ROOTS.root.resolve(strict=True))
    return "nodalarc:" + rel.as_posix()


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
_active_cr_generation: int | None = None
_cr_monitor_task: asyncio.Task | None = None
_session_transition_lock = asyncio.Lock()

_CR_MONITOR_INTERVAL_SECONDS = 2.0
_CR_CONTEXT_READY_TIMEOUT_SECONDS = 60.0

# System OpsEvents — meta-session, not cleared on switch
from collections import deque

_system_ops_events: deque = deque(maxlen=500)

# On-demand debug — state managed by WebSocket command handlers
_debug_sources: set[str] = set()
_debug_clients: dict[int, set[str]] = {}
_debug_sub: object | None = None
_debug_events: deque = deque(maxlen=500)

_KNOWN_DEBUG_SOURCES = ("ome", "scheduler", "node_agent", "operator", "vs_api")


async def _enable_debug_source(source: str) -> bool:
    """Enable debug for a service type via NATS request/reply.

    Returns True on success. On failure, publishes an ERROR event
    to the log panel and returns False.
    """
    global _debug_sub

    if source not in _KNOWN_DEBUG_SOURCES:
        log.error("Unknown debug source: %s", source)
        return False

    nc = _nats_connection
    if nc is None:
        log.error("Cannot enable debug for %s: no NATS connection", source)
        return False

    subject = debug_ctrl_subject(source)
    payload = json.dumps({"action": "enable"}).encode()
    try:
        resp = await nc.request(subject, payload, timeout=5.0)
        result = json.loads(resp.data)
        if result.get("status") != "ok":
            error = result.get("error", "unknown")
            log.error("Debug enable failed for %s: %s", source, error)
            await _publish_system_ops_event(
                "error",
                "DEBUG_ENABLE_FAILED",
                f"Failed to enable debug for {source}: {error}",
            )
            return False
    except Exception as exc:
        log.error("Debug enable failed for %s: %s", source, exc)
        await _publish_system_ops_event(
            "error",
            "DEBUG_ENABLE_FAILED",
            f"Failed to enable debug for {source}: {exc}",
        )
        return False

    _debug_sources.add(source)

    if _debug_sub is None:
        try:
            js = nc.jetstream()
            from nats.js.api import DeliverPolicy

            _debug_sub = await js.subscribe(
                "nodalarc.debug.>",
                stream=STREAM_DEBUG_EVENTS,
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
                cb=_on_debug_event,
            )
        except Exception as exc:
            log.error("Failed to subscribe to debug stream: %s", exc)
            await _publish_system_ops_event(
                "error",
                "DEBUG_SUBSCRIBE_FAILED",
                f"Failed to subscribe to NODALARC_DEBUG: {exc}",
            )

    log.info("Debug enabled for %s", source)
    return True


async def _disable_debug_source(source: str) -> None:
    """Disable debug for a service type."""
    global _debug_sub

    nc = _nats_connection
    if nc is None:
        return

    subject = debug_ctrl_subject(source)
    payload = json.dumps({"action": "disable"}).encode()
    try:
        await nc.request(subject, payload, timeout=5.0)
    except Exception as exc:
        log.warning("Debug disable request failed for %s: %s", source, exc)

    _debug_sources.discard(source)

    if not _debug_sources and _debug_sub is not None:
        with contextlib.suppress(Exception):
            await _debug_sub.unsubscribe()
        _debug_sub = None


async def _cleanup_debug_client(ws_id: int) -> None:
    """Clean up debug sources when a WebSocket client disconnects."""
    sources = _debug_clients.pop(ws_id, set())
    for source in sources:
        still_wanted = any(
            source in client_sources
            for cid, client_sources in _debug_clients.items()
            if cid != ws_id
        )
        if not still_wanted:
            await _disable_debug_source(source)


async def _on_debug_event(msg) -> None:
    """Callback for NODALARC_DEBUG stream subscription."""
    with contextlib.suppress(Exception):
        _debug_events.append(json.loads(msg.data))


async def _handle_ws_debug_command(ws_id: int, msg: dict) -> None:
    """Handle debug_stream/debug_stop WebSocket commands."""
    action = msg.get("action")

    if action == "debug_stream":
        sources = msg.get("sources", [])
        if not sources:
            return
        if ws_id not in _debug_clients:
            _debug_clients[ws_id] = set()
        for source in sources:
            if await _enable_debug_source(source):
                _debug_clients[ws_id].add(source)

    elif action == "debug_stop":
        sources = msg.get("sources", [])
        client_sources = _debug_clients.get(ws_id, set())
        for source in sources:
            client_sources.discard(source)
            still_wanted = any(source in cs for cid, cs in _debug_clients.items() if cid != ws_id)
            if not still_wanted:
                await _disable_debug_source(source)

    elif action == "debug_stop_all":
        await _cleanup_debug_client(ws_id)


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


def _is_operator_visible_ops_event(event: dict) -> bool:
    """Return False for routine successful telemetry that does not need UI attention."""
    level = str(event.get("level") or "").lower()
    code = event.get("code")
    message = str(event.get("message") or "")

    if level == "debug":
        return False

    if code == "COMMAND_APPLIED" and level in {"", "info"}:
        return False

    return not (
        code == "DISPATCH_ACTUATOR"
        and level in {"", "info"}
        and message.startswith("Actuation latency op=")
        and " failed=0" in message
    )


def _operator_visible_ops_events(events: list[dict]) -> list[dict]:
    return [event for event in events if _is_operator_visible_ops_event(event)]


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
            session_id=ctx.session_id,
            nodes=nodes,
            links=links,
            kernel_actual_pairs=[[a, b] for (a, b) in sorted(ctx.actual_kernel_pairs())],
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
            actuation_notices=list(ctx.actuation_notices_by_key.values()),
            ome_lifecycle_notices=list(ctx.ome_lifecycle_notices_by_key.values()),
            actuation_health=ctx.build_actuation_health(),
        )
        result = json.loads(snapshot.model_dump_json())
        # System + session OpsEvents merged for the log panel. Routine successful
        # control-loop telemetry stays out of the operator-facing stream.
        all_ops = _operator_visible_ops_events(
            list(_system_ops_events) + list(ctx.session_ops_events)
        )
        all_ops.sort(key=lambda e: e.get("timestamp", ""))
        result["ops_events"] = all_ops[-500:]
        if _debug_sources:
            result["debug_events"] = list(_debug_events)[-100:]
            result["debug_sources"] = sorted(_debug_sources)
        return result


# --- NATS subscriber ---

_pending_cr_poll: bool = False
_ws_clients: set = set()  # Active WebSocket connections for instant broadcast


@dataclass(frozen=True)
class CRSessionIdentity:
    """Authoritative runtime identity from the ConstellationSpec CR."""

    session_id: str
    session_name: str
    session_yaml: str
    session: ResolvedSession
    generation: int


def _as_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_session_yaml(session_yaml: str) -> tuple[str, ResolvedSession]:
    raw = yaml.safe_load(session_yaml)
    if not raw:
        raise ValueError("sessionYaml is empty")
    resolution = resolve_session_with_assets(
        raw,
        catalog_roots=_CATALOG_ROOTS,
        source_context=SourceContext(origin="vs_api.cr"),
    )
    session = resolution.resolved
    return session.session.name, session


def _extract_ready_cr_session(cr: dict[str, Any]) -> CRSessionIdentity | None:
    """Return the CR session only when its Ready state is generation-consistent."""
    return _extract_cr_session(cr, require_ready=True)


def _extract_cr_session(
    cr: dict[str, Any],
    *,
    require_ready: bool,
) -> CRSessionIdentity | None:
    """Return the CR session only when status carries current runtime identity."""

    metadata = cr.get("metadata") or {}
    status = cr.get("status") or {}
    spec = cr.get("spec") or {}

    generation = _as_positive_int(metadata.get("generation"))
    observed_generation = _as_positive_int(status.get("observedGeneration"))
    ready_pods = _as_positive_int(status.get("readyPods"))
    pod_count = _as_positive_int(status.get("podCount"))
    wired_pods = _as_positive_int(status.get("wiredPods"))

    if generation is None or observed_generation != generation:
        return None
    if require_ready:
        if status.get("phase") != "Ready":
            return None
        if pod_count is None or ready_pods != pod_count:
            return None
        if wired_pods != pod_count:
            return None

    session_yaml = str(spec.get("sessionYaml") or "")
    if not session_yaml.strip():
        raise ValueError("Ready ConstellationSpec is missing spec.sessionYaml")

    _display_session_id, session = _parse_session_yaml(session_yaml)
    session_run_id = str(status.get("sessionRunId") or "")
    if not session_run_id:
        if require_ready:
            raise ValueError("Ready ConstellationSpec is missing status.sessionRunId")
        return None
    session_run_id = sanitize_session_id(session_run_id)

    status_name = str(status.get("sessionName") or "")
    if require_ready and not status_name:
        raise ValueError("Ready ConstellationSpec is missing status.sessionName")
    if status_name and status_name != session.session.name:
        raise ValueError(
            "ConstellationSpec status.sessionName does not match spec.session.name "
            f"({status_name!r} != {session.session.name!r})"
        )
    return CRSessionIdentity(
        session_id=session_run_id,
        session_name=session.session.name,
        session_yaml=session_yaml,
        session=session,
        generation=generation,
    )


def _extract_current_cr_session(cr: dict[str, Any]) -> CRSessionIdentity | None:
    """Return any current-generation CR session with a runtime identity."""
    return _extract_cr_session(cr, require_ready=False)


def _cr_status_observes_current_generation(cr: dict[str, Any]) -> bool:
    """Return true when CR status belongs to the current spec generation."""
    metadata = cr.get("metadata") or {}
    status = cr.get("status") or {}
    generation = _as_positive_int(metadata.get("generation"))
    observed_generation = _as_positive_int(status.get("observedGeneration"))
    return generation is not None and observed_generation == generation


def _write_cr_session_file(ready: CRSessionIdentity) -> Path:
    path = Path(f"/tmp/_session-{ready.session_id}.yaml")
    path.write_text(ready.session_yaml, encoding="utf-8")
    return path


def _mark_session_manager_ready(session: ResolvedSession, session_path: Path) -> None:
    if not _session_manager:
        return
    with contextlib.suppress(Exception):
        _session_manager.rescan()
    for available in _session_manager._available:
        if available.get("name") == session.session.name:
            _session_manager.set_active(available["file"])
            break
    else:
        _session_manager.set_active(str(session_path))
    _session_manager._status = "ready"
    _session_manager.status_detail = ""


async def _activate_session_context_from_cr(ready: CRSessionIdentity, source: str) -> None:
    """Replace VS-API state with the authoritative ready CR session."""

    global _active_context, _active_cr_generation

    if _nats_connection is None:
        raise RuntimeError("No NATS connection available for CR session activation")

    old_ctx = _active_context
    if (
        old_ctx is not None
        and old_ctx.session_id == ready.session_id
        and _active_cr_generation == ready.generation
    ):
        session_path = _write_cr_session_file(ready)
        _mark_session_manager_ready(ready.session, session_path)
        return

    old_session = old_ctx.session_id if old_ctx else None
    session_path = _write_cr_session_file(ready)

    await _publish_system_ops_event(
        "info",
        "SESSION_CR_ACTIVATION_INITIATED",
        f"Activating CR session {ready.session_id}",
        {
            "old_session": old_session,
            "new_session": ready.session_id,
            "session_name": ready.session_name,
            "generation": ready.generation,
            "source": source,
        },
    )

    _active_context = None
    _active_cr_generation = None
    await _broadcast_to_all(
        json.dumps(
            {
                "msg_type": "session_transitioning",
                "detail": f"Activating session {ready.session_name}",
            }
        )
    )
    await _terminal_manager.close_all("Session switched")
    if old_ctx is not None:
        await old_ctx.stop()

    new_ctx = SessionContext(ready.session_id, str(session_path))
    await new_ctx.start(_nats_connection, mode="recovery")

    try:
        await asyncio.wait_for(new_ctx._ready.wait(), timeout=_CR_CONTEXT_READY_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        await new_ctx.stop()
        if _session_manager:
            _session_manager._status = "error"
            _session_manager.status_detail = (
                f"VS-API did not receive live state for {ready.session_id}"
            )
        await _publish_system_ops_event(
            "error",
            "SESSION_CR_ACTIVATION_TIMEOUT",
            f"CR session {ready.session_id} did not become ready in VS-API",
            {
                "session_id": ready.session_id,
                "session_name": ready.session_name,
                "generation": ready.generation,
                "timeout_seconds": _CR_CONTEXT_READY_TIMEOUT_SECONDS,
            },
        )
        await _broadcast_to_all(
            json.dumps(
                {
                    "msg_type": "session_failed",
                    "error": f"Session {ready.session_id} did not publish live state",
                }
            )
        )
        raise TimeoutError(f"VS-API context did not become ready for {ready.session_id}") from exc

    _active_context = new_ctx
    _active_cr_generation = ready.generation

    from nodal.logging import set_session as _set_log_session

    _set_log_session(ready.session_id)
    _mark_session_manager_ready(ready.session, session_path)

    await _publish_system_ops_event(
        "info",
        "SESSION_CR_ACTIVATION_COMPLETE",
        f"CR session activation complete: {ready.session_id}",
        {
            "session_id": ready.session_id,
            "session_name": ready.session_name,
            "generation": ready.generation,
            "links": len(new_ctx.links),
            "source": source,
        },
    )

    if new_ctx.cached_ephemeris:
        await _broadcast_to_all(json.dumps(new_ctx.cached_ephemeris))
    await _broadcast_to_all(
        json.dumps({"msg_type": "session_ready", "snapshot": _build_snapshot()})
    )
    log.info(
        "CR session activation complete: session_id=%s generation=%s source=%s",
        ready.session_id,
        ready.generation,
        source,
    )


async def _monitor_cr_session(api: Any, namespace: str) -> None:
    """Continuously reconcile VS-API SessionContext with the authoritative CR."""

    global _active_cr_generation

    log.info("CR session monitor started")
    while True:
        await asyncio.sleep(_CR_MONITOR_INTERVAL_SECONDS)
        try:
            cr = api.get_namespaced_custom_object(
                group="nodalarc.io",
                version="v1alpha1",
                namespace=namespace,
                plural="constellationspecs",
                name="current-session",
            )
            ready = _extract_ready_cr_session(cr)
            if ready is None:
                continue

            ctx = _active_context
            if (
                ctx is not None
                and ctx.session_id == ready.session_id
                and _active_cr_generation in (None, ready.generation)
            ):
                _active_cr_generation = ready.generation
                _mark_session_manager_ready(ready.session, _write_cr_session_file(ready))
                continue

            if _session_transition_lock.locked():
                continue

            async with _session_transition_lock:
                ctx = _active_context
                if (
                    ctx is not None
                    and ctx.session_id == ready.session_id
                    and _active_cr_generation in (None, ready.generation)
                ):
                    _active_cr_generation = ready.generation
                    _mark_session_manager_ready(ready.session, _write_cr_session_file(ready))
                    continue

                log.info(
                    "CR Ready session differs from active context: active=%s/%s cr=%s/%s",
                    ctx.session_id if ctx else None,
                    _active_cr_generation,
                    ready.session_id,
                    ready.generation,
                )
                await _activate_session_context_from_cr(ready, source="cr-monitor")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("CR session monitor tick failed: %s", exc)


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
    global _active_cr_generation, _cr_monitor_task

    _main_event_loop = asyncio.get_running_loop()
    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    _nats_connection = nc
    await _connect_logging(nc)
    log.info("VS-API NATS connected to %s", nats_url())

    # If main() detected a CR in Wiring/Creating phase, start polling
    global _pending_cr_poll
    _started_pending_poll = False
    if _pending_cr_poll:
        _pending_cr_poll = False
        asyncio.ensure_future(_poll_cr_until_ready())
        _started_pending_poll = True

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
            "nodalarc.ops.>",
            stream=STREAM_OPS_EVENTS,
            ordered_consumer=True,
            deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
            cb=_on_system_ops_event,
        )
    except Exception as exc:
        log.warning("System OpsEvent subscription failed: %s", exc)

    # Bootstrap session from the ConstellationSpec CR — the single source of truth.
    # The CR's spec.sessionYaml has the original catalog session YAML. VS-API has
    # the catalog baked into its image, so nodalarc:<path> references resolve
    # consistently after restart.
    #
    # We do NOT read from ConfigMap mounts. The Operator rewrites paths in the
    # nodalarc-session ConfigMap for FRR pod consumption (/etc/nodalarc/*.yaml).
    # Those paths don't exist in VS-API (we removed the subPath mounts). And for
    # wizard-generated sessions, the original file only existed in the previous
    # VS-API pod's ephemeral filesystem — it's gone on restart. The CR survives.
    import kubernetes.client as _k8s
    import kubernetes.config as _k8s_config

    try:
        _k8s_config.load_incluster_config()
    except _k8s_config.ConfigException:
        _k8s_config.load_kube_config()
    _cr_api = _k8s.CustomObjectsApi()
    _cr_ns = get_platform_config().kubernetes_namespace

    _cr_session: CRSessionIdentity | None = None
    _cr_phase = ""
    _cr_message = ""

    def _candidate_from_cr(cr: dict[str, Any]) -> CRSessionIdentity | None:
        nonlocal _cr_phase, _cr_message
        _cr_phase = cr.get("status", {}).get("phase", "")
        _cr_message = cr.get("status", {}).get("message", "")
        if _cr_phase == "Ready":
            return _extract_ready_cr_session(cr)
        if _cr_phase in ("Pending", "Creating", "Wiring"):
            return _extract_current_cr_session(cr)
        if _cr_phase == "Error" and _cr_status_observes_current_generation(cr):
            if _session_manager:
                _session_manager._status = "error"
                _session_manager.status_detail = _cr_message or "Operator reported error"
            log.error("Current ConstellationSpec is Error: %s", _cr_message)
        return None

    # Poll until a CR with sessionYaml exists. Handles both cases:
    # - VS-API starts before `make session` creates the CR (poll waits)
    # - VS-API restarts while a session is running (CR exists immediately)
    while _cr_session is None:
        try:
            _cr = _cr_api.get_namespaced_custom_object(
                group="nodalarc.io",
                version="v1alpha1",
                namespace=_cr_ns,
                plural="constellationspecs",
                name="current-session",
            )
            _cr_session = _candidate_from_cr(_cr)
        except Exception as exc:
            log.debug("Waiting for runtime session identity from CR: %s", exc)
        if _cr_session is None:
            log.info("No active Ready or wiring runtime session CR — waiting for session to deploy")
            await asyncio.sleep(5)

    if _cr_phase in ("Pending", "Creating", "Wiring"):
        log.info("CR phase=%s — waiting for Ready before activating SessionContext", _cr_phase)
        if _session_manager:
            _session_manager._status = "wiring"
            _session_manager.status_detail = _cr_message or f"Status: {_cr_phase}"
            for _s in _session_manager._available:
                if _s.get("name") == _cr_session.session_name:
                    _session_manager.set_active(_s["file"])
                    break
        if not _started_pending_poll:
            asyncio.ensure_future(_poll_cr_until_ready())
        if _cr_monitor_task is None or _cr_monitor_task.done():
            _cr_monitor_task = asyncio.create_task(
                _monitor_cr_session(_cr_api, _cr_ns), name="cr-session-monitor"
            )
    elif _cr_phase != "Ready":
        log.info("CR phase=%s has no active session context", _cr_phase)
    else:
        session_id = _cr_session.session_id
        _tmp_session = Path(f"/tmp/_session-{session_id}.yaml")
        _tmp_session.write_text(_cr_session.session_yaml, encoding="utf-8")

        log.info(
            "Bootstrapping session %s from CR (name=%s phase=%s)",
            session_id,
            _cr_session.session_name,
            _cr_phase,
        )

        if _session_manager:
            _session_manager._status = "ready"
            _session_manager.status_detail = ""
            for _s in _session_manager._available:
                if _s.get("name") == _cr_session.session_name:
                    _session_manager.set_active(_s["file"])
                    break

        ctx = SessionContext(session_id, str(_tmp_session))
        await ctx.start(nc, mode="recovery")
        _active_context = ctx
        _active_cr_generation = _cr_session.generation
        await _publish_system_ops_event(
            "info",
            "SESSION_BOOTSTRAP",
            f"VS-API started with session {session_id}",
            {
                "session_id": session_id,
                "session_name": _cr_session.session_name,
                "mode": "recovery",
            },
        )

        if _cr_monitor_task is None or _cr_monitor_task.done():
            _cr_monitor_task = asyncio.create_task(
                _monitor_cr_session(_cr_api, _cr_ns), name="cr-session-monitor"
            )

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
        if _cr_monitor_task is not None:
            _cr_monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _cr_monitor_task
            _cr_monitor_task = None
        if _active_context:
            await _active_context.stop()
            _active_context = None
            _active_cr_generation = None
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


app = FastAPI(title="Nodal Arc VS-API", version=project_version(), lifespan=lifespan)
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


@app.get("/api/v1/about")
def about() -> dict:
    """Unauthenticated project attribution and provenance metadata."""
    return project_attribution()


@app.get("/api/v1/auth/token")
def get_auth_token() -> dict:
    """Return the current API key. Unauthenticated — dev-mode only."""
    return {"token": _API_KEY}


@app.get("/api/v1/ops/health", dependencies=[Depends(_require_api_key)])
def get_ops_health() -> dict:
    """Return latest Scheduler actuation health derived from typed OpsEvents."""
    ctx = _active_context
    if ctx is None:
        return {"session_id": "", "wiring_generation": "", "scheduler_instances": []}
    with ctx.state_lock:
        return ctx.build_actuation_health()


@app.post("/api/v1/ops/repair", dependencies=[Depends(_require_api_key)])
async def request_operator_repair(body: dict) -> dict:
    """Explicit operator-triggered GS repair routed to the reporting Scheduler."""
    ctx = _active_context
    nc = _nats_connection
    if ctx is None:
        return JSONResponse(status_code=503, content={"error": "No active session"})
    if nc is None:
        return JSONResponse(status_code=503, content={"error": "NATS not connected"})
    gs_id = body.get("gs_id", "")
    reason = body.get("reason", "")
    if not gs_id or not reason:
        return JSONResponse(status_code=400, content={"error": "gs_id and reason are required"})
    with ctx.state_lock:
        matching = [
            event
            for (_instance, event_gs), event in ctx.actuation_latest_by_gs.items()
            if event_gs == gs_id
        ]
        latest = matching[-1] if matching else {}
    details = latest.get("details") or {}
    scheduler_instance_id = body.get("scheduler_instance_id") or details.get(
        "scheduler_instance_id"
    )
    wiring_generation = body.get("wiring_generation") or details.get("wiring_generation")
    if not scheduler_instance_id or not wiring_generation:
        return JSONResponse(
            status_code=409,
            content={"error": "No Scheduler actuation state is available for that GS"},
        )
    cmd = OperatorRepairCommand(
        session_id=ctx.session_id,
        wiring_generation=wiring_generation,
        scheduler_instance_id=scheduler_instance_id,
        gs_id=gs_id,
        reason=reason,
        intervention_id=body.get("intervention_id") or str(uuid.uuid4()),
    )
    try:
        resp = await nc.request(
            scheduler_repair_subject(ctx.session_id),
            cmd.model_dump_json().encode(),
            timeout=10,
        )
        return json.loads(resp.data)
    except TimeoutError:
        return JSONResponse(
            status_code=504, content={"error": "Scheduler repair request timed out"}
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/v1/ops/events", dependencies=[Depends(_require_api_key)])
async def get_ops_events(
    source: str = Query("", description="Filter by event source (e.g. 'operator', 'scheduler')"),
    level: str = Query("", description="Filter by level (e.g. 'error', 'warning')"),
    limit: int = Query(100, ge=1, le=500, description="Max events to return"),
) -> list[dict]:
    """Return recent operational events from the NODALARC_OPS stream."""
    ctx = _active_context
    session_events = list(ctx.session_ops_events) if ctx else []
    events = _operator_visible_ops_events(list(_system_ops_events) + session_events)
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
    """WebSocket endpoint — bidirectional: push snapshots + receive commands.

    Sender: pushes StateSnapshot at ~1Hz (existing behavior).
    Receiver: handles debug_stream/debug_stop commands from the log panel.
    Both run concurrently via asyncio.gather. Either side ending
    terminates the connection cleanly.
    """
    ws_ip = websocket.client.host if websocket.client else "unknown"
    if _API_KEY:
        token = websocket.query_params.get("token", "")
        if token != _API_KEY:
            _audit_log.warning(f"WS_AUTH_FAIL ip={ws_ip}")
            await websocket.close(code=4401, reason="Unauthorized")
            return
    await websocket.accept()
    _ws_clients.add(websocket)
    ws_id = id(websocket)
    _audit_log.info(f"WS_CONNECT ip={ws_ip}")

    done = asyncio.Event()

    async def _sender():
        ctx = _active_context
        if ctx and ctx.cached_ephemeris:
            await websocket.send_json(ctx.cached_ephemeris)
        while not done.is_set():
            snapshot = _build_snapshot()
            if snapshot is None:
                await asyncio.sleep(1.0)
                continue
            await websocket.send_json(snapshot)
            await asyncio.sleep(1.0)

    async def _receiver():
        try:
            while True:
                data = await websocket.receive_json()
                try:
                    action = data.get("action", "")
                    if action in ("debug_stream", "debug_stop", "debug_stop_all"):
                        await _handle_ws_debug_command(ws_id, data)
                    elif action:
                        log.warning("Unknown WS action: %s", action)
                except Exception as exc:
                    log.warning("WS command error (ignored): %s", exc)
        except WebSocketDisconnect:
            pass
        finally:
            done.set()

    try:
        await asyncio.gather(_sender(), _receiver())
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        if not done.is_set():
            log.warning("WS error for %s: %s %s", ws_ip, type(exc).__name__, exc)
    finally:
        await _cleanup_debug_client(ws_id)
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
    _term_conn_id: str | None = None
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
            except WebSocketDisconnect, asyncio.CancelledError:
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
    except Exception:
        log.exception("Terminal session error for %s", node_id)
    finally:
        if _term_conn_id is not None:
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
    except RuntimeError as exc:
        log.warning("SSH key unavailable for config export: %s", exc)
        return JSONResponse(status_code=503, content={"error": "SSH key unavailable"})

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
        log.warning("Config export error for %s: %s", node_id, exc, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Failed to retrieve config"})
    finally:
        await session.close()


@app.get("/api/v1/state", response_model=None, dependencies=[Depends(_require_api_key)])
def get_state() -> dict | JSONResponse:
    """Current state snapshot."""
    snapshot = _build_snapshot()
    if snapshot is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "No active session",
                "session_status": _session_manager.status if _session_manager else "idle",
                "session_status_detail": _session_manager.status_detail if _session_manager else "",
            },
        )
    return snapshot


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
    except httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError:
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


@app.get(
    "/api/v1/link-decision-traces",
    dependencies=[Depends(_require_api_key)],
    response_model=None,
)
def get_link_decision_traces(
    node_a: str = Query(None),
    node_b: str = Query(None),
) -> list[dict] | dict | JSONResponse:
    """Return active-link decision traces retained by the current session."""
    ctx = _active_context
    if ctx is None:
        return []
    if (node_a is None) != (node_b is None):
        return JSONResponse(
            status_code=400,
            content={"error": "node_a and node_b must be provided together"},
        )
    with ctx.state_lock:
        if node_a and node_b:
            key = ":".join(sorted((node_a, node_b)))
            trace = ctx.link_decision_traces.get(key)
            if trace is None:
                return JSONResponse(status_code=404, content={"error": "Link trace not found"})
            return json.loads(trace.model_dump_json())
        traces = [json.loads(t.model_dump_json()) for t in ctx.link_decision_traces.values()]
    traces.sort(key=lambda item: (item["node_a"], item["node_b"]))
    return traces


@app.get(
    "/api/v1/decision-explanation",
    dependencies=[Depends(_require_api_key)],
    response_model=None,
)
def get_decision_explanation(
    gs: str = Query(...), sat: str | None = Query(None)
) -> dict | JSONResponse:
    """Composed decision-explanation FACTS for one ground station, or one pair.

    VS-API composes the funnel ladder, effective envelope, best-candidate,
    and actuation/divergence facts from the
    committed ground-decision snapshot, the kernel-actual link set, the actuation
    roster, and the Scheduler-owned pending clock (divergence timing, recovered from
    the retained ActualLinkSnapshot). The client registry assigns family/severity/text.
    ``kernel_up`` comes from the Scheduler's recovered ``_actual_links`` (verified
    kernel truth), NOT ``ctx.links`` (OME's desired/visible snapshot) — otherwise a
    scheduled-but-unactuated pair masks as connected. Actuation state defaults to
    ``unknown`` when no roster has reached VS-API — honest, not faked clean.

    With ``sat`` the facts describe that exact GS<->sat pair (the Per-Pair Inspector,
    ``node_focus="pair"``); without it the GS card auto-selects its focal pair by
    precedence. ``404`` if no snapshot has arrived, or no decision covers the GS (or
    the requested pair).
    """
    from nodalarc.explain import compose_gs_explanation

    ctx = _active_context
    if ctx is None:
        return JSONResponse(status_code=404, content={"error": "No active session"})
    with ctx.state_lock:
        snapshot = ctx.latest_ground_link_decision_snapshot
        active_pairs = ctx.actual_kernel_pairs()
        pending_by_pair = ctx.pending_actuation(datetime.now(UTC))
        expected_latency_ms = ctx.actuation_expected_latency_ms
        fault_after_ms = ctx.actuation_fault_after_ms
        actuation_by_gs: dict[str, str] = {}
        health = ctx.build_actuation_health()
        for inst in health.get("scheduler_instances", []):
            for gs_entry in inst.get("ground_stations", []):
                gid = gs_entry.get("gs_id")
                state = gs_entry.get("actuation_state")
                if gid and state:
                    actuation_by_gs[gid] = state
    if snapshot is None:
        return JSONResponse(
            status_code=404, content={"error": "No GroundLinkDecisionSnapshot received yet"}
        )
    focal_pair = tuple(sorted((gs, sat))) if sat else None
    facts = compose_gs_explanation(
        gs_id=gs,
        snapshot=snapshot,
        active_pairs=active_pairs,
        actuation_state_by_gs=actuation_by_gs,
        pending_by_pair=pending_by_pair,
        expected_latency_ms=expected_latency_ms,
        fault_after_ms=fault_after_ms,
        focal_pair=focal_pair,
    )
    if facts is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No ground decision covers {gs} in the latest snapshot"},
        )
    return json.loads(facts.model_dump_json())


@app.get(
    "/api/v1/decision-explanation/timeline",
    dependencies=[Depends(_require_api_key)],
    response_model=None,
)
def get_decision_explanation_timeline(
    gs: str = Query(...), limit: int = Query(120, ge=1, le=720)
) -> dict | JSONResponse:
    """Bounded observed decision window for one ground station.

    This is not historical playback. VS-API samples the committed OME ground
    decision surface as it arrives and retains only a bounded per-GS window so
    the UI can roll up recent no-link causes without polling the full GS×sat
    matrix.
    """
    ctx = _active_context
    if ctx is None:
        return JSONResponse(status_code=404, content={"error": "No active session"})
    timeline = ctx.ground_decision_timeline(gs, limit=limit)
    if timeline is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No decision timeline samples for {gs}"},
        )
    return json.loads(timeline.model_dump_json())


@app.get(
    "/api/v1/ground-link-decisions",
    dependencies=[Depends(_require_api_key)],
    response_model=None,
)
def get_ground_link_decisions(
    node_a: str = Query(None),
    node_b: str = Query(None),
    node: str = Query(None),
) -> dict | JSONResponse:
    """Return the latest OME GroundLinkDecisionSnapshot.

    GROUND-SCOPED. The OME only publishes ground (GS↔satellite) link
    decisions today; ISL pair decisions are not yet snapshotted and a
    separate endpoint will be added when they are. Querying an
    ISL-only pair (sat-sat) returns 404 — not because the OME has no
    opinion, but because the ISL decision surface does not exist yet.

    Operator-facing surface for "why isn't this ground pair up?" Every
    ground pair the OME considered carries
    ``visibility_reject_reason``; visible-but-unscheduled pairs
    additionally carry ``unscheduled_reason`` plus the incumbent or
    capacity constraint the allocator chose them over.

    Three modes, mutually exclusive:

    - No query: the full snapshot (``sim_time``, ``snapshot_seq``,
      ``epoch_id``, all ground decisions, all unscheduled ground pairs).
    - ``node`` (a single GS or satellite id): the snapshot SLICED to the
      decisions and unscheduled pairs that node participates in — the
      candidate-list surface for the selected node, so a node card does not
      poll and discard the whole GS×satellite cross-product (wrong primitive
      at thousand-satellite scale). Same shape as the full snapshot, fewer
      rows; ``200`` with empty ``decisions``/``unscheduled_pairs`` when the
      node has no candidates this tick (honest — the snapshot exists, the node
      simply has none), distinct from the no-snapshot ``404``.
    - ``node_a`` + ``node_b`` (both): just that ground pair's decision and
      matching unscheduled-pair record (if any).

    ``404`` if no snapshot has been received yet; ``404`` for a
    specific pair the OME's ground decision set does not cover.
    """
    ctx = _active_context
    if ctx is None:
        return JSONResponse(status_code=404, content={"error": "No active session"})
    if (node_a is None) != (node_b is None):
        return JSONResponse(
            status_code=400,
            content={"error": "node_a and node_b must be provided together"},
        )
    if node is not None and (node_a is not None or node_b is not None):
        return JSONResponse(
            status_code=400,
            content={"error": "node is mutually exclusive with node_a/node_b"},
        )
    with ctx.state_lock:
        snapshot = ctx.latest_ground_link_decision_snapshot
    if snapshot is None:
        return JSONResponse(
            status_code=404,
            content={"error": "No GroundLinkDecisionSnapshot received yet"},
        )
    if node is not None:
        return {
            "sim_time": snapshot.sim_time.isoformat(),
            "snapshot_seq": snapshot.snapshot_seq,
            "epoch_id": snapshot.epoch_id,
            "decisions": [
                json.loads(d.model_dump_json()) for d in snapshot.decisions if node in d.pair
            ],
            "unscheduled_pairs": [
                json.loads(u.model_dump_json())
                for u in snapshot.unscheduled_pairs
                if node in u.pair
            ],
        }
    if node_a and node_b:
        target = tuple(sorted((node_a, node_b)))
        decision = next((d for d in snapshot.decisions if d.pair == target), None)
        if decision is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": (
                        f"OME ground-decision snapshot does not cover pair "
                        f"{node_a}<->{node_b}. Note: ISL pair decisions are "
                        "not exposed on this endpoint."
                    )
                },
            )
        unscheduled = next((u for u in snapshot.unscheduled_pairs if u.pair == target), None)
        return {
            "sim_time": snapshot.sim_time.isoformat(),
            "snapshot_seq": snapshot.snapshot_seq,
            "epoch_id": snapshot.epoch_id,
            "decision": json.loads(decision.model_dump_json()),
            "unscheduled": (
                json.loads(unscheduled.model_dump_json()) if unscheduled is not None else None
            ),
        }
    return json.loads(snapshot.model_dump_json())


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
        log.warning("Failed to create continuous tracer: %s", exc, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Tracer initialization failed"})

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
        # NodalPath is optional and may be installed as a separate package.
        # Keep VS-API usable when the external path engine is not present.
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
        except Exception as exc:
            log.debug("NodalPath package unavailable for continuous tracer: %s", exc)
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
        except ModuleNotFoundError as exc:
            log.debug("NodalPath session loader unavailable for continuous tracer: %s", exc)
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
    from nodalarc.session_generator import constellation_source_mode, load_constellation_presets

    presets = load_constellation_presets()
    return [
        {
            "name": p.name,
            "description": p.description,
            "satellite_count": p.satellite_count,
            "constellation": p.constellation,
            "ground_stations": p.ground_stations,
            "mode": constellation_source_mode(p.constellation),
        }
        for p in presets.values()
    ]


@app.get("/api/v1/presets/satellite-types", dependencies=[Depends(_require_api_key)])
def list_satellite_types() -> list[dict]:
    """Return satellite-type overrides for the wizard.

    Satellite-type override was a retired config convenience. Current catalog
    constellations own their node model, terminal mounts, and orbit; users who
    want a different combination author or choose a different constellation
    primitive. Returning an empty list prevents the UI from offering an invalid
    override path.
    """
    return []


@app.get("/api/v1/presets/ground-stations", dependencies=[Depends(_require_api_key)])
def list_ground_station_sets() -> list[dict]:
    """Return available catalog site-set presets for the wizard."""
    gs_sets_dir = _CATALOG_ROOTS.root / "site-sets"
    results: list[dict] = []
    if not gs_sets_dir.is_dir():
        return results
    for yaml_path in sorted(gs_sets_dir.rglob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        wrapper, model = validate_catalog_document(raw)
        if wrapper != "site_set":
            continue
        data = model.model_dump(mode="python", by_alias=True, exclude_none=True)
        results.append(
            {
                "name": data["id"],
                "description": data.get("display_name") or data.get("notes") or "",
                "stations": [
                    site.get("site", {}).get("id", "") if isinstance(site, dict) else str(site)
                    for site in data.get("sites", [])
                ],
                "file": _catalog_ref_for_path(yaml_path),
            }
        )
    return results


@app.get("/api/v1/presets/ground-stations/stations", dependencies=[Depends(_require_api_key)])
def list_individual_stations() -> list[dict]:
    """Return all available catalog sites for custom set building."""
    stations_dir = _CATALOG_ROOTS.root / "sites"
    results: list[dict] = []
    if not stations_dir.is_dir():
        return results
    for yaml_path in sorted(stations_dir.rglob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        wrapper, model = validate_catalog_document(raw)
        if wrapper != "site":
            continue
        site = model.model_dump(mode="python", by_alias=True, exclude_none=True)
        location = site.get("location") or {}
        results.append(
            {
                "name": site["id"],
                "lat_deg": location.get("lat_deg", 0),
                "lon_deg": location.get("lon_deg", 0),
                "file": _catalog_ref_for_path(yaml_path),
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
        },
        "area_strategies": ["flat", "stripe", "per_plane"],
    }


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def _catalog_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, CatalogPathError):
        message = (
            exc.args[0]
            if exc.args and isinstance(exc.args[0], str)
            else "Invalid catalog reference"
        )
    elif isinstance(exc, FileExistsError):
        message = "Catalog file already exists"
    elif isinstance(exc, FileNotFoundError):
        message = "Catalog reference not found"
    else:
        message = "Invalid catalog reference"
    return _error_response(400, message)


def _resolve_api_constellation_source(source: Any) -> Any:
    if isinstance(source, str):
        return config_value_for(resolve_constellation_reference(source, _CATALOG_ROOTS))
    return source


def _resolve_api_ground_station_source(source: Any) -> Any:
    if isinstance(source, str):
        return config_value_for(resolve_site_set_reference(source, _CATALOG_ROOTS))
    if isinstance(source, list) and all(isinstance(item, str) for item in source):
        validate_station_names(source)
    return source


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
    orbit_propagator = body.get("orbit_propagator")
    if not constellation or not protocol:
        return JSONResponse(
            status_code=400, content={"error": "constellation and protocol are required"}
        )
    if not orbit_propagator:
        return JSONResponse(status_code=400, content={"error": "orbit_propagator is required"})
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
            orbit_propagator=orbit_propagator,
            catalog_roots=_CATALOG_ROOTS,
        )
    except CatalogPathError as exc:
        return _catalog_error(exc)
    except FileNotFoundError as exc:
        return _catalog_error(exc)
    except ValueError as exc:
        log.info("Invalid session generation request: %s", exc)
        return _error_response(400, "Invalid session request")
    return {"yaml": yaml_str, "warnings": warnings}


@app.post("/api/v1/session/preview-coverage", dependencies=[Depends(_require_api_key)])
async def preview_coverage(body: dict) -> dict:
    """Run OME coverage preview for the given combination.

    Accepts constellation (name or inline dict), satellite_type (name),
    and ground_stations (set name, station list, or inline dict).
    Computes visibility at 10-second steps for one orbital period.
    Returns ISL/GS coverage statistics and warnings.
    """
    from functools import partial

    from ome.coverage_preview import compute_coverage_preview

    constellation = body.get("constellation")
    ground_stations = body.get("ground_stations")

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            partial(
                compute_coverage_preview,
                constellation,
                body.get("satellite_type"),
                ground_stations,
                catalog_roots=_CATALOG_ROOTS,
            ),
        )
    except CatalogPathError as exc:
        return _catalog_error(exc)
    except FileNotFoundError as exc:
        return _catalog_error(exc)
    except ValueError as exc:
        log.info("Invalid coverage preview request: %s", exc)
        return _error_response(400, "Coverage preview request is invalid")
    except Exception as exc:
        log.error("Coverage preview internal error: %s", exc, exc_info=True)
        return _error_response(500, "Coverage preview failed")
    return result.model_dump()


@app.post("/api/v1/session/deploy", dependencies=[Depends(_require_api_key)])
async def deploy_generated_session(body: dict) -> dict:
    """Validate YAML, write to sessions dir, and trigger deploy.

    Validates the FULL session config before creating any K8s resources:
    schema validation (Pydantic), constellation expansion, ground station
    loading, and session readiness checks. If any validation fails, the
    user sees the error immediately — no CR is created, no switch is
    started, no stuck "Switching session..." state.
    """
    import yaml as _yaml

    yaml_str = body.get("yaml", "")
    if not yaml_str:
        return JSONResponse(status_code=400, content={"error": "yaml field required"})
    try:
        raw = _yaml.safe_load(yaml_str)
    except YAMLError as exc:
        log.info("Invalid session YAML rejected: %s", exc)
        return _error_response(400, "Invalid session YAML")
    try:
        resolution = resolve_session_with_assets(
            raw,
            catalog_roots=_CATALOG_ROOTS,
            source_context=SourceContext(origin="vs_api.deploy"),
        )
    except CatalogPathError as exc:
        return _catalog_error(exc)
    except FileNotFoundError as exc:
        return _catalog_error(exc)
    except Exception as exc:
        log.info("Invalid segment session YAML rejected: %s", exc)
        return _error_response(400, "Invalid segment session YAML")

    # Write the validated segment YAML exactly as supplied. The resolver is the
    # semantic authority; VS-API must not rewrite it into the old session shape.
    try:
        stem = generated_file_stem(resolution.resolved.session.name)
        session_file = generated_file_path(_generated_sessions_dir(), f"_wizard-{stem}.yaml")
        write_text_exclusive(session_file, _yaml.dump(raw, default_flow_style=False))
    except (CatalogPathError, FileExistsError) as exc:
        return _catalog_error(exc)

    # Rescan and deploy
    if _session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not initialized"})
    if _session_manager.status == "switching":
        return JSONResponse(status_code=409, content={"error": "Switch already in progress"})
    _session_manager.rescan()
    session_file_value = config_value_for(session_file)
    asyncio.create_task(_run_switch(session_file_value))
    return {"status": "switching", "session_file": session_file_value}


@app.post("/api/v1/session/deploy-from-yaml", dependencies=[Depends(_require_api_key)])
async def deploy_from_yaml(body: dict) -> dict:
    """Deploy a self-contained session YAML (e.g., downloaded from a previous session).

    Extracts inline constellation/ground-station definitions, writes
    ephemeral files, and deploys identically to the wizard path.
    """
    import yaml as _yaml

    yaml_str = body.get("yaml", "")
    if not yaml_str:
        return JSONResponse(status_code=400, content={"error": "yaml field required"})
    try:
        raw = _yaml.safe_load(yaml_str)
    except YAMLError as exc:
        log.info("Invalid session YAML rejected: %s", exc)
        return _error_response(400, "Invalid session YAML")
    try:
        resolution = resolve_session_with_assets(
            raw,
            catalog_roots=_CATALOG_ROOTS,
            source_context=SourceContext(origin="vs_api.upload"),
        )
        stem = generated_file_stem(resolution.resolved.session.name)
    except CatalogPathError as exc:
        return _catalog_error(exc)
    except FileNotFoundError as exc:
        return _catalog_error(exc)
    except Exception as exc:
        log.info("Invalid segment session YAML rejected: %s", exc)
        return _error_response(400, "Invalid segment session YAML")

    session_file = generated_file_path(_generated_sessions_dir(), f"_wizard-{stem}.yaml")
    try:
        write_text_exclusive(session_file, _yaml.dump(raw, default_flow_style=False))
    except FileExistsError as exc:
        return _catalog_error(exc)

    if _session_manager is None:
        return JSONResponse(status_code=503, content={"error": "Session manager not initialized"})
    if _session_manager.status == "switching":
        return JSONResponse(status_code=409, content={"error": "Switch already in progress"})
    _session_manager.rescan()
    session_file_value = config_value_for(session_file)
    asyncio.create_task(_run_switch(session_file_value))
    return {"status": "switching", "session_file": session_file_value}


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
    except ValueError as exc:
        log.info("Invalid introspection request: %s", exc)
        return _error_response(400, "Invalid introspection request")
    except Exception as exc:
        log.warning("Introspection command failed: %s", exc, exc_info=True)
        return _error_response(500, "Introspection command failed")
    if result.get("error") == "Command timed out":
        return JSONResponse(status_code=504, content=result)
    return result


async def _run_switch(session_path: str) -> None:
    async with _session_transition_lock:
        await _run_switch_locked(session_path)


async def _run_switch_locked(session_path: str) -> None:
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
    global _active_context, _active_cr_generation

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
        _active_cr_generation = None

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

        # (e) Deploy new session via K8s CR (fully async).
        # Progress callback broadcasts status updates to all connected
        # browsers so the switch overlay shows real-time progress instead
        # of a static "Switching session..." message.
        async def _switch_progress(detail: str) -> None:
            await _broadcast_to_all(
                json.dumps({"msg_type": "session_transitioning", "detail": detail})
            )

        ready_cr = await _session_manager.switch(session_path, progress_fn=_switch_progress)
        ready = _extract_ready_cr_session(ready_cr)
        if ready is None:
            raise RuntimeError("Operator returned Ready without a current session_run_id")

        # (f) Create new SessionContext
        session_id = ready.session_id
        session_path_for_context = _write_cr_session_file(ready)

        if _nats_connection is None:
            log.error("FATAL: No NATS connection for new SessionContext")
            raise RuntimeError("No NATS connection available")

        new_ctx = SessionContext(session_id, str(session_path_for_context))
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
        _active_cr_generation = ready.generation
        from nodal.logging import set_session as _set_log_session

        _set_log_session(session_id)
        _session_manager._status = "ready"
        _session_manager.status_detail = ""

        await _publish_system_ops_event(
            "info",
            "SESSION_SWITCH_COMPLETE",
            f"Session switch complete: now running {ready.session_name}",
            {
                "session_id": session_id,
                "session_name": ready.session_name,
                "generation": ready.generation,
                "links": len(new_ctx.links),
            },
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

        log.info("Session switch complete — %s active (name=%s)", session_id, ready.session_name)

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
    global _active_context, _active_cr_generation
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
            status_is_current = _cr_status_observes_current_generation(cr)
            # Try to load session_id on each tick — the ConfigMap appears
            if _session_manager and phase != "Wiring" and status_is_current:
                # During Wiring, Node Agent NATS progress owns _status_detail.
                # Only update from CR for non-Wiring phases.
                _session_manager.status_detail = message or f"Status: {phase}"
            if phase == "Ready":
                ready = _extract_ready_cr_session(cr)
                if ready is None:
                    log.info("CR phase is Ready but generation/pod status is not consistent yet")
                    continue
                ctx = _active_context
                needs_activation = (
                    ctx is None
                    or ctx.session_id != ready.session_id
                    or _active_cr_generation not in (None, ready.generation)
                )
                if needs_activation:
                    if _session_transition_lock.locked():
                        continue
                    async with _session_transition_lock:
                        ctx = _active_context
                        if (
                            ctx is None
                            or ctx.session_id != ready.session_id
                            or _active_cr_generation not in (None, ready.generation)
                        ):
                            log.info(
                                "CR Ready session changed during wiring poll: active=%s/%s cr=%s/%s",
                                ctx.session_id if ctx else None,
                                _active_cr_generation,
                                ready.session_id,
                                ready.generation,
                            )
                            await _activate_session_context_from_cr(ready, source="cr-ready-poll")
                            return
                elif ctx is not None and ctx.session_id == ready.session_id:
                    _active_cr_generation = ready.generation
                    _mark_session_manager_ready(ready.session, _write_cr_session_file(ready))

                ctx = _active_context
                if ctx:
                    ctx.session_ready_time = _time.monotonic()
                if _session_manager:
                    _session_manager._status = "ready"
                    _session_manager.status_detail = ""
                log.info("CR reached Ready — session is now operational")
                return
            if phase == "Error" and status_is_current:
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
        log.info(
            "Auto-generated ephemeral API key; retrieve it from /api/v1/auth/token "
            "or set NODAL_API_KEY to use a fixed key"
        )
    else:
        log.info("Using API key from NODAL_API_KEY environment variable")

    parser = argparse.ArgumentParser(description="VS-API server")
    parser.add_argument("--session", default=None, help="Path to session YAML (optional)")
    parser.add_argument("--db", default=None, help="Path to SQLite database (optional)")
    parser.add_argument("--port", type=int, default=None, help="HTTP port")
    parser.add_argument(
        "--sessions-dir",
        default="catalog/nodalarc/sessions",
        help="Directory with catalog session YAMLs",
    )
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    args = parser.parse_args()

    from nodalarc.platform_config import init_platform_config

    init_platform_config(Path(args.platform_config))

    # NodalPath is distributed separately. Initialize it when present so
    # NodalPath-specific trace routes can use live SID lookups; absence of that
    # package must not prevent ordinary NodalArc sessions from starting.
    try:
        from nodalpath.platform import init_nodalpath_config

        init_nodalpath_config(Path("configs/nodalpath.yaml"))
        log.info("Initialized NodalPath config")
    except ModuleNotFoundError as exc:
        log.info("NodalPath package unavailable; NodalPath-specific routes disabled: %s", exc)
    except Exception as exc:
        log.warning(
            "NodalPath config initialization failed; NodalPath-specific routes disabled: %s", exc
        )

    if args.port is None:
        args.port = get_platform_config().vs_api_http_port

    global _session_manager, _initial_session_file, _pending_cr_poll

    _session_manager = SessionManager(
        args.sessions_dir,
        initial_db_path=args.db,
        generated_sessions_dir=str(_generated_sessions_dir()),
    )

    log.info("VS-API starting [build=%s]", os.environ.get("NODAL_BUILD", "dev"))

    if args.session and args.db:
        _initial_session_file = args.session

        session_path = Path(args.session)
        if not session_path.is_file():
            log.info(f"Session config not found at {args.session} — starting in idle mode")
            session_data = None
        else:
            session_data = yaml.safe_load(session_path.read_text())
        if session_data:
            startup_resolution = resolve_session_with_assets(
                session_data,
                catalog_roots=_CATALOG_ROOTS,
                source_context=SourceContext(origin="vs_api.startup"),
            )
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
            _loaded_name = startup_resolution.resolved.session.name
            if _loaded_name:
                for _s in _session_manager._available:
                    if _s.get("name") == _loaded_name:
                        _session_manager.set_active(_s["file"])
                        _initial_session_file = _s["file"]
                        break
            # Check CR phase — do not claim ready unless the authoritative CR is
            # Ready and generation/pod/wiring identity checks all pass.
            _cr_phase = ""
            _cr_message = ""
            _cr_ready = None
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
                if _cr_phase == "Ready":
                    _cr_ready = _extract_ready_cr_session(_cr)
            except Exception:
                pass  # CR may not exist yet — NATS bootstrap will wait for it.

            if _cr_ready is not None:
                _session_manager._status = "ready"
                _session_manager.status_detail = ""
            elif _cr_phase in ("Pending", "Wiring", "Creating"):
                log.info(f"Session config loaded but CR phase={_cr_phase} — wiring in progress")
                _session_manager._status = "wiring"
                _session_manager.status_detail = _cr_message or f"Status: {_cr_phase}"
                _pending_cr_poll = True
            elif _cr_phase == "Error":
                _session_manager._status = "error"
                _session_manager.status_detail = _cr_message or "Operator reported error"
            else:
                _session_manager._status = "idle"
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
                ready = _extract_ready_cr_session(cr) if phase == "Ready" else None
                if ready is not None:
                    log.info("Active ConstellationSpec CR found (phase=Ready)")
                    _session_manager._status = "ready"
                    _session_manager.status_detail = ""
                elif phase in ("Pending", "Wiring", "Creating"):
                    log.info(
                        f"Active ConstellationSpec CR found (phase={phase}) — wiring in progress"
                    )
                    _session_manager._status = "wiring"
                    _session_manager.status_detail = message or f"Status: {phase}"
                    _pending_cr_poll = True
                elif phase == "Error":
                    _session_manager._status = "error"
                    _session_manager.status_detail = message or "Operator reported error"
                # Try to match session name from mounted config
                if phase in ("Ready", "Pending", "Wiring", "Creating"):
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
