# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Node Agent OpsEvent publishing with a local pre-NATS JSONL spool."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodalarc.models.events import OpsEvent
from nodalarc.nats_channels import ops_event_subject

log = logging.getLogger(__name__)

DEFAULT_SPOOL_PATH = Path("/var/lib/nodalarc/node-agent/ops-events.jsonl")

_js: Any = None
_loop: asyncio.AbstractEventLoop | None = None
_hostname: str = socket.gethostname()
_lock = threading.Lock()


def spool_path() -> Path:
    return Path(os.environ.get("NODE_AGENT_OPS_SPOOL", str(DEFAULT_SPOOL_PATH)))


def build_event(
    *,
    level: str,
    code: str,
    message: str,
    session_id: str = "",
    details: dict[str, Any] | None = None,
    hostname: str | None = None,
) -> dict[str, Any]:
    """Build and validate a Node Agent OpsEvent payload."""
    event = OpsEvent(
        timestamp=datetime.now(UTC),
        session_id=session_id,
        source="node_agent",
        hostname=hostname or _hostname,
        level=level,
        code=code,
        message=message,
        details=details,
    )
    return json.loads(event.model_dump_json())


def spool_event(event: dict[str, Any], *, path: Path | None = None) -> None:
    """Append an OpsEvent to the durable local JSONL spool."""
    target = path or spool_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        with _lock, target.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        log.critical("FATAL: could not write Node Agent OpsEvent spool at %s", target)
        raise


def spool_failure(
    *,
    code: str,
    message: str,
    level: str = "critical",
    session_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Build and spool a startup/runtime failure before NATS is available."""
    spool_event(
        build_event(
            level=level,
            code=code,
            message=message,
            session_id=session_id,
            details=details,
        )
    )


async def _publish_event(js: Any, event: dict[str, Any]) -> None:
    session_id = str(event.get("session_id") or "")
    subject = ops_event_subject(session_id, "node_agent", str(event.get("code") or ""))
    await js.publish(subject, json.dumps(event, sort_keys=True).encode())


async def drain_spool(js: Any, *, path: Path | None = None) -> int:
    """Publish all spooled OpsEvents and truncate the spool on success."""
    target = path or spool_path()
    if not target.exists():
        return 0
    lines = target.read_text(encoding="utf-8").splitlines()
    count = 0
    for line in lines:
        if not line.strip():
            continue
        event = json.loads(line)
        await _publish_event(js, event)
        count += 1
    with _lock:
        target.write_text("", encoding="utf-8")
    if count:
        log.info("Drained %d Node Agent OpsEvent(s) from %s", count, target)
    return count


async def init(nc: Any, *, hostname: str, loop: asyncio.AbstractEventLoop) -> None:
    """Enable NATS OpsEvent publishing and drain pre-NATS spool."""
    global _js, _loop, _hostname
    _js = nc.jetstream()
    _loop = loop
    _hostname = hostname
    await drain_spool(_js)


async def _publish_or_spool(event: dict[str, Any]) -> None:
    if _js is None:
        spool_event(event)
        return
    try:
        await _publish_event(_js, event)
    except Exception as exc:
        log.error("Failed to publish Node Agent OpsEvent; spooling locally: %s", exc)
        spool_event(event)


def publish(
    *,
    level: str,
    code: str,
    message: str,
    session_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Publish an OpsEvent after NATS init.

    Startup paths that run before NATS must call ``spool_failure`` directly.
    Handler unit tests and offline direct calls do not create spool files.
    """
    event = build_event(
        level=level,
        code=code,
        message=message,
        session_id=session_id,
        details=details,
    )
    if _loop is None:
        log.debug("Node Agent OpsEvent dropped before NATS init: %s", code)
        return
    try:
        asyncio.run_coroutine_threadsafe(_publish_or_spool(event), _loop)
    except RuntimeError:
        spool_event(event)


def _reset_for_tests() -> None:
    global _js, _loop, _hostname
    _js = None
    _loop = None
    _hostname = socket.gethostname()
