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
import base64
import contextlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import asyncssh
import nats
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from kubernetes.client.rest import ApiException
from nodal.logging import configure as _configure_logging
from nodal.logging import connect as _connect_logging
from nodal.logging import uvicorn_settings as _uvicorn_logging_settings
from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    load_catalog_object,
)
from nodalarc.catalog_refs import SessionRef
from nodalarc.catalog_registry import (
    catalog_family_spec,
)
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogReadSnapshot,
    CatalogValidationError,
)
from nodalarc.catalog_upload import DEFAULT_CATALOG_UPLOAD_LIMITS
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.cr_runtime_config import (
    CR_GROUP,
    CR_NAME,
    CR_PLURAL,
    CR_VERSION,
    SOURCE_ID_ANNOTATION,
    ConstellationSpecSpec,
    ConstellationSpecStatus,
    cr_status_observes_current_generation,
    load_cr_runtime_config,
)
from nodalarc.db.queries import (
    count_link_events,
    get_metadata,
    query_link_events,
    query_nearest_snapshot,
)
from nodalarc.db.retention import RETAINED_FROM_KEY
from nodalarc.kubernetes_runtime_config import (
    KubernetesRuntimeConfigError,
    KubernetesRuntimeConfigErrorCode,
)
from nodalarc.models.api_refusal import ApiRefusal
from nodalarc.models.builder_api import (
    WizardAvailableStation,
    WizardAvailableStationResponse,
    WizardConstellationPresetResponse,
    WizardCoverageRequest,
    WizardExtensionRulesResponse,
    WizardGroundStationSetPreset,
    WizardGroundStationSetPresetResponse,
    WizardSatelliteTerminalSummary,
    WizardSatelliteTypePreset,
    WizardSatelliteTypePresetResponse,
)
from nodalarc.models.coverage import CoveragePreviewResult
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.models.scheduler_ops import OperatorRepairCommand
from nodalarc.models.session_sources import (
    CatalogSessionSourceId,
    CatalogSessionSummary,
    CatalogSessionSwitchAccepted,
    CatalogSessionSwitchRequest,
    CatalogSessionYamlUploadRequest,
)
from nodalarc.models.vs_api import (
    LINK_HISTORY_PAGE_MAX,
    LinkHistoryEvent,
    LinkHistoryPage,
    StateSnapshot,
    TracedPath,
)
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    STREAM_DEBUG_EVENTS,
    STREAM_OPS_EVENTS,
    debug_ctrl_subject,
    debug_subscribe_all_subject,
    nats_url,
    ops_subscribe_all_subject,
    sanitize_session_id,
    scheduler_repair_subject,
    wiring_progress_subscribe_subject,
)
from nodalarc.platform_config import get_platform_config
from nodalarc.project_info import project_attribution, project_version
from nodalarc.resolve_session import (
    SessionResolution,
)
from nodalarc.runtime_config import ResolvedRuntimeConfig, RuntimeConfigError
from nodalarc.session_nodes import available_session_nodes
from pydantic import TypeAdapter
from urllib3.exceptions import HTTPError as TransportHTTPError
from yaml import YAMLError

from vs_api import k8s
from vs_api.catalog_context import CatalogContext, get_catalog_context
from vs_api.catalog_session_service import CatalogSessionService
from vs_api.catalog_upload_lifecycle import (
    reconcile_catalog_upload_lifecycle as reconcile_catalog_upload_resources,
)
from vs_api.catalog_upload_store import (
    CatalogUploadResourceEvidence,
    CatalogUploadStoreError,
    CatalogUploadStoreErrorCode,
    KubernetesCatalogUploadStore,
)
from vs_api.continuous_tracer import ContinuousTracer
from vs_api.introspect import VTYSH_COMMANDS, IntrospectRequest, IntrospectResult, run_vtysh
from vs_api.ops_log import (
    OPS_LOG_TOKEN,
    is_operator_visible_ops_event,
    operator_visible_ops_events,
    stamp_ops_event,
)
from vs_api.path_tracer import PathTracer
from vs_api.refusals import (
    REFUSAL_FAMILIES,
    install_refusal_handlers,
    internal_error_refusal,
    refusal_from_exception,
    refusal_response,
)
from vs_api.resolved_runtime_views import tracer_node_registry
from vs_api.session_context import SessionContext, SessionInactiveError
from vs_api.session_manager import SessionManager
from vs_api.terminal import TerminalManager
from vs_api.transition_operations import (
    FilesystemTransitionOperationStore,
    TransitionConstellationSpecObservation,
    TransitionOperation,
    TransitionOperationConflictError,
    TransitionOperationFacts,
    TransitionOperationFailure,
    TransitionOperationNotFoundError,
    TransitionOperationProvenance,
    TransitionOperationProvenancePatch,
    TransitionOperationReservation,
    TransitionOperationSource,
    TransitionOperationSourceKind,
    TransitionOperationState,
    TransitionOperationStore,
    TransitionReconciliationDisposition,
    TransitionRuntimePlan,
    TransitionRuntimeResult,
    TransitionRuntimeStatusProof,
    reconcile_transition_operation,
    transition_failure_from_exception,
)

log = logging.getLogger(__name__)

_INSTALLED_SHIPPED_ROOT = Path("catalog/nodalarc")


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
_active_cr_generation: int | None = None
_cr_monitor_task: asyncio.Task | None = None
_session_transition_lock = asyncio.Lock()
_transition_admission_lock = asyncio.Lock()
_active_transition_operation_id: str | None = None
_local_transition_operation_id: str | None = None
_current_transition_operation_id: ContextVar[str | None] = ContextVar(
    "current_transition_operation_id",
    default=None,
)
_transition_operation_store: TransitionOperationStore | None = None
_transition_operation_store_lock = threading.Lock()

_CR_MONITOR_INTERVAL_SECONDS = 2.0
_CR_CONTEXT_READY_TIMEOUT_SECONDS = 60.0
_CATALOG_UPLOAD_RECONCILE_INTERVAL_SECONDS = 60.0

# System OpsEvents — meta-session, not cleared on switch
from collections import deque

_system_ops_events: deque = deque(maxlen=500)

# On-demand debug — state managed by WebSocket command handlers
_debug_sources: set[str] = set()
_debug_clients: dict[int, set[str]] = {}
_debug_sub: object | None = None
_debug_events: deque = deque(maxlen=500)

_KNOWN_DEBUG_SOURCES = ("ome", "scheduler", "node_agent", "operator", "vs_api")


def _get_transition_operation_store() -> TransitionOperationStore:
    """Return the replaceable single-writer store on the existing session PVC."""

    global _transition_operation_store
    if _transition_operation_store is None:
        with _transition_operation_store_lock:
            if _transition_operation_store is None:
                root = os.environ.get("NODALARC_TRANSITION_OPERATION_ROOT")
                if not root:
                    root = str(
                        Path(get_platform_config().session_data_root) / "transition-operations"
                    )
                _transition_operation_store = FilesystemTransitionOperationStore(root)
    return _transition_operation_store


TransitionStoreOperation = Literal[
    "active",
    "advance",
    "get_operation",
    "reserve",
    "update_provenance",
]


async def _invoke_transition_store(
    operation_name: TransitionStoreOperation,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    store = _get_transition_operation_store()
    operation = getattr(store, operation_name)
    if store.blocking_io:
        return await asyncio.to_thread(operation, *args, **kwargs)
    return operation(*args, **kwargs)


def _runtime_release_identity() -> str:
    """Return the release identity shared with Operator runtime proofs."""

    return os.environ.get("NODALARC_RELEASE", project_version())


def _runtime_build_identity() -> str:
    """Return the build identity shared with Operator runtime proofs."""

    return os.environ.get("NODAL_BUILD", "dev")


async def _advance_transition_operation(
    state: TransitionOperationState,
    *,
    operation_id: str | None = None,
    detail: str | None = None,
    failure: TransitionOperationFailure | None = None,
    runtime: TransitionRuntimeResult | None = None,
) -> None:
    selected_id = operation_id or _current_transition_operation_id.get()
    if selected_id is None:
        return
    normalized_detail = detail.strip()[:512] if detail is not None else None
    if not normalized_detail:
        normalized_detail = None
    await _invoke_transition_store(
        "advance",
        selected_id,
        state,
        detail=normalized_detail,
        failure=failure,
        runtime=runtime,
    )


async def _reconcile_catalog_upload_lifecycle(
    cr: dict[str, Any] | None,
    *,
    core_v1_api: Any,
    namespace: str,
) -> None:
    """Fail-closed reconciliation of upload ownership and bounded GC."""

    async with _transition_admission_lock:
        active = await _invoke_transition_store("active")
        store = KubernetesCatalogUploadStore(core_v1_api, namespace)
        await asyncio.to_thread(
            reconcile_catalog_upload_resources,
            store,
            constellation_spec=cr,
            active_operation=active,
        )


async def _run_admitted_transition(
    operation_id: str,
    worker: Callable[[], Awaitable[TransitionRuntimeResult | None]],
) -> None:
    global _active_transition_operation_id, _local_transition_operation_id
    token = _current_transition_operation_id.set(operation_id)
    try:
        await _advance_transition_operation(
            TransitionOperationState.COLLECTING,
            detail="Collecting and rechecking the trusted session source",
        )
        runtime = await worker()
        await _advance_transition_operation(
            TransitionOperationState.SUCCEEDED,
            detail="Session runtime reached Ready",
            runtime=runtime,
        )
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await _advance_transition_operation(
                TransitionOperationState.CANCELLED,
                detail="Session transition task was cancelled",
                failure=TransitionOperationFailure(
                    code="transition.worker.cancelled",
                    message="Session transition was cancelled",
                ),
            )
        if _session_manager is not None and _session_manager.status == "switching":
            _session_manager._status = "error"
            _session_manager.status_detail = "Session transition cancelled"
        raise
    except Exception as exc:
        with contextlib.suppress(Exception):
            await _advance_transition_operation(
                TransitionOperationState.FAILED,
                detail="Session transition failed",
                failure=transition_failure_from_exception(exc),
            )
        log.error("Admitted session transition failed", exc_info=True)
    finally:
        _current_transition_operation_id.reset(token)
        async with _transition_admission_lock:
            if _active_transition_operation_id == operation_id:
                _active_transition_operation_id = None
            if _local_transition_operation_id == operation_id:
                _local_transition_operation_id = None


async def _admit_transition(
    worker: Callable[[], Awaitable[TransitionRuntimeResult | None]],
    *,
    reservation: TransitionOperationReservation,
) -> str | None:
    global _active_transition_operation_id, _local_transition_operation_id
    async with _transition_admission_lock:
        operation_id = uuid.uuid4().hex
        try:
            await _invoke_transition_store(
                "reserve",
                operation_id,
                reservation,
            )
        except TransitionOperationConflictError as exc:
            _active_transition_operation_id = exc.active_operation_id
            return None
        _active_transition_operation_id = operation_id
        _local_transition_operation_id = operation_id
        if _session_manager is not None:
            _session_manager._status = "switching"
            _session_manager.status_detail = "Transition reserved"
        coroutine = _run_admitted_transition(operation_id, worker)
        try:
            asyncio.create_task(coroutine, name=f"session-transition-{operation_id}")
        except Exception as exc:
            coroutine.close()
            cause_type = type(exc).__name__
            await _invoke_transition_store(
                "advance",
                operation_id,
                TransitionOperationState.FAILED,
                detail="Session transition task could not be scheduled",
                failure=TransitionOperationFailure(
                    code="transition.scheduling.failed",
                    message="Session transition could not be scheduled",
                    cause_type=cause_type,
                ),
            )
            _active_transition_operation_id = None
            _local_transition_operation_id = None
            raise
        return operation_id


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
        log.error("Debug enable failed for %s: %s", source, exc, exc_info=True)
        await _publish_system_ops_event(
            "error",
            "DEBUG_ENABLE_FAILED",
            f"Failed to enable debug for {source}",
            {"cause_type": type(exc).__name__},
        )
        return False

    _debug_sources.add(source)

    if _debug_sub is None:
        try:
            js = nc.jetstream()
            from nats.js.api import DeliverPolicy

            _debug_sub = await js.subscribe(
                debug_subscribe_all_subject(),
                stream=STREAM_DEBUG_EVENTS,
                ordered_consumer=True,
                deliver_policy=DeliverPolicy.NEW,
                cb=_on_debug_event,
            )
        except Exception as exc:
            log.error("Failed to subscribe to debug stream: %s", exc, exc_info=True)
            await _publish_system_ops_event(
                "error",
                "DEBUG_SUBSCRIBE_FAILED",
                "Failed to subscribe to NODALARC_DEBUG",
                {"cause_type": type(exc).__name__},
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
    if is_operator_visible_ops_event(event):
        _system_ops_events.append(stamp_ops_event(event))

    log_level = getattr(logging, level.upper(), logging.INFO)
    log.log(log_level, "%s", message, extra={"code": code, "details": details})


# Operator-log visibility policy lives in ops_log (shared with the
# session context, which filters at append time).
_operator_visible_ops_events = operator_visible_ops_events


def _ops_events_tail(ctx, *, ops_after: int = 0) -> list[dict]:
    """Operator-visible ops events newer than the cursor, seq-ordered.

    ops_after=0 returns the full 500-tail (REST consumers, first frame
    of a fresh websocket); a connection's producer passes its cursor so
    steady-state frames carry only newly arrived events.
    """
    all_ops = _operator_visible_ops_events(
        list(_system_ops_events) + (list(ctx.session_ops_events) if ctx else [])
    )
    all_ops.sort(key=lambda e: e.get("seq", 0))
    if ops_after:
        all_ops = [e for e in all_ops if e.get("seq", 0) > ops_after]
    return all_ops[-500:]


@dataclass
class _SharedFrame:
    """One snapshot build, shared read-only by every connection.

    The per-client producers used to each rebuild and double-serialize
    the full snapshot every second under the state lock — O(clients ×
    session size) of identical work on the serving loop. One builder
    now produces the base frame once per second; each connection
    overlays only its per-connection increments (ops cursor slice,
    health-on-change) onto a shallow copy. Nested structures are shared
    read-only — overlays replace top-level keys, never mutate nested
    state.
    """

    frame: dict
    health_json: str


_shared_frame: _SharedFrame | None = None
_shared_frame_ready: asyncio.Event | None = None


def _overlay_connection_increments(
    shared: _SharedFrame,
    *,
    ops_cursor: int,
    last_health_json: str | None,
) -> tuple[dict, int, str]:
    """Per-connection view of a shared frame. Pure; never mutates shared.

    Returns (snapshot, new_ops_cursor, new_health_json): the ops list is
    sliced to events newer than the cursor (cursor 0 = first frame =
    full tail), and actuation_health is omitted when unchanged for this
    connection.
    """
    snapshot = dict(shared.frame)
    full_ops = snapshot.get("ops_events") or []
    if ops_cursor:
        snapshot["ops_events"] = [e for e in full_ops if e.get("seq", 0) > ops_cursor]
    new_cursor = ops_cursor
    if snapshot["ops_events"]:
        new_cursor = max(new_cursor, snapshot["ops_events"][-1].get("seq", 0))
    if shared.health_json == last_health_json:
        snapshot.pop("actuation_health", None)
    return snapshot, new_cursor, shared.health_json


def _carry_unsent_increments(pending: dict | None, snapshot: dict) -> dict:
    """Fold an UNSENT frame's increments into its replacement.

    The mailbox is latest-wins: a slow client's unshipped frame is
    overwritten. State fields (nodes, links, clock) are idempotent and
    safely replaced — but the incremental fields are deltas, and a delta
    that never shipped would be lost with its frame. Ops events from the
    pending frame prepend (both lists are seq-sorted and the pending
    ones are strictly older); an unsent actuation_health update carries
    into a replacement that omitted health as unchanged.
    """
    if pending is None:
        return snapshot
    pending_ops = pending.get("ops_events") or []
    if pending_ops:
        snapshot["ops_events"] = (pending_ops + (snapshot.get("ops_events") or []))[-500:]
    if "actuation_health" not in snapshot and "actuation_health" in pending:
        snapshot["actuation_health"] = pending["actuation_health"]
    return snapshot


def _build_snapshot(*, ops_after: int = 0) -> dict | None:
    """Build a StateSnapshot dict from the active SessionContext.

    Returns None if no active context (mid-transition or no session), or
    before the session clock has reported its sim time. Takes a local
    reference to _active_context to prevent mixed-state reads if the context
    is swapped mid-tick.
    """
    ctx = _active_context
    if ctx is None:
        return None

    with ctx.state_lock:
        if ctx.sim_time is None:
            return None
        sim_time = datetime.fromisoformat(ctx.sim_time)
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
        if ctx.continuous_tracer is not None:
            tp = ctx.continuous_tracer.traced_path
            if tp is not None:
                _traced.append(tp)

        snapshot = StateSnapshot(
            sim_time=sim_time,
            # Engine-stamped wall clock. During play this is the same
            # instant as sim_time (stamped together on each ClockTick);
            # during pause it advances via heartbeats while sim freezes —
            # that divergence is real and must display. Serve-time `now`
            # is only a fallback before the first engine signal arrives.
            wall_time=(
                datetime.fromisoformat(engine_wall)
                if (engine_wall := ctx.engine_wall_time())
                else now
            ),
            schema_version=1,
            session_id=ctx.session_id,
            nodes=nodes,
            links=links,
            kernel_actual_pairs=[[a, b] for (a, b) in sorted(ctx.actual_kernel_pairs())],
            traced_paths=_traced,
            recent_events=recent,
            network_health=health,
            routing_stack=ctx.routing_stack,
            constellation_name=ctx.constellation_name,
            session_status=_session_manager.status if _session_manager else None,
            session_status_detail=_session_manager.status_detail if _session_manager else None,
            playback_paused=ctx.playback_paused,
            playback_speed=ctx.playback_speed,
            playback_achieved=ctx.playback_achieved,
            pacing_degraded=ctx.pacing_degraded,
            stale=ctx.is_stale(),
            actuation_notices=list(ctx.actuation_notices_by_key.values()),
            ome_lifecycle_notices=list(ctx.ome_lifecycle_notices_by_key.values()),
            actuation_health=ctx.build_actuation_health(),
        )
        result = json.loads(snapshot.model_dump_json())
        # System + session OpsEvents merged for the log panel — shipped
        # INCREMENTALLY per connection (ops_after cursor): re-serializing
        # the whole 500-entry log into every 1 Hz frame measured 96% of a
        # 2.5 MB frame. The token lets clients detect a seq-space restart
        # and replace their scrollback instead of merging.
        result["ops_events"] = _ops_events_tail(ctx, ops_after=ops_after)
        result["ops_log_token"] = OPS_LOG_TOKEN
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
    resolution: SessionResolution
    runtime_config: ResolvedRuntimeConfig
    source_id: str
    generation: int
    # The deploy request's choice to keep this session run's history database.
    record_history: bool


def _as_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed > 0 else None


class CRSessionRefusal(ValueError):
    """The ConstellationSpec's content cannot become the active session.

    The message names the CR field at fault and nothing else.
    """


def _report_cr_bootstrap_failure(exc: Exception, session_manager: SessionManager | None) -> None:
    """Report one failed read of the runtime session CR during bootstrap.

    A transport failure is retried by the next poll. Any other failure means
    the CR exists and cannot become the active session: it stays a visible
    session error, with the refusal's own message when it is a typed refusal,
    until the CR changes.
    """
    if _poll_failure_is_transport(exc):
        log.warning("Reading the runtime session CR failed on transport; retrying: %s", exc)
        return
    log.error("The runtime session CR cannot be activated: %s", exc, exc_info=exc)
    if session_manager is None:
        return
    session_manager._status = "error"
    session_manager.status_detail = (
        str(exc)
        if isinstance(exc, (*REFUSAL_FAMILIES, CRSessionRefusal))
        else "The runtime session could not be activated; see the VS-API log"
    )


def _load_cr_runtime_session(
    spec: dict[str, Any],
    *,
    namespace: str,
    run_id: str,
    core_v1: Any | None = None,
) -> ResolvedRuntimeConfig:
    ConstellationSpecSpec.from_cr(spec)
    if core_v1 is None:
        core_v1 = k8s.core_v1()
    return load_cr_runtime_config(
        spec,
        core_v1=core_v1,
        namespace=namespace,
        source_origin="vs_api.cr",
        run_id=run_id,
        installed_shipped_root=_INSTALLED_SHIPPED_ROOT,
    )


def _cr_ready_identity(cr: dict[str, Any]) -> tuple[int, str] | None:
    """Cheap Ready-state identity from CR metadata/status: no YAML work.

    The 2-second trust poll's question is "did the authoritative session
    change?" — answerable from generation/runId/pod counts alone.
    Materializing the session (YAML parse + full resolution — measured
    0.494 s median in-pod for the 132-node flagship, 2026-06-11) is
    reserved for actual changes and must run off the event loop.
    """
    metadata = cr.get("metadata") or {}
    status = ConstellationSpecStatus.from_cr(cr.get("status"))
    generation = _as_positive_int(metadata.get("generation"))
    if generation is None or not status.observes_generation(generation):
        return None
    if status.phase != "Ready":
        return None
    if status.ready_pod_count() is None:
        return None
    if not status.session_run_id:
        raise CRSessionRefusal("Ready ConstellationSpec is missing status.sessionRunId")
    return generation, sanitize_session_id(status.session_run_id)


def _extract_ready_cr_session(cr: dict[str, Any]) -> CRSessionIdentity | None:
    """Return the CR session only when its Ready state is generation-consistent."""
    return _extract_cr_session(cr, require_ready=True)


def _extract_cr_session(
    cr: dict[str, Any],
    *,
    require_ready: bool,
    core_v1: Any | None = None,
) -> CRSessionIdentity | None:
    """Return the CR session only when status carries current runtime identity.

    Materializes the selected root and catalog-upload files, then resolves them.
    This is unbounded CPU and Kubernetes I/O that must never run on the event
    loop (call via asyncio.to_thread).
    """

    metadata = cr.get("metadata") or {}
    status = ConstellationSpecStatus.from_cr(cr.get("status"))
    spec = cr.get("spec") or {}
    if require_ready:
        ident = _cr_ready_identity(cr)
        if ident is None:
            return None
        generation, session_run_id = ident
    else:
        generation = _as_positive_int(metadata.get("generation"))
        if generation is None or not status.observes_generation(generation):
            return None
        if not status.session_run_id:
            return None
        session_run_id = sanitize_session_id(status.session_run_id)

    cr_spec = ConstellationSpecSpec.from_cr(spec)
    session_yaml = cr_spec.session_yaml

    runtime_namespace = metadata.get("namespace")
    if not isinstance(runtime_namespace, str) or not runtime_namespace:
        raise CRSessionRefusal("ConstellationSpec metadata has no namespace")
    runtime_config = _load_cr_runtime_session(
        spec,
        namespace=runtime_namespace,
        run_id=session_run_id,
        core_v1=core_v1,
    )
    resolution = runtime_config.resolution
    session = resolution.resolved
    proof = runtime_config.proof
    mismatches = status.runtime_mismatches(
        document_digest=proof.document_digest,
        closure_digest=proof.closure_digest,
        resolved_semantic_digest=proof.resolved_semantic_digest,
        release=_runtime_release_identity(),
        build=_runtime_build_identity(),
    )
    if mismatches:
        raise CRSessionRefusal(
            "ConstellationSpec status does not match verified runtime configuration: "
            + ", ".join(f"status.{key}" for key in mismatches)
        )
    status_name = status.session_name or ""
    if require_ready and not status_name:
        raise CRSessionRefusal("Ready ConstellationSpec is missing status.sessionName")
    if status_name and status_name != session.session.name:
        raise CRSessionRefusal(
            "ConstellationSpec status.sessionName does not match spec.session.name "
            f"({status_name!r} != {session.session.name!r})"
        )
    source_id = (metadata.get("annotations") or {}).get(SOURCE_ID_ANNOTATION)
    if not isinstance(source_id, str) or not source_id:
        raise CRSessionRefusal(
            f"ConstellationSpec is missing the {SOURCE_ID_ANNOTATION} annotation"
        )
    return CRSessionIdentity(
        session_id=session_run_id,
        session_name=session.session.name,
        session_yaml=session_yaml,
        session=session,
        resolution=resolution,
        runtime_config=runtime_config,
        source_id=source_id,
        generation=generation,
        record_history=cr_spec.record_history,
    )


def _history_path(identity: CRSessionIdentity) -> Path | None:
    """The history file of a recorded session run; None for a session not recorded.

    VS-API owns every history file: one per recorded run, named by its run id,
    under the platform's session data root.
    """
    if not identity.record_history:
        return None
    root = Path(get_platform_config().session_data_root)
    return root / "history" / f"{identity.session_id}.db"


def _extract_current_cr_session(cr: dict[str, Any]) -> CRSessionIdentity | None:
    """Return any current-generation CR session with a runtime identity."""
    return _extract_cr_session(cr, require_ready=False)


async def _reconcile_interrupted_transition(cr: dict[str, Any] | None) -> None:
    """Reconcile a persisted, non-local operation from trusted live CR state."""

    global _active_transition_operation_id
    try:
        active = await _invoke_transition_store("active")
        if active is None:
            if _local_transition_operation_id is None:
                _active_transition_operation_id = None
            return
        if active.operation_id == _local_transition_operation_id:
            return
        reconciliation = await asyncio.to_thread(reconcile_transition_operation, active, cr)
        runtime_mismatch = (
            reconciliation.failure is not None
            and reconciliation.failure.code == "transition.recovery.runtime_mismatch"
        )
        if cr is not None and active.provenance.runtime_plan is not None and not runtime_mismatch:
            observation = _constellation_spec_observation(cr)
            active = await _invoke_transition_store(
                "update_provenance",
                active.operation_id,
                TransitionOperationProvenancePatch(constellation_spec=observation),
            )
        if reconciliation.disposition is TransitionReconciliationDisposition.STILL_SWITCHING:
            _active_transition_operation_id = active.operation_id
            if not (
                active.state is TransitionOperationState.SWITCHING
                and active.events[-1].detail == reconciliation.detail
            ):
                await _invoke_transition_store(
                    "advance",
                    active.operation_id,
                    TransitionOperationState.SWITCHING,
                    detail=reconciliation.detail,
                )
            return
        state = {
            TransitionReconciliationDisposition.SUCCEEDED: TransitionOperationState.SUCCEEDED,
            TransitionReconciliationDisposition.FAILED: TransitionOperationState.FAILED,
            TransitionReconciliationDisposition.CANCELLED: TransitionOperationState.CANCELLED,
        }[reconciliation.disposition]
        failure = reconciliation.failure
        runtime = reconciliation.runtime
        detail = reconciliation.detail
        if state is TransitionOperationState.SUCCEEDED:
            try:
                verified = await asyncio.to_thread(
                    _extract_cr_session,
                    cr,
                    require_ready=True,
                )
                if verified is None:
                    raise ValueError("Ready runtime identity is not generation-consistent")
                runtime = TransitionRuntimeResult(
                    session_id=verified.session_id,
                    generation=verified.generation,
                )
                if runtime != reconciliation.runtime:
                    raise ValueError("verified runtime identity differs from status proof")
            except Exception as exc:
                state = TransitionOperationState.FAILED
                runtime = None
                detail = "Selected runtime could not be verified after restart"
                failure = TransitionOperationFailure(
                    code="transition.runtime.verification_failed",
                    message="Selected runtime could not be verified",
                    cause_type=type(exc).__name__,
                )
        await _invoke_transition_store(
            "advance",
            active.operation_id,
            state,
            detail=detail,
            failure=failure,
            runtime=runtime,
        )
        _active_transition_operation_id = None
    except Exception:
        # Fail closed: an unreadable or unreconciled operation remains active in
        # the durable store and admission continues to refuse another switch.
        log.error("Transition operation startup reconciliation failed", exc_info=True)


def _mark_session_manager_ready(_session: ResolvedSession, source_id: str) -> None:
    """Steady-state CR reconciliation: status bookkeeping only.

    This runs on every 2-second trust poll. It must never do unbounded
    work such as catalog listing or session resolution on the event loop.
    """
    if not _session_manager:
        return
    _session_manager.set_active(source_id)
    _session_manager._status = "ready"
    _session_manager.status_detail = ""


async def _activate_session_context_from_cr(
    ready: CRSessionIdentity,
    source: str,
    *,
    transition_already_started: bool = False,
) -> None:
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
        _mark_session_manager_ready(ready.session, ready.source_id)
        return

    old_session = old_ctx.session_id if old_ctx else None

    if not transition_already_started:
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
    elif old_ctx is not None:
        raise RuntimeError("catalog transition callback did not release the old SessionContext")

    new_ctx = await asyncio.to_thread(
        SessionContext,
        ready.session_id,
        resolution=ready.resolution,
        source_id=ready.source_id,
        history_path=_history_path(ready),
    )
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
    _mark_session_manager_ready(ready.session, ready.source_id)

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


async def _monitor_cr_session(api: Any, core_v1_api: Any, namespace: str) -> None:
    """Continuously reconcile VS-API SessionContext with the authoritative CR.

    Steady state must stay cheap AND off the event loop: the poll answers
    "did the session change?" from CR metadata alone (_cr_ready_identity).
    Materialization (YAML parse + full resolution — measured 0.494 s
    median in-pod for the 132-node flagship) happens only when identity
    changes, in a worker thread. Running it per-poll on the loop froze
    every websocket sender ~0.5 s every 2 s — mostly BELOW the watchdog's
    old 0.5 s floor, so the freeze was live for hours with zero warnings
    (incident 2026-06-11, second occurrence).

    The single-entry cache below is a memo of the last materialization,
    keyed by the exact (generation, session_run_id) it was parsed from.
    The CR remains the source of truth: any identity change reparses.
    """

    global _active_cr_generation

    log.info("CR session monitor started")
    cached: CRSessionIdentity | None = None
    next_upload_reconciliation = 0.0
    while True:
        await asyncio.sleep(_CR_MONITOR_INTERVAL_SECONDS)
        try:
            cr = await asyncio.to_thread(
                api.get_namespaced_custom_object,
                group=CR_GROUP,
                version=CR_VERSION,
                namespace=namespace,
                plural=CR_PLURAL,
                name=CR_NAME,
            )
            await _reconcile_interrupted_transition(cr)
            now = asyncio.get_running_loop().time()
            if now >= next_upload_reconciliation:
                await _reconcile_catalog_upload_lifecycle(
                    cr,
                    core_v1_api=core_v1_api,
                    namespace=namespace,
                )
                next_upload_reconciliation = now + _CATALOG_UPLOAD_RECONCILE_INTERVAL_SECONDS
            ident = _cr_ready_identity(cr)
            if ident is None:
                continue
            generation, run_id = ident

            ctx = _active_context
            if (
                ctx is not None
                and ctx.session_id == run_id
                and _active_cr_generation in (None, generation)
                and cached is not None
                and (cached.generation, cached.session_id) == ident
            ):
                _active_cr_generation = generation
                _mark_session_manager_ready(cached.session, cached.source_id)
                continue

            ready = await asyncio.to_thread(
                _extract_cr_session,
                cr,
                require_ready=True,
                core_v1=core_v1_api,
            )
            if ready is None:
                continue
            cached = ready

            ctx = _active_context
            if (
                ctx is not None
                and ctx.session_id == ready.session_id
                and _active_cr_generation in (None, ready.generation)
            ):
                _active_cr_generation = ready.generation
                _mark_session_manager_ready(ready.session, ready.source_id)
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
                    _mark_session_manager_ready(ready.session, ready.source_id)
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


async def _shared_frame_builder() -> None:
    """Build the snapshot once per second for ALL websocket clients.

    Replaces N identical per-connection builds (full pydantic dump +
    re-parse under the state lock, per client, per second) with one.
    Each cycle publishes a fresh _SharedFrame and wakes every waiting
    connection by setting the previous ready-event; connections overlay
    their own increments on shallow copies. Builds are skipped while no
    clients are connected.
    """
    global _shared_frame, _shared_frame_ready
    import time as _time

    _shared_frame_ready = asyncio.Event()
    next_build = _time.monotonic()
    while True:
        if _ws_clients:
            frame = _build_snapshot()
            if frame is not None:
                _shared_frame = _SharedFrame(
                    frame=frame,
                    health_json=json.dumps(frame.get("actuation_health"), sort_keys=True),
                )
                previous_ready = _shared_frame_ready
                _shared_frame_ready = asyncio.Event()
                previous_ready.set()
        next_build += 1.0
        delay = next_build - _time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            next_build = _time.monotonic()
            await asyncio.sleep(0)


async def _event_loop_watchdog() -> None:
    """Permanent loop-responsiveness monitor.

    Sleeps 0.5 s and measures its own scheduling drift: when something
    blocks the event loop, every consumer sharing it (websocket senders,
    NATS handlers) stalls together and this task wakes late. The warn
    floor is 0.2 s — at the 1 Hz feed cadence that is already 20% of a
    frame budget. The original 0.5 s floor hid a real 0.49 s-every-2 s
    freeze for hours (trust-poll resolution, 2026-06-11): a detection
    threshold at the exact magnitude of the defect class is no detection
    at all. Companion to the static gate in
    tests/unit/test_event_loop_contract.py.
    """
    import time as _t

    while True:
        before = _t.monotonic()
        await asyncio.sleep(0.5)
        stall = _t.monotonic() - before - 0.5
        if stall > 0.2:
            log.warning(
                "Event loop blocked ~%.2fs: synchronous work is running on "
                "the serving loop — find it (PYTHONASYNCIODEBUG=1 names the "
                "task) and offload it with asyncio.to_thread",
                stall,
            )


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
        await nc.subscribe(wiring_progress_subscribe_subject(), cb=_on_wiring_progress)
    except Exception as exc:
        log.warning("Wiring progress subscription failed: %s", exc)

    # System OpsEvents — global, not session-scoped. Routine telemetry
    # is filtered at append so it never evicts real history from the
    # 500-entry log window.
    async def _on_system_ops_event(msg):
        with contextlib.suppress(Exception):
            event = json.loads(msg.data)
            if is_operator_visible_ops_event(event):
                _system_ops_events.append(stamp_ops_event(event))

    try:
        js = nc.jetstream()
        from nats.js.api import DeliverPolicy

        await js.subscribe(
            ops_subscribe_all_subject(),
            stream=STREAM_OPS_EVENTS,
            ordered_consumer=True,
            deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
            cb=_on_system_ops_event,
        )
    except Exception as exc:
        log.warning("System OpsEvent subscription failed: %s", exc)

    # Bootstrap from the live ConstellationSpec. Its root YAML and catalogUpload
    # selection identify the ordinary files that every runtime consumer verifies
    # and resolves through the shared configuration path.
    _cr_api = await asyncio.to_thread(k8s.custom_objects)
    _cr_core_api = await asyncio.to_thread(k8s.core_v1)
    _cr_ns = get_platform_config().kubernetes_namespace

    _cr_session: CRSessionIdentity | None = None
    _cr_phase = ""
    _cr_message = ""

    def _candidate_from_cr(cr: dict[str, Any]) -> CRSessionIdentity | None:
        nonlocal _cr_phase, _cr_message
        observed = ConstellationSpecStatus.from_cr(cr.get("status"))
        _cr_phase = observed.phase or ""
        _cr_message = observed.message or ""
        if _cr_phase == "Ready":
            return _extract_ready_cr_session(cr)
        if _cr_phase in ("Pending", "Creating", "Wiring"):
            return _extract_current_cr_session(cr)
        if _cr_phase == "Error" and cr_status_observes_current_generation(cr):
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
            _cr = await asyncio.to_thread(
                _cr_api.get_namespaced_custom_object,
                group=CR_GROUP,
                version=CR_VERSION,
                namespace=_cr_ns,
                plural=CR_PLURAL,
                name=CR_NAME,
            )
            await _reconcile_interrupted_transition(_cr)
            await _reconcile_catalog_upload_lifecycle(
                _cr,
                core_v1_api=_cr_core_api,
                namespace=_cr_ns,
            )
            _cr_session = await asyncio.to_thread(_candidate_from_cr, _cr)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                # No ConstellationSpec yet: the session has not been deployed.
                await _reconcile_interrupted_transition(None)
                with contextlib.suppress(Exception):
                    await _reconcile_catalog_upload_lifecycle(
                        None,
                        core_v1_api=_cr_core_api,
                        namespace=_cr_ns,
                    )
            else:
                _report_cr_bootstrap_failure(exc, _session_manager)
        if _cr_session is None:
            log.info("No active Ready or wiring runtime session CR — waiting for session to deploy")
            await asyncio.sleep(5)

    if _cr_phase in ("Pending", "Creating", "Wiring"):
        log.info("CR phase=%s — waiting for Ready before activating SessionContext", _cr_phase)
        if _session_manager:
            _session_manager._status = "wiring"
            _session_manager.status_detail = _cr_message or f"Status: {_cr_phase}"
            _session_manager.set_active(_cr_session.source_id)
        if not _started_pending_poll:
            asyncio.ensure_future(_poll_cr_until_ready())
        if _cr_monitor_task is None or _cr_monitor_task.done():
            _cr_monitor_task = asyncio.create_task(
                _monitor_cr_session(_cr_api, _cr_core_api, _cr_ns),
                name="cr-session-monitor",
            )
    elif _cr_phase != "Ready":
        log.info("CR phase=%s has no active session context", _cr_phase)
    else:
        session_id = _cr_session.session_id

        log.info(
            "Bootstrapping session %s from CR (name=%s phase=%s)",
            session_id,
            _cr_session.session_name,
            _cr_phase,
        )

        if _session_manager:
            _session_manager._status = "ready"
            _session_manager.status_detail = ""
            _session_manager.set_active(_cr_session.source_id)

        ctx = await asyncio.to_thread(
            SessionContext,
            session_id,
            resolution=_cr_session.resolution,
            source_id=_cr_session.source_id,
            history_path=_history_path(_cr_session),
        )
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
                _monitor_cr_session(_cr_api, _cr_core_api, _cr_ns),
                name="cr-session-monitor",
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
    # The loop holds only weak references to tasks: a discarded handle
    # makes a "permanent" task garbage-collection-eligible.
    watchdog_task = asyncio.create_task(_event_loop_watchdog(), name="event-loop-watchdog")
    frame_builder_task = asyncio.create_task(_shared_frame_builder(), name="shared-frame-builder")

    def _on_subscriber_done(task: asyncio.Task) -> None:
        exc = task.exception() if not task.cancelled() else None
        if exc:
            log.error("NATS subscriber task DIED with exception: %s", exc, exc_info=exc)
        elif task.cancelled():
            log.info("NATS subscriber task cancelled")
        else:
            log.warning(
                "NATS subscriber task for session state exited without error - "
                "no further snapshots will be consumed until restart"
            )

    sub_task.add_done_callback(_on_subscriber_done)

    recorder_task = asyncio.create_task(
        _history_snapshot_recorder(), name="history-snapshot-recorder"
    )

    def _on_recorder_done(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.error(
                "History snapshot recorder DIED; no further snapshots are recorded until "
                "restart: %s",
                task.exception(),
                exc_info=task.exception(),
            )

    recorder_task.add_done_callback(_on_recorder_done)

    yield

    sub_task.cancel()
    recorder_task.cancel()
    watchdog_task.cancel()
    frame_builder_task.cancel()


app = FastAPI(title="Nodal Arc VS-API", version=project_version(), lifespan=lifespan)
install_refusal_handlers(app)
# OpenAPI for the routes that refuse through the envelope: every refusal is an
# ApiRefusal; a 422 is also FastAPI's own request-validation body.
_REFUSAL_RESPONSES = {
    **{status: {"model": ApiRefusal} for status in (400, 404, 409, 500, 502, 503)},
    422: {
        "description": "Refused, or the request failed validation",
        "content": {
            "application/json": {
                "schema": {
                    "anyOf": [
                        {"$ref": "#/components/schemas/ApiRefusal"},
                        {"$ref": "#/components/schemas/HTTPValidationError"},
                    ]
                }
            }
        },
    },
}
app.mount(
    "/docs/ops",
    StaticFiles(directory=Path(__file__).resolve().parents[2] / "docs" / "ops"),
    name="configuration-docs",
)
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
_BUILDER_PATH_PREFIX = "/api/v1/builder/"
_BUILDER_BODY_BYTES = 4 * DEFAULT_CATALOG_UPLOAD_LIMITS.max_aggregate_bytes + 1_048_576


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
    """Bound actual HTTP body bytes before request parsing. Passes WebSocket through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        limit = _BUILDER_BODY_BYTES if path.startswith(_BUILDER_PATH_PREFIX) else _MAX_BODY_BYTES
        content_length = next(
            (
                value
                for name, value in scope.get("headers", ())
                if name.lower() == b"content-length"
            ),
            None,
        )
        try:
            declared_length = int(content_length) if content_length is not None else None
        except TypeError, ValueError:
            declared_length = None
        if declared_length is not None and declared_length > limit:
            response = JSONResponse(status_code=413, content={"error": "Request body too large"})
            await response(scope, receive, send)
            return

        messages = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > limit:
                response = JSONResponse(
                    status_code=413,
                    content={"error": "Request body too large"},
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        iterator = iter(messages)

        async def replay_receive():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()

        await self.app(scope, replay_receive, send)


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
    ctx = _require_active_context()
    with ctx.state_lock:
        return ctx.build_actuation_health()


@app.post("/api/v1/ops/repair", dependencies=[Depends(_require_api_key)])
async def request_operator_repair(body: dict) -> dict:
    """Explicit operator-triggered GS repair routed to the reporting Scheduler."""
    ctx = _require_active_context()
    nc = _nats_connection
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
        log.warning("Scheduler repair request failed for %s: %s", gs_id, exc, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Scheduler repair request failed"})


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


def _require_active_context(detail: str = "") -> SessionContext:
    """The active session context; a request without one is refused."""
    ctx = _active_context
    if ctx is None:
        raise SessionInactiveError(detail)
    return ctx


def _public_no_active_session_detail(status: str) -> str:
    """Return public lifecycle text without echoing internal failure details."""
    if status == "switching":
        return "Session switch in progress"
    if status == "wiring":
        return "Session wiring in progress"
    if status == "error":
        return "Session is not active; check operational events or server logs"
    if status == "ready":
        return "Session state is not available yet"
    return ""


async def _history_snapshot_recorder() -> None:
    """Record a full state snapshot about every ten seconds for a recorded session."""
    tick = 0
    while True:
        await asyncio.sleep(0.1)
        tick += 1
        ctx = _active_context
        if tick % 100 == 0 and ctx is not None and ctx.history_path is not None:
            snapshot = _build_snapshot()
            if snapshot is None:
                continue
            await asyncio.to_thread(ctx.record_snapshot, snapshot)


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
        # Latest-wins delivery (the websocket form of replace-not-merge):
        # the producer fills a one-slot mailbox on an absolute 1 s
        # schedule and never blocks on the socket; the shipper always
        # sends the freshest state. A client slower than the feed gets
        # fewer-but-current frames — backpressure skips frames instead of
        # queueing them, so a slow consumer's clock lag stays bounded at
        # ~one consumption period rather than growing without limit
        # (measured live 2026-06-11: a 1.6 s/frame consumer accumulated
        # +0.6 s of staleness per queued frame under the old send loop).

        latest: list[dict | None] = [None]
        fresh = asyncio.Event()

        async def _producer():
            # Per-connection increment state: ops events ship once (seq
            # cursor) and actuation_health ships on change — re-sending
            # both wholesale measured 98% of the frame. The frame itself
            # is built ONCE per second by the shared builder; this task
            # only overlays this connection's increments on a copy.
            ops_cursor = 0
            last_health_json: str | None = None
            while not done.is_set():
                ready = _shared_frame_ready
                if ready is None:
                    await asyncio.sleep(0.2)
                    continue
                await ready.wait()
                shared = _shared_frame
                if shared is None:
                    continue
                snapshot, ops_cursor, last_health_json = _overlay_connection_increments(
                    shared, ops_cursor=ops_cursor, last_health_json=last_health_json
                )
                # A still-unshipped previous frame is about to be
                # overwritten — its increments must survive into the
                # replacement or a slow client silently loses them.
                pending = latest[0] if fresh.is_set() else None
                latest[0] = _carry_unsent_increments(pending, snapshot)
                fresh.set()

        producer_task = asyncio.create_task(_producer())
        try:
            while not done.is_set():
                await fresh.wait()
                fresh.clear()
                snapshot = latest[0]
                if snapshot is not None:
                    await websocket.send_json(snapshot)
        finally:
            producer_task.cancel()

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
    from vs_api.terminal import (
        ExecTerminalSession,
        TerminalSession,
        _load_ssh_key,
        resolve_pod_terminal,
    )

    ws_ip = websocket.client.host if websocket.client else "unknown"
    if _API_KEY:
        token = websocket.query_params.get("token", "")
        if token != _API_KEY:
            _audit_log.warning(f"WS_TERMINAL_AUTH_FAIL ip={ws_ip} node={node_id}")
            await websocket.close(code=4401, reason="Unauthorized")
            return

    # Resolve node_id to pod + terminal contract (async — runs the K8s
    # API call in a thread executor so it doesn't block active sessions).
    namespace = get_platform_config().kubernetes_namespace
    resolved = await resolve_pod_terminal(node_id, namespace)
    if resolved is None:
        await websocket.close(code=4404, reason="Node not found")
        return
    pod_name, pod_ip, contract = resolved
    if contract is None:
        # The workload declares no terminal surface: refuse typed and
        # immediately — never spin dialing a pod that cannot answer.
        _audit_log.info(f"WS_TERMINAL_NO_SURFACE ip={ws_ip} node={node_id}")
        await websocket.accept()
        await websocket.send_text(
            json.dumps(
                {
                    "type": "output",
                    "data": (
                        f"\r\nNode {node_id} runs a workload that declares "
                        "no terminal access.\r\nInspect it with its own "
                        "logs and tooling instead.\r\n"
                    ),
                }
            )
        )
        await websocket.close(code=4409, reason="Workload declares no terminal access")
        return

    if contract["surface"] == "ssh":
        # Load SSH key (cached in memory after first call — never written
        # to disk). The Secret freshness check is a sync K8s read.
        try:
            ssh_key = await asyncio.to_thread(_load_ssh_key, namespace)
        except RuntimeError as e:
            log.warning("Terminal key error: %s", e)
            await websocket.close(code=4503, reason="Terminal key unavailable")
            return
        session = TerminalSession(pod_ip, ssh_key)
    else:
        session = ExecTerminalSession(
            namespace, pod_name, contract["container"], contract["command"]
        )

    await websocket.accept()
    _audit_log.info(
        f"WS_TERMINAL_CONNECT ip={ws_ip} node={node_id} pod_ip={pod_ip} "
        f"surface={contract['surface']}"
    )
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
    from vs_api.terminal import TerminalSession, _load_ssh_key, resolve_pod_terminal

    namespace = get_platform_config().kubernetes_namespace
    resolved = await resolve_pod_terminal(node_id, namespace)
    if resolved is None:
        return JSONResponse(status_code=404, content={"error": "Node not found"})
    _pod_name, pod_ip, contract = resolved
    if contract is None or contract["surface"] != "ssh":
        return JSONResponse(
            status_code=409,
            content={"error": "Configuration export requires an ssh terminal surface"},
        )

    try:
        ssh_key = await asyncio.to_thread(_load_ssh_key, namespace)
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
def get_state() -> dict:
    """Current state snapshot."""
    snapshot = _build_snapshot()
    if snapshot is None:
        session_status = _session_manager.status if _session_manager else "idle"
        raise SessionInactiveError(_public_no_active_session_detail(session_status))
    return snapshot


def _history_session() -> tuple[SessionContext | None, Response | None]:
    """The active session whose history is readable, or the refusal that says why not."""
    ctx = _require_active_context()
    if ctx.history_path is None:
        return None, refusal_response(
            409, "history.not_recorded", "History recording is off for this session"
        )
    if ctx.history_error is not None:
        return None, refusal_response(
            503,
            "history.failed",
            "History recording failed for this session and stopped; see the VS-API log",
        )
    return ctx, None


def _read_history(ctx: SessionContext, read: Callable[[sqlite3.Connection], Any]) -> Any:
    conn = sqlite3.connect(f"file:{ctx.history_path}?mode=ro", uri=True)
    try:
        return read(conn)
    finally:
        conn.close()


@app.get(
    "/api/v1/state/{sim_time}",
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key)],
)
def get_historical_state(sim_time: str) -> Any:
    """The recorded session's state nearest a sim_time, from its history snapshots."""
    ctx, refusal = _history_session()
    if refusal is not None:
        return refusal
    result = _read_history(
        ctx, lambda conn: query_nearest_snapshot(conn, session_id=ctx.session_id, sim_time=sim_time)
    )
    if result is None:
        return refusal_response(
            404, "history.no_snapshot", "No state snapshot is recorded for this session yet"
        )
    return json.loads(result["snapshot_json"])


@app.get(
    "/api/v1/links",
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key)],
)
def get_link_events(
    start: str = Query(None),
    end: str = Query(None),
    node: str = Query(None),
    peer: str = Query(None, description="The node at the other end of the link; needs node"),
    order: Literal["oldest_first", "newest_first"] = Query("oldest_first"),
    limit: int = Query(LINK_HISTORY_PAGE_MAX, ge=1, le=LINK_HISTORY_PAGE_MAX),
    cursor: str = Query(None, description="next_cursor of the previous page"),
) -> Any:
    """One page of the recorded session's link events, optionally for one node's
    links or one link. Further pages follow ``next_cursor`` with the same filters."""
    ctx, refusal = _history_session()
    if refusal is not None:
        return refusal
    if peer is not None and node is None:
        return refusal_response(
            400, "history.peer_without_node", "A peer filter needs the node at the link's other end"
        )
    after = None
    if cursor is not None:
        after = _decode_link_history_cursor(cursor)
        if after is None:
            return refusal_response(
                400, "history.invalid_cursor", "The cursor is not one this API returned"
            )
    filters = {"start_time": start, "end_time": end, "node": node, "peer": peer}

    def read(conn: sqlite3.Connection) -> tuple[list[dict], int, str | None]:
        # One read transaction: the page, its total and retained_from describe the
        # same state of the file. One row past the page tells whether another follows.
        conn.execute("BEGIN")
        rows = query_link_events(
            conn,
            session_id=ctx.session_id,
            newest_first=order == "newest_first",
            after=after,
            limit=limit + 1,
            **filters,
        )
        total = count_link_events(conn, session_id=ctx.session_id, **filters)
        retained_from = get_metadata(conn, session_id=ctx.session_id, key=RETAINED_FROM_KEY)
        return rows, total, retained_from

    rows, total, retained_from = _read_history(ctx, read)
    page = rows[:limit]
    return LinkHistoryPage(
        events=[LinkHistoryEvent.model_validate(row) for row in page],
        returned=len(page),
        total=total,
        next_cursor=_link_history_cursor(page[-1]) if len(rows) > limit else None,
        retained_from=retained_from,
    )


_LINK_HISTORY_CURSOR = TypeAdapter(tuple[str, int])


def _link_history_cursor(row: dict) -> str:
    """The opaque cursor after one link event: its (sim_time, id) position."""
    return base64.urlsafe_b64encode(
        _LINK_HISTORY_CURSOR.dump_json((row["sim_time"], row["id"]))
    ).decode()


def _decode_link_history_cursor(cursor: str) -> tuple[str, int] | None:
    try:
        return _LINK_HISTORY_CURSOR.validate_json(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError:
        return None


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
    ctx = _require_active_context()
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

    ctx = _require_active_context()
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
    ctx = _require_active_context()
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
    ctx = _require_active_context()
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


@app.get(
    "/api/v1/metrics/convergence",
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key)],
)
def get_convergence_events() -> JSONResponse:
    """Refused: VS-API records no convergence events, so a history would be empty."""
    return refusal_response(
        501, "history.not_collected", "VS-API does not record convergence events"
    )


@app.get(
    "/api/v1/metrics/flows/{flow_id}",
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key)],
)
def get_flow_metrics() -> JSONResponse:
    """Refused: VS-API records no probe results, so a history would be empty."""
    return refusal_response(501, "history.not_collected", "VS-API does not record probe results")


# --- Path trace endpoints ---

ONE_SHOT_TRACE_FLOW_ID = "__trace__"


# One one-shot trace runs at a time: each holds traceroute execs and threads
# for up to the trace deadline.
_one_shot_trace_lock = asyncio.Lock()


def _trace_request_endpoints(ctx: SessionContext, body: dict) -> tuple[str, str] | JSONResponse:
    """The request's source and destination nodes, or the refusal that says why not."""
    src = body.get("src_node", "")
    dst = body.get("dst_node", "")
    if not src or not dst:
        return refusal_response(400, "trace.invalid_request", "src_node and dst_node are required")
    with ctx.state_lock:
        for node_id in (src, dst):
            if node_id not in ctx.nodes:
                return refusal_response(404, "trace.unknown_node", f"Unknown node: {node_id}")
    # A trace records the session's sim time; refuse until the clock reports.
    ctx.read_sim_time()
    return src, dst


@app.post("/api/v1/trace", responses=_REFUSAL_RESPONSES, dependencies=[Depends(_require_api_key)])
async def trace_between(body: dict) -> Any:
    """Trace the path between two nodes once, in both directions.

    The request returns when both traceroutes finish, within the trace
    deadline. One one-shot trace runs at a time; another request is refused
    while it runs.
    """
    ctx = _require_active_context()
    endpoints = _trace_request_endpoints(ctx, body)
    if isinstance(endpoints, JSONResponse):
        return endpoints
    src, dst = endpoints
    if _one_shot_trace_lock.locked():
        return refusal_response(
            409, "trace.busy", "A one-shot trace is already running; try again when it finishes"
        )
    async with _one_shot_trace_lock:
        result = await asyncio.to_thread(
            _create_path_tracer(ctx).trace_between, src, dst, flow_id=ONE_SHOT_TRACE_FLOW_ID
        )
    return result.model_dump(mode="json")


@app.post(
    "/api/v1/trace/start", responses=_REFUSAL_RESPONSES, dependencies=[Depends(_require_api_key)]
)
async def start_continuous_trace(body: dict) -> Any:
    """Start continuous path tracing between two nodes, replacing a running trace.

    The trace stops on its own after the platform's trace time limit.
    """
    ctx = _require_active_context()
    endpoints = _trace_request_endpoints(ctx, body)
    if isinstance(endpoints, JSONResponse):
        return endpoints
    src, dst = endpoints
    async with ctx.trace_lock:
        # A context that began tearing down takes no new trace.
        if ctx.stopped or ctx is not _active_context:
            raise SessionInactiveError("Session switch in progress")
        if ctx.continuous_tracer is not None:
            await ctx.continuous_tracer.stop()
            ctx.continuous_tracer = None
        tracer = _create_continuous_tracer(ctx)
        await tracer.start(src, dst)
        ctx.continuous_tracer = tracer
    return {"ok": True, "src": src, "dst": dst}


@app.post(
    "/api/v1/trace/stop", responses=_REFUSAL_RESPONSES, dependencies=[Depends(_require_api_key)]
)
async def stop_continuous_trace() -> dict:
    """Stop continuous path tracing and forget its result."""
    ctx = _require_active_context()
    async with ctx.trace_lock:
        if ctx.continuous_tracer is not None:
            await ctx.continuous_tracer.stop()
            ctx.continuous_tracer = None
    return {"ok": True}


@app.get(
    "/api/v1/trace/status", responses=_REFUSAL_RESPONSES, dependencies=[Depends(_require_api_key)]
)
def get_trace_status() -> dict:
    """The running trace's endpoints and latest result, or that none runs."""
    tracer = _require_active_context().continuous_tracer
    if tracer is None:
        return {"active": False, "src": None, "dst": None, "result": None}
    result = tracer.traced_path
    return {
        "active": tracer.active,
        "src": tracer.src,
        "dst": tracer.dst,
        "result": result.model_dump(mode="json") if result is not None else None,
    }


def _create_path_tracer(ctx: SessionContext) -> PathTracer:
    """A path tracer over the session's resolved nodes, reading that session's sim time."""
    return PathTracer(
        node_registry=tracer_node_registry(ctx.session_resolution),
        namespace=get_platform_config().kubernetes_namespace,
        core_v1=k8s.core_v1,
        read_sim_time=ctx.read_sim_time,
    )


def _create_continuous_tracer(ctx: SessionContext) -> ContinuousTracer:
    """A live tracer bound to the session, recording its path changes there."""
    return ContinuousTracer(
        path_tracer=_create_path_tracer(ctx),
        interval_s=get_platform_config().trace_interval_seconds,
        unreached_retrace_s=get_platform_config().trace_unreached_retrace_seconds,
        max_seconds=get_platform_config().trace_max_seconds,
        on_path_change=ctx.record_path_change,
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


@app.get(
    "/api/v1/sessions",
    response_model=list[CatalogSessionSummary],
    dependencies=[Depends(_require_api_key)],
)
def list_sessions(
    catalog_context: CatalogContext = Depends(get_catalog_context),
) -> tuple[CatalogSessionSummary, ...]:
    """List shipped and user sessions in the request catalog scope."""
    if _session_manager is None:
        return ()
    return CatalogSessionService(catalog_context).list_sessions(
        active_session_ref=_session_manager.active_source_id,
        available_node_count=_available_session_node_count(),
    )


@app.get(
    "/api/v1/sessions/yaml",
    response_class=Response,
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key)],
)
def download_session_yaml(
    session_ref: str = Query(...),
    catalog_context: CatalogContext = Depends(get_catalog_context),
) -> Response:
    """Return exact stored YAML for one scoped catalog session reference."""
    root_yaml = CatalogSessionService(catalog_context).read_session_yaml(session_ref)
    return Response(content=root_yaml, media_type="application/yaml")


def _available_session_node_count() -> int:
    """The number of nodes that accept session pods; a failed listing raises."""
    return len(available_session_nodes(k8s.core_v1()))


def _prepared_transition_reservation(deployment: Any) -> TransitionOperationReservation:
    prepared = deployment.prepared
    repository_generation = deployment.repository_generation
    selection = deployment.upload.selection
    return TransitionOperationReservation(
        source=TransitionOperationSource(
            kind=TransitionOperationSourceKind.CATALOG_SESSION,
            logical_id=str(prepared.source.logical_id),
        ),
        facts=TransitionOperationFacts(
            document_digest=prepared.document_digest,
            closure_digest=prepared.closure_digest,
            resolved_semantic_digest=prepared.resolved_semantic_digest,
            file_count=prepared.file_count,
            total_bytes=prepared.total_bytes,
            release=_runtime_release_identity(),
            build=_runtime_build_identity(),
        ),
        provenance=TransitionOperationProvenance(
            source_revision=prepared.source_revision,
            repository_generation=(
                str(repository_generation) if repository_generation is not None else None
            ),
            upload_id=selection.upload_id,
            runtime_plan=TransitionRuntimePlan(
                namespace=get_platform_config().kubernetes_namespace,
                name=CR_NAME,
            ),
        ),
    )


def _nonempty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_nonnegative_int(value: Any) -> int | None:
    try:
        result = int(value)
    except TypeError, ValueError:
        return None
    return result if result >= 0 else None


def _constellation_spec_observation(cr: Any) -> TransitionConstellationSpecObservation:
    """Normalize exact server-observed CR identity and status proof."""

    if not isinstance(cr, dict):
        raise TypeError("ConstellationSpec observation must be a mapping")
    metadata = cr.get("metadata") or {}
    status = ConstellationSpecStatus.from_cr(cr.get("status"))
    namespace = _nonempty_string(metadata.get("namespace"))
    name = _nonempty_string(metadata.get("name"))
    generation = _as_positive_int(metadata.get("generation"))
    if namespace is None or name is None or generation is None:
        raise ValueError("ConstellationSpec observation is missing Kubernetes identity")
    return TransitionConstellationSpecObservation(
        namespace=namespace,
        name=name,
        generation=generation,
        status=TransitionRuntimeStatusProof(
            observed_generation=_as_positive_int(status.observed_generation),
            phase=status.phase,
            session_id=status.session_run_id or None,
            pod_count=_as_nonnegative_int(status.pod_count),
            ready_pods=_as_nonnegative_int(status.ready_pods),
            wired_pods=_as_nonnegative_int(status.wired_pods),
            document_digest=status.document_digest or None,
            closure_digest=status.closure_digest or None,
            resolved_semantic_digest=status.resolved_semantic_digest or None,
            release=status.runtime_release or None,
            build=status.runtime_build or None,
        ),
    )


async def _deploy_builder_catalog_session(request: Any, catalog_context: Any) -> Any:
    from nodalarc.models.builder_api import (
        BuilderSessionDeployAccepted,
        BuilderSessionDeployRefusal,
    )
    from nodalarc.prepared_session import PreparedSessionError

    from vs_api.builder_router import BuilderSessionDeployError
    from vs_api.session_deployment import (
        SessionDeploymentPreparationError,
        prepare_catalog_session_deployment,
    )

    def refuse(
        status_code: int,
        code: str,
        message: str,
        *,
        expected: str | None = None,
        observed: str | None = None,
        cause_type: str | None = None,
    ) -> None:
        raise BuilderSessionDeployError(
            BuilderSessionDeployRefusal(
                code=code,
                message=message,
                session_ref=request.session_ref,
                expected=expected,
                observed=observed,
                cause_type=cause_type,
            ),
            status_code=status_code,
        )

    def refuse_translated(exc: Exception, *, expected: str | None, observed: str | None) -> None:
        # One translation for the whole service; this seam only adds the
        # deployment's identity and precondition evidence.
        outcome = refusal_from_exception(exc)
        if outcome is None:
            log.error("Builder deployment preparation failed", exc_info=exc)
            refusal = internal_error_refusal("Session deployment preparation failed")
            refuse(500, refusal.code, refusal.message)
            return
        refuse(
            outcome.status_code,
            outcome.refusal.code,
            outcome.refusal.message,
            expected=expected,
            observed=observed,
            cause_type=outcome.refusal.cause_type,
        )

    if _session_manager is None:
        refuse(503, "vs_api.session_manager_unavailable", "Session deployment is unavailable")
    available_node_count = await asyncio.to_thread(_available_session_node_count)
    try:
        deployment = await asyncio.to_thread(
            prepare_catalog_session_deployment,
            catalog_context,
            session_ref=str(request.session_ref),
            expected_session_revision=str(request.expected_session_revision),
            expected_document_digest=str(request.expected_document_digest),
            expected_closure_digest=str(request.expected_dependency_digest),
            available_node_count=available_node_count,
            record_history=request.record_history,
        )
    except SessionDeploymentPreparationError as exc:
        refuse_translated(exc, expected=exc.evidence.expected, observed=exc.evidence.observed)
    except PreparedSessionError as exc:
        refuse_translated(exc, expected=exc.evidence.expected, observed=exc.evidence.actual)
    except Exception as exc:
        refuse_translated(exc, expected=None, observed=None)

    operation_id = await _admit_transition(
        lambda: _run_catalog_switch(deployment, catalog_context),
        reservation=_prepared_transition_reservation(deployment),
    )
    if operation_id is None:
        refuse(409, "session_switch.conflict", "A session transition is already active")
    return BuilderSessionDeployAccepted(
        operation_id=operation_id,
        source=request,
    )


from vs_api.builder_router import BuilderRouterServices, create_builder_router

app.include_router(
    create_builder_router(
        BuilderRouterServices(
            context_provider=get_catalog_context,
            available_node_count_provider=lambda: _available_session_node_count(),
            deploy_callback=_deploy_builder_catalog_session,
        )
    ),
    dependencies=[Depends(_require_api_key)],
)


@app.get(
    "/api/v1/session-transitions/{operation_id}",
    response_model=TransitionOperation,
    dependencies=[Depends(_require_api_key)],
)
async def get_session_transition(
    operation_id: str,
) -> TransitionOperation:
    """Return durable, path-free evidence for one opaque transition ID."""

    try:
        record = await _invoke_transition_store(
            "get_operation",
            operation_id,
        )
    except TransitionOperationNotFoundError, ValueError:
        raise HTTPException(status_code=404, detail="Session transition was not found") from None
    return record.public_view()


@app.post(
    "/api/v1/sessions/switch",
    response_model=CatalogSessionSwitchAccepted,
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key), Depends(_rate_limit_session_switch)],
)
async def switch_session(
    body: CatalogSessionSwitchRequest,
    catalog_context: CatalogContext = Depends(get_catalog_context),
) -> CatalogSessionSwitchAccepted | Response:
    """Deploy one reviewed session revision from the request catalog scope."""
    from vs_api.session_deployment import (
        prepare_catalog_session_deployment,
    )

    if _session_manager is None:
        return refusal_response(
            503, "vs_api.session_manager_unavailable", "Session manager not initialized"
        )
    available_node_count = await asyncio.to_thread(_available_session_node_count)
    deployment = await asyncio.to_thread(
        prepare_catalog_session_deployment,
        catalog_context,
        session_ref=str(body.source.session_ref),
        available_node_count=available_node_count,
        expected_session_revision=str(body.expected_source_revision),
        expected_document_digest=str(body.expected_document_digest),
        expected_closure_digest=str(body.expected_dependency_digest),
        record_history=body.record_history,
    )
    operation_id = await _admit_transition(
        lambda: _run_catalog_switch(deployment, catalog_context),
        reservation=_prepared_transition_reservation(deployment),
    )
    if operation_id is None:
        return refusal_response(409, "session_switch.conflict", "Switch already in progress")
    return CatalogSessionSwitchAccepted(
        operation_id=operation_id,
        source=body.source,
    )


# --- Wizard API endpoints ---


def _shipped_snapshot(context: CatalogContext) -> CatalogReadSnapshot:
    """One request's pinned catalog snapshot; presets list the shipped namespace."""
    return context.repository.snapshot(context.scope)


@app.get(
    "/api/v1/presets/constellations",
    dependencies=[Depends(_require_api_key)],
    response_model=WizardConstellationPresetResponse,
)
def list_constellation_presets(
    catalog_context: CatalogContext = Depends(get_catalog_context),
) -> WizardConstellationPresetResponse:
    """Return catalog presets with backend-owned runtime capability facts."""
    from nodalarc.session_generator import load_constellation_preset_response

    return load_constellation_preset_response(_shipped_snapshot(catalog_context))


@app.get(
    "/api/v1/presets/satellite-types",
    dependencies=[Depends(_require_api_key)],
    response_model=WizardSatelliteTypePresetResponse,
)
def list_satellite_types(
    catalog_context: CatalogContext = Depends(get_catalog_context),
) -> WizardSatelliteTypePresetResponse:
    """Return the space node primitives that can fly a constellation.

    Sessions assemble from primitives: the constellation supplies geometry
    and a default node; the wizard may swap in any of these. (The retired
    config-root satellite-type overrides are gone; these are catalog node
    primitives, composed by the generator.)
    """
    from nodalarc.session_generator import list_space_node_presets

    return WizardSatelliteTypePresetResponse(
        presets=tuple(
            WizardSatelliteTypePreset(
                name=preset["name"],
                display_name=preset["display_name"],
                notes=preset["notes"],
                file=preset["file"],
                terminals=tuple(
                    WizardSatelliteTerminalSummary(
                        id=terminal["id"],
                        role=terminal["role"],
                        count=terminal["count"],
                    )
                    for terminal in preset["terminals"]
                ),
            )
            for preset in list_space_node_presets(_shipped_snapshot(catalog_context))
        )
    )


@app.get(
    "/api/v1/presets/ground-stations",
    dependencies=[Depends(_require_api_key)],
    response_model=WizardGroundStationSetPresetResponse,
)
def list_ground_station_sets(
    catalog_context: CatalogContext = Depends(get_catalog_context),
) -> WizardGroundStationSetPresetResponse:
    """Return available catalog site-set presets for the wizard."""
    snapshot = _shipped_snapshot(catalog_context)
    results: list[WizardGroundStationSetPreset] = []
    for entry in snapshot.list(namespace="nodalarc", family="site-sets"):
        wrapper, model = load_catalog_object(entry.ref, snapshot)
        if wrapper != "site_set":
            continue
        data = model.model_dump(mode="python", by_alias=True, exclude_none=True)
        results.append(
            WizardGroundStationSetPreset(
                name=data["id"],
                description=data.get("display_name") or data.get("notes") or "",
                stations=tuple(
                    site.get("site", {}).get("id", "") if isinstance(site, dict) else str(site)
                    for site in data.get("sites", [])
                ),
                file=str(entry.ref),
            )
        )
    return WizardGroundStationSetPresetResponse(presets=tuple(results))


@app.get(
    "/api/v1/presets/ground-stations/stations",
    dependencies=[Depends(_require_api_key)],
    response_model=WizardAvailableStationResponse,
)
def list_individual_stations(
    catalog_context: CatalogContext = Depends(get_catalog_context),
) -> WizardAvailableStationResponse:
    """Return all available catalog sites for custom set building."""
    snapshot = _shipped_snapshot(catalog_context)
    results: list[WizardAvailableStation] = []
    for entry in snapshot.list(namespace="nodalarc", family="sites"):
        wrapper, model = load_catalog_object(entry.ref, snapshot)
        if wrapper != "site":
            continue
        site = model.model_dump(mode="python", by_alias=True, exclude_none=True)
        location = site.get("location") or {}
        if "lat_deg" not in location or "lon_deg" not in location:
            continue
        results.append(
            WizardAvailableStation(
                name=site["id"],
                lat_deg=float(location["lat_deg"]),
                lon_deg=float(location["lon_deg"]),
                file=str(entry.ref),
            )
        )
    return WizardAvailableStationResponse(stations=tuple(results))


@app.get(
    "/api/v1/wizard/extensions",
    dependencies=[Depends(_require_api_key)],
    response_model=WizardExtensionRulesResponse,
)
def wizard_extension_rules() -> WizardExtensionRulesResponse:
    """Return the Wizard's backend-owned routing choices and initial controls."""
    from vs_api.wizard_builder import wizard_extension_rules_response

    return wizard_extension_rules_response()


@app.post(
    "/api/v1/session/preview-coverage",
    response_model=CoveragePreviewResult,
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key)],
)
async def preview_coverage(
    request: WizardCoverageRequest,
) -> CoveragePreviewResult | JSONResponse:
    """Run OME coverage preview from typed Wizard intent and scoped catalog facts."""
    from ome.coverage_preview import compute_coverage_preview

    from vs_api.wizard_builder import wizard_preview_inputs

    def compute():
        context = get_catalog_context()
        snapshot = context.repository.snapshot(context.scope)
        inputs = wizard_preview_inputs(request, snapshot)
        return compute_coverage_preview(
            inputs.constellation_ref,
            inputs.ground_site_set_ref,
            catalog=inputs.catalog,
        )

    try:
        result = await asyncio.to_thread(compute)
    except ValueError as exc:
        # Typed refusals are ValueError subclasses; they keep their own
        # translation. A plain ValueError is a preview input the intent
        # grammar admitted and the preview refused.
        outcome = refusal_from_exception(exc)
        if outcome is not None:
            return outcome.response()
        log.info("Invalid coverage preview request: %s", exc)
        return refusal_response(
            400, "coverage_preview.invalid", "Coverage preview request is invalid"
        )
    return result


@app.post(
    "/api/v1/session/deploy-from-yaml",
    response_model=CatalogSessionSwitchAccepted,
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key)],
)
async def deploy_from_yaml(
    body: CatalogSessionYamlUploadRequest,
    catalog_context: CatalogContext = Depends(get_catalog_context),
) -> CatalogSessionSwitchAccepted | Response:
    """Save standard session YAML in the user catalog and deploy that exact ref."""
    from vs_api.builder_compiler import canonicalize_persisted_configuration
    from vs_api.session_deployment import prepare_catalog_session_deployment

    yaml_str = body.yaml
    try:
        raw = await asyncio.to_thread(load_configuration_yaml, yaml_str)
    except (UnicodeError, YAMLError) as exc:
        log.info("Invalid session YAML rejected: %s", exc)
        return refusal_response(400, "session_yaml.invalid", "Invalid session YAML")
    try:
        persisted = catalog_family_spec("sessions").validate_document(raw)
        session_ref = SessionRef(f"user:sessions/{persisted.session.name}.yaml")
        canonical = canonicalize_persisted_configuration(session_ref, raw)
    except (TypeError, ValueError) as exc:
        log.info("Invalid persisted session rejected: %s", exc)
        return refusal_response(
            422,
            "session_yaml.not_ref_composed",
            "Single-file YAML upload must satisfy the ref-composed published grammar",
        )

    def persist_session():
        snapshot = catalog_context.repository.snapshot(catalog_context.scope)
        transaction = catalog_context.repository.begin(
            catalog_context.scope,
            base_generation=snapshot.generation,
        )
        try:
            transaction.write_bytes(
                session_ref,
                canonical.yaml_bytes,
                expected_revision=None,
            )
            committed = transaction.commit()
        except Exception:
            transaction.abort()
            raise
        saved = committed.get(session_ref)
        closure = CatalogClosureCollector.collect(saved.content, committed)
        return saved, closure

    try:
        saved, closure = await asyncio.to_thread(persist_session)
    except CatalogConflictError:
        return refusal_response(
            409, "catalog_repository.conflict", f"Catalog session already exists: {session_ref}"
        )
    except CatalogValidationError as exc:
        log.info("Uploaded session catalog graph refused: %s", exc)
        return refusal_response(
            422,
            "catalog_repository.invalid_document",
            "Session references unresolved catalog content; "
            "import all referenced user component YAML files through Session Builder",
        )

    available_node_count = await asyncio.to_thread(_available_session_node_count)
    deployment = await asyncio.to_thread(
        prepare_catalog_session_deployment,
        catalog_context,
        session_ref=str(session_ref),
        expected_session_revision=str(saved.revision),
        expected_document_digest=closure.document_digest,
        expected_closure_digest=closure.closure_digest,
        available_node_count=available_node_count,
        record_history=body.record_history,
    )

    operation_id = await _admit_transition(
        lambda: _run_catalog_switch(deployment, catalog_context),
        reservation=_prepared_transition_reservation(deployment),
    )
    if operation_id is None:
        return refusal_response(409, "session_switch.conflict", "Switch already in progress")
    return CatalogSessionSwitchAccepted(
        operation_id=operation_id,
        source=CatalogSessionSourceId(session_ref=session_ref),
    )


@app.get(
    "/api/v1/introspect/commands",
    dependencies=[Depends(_require_api_key), Depends(_rate_limit_introspect)],
)
def introspect_commands() -> list[str]:
    """Return sorted list of whitelisted vtysh commands."""
    return sorted(VTYSH_COMMANDS)


@app.post(
    "/api/v1/introspect",
    response_model=IntrospectResult,
    responses=_REFUSAL_RESPONSES,
    dependencies=[Depends(_require_api_key), Depends(_rate_limit_introspect)],
)
def introspect(body: IntrospectRequest) -> IntrospectResult | JSONResponse:
    """Run a whitelisted vtysh command on a node's FRR container."""
    if body.command not in VTYSH_COMMANDS:
        return refusal_response(
            400, "introspect.command_not_allowed", f"Command not allowed: {body.command}"
        )
    try:
        return run_vtysh(body.node_id, body.command)
    except ValueError as exc:
        log.info("Invalid introspection request: %s", exc)
        return refusal_response(400, "introspect.invalid_request", "Invalid introspection request")


async def _run_catalog_switch(
    deployment: Any,
    catalog_context: CatalogContext,
) -> TransitionRuntimeResult:
    async with _session_transition_lock:
        return await _run_prepared_switch_locked(
            deployment,
            catalog_context=catalog_context,
        )


async def _run_prepared_switch_locked(
    deployment: Any,
    *,
    catalog_context: CatalogContext,
) -> TransitionRuntimeResult:
    global _active_context, _active_cr_generation

    if _session_manager is None:
        raise RuntimeError("Session manager is not initialized")

    custom_objects_api = await asyncio.to_thread(k8s.custom_objects)
    core_v1_api = await asyncio.to_thread(k8s.core_v1)
    namespace = get_platform_config().kubernetes_namespace
    upload_store = KubernetesCatalogUploadStore(core_v1_api, namespace)
    old_context = _active_context
    old_session = old_context.session_id if old_context is not None else None
    source_id = str(deployment.prepared.source.logical_id)
    operation_id = _current_transition_operation_id.get()

    def _upload_resource_observed(resource: CatalogUploadResourceEvidence) -> None:
        if operation_id is None:
            return
        _get_transition_operation_store().update_provenance(
            operation_id,
            TransitionOperationProvenancePatch(
                upload_resource_name=resource.name,
            ),
        )

    async def _constellation_spec_observed(cr: dict[str, Any]) -> None:
        if operation_id is None:
            return
        observation = _constellation_spec_observation(cr)
        await _invoke_transition_store(
            "update_provenance",
            operation_id,
            TransitionOperationProvenancePatch(constellation_spec=observation),
        )

    async def _transition_started() -> None:
        global _active_context, _active_cr_generation

        await _advance_transition_operation(
            TransitionOperationState.VERIFYING,
            detail="Exact uploaded catalog and reviewed source are verified",
        )
        await _advance_transition_operation(
            TransitionOperationState.SWITCHING,
            detail="Selecting the verified runtime configuration",
        )
        await _publish_system_ops_event(
            "info",
            "SESSION_SWITCH_INITIATED",
            f"Session switch initiated: {old_session} → {source_id}",
            {"old_session": old_session, "new_session_source": source_id},
        )
        await _broadcast_to_all(json.dumps({"msg_type": "session_transitioning"}))
        await _terminal_manager.close_all("Session switched")
        if old_context is not None:
            await old_context.stop()
        _active_context = None
        _active_cr_generation = None
        await _publish_system_ops_event(
            "info",
            "SESSION_TEARDOWN_COMPLETE",
            f"Old session {old_session} torn down",
        )

    async def _switch_progress(detail: str) -> None:
        if detail == "Uploading exact session catalog":
            await _advance_transition_operation(
                TransitionOperationState.UPLOADING,
                detail=detail,
            )
        elif _current_transition_operation_id.get() is not None:
            selected_operation_id = _current_transition_operation_id.get()
            operation = await _invoke_transition_store(
                "get_operation",
                selected_operation_id or "",
            )
            if operation.state is TransitionOperationState.SWITCHING:
                await _advance_transition_operation(
                    TransitionOperationState.SWITCHING,
                    detail=detail,
                )
        await _broadcast_to_all(json.dumps({"msg_type": "session_transitioning", "detail": detail}))

    try:
        ready_cr = await _session_manager.switch_catalog(
            deployment,
            context=catalog_context,
            upload_store=upload_store,
            custom_objects_api=custom_objects_api,
            core_v1_api=core_v1_api,
            namespace=namespace,
            progress_fn=_switch_progress,
            transition_started=_transition_started,
            upload_resource_observed=_upload_resource_observed,
            constellation_spec_observed=_constellation_spec_observed,
        )
        await _reconcile_catalog_upload_lifecycle(
            ready_cr,
            core_v1_api=core_v1_api,
            namespace=namespace,
        )
        ready = await asyncio.to_thread(
            _extract_cr_session,
            ready_cr,
            require_ready=True,
            core_v1=core_v1_api,
        )
        if ready is None:
            raise RuntimeError("Operator returned Ready without current runtime identity")
        await _activate_session_context_from_cr(
            ready,
            source="catalog-switch",
            transition_already_started=True,
        )
        return TransitionRuntimeResult(
            session_id=ready.session_id,
            generation=ready.generation,
        )
    except Exception as exc:
        log.error("Prepared session switch failed", exc_info=True)
        if _session_manager.status == "switching":
            _session_manager._status = "error"
            _session_manager.status_detail = "Session switch failed"
        await _publish_system_ops_event(
            "error",
            "SESSION_SWITCH_FAILED",
            "Session switch failed",
            {"session_source": source_id, "error_type": type(exc).__name__},
        )
        await _broadcast_to_all(
            json.dumps({"msg_type": "session_failed", "error": "Session switch failed"})
        )
        raise


_TYPED_REFUSALS = (
    CatalogClosureError,
    CatalogUploadStoreError,
    KubernetesRuntimeConfigError,
    RuntimeConfigError,
)
_TRANSPORT_WRAPPER_CODES = frozenset(
    {
        CatalogUploadStoreErrorCode.LIST_FAILED,
        CatalogUploadStoreErrorCode.DELETE_FAILED,
        KubernetesRuntimeConfigErrorCode.CONFIG_MAP_FETCH_FAILED,
    }
)
_TRANSPORT_ROOTS = (ApiException, TransportHTTPError, OSError)


def _poll_failure_is_transport(exc: BaseException) -> bool:
    """Decide whether one poll-tick failure is transport, so polling continues.

    The caught exception and its ``__cause__`` chain are judged from the
    outside in, at the first link that can decide. A typed refusal whose code
    names a transport wrapper (an upload list or delete, a ConfigMap fetch)
    defers to its retained cause. Any other typed refusal is an explicit
    content or configuration refusal and is terminal, whatever it wraps. An
    exception group is transport only when every member is. A Kubernetes API,
    HTTP or OS error is transport. Anything else, including a strict model
    parse failure or an error with no cause, is terminal.
    """
    if isinstance(exc, _TYPED_REFUSALS):
        if exc.code not in _TRANSPORT_WRAPPER_CODES:
            return False
        return exc.__cause__ is not None and _poll_failure_is_transport(exc.__cause__)
    if isinstance(exc, BaseExceptionGroup):
        return all(_poll_failure_is_transport(member) for member in exc.exceptions)
    return isinstance(exc, _TRANSPORT_ROOTS)


async def _poll_cr_until_ready() -> None:
    """Poll ConstellationSpec CR until Ready, updating session status_detail.

    Runs as a background task when VS-API starts and finds the selected CR in
    Wiring/Creating phase after a process restart. Mirrors the polling in the
    session manager so the frontend sees progress throughout recovery.
    """
    global _active_context, _active_cr_generation
    log.info("_poll_cr_until_ready: starting background CR polling task")
    api = await asyncio.to_thread(k8s.custom_objects)
    core_v1_api = await asyncio.to_thread(k8s.core_v1)
    ns = get_platform_config().kubernetes_namespace
    upload_reconciled = False

    for _ in range(600):  # 10 minutes max
        await asyncio.sleep(1)
        try:
            cr = await asyncio.to_thread(
                api.get_namespaced_custom_object,
                group=CR_GROUP,
                version=CR_VERSION,
                namespace=ns,
                plural=CR_PLURAL,
                name=CR_NAME,
            )
            await _reconcile_interrupted_transition(cr)
            if not upload_reconciled:
                await _reconcile_catalog_upload_lifecycle(
                    cr,
                    core_v1_api=core_v1_api,
                    namespace=ns,
                )
                upload_reconciled = True
            observed = ConstellationSpecStatus.from_cr(cr.get("status"))
            phase = observed.phase or ""
            message = observed.message or ""
            status_is_current = cr_status_observes_current_generation(cr)
            # Try to load session_id on each tick — the ConfigMap appears
            if _session_manager and phase != "Wiring" and status_is_current:
                # During Wiring, Node Agent NATS progress owns _status_detail.
                # Only update from CR for non-Wiring phases.
                _session_manager.status_detail = message or f"Status: {phase}"
            if phase == "Ready":
                ready = await asyncio.to_thread(_extract_ready_cr_session, cr)
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
                    _mark_session_manager_ready(ready.session, ready.source_id)

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
            if _poll_failure_is_transport(exc):
                log.warning("_poll_cr_until_ready: transport failure, polling continues: %s", exc)
                continue
            log.error(
                "_poll_cr_until_ready: %s/%s failed: %s: %s",
                ns,
                CR_NAME,
                type(exc).__name__,
                exc,
                exc_info=exc,
            )
            if _session_manager:
                outcome = refusal_from_exception(exc)
                _session_manager._status = "error"
                _session_manager.status_detail = (
                    outcome.refusal.message
                    if outcome is not None
                    else "Runtime ConstellationSpec poll failed"
                )
            return

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
    parser.add_argument("--port", type=int, default=None, help="HTTP port")
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    args = parser.parse_args()

    from nodalarc.platform_config import init_platform_config

    init_platform_config(Path(args.platform_config))

    if args.port is None:
        args.port = get_platform_config().vs_api_http_port

    global _session_manager, _pending_cr_poll

    _session_manager = SessionManager()

    log.info("VS-API starting [build=%s]", os.environ.get("NODAL_BUILD", "dev"))

    # The live ConstellationSpec is the sole runtime bootstrap authority. Do
    # not parse --session with default catalog roots: uploaded user: closures
    # are selected by spec.catalogUpload and verified by _extract_cr_session.
    try:
        cr = k8s.custom_objects().get_namespaced_custom_object(
            group=CR_GROUP,
            version=CR_VERSION,
            namespace=get_platform_config().kubernetes_namespace,
            plural=CR_PLURAL,
            name=CR_NAME,
        )
        observed = ConstellationSpecStatus.from_cr(cr.get("status"))
        phase = observed.phase or ""
        message = observed.message or ""
        if phase == "Ready":
            ready = _extract_ready_cr_session(cr)
            if ready is None:
                raise ValueError("Ready ConstellationSpec lacks verified runtime identity")
            _session_manager.set_active(ready.source_id)
            _session_manager._status = "ready"
            _session_manager.status_detail = ""
        elif phase in {"Pending", "Wiring", "Creating"}:
            _session_manager._status = "wiring"
            _session_manager.status_detail = message or f"Status: {phase}"
            _pending_cr_poll = True
        elif phase == "Error":
            _session_manager._status = "error"
            _session_manager.status_detail = message or "Operator reported error"
        else:
            _session_manager._status = "idle"
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            _session_manager._status = "idle"
            log.info("No runtime ConstellationSpec exists at startup")
        else:
            _session_manager._status = "error"
            _session_manager.status_detail = "Runtime session verification failed"
            log.error("Runtime ConstellationSpec verification failed", exc_info=True)

    uvicorn.run(app, host="0.0.0.0", port=args.port, **_uvicorn_logging_settings())


if __name__ == "__main__":
    main()
