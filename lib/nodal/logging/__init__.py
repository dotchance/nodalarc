# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Nodal Unified Logging Library.

Standard Python logging with dual output (stdout + NATS OpsEvents)
and structured record enrichment. One configure() call at service
startup — every log.info/warning/error call works automatically.

Usage::

    from nodal.logging import configure, connect

    configure("nodal.arc.ome", session_id=session_id)
    log = logging.getLogger(__name__)
    log.info("Starting up")

    # After NATS connects:
    await connect(nc)
"""

from __future__ import annotations

import atexit
import logging
import sys
from typing import Any

from nodal.logging._filter import NodalFilter
from nodal.logging._formatter import HumanFormatter, JsonFormatter
from nodal.logging._nats_handler import NatsHandler

__all__ = [
    "configure",
    "connect",
    "set_session",
    "set_tenant",
    "get_logger",
]

_nodal_filter: NodalFilter | None = None
_nats_handler: NatsHandler | None = None
_atexit_registered: bool = False

_THIRD_PARTY_LOGGERS: tuple[str, ...] = (
    "nats",
    "asyncio",
    "kubernetes",
    "urllib3",
    "asyncssh",
    "grpc",
    "grpc._cython",
    "httpx",
    "httpcore",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "fastapi",
)


def configure(
    service: str,
    *,
    tenant_id: str = "",
    session_id: str = "",
    stdout_format: str = "human",
    nats_level: int = logging.WARNING,
    stdout_level: int = logging.INFO,
) -> None:
    """Configure the Nodal logging system.

    Call once at service startup. Replaces logging.basicConfig().
    Idempotent — safe to call again (removes previous handlers first).

    Args:
        service: Hierarchical service name (e.g. "nodal.arc.ome").
        tenant_id: Tenant scope for OpsEvents (empty = infrastructure).
        session_id: Session scope for OpsEvents.
        stdout_format: "human" for terminal, "json" for log aggregation.
        nats_level: Minimum level for NATS OpsEvent publishing.
        stdout_level: Minimum level for stdout output.
    """
    global _nodal_filter, _nats_handler, _atexit_registered

    root = logging.getLogger()

    for h in root.handlers[:]:
        root.removeHandler(h)
    for f in root.filters[:]:
        root.removeFilter(f)

    root.setLevel(min(nats_level, stdout_level))

    _nodal_filter = NodalFilter(service, tenant_id=tenant_id, session_id=session_id)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(stdout_level)
    stdout_handler.addFilter(_nodal_filter)
    if stdout_format == "json":
        stdout_handler.setFormatter(JsonFormatter())
    else:
        stdout_handler.setFormatter(HumanFormatter())
    root.addHandler(stdout_handler)

    _nats_handler = NatsHandler(service, level=nats_level)
    _nats_handler.addFilter(_nodal_filter)
    root.addHandler(_nats_handler)

    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    if not _atexit_registered:
        atexit.register(_flush_on_exit)
        _atexit_registered = True


async def connect(nc: Any) -> None:
    """Enable NATS publishing and debug control after connection established.

    Call from an async context after the NATS connection is live.
    Pre-connect records buffered in the deque drain immediately.

    Also subscribes to the debug control subject so the VS-API can
    enable/disable DEBUG publishing via NATS request/reply. The
    logging library owns this — no service code changes needed.
    """
    if _nats_handler is not None:
        await _nats_handler.connect(nc)

    if _nodal_filter is not None and _nats_handler is not None:
        import json

        source = _nodal_filter._source
        subject = f"nodalarc.logging.debug_ctrl.{source}"
        _log = logging.getLogger(__name__)

        async def _handle_debug_ctrl(msg):
            try:
                cmd = json.loads(msg.data)
            except Exception as exc:
                _log.error("Malformed debug_ctrl message: %s", exc)
                await msg.respond(
                    json.dumps({"status": "error", "error": f"malformed: {exc}"}).encode()
                )
                return

            action = cmd.get("action")
            try:
                if action == "enable":
                    _nats_handler.set_nats_level(logging.DEBUG)
                    _log.info("Debug logging enabled by operator")
                    await msg.respond(json.dumps({"status": "ok", "level": "debug"}).encode())
                elif action == "disable":
                    _nats_handler.set_nats_level(logging.INFO)
                    _log.info("Debug logging disabled")
                    await msg.respond(json.dumps({"status": "ok", "level": "info"}).encode())
                else:
                    _log.error("Unknown debug_ctrl action: %s", action)
                    await msg.respond(
                        json.dumps(
                            {"status": "error", "error": f"unknown action: {action}"}
                        ).encode()
                    )
            except Exception as exc:
                _log.error("Failed to change debug level: %s", exc)
                await msg.respond(json.dumps({"status": "error", "error": str(exc)}).encode())

        try:
            await nc.subscribe(subject, cb=_handle_debug_ctrl)
            _log.debug("Debug control active on %s", subject)
        except Exception as exc:
            _log.error(
                "FATAL: Cannot subscribe to debug control %s: %s",
                subject,
                exc,
            )
            raise


def set_session(session_id: str) -> None:
    """Update session_id for future log records.

    Thread-safe. Already-queued records retain their original session_id
    (captured at log-call time by the filter).
    """
    if _nodal_filter is not None:
        _nodal_filter.session_id = session_id


def set_tenant(tenant_id: str) -> None:
    """Update tenant_id for future log records.

    Same semantics as set_session.
    """
    if _nodal_filter is not None:
        _nodal_filter.tenant_id = tenant_id


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper around logging.getLogger.

    Functionally identical — exists for discoverability.
    """
    return logging.getLogger(name)


def _flush_on_exit() -> None:
    """atexit handler: flush pending records to NATS or stderr."""
    if _nats_handler is not None:
        _nats_handler.flush_sync(timeout=2.0)
