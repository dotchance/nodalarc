# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Continuous live path trace between two nodes.

The live trace repeats a ``PathTracer`` trace on an interval, and at once
when a link on the traced path changes, until it is stopped. The latest
result is what the UI shows.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from nodalarc.models.vs_api import TracedPath

from vs_api.path_tracer import PathTracer, TraceEndpoint

log = logging.getLogger(__name__)

TRACE_FLOW_ID = "__continuous_trace__"

# A direction that did not reach its destination is traced again after this
# many seconds, so a path that comes up is shown within a second.
_UNREACHED_RETRACE_SECONDS = 1.0


class ContinuousTracer:
    """Traces one node pair in both directions until stopped."""

    def __init__(
        self,
        *,
        path_tracer: PathTracer,
        interval_s: float,
        on_path_change: Callable[[str, str, list[str], list[str]], None],
    ) -> None:
        self._path_tracer = path_tracer
        self._interval_s = interval_s
        self._on_path_change = on_path_change
        self._task: asyncio.Task | None = None
        self._latest: TracedPath | None = None
        self._src: TraceEndpoint | None = None
        self._dst: TraceEndpoint | None = None
        # Set by notify_topology_change() to wake the trace loop early.
        self._retrace_event = asyncio.Event()

    async def start(self, src: str, dst: str) -> None:
        """Start tracing between src and dst, replacing any running trace."""
        await self.stop()
        self._src = self._path_tracer.endpoint(src)
        self._dst = self._path_tracer.endpoint(dst)
        self._task = asyncio.create_task(self._trace_loop())

    async def stop(self) -> None:
        """Stop the trace loop and forget its result."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._latest = None

    def notify_topology_change(self, node_a: str, node_b: str) -> None:
        """Wake the trace loop when a link change touches the traced path."""
        if not self.active or self._src is None or self._dst is None:
            return
        watched = {self._src.node.node_id, self._dst.node.node_id}
        if self._latest is not None:
            watched |= set(self._latest.hops) | set(self._latest.reverse_hops)
        if node_a in watched or node_b in watched:
            self._retrace_event.set()

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def src(self) -> str | None:
        return self._src.node.node_id if self._src is not None else None

    @property
    def dst(self) -> str | None:
        return self._dst.node.node_id if self._dst is not None else None

    @property
    def traced_path(self) -> TracedPath | None:
        """The latest trace, including one that stopped on an internal error."""
        return self._latest

    async def _trace_loop(self) -> None:
        loop = asyncio.get_running_loop()
        previous_hops: list[str] | None = None
        try:
            while True:
                result = await loop.run_in_executor(None, self._trace_once)
                self._latest = result
                if previous_hops is not None and result.hops != previous_hops:
                    self._on_path_change(
                        result.src_node, result.dst_node, previous_hops, result.hops
                    )
                previous_hops = result.hops

                both_reached = result.state == "reached" and result.reverse_state == "reached"
                interval = self._interval_s if both_reached else _UNREACHED_RETRACE_SECONDS
                self._retrace_event.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._retrace_event.wait(), timeout=interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The loop ends; the dialog shows the failure until the user stops
            # or restarts the trace.
            log.error(
                "Continuous trace %s -> %s stopped: %s", self.src, self.dst, exc, exc_info=exc
            )
            src, dst = self._require_endpoints()
            self._latest = self._path_tracer.failed(
                src,
                dst,
                f"The trace stopped on an internal error ({type(exc).__name__}); see the VS-API log",
                flow_id=TRACE_FLOW_ID,
            )

    def _trace_once(self) -> TracedPath:
        src, dst = self._require_endpoints()

        def publish(progress: TracedPath) -> None:
            self._latest = progress

        # Until the first cycle completes, the forward path grows hop by hop in
        # the UI. After that, each completed cycle replaces the last one whole.
        first_cycle = self._latest is None or self._latest.state == "running"
        return self._path_tracer.trace(
            src,
            dst,
            flow_id=TRACE_FLOW_ID,
            tracing=True,
            on_progress=publish if first_cycle else None,
        )

    def _require_endpoints(self) -> tuple[TraceEndpoint, TraceEndpoint]:
        if self._src is None or self._dst is None:
            raise RuntimeError("the tracer has no endpoints; start() sets them")
        return self._src, self._dst
