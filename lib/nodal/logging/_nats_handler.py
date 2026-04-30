# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""NatsHandler — non-blocking NATS OpsEvent publisher.

Records are pre-formatted and queued in a bounded deque at emit() time.
An event-driven async drain task publishes them to NATS JetStream.

Threading model:
  - emit() is called from ANY thread (OME pacing thread, Node Agent
    wiring thread, main asyncio thread). It appends to deque (thread-safe)
    and signals the drain task via loop.call_soon_threadsafe.
  - The drain task runs on the asyncio event loop established by connect().
  - Errors in the drain task go to sys.stderr directly (never through
    the logging system — prevents recursion).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import sys
import time
from typing import Any

from nodal.logging._formatter import OpsEventFormatter


class NatsHandler(logging.Handler):
    """Logging handler that publishes OpsEvents to NATS JetStream."""

    def __init__(self, service: str, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self._service = service
        self._source = service.rsplit(".", 1)[-1]
        self._service_package = service.rsplit(".", 1)[-1]
        self._nats_level = level
        self._deque: collections.deque[tuple[str, bytes]] = collections.deque(maxlen=500)
        self._ops_formatter = OpsEventFormatter()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None
        self._js: Any = None
        self._drain_task: asyncio.Task | None = None
        self._last_error_time: float = 0.0
        self._dropped_since_last_report: int = 0

    def set_nats_level(self, level: int) -> None:
        """Change the minimum level for NATS publishing.

        Adjusts both the handler's level gate AND the service's package
        logger so DEBUG records are created by the originating logger.
        The root logger is never touched — only the package logger
        (e.g., "scheduler") changes, which scopes DEBUG record creation
        to that package's loggers (scheduler.dispatcher, scheduler.__main__,
        etc.) with zero effect on other packages or third-party libraries.
        """
        self._nats_level = level
        self.setLevel(level)
        pkg_logger = logging.getLogger(self._service_package)
        if level <= logging.DEBUG:
            pkg_logger.setLevel(logging.DEBUG)
        else:
            pkg_logger.setLevel(logging.NOTSET)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = self._ops_formatter.format(record).encode("utf-8")
            subject = self._build_subject(record)
            self._deque.append((subject, payload))

            if self._loop is not None and self._event is not None:
                with contextlib.suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(self._event.set)
        except Exception:
            self.handleError(record)

    def _build_subject(self, record: logging.LogRecord) -> str:
        tenant = getattr(record, "nodal_tenant", "")
        session = getattr(record, "nodal_session", "")
        source = getattr(record, "nodal_source", self._source)
        code = getattr(record, "nodal_code", "")
        code_lower = code.lower() if code else ""

        stream_prefix = "nodalarc.debug" if record.levelno < logging.INFO else "nodalarc.ops"

        if not tenant and not session:
            base = f"{stream_prefix}._infra.{source}"
        elif not tenant and session:
            base = f"{stream_prefix}.{session}.{source}"
        elif tenant and not session:
            base = f"{stream_prefix}.{tenant}._tenant.{source}"
        else:
            base = f"{stream_prefix}.{tenant}.{session}.{source}"

        if code_lower:
            return f"{base}.{code_lower}"
        return base

    async def connect(self, nc: Any) -> None:
        """Enable NATS publishing. Call after NATS connection established."""
        self._js = nc.jetstream()
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        self._event.set()
        self._drain_task = asyncio.create_task(self._drain_loop())

    async def _drain_loop(self) -> None:
        try:
            while True:
                await self._event.wait()
                self._event.clear()

                count = 0
                while self._deque and count < 200:
                    subject, payload = self._deque.popleft()
                    try:
                        await self._js.publish(subject, payload)
                    except Exception:
                        self._dropped_since_last_report += 1
                        self._log_error_throttled()
                    count += 1

                if self._deque:
                    self._event.set()
        except asyncio.CancelledError:
            return

    def _log_error_throttled(self) -> None:
        now = time.monotonic()
        if now - self._last_error_time > 60.0:
            dropped = self._dropped_since_last_report
            self._dropped_since_last_report = 0
            print(
                f"nodal.logging: NATS publish failed, {dropped} record(s) dropped"
                " (suppressing for 60s)",
                file=sys.stderr,
                flush=True,
            )
            self._last_error_time = now

    def flush_sync(self, timeout: float = 2.0) -> None:
        """Synchronous flush for atexit. Best-effort, never hangs."""
        if not self._deque:
            return

        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()

        if self._js is not None and self._loop is not None:
            try:
                if not self._loop.is_running() and not self._loop.is_closed():
                    self._loop.run_until_complete(
                        asyncio.wait_for(self._flush_async(), timeout=timeout)
                    )
                    return
            except Exception as exc:
                print(
                    f"nodal.logging: atexit NATS flush failed: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

        self._dump_to_stderr()

    async def _flush_async(self) -> None:
        while self._deque:
            subject, payload = self._deque.popleft()
            try:
                await self._js.publish(subject, payload)
            except Exception:
                break

    def _dump_to_stderr(self) -> None:
        while self._deque:
            _subject, payload = self._deque.popleft()
            with contextlib.suppress(Exception):
                print(
                    f"nodal.logging [unflushed]: {payload.decode('utf-8', errors='replace')}",
                    file=sys.stderr,
                    flush=True,
                )
