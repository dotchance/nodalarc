# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Continuous live path trace between two nodes.

The live trace repeats a ``PathTracer`` trace on an interval, and at once
when a link near the traced path changes, until it is stopped or reaches its
time limit. The latest result is what the UI shows. When a link on the shown
path goes down, that path no longer exists: the cycle in flight is closed and
the next measurement is shown as its hops arrive. After the time limit the
last finished cycle whose path still stands stays, marked stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import threading
from collections.abc import Callable

from nodalarc.models.vs_api import TracedPath, TraceStopReason

from vs_api.path_tracer import PathTracer, TraceEndpoint

log = logging.getLogger(__name__)

TRACE_FLOW_ID = "__continuous_trace__"


class ContinuousTracer:
    """Traces one node pair in both directions until stopped or out of time."""

    def __init__(
        self,
        *,
        path_tracer: PathTracer,
        interval_s: float,
        unreached_retrace_s: float,
        max_seconds: float,
        on_path_change: Callable[[str, str, list[str], list[str]], None],
    ) -> None:
        self._path_tracer = path_tracer
        self._interval_s = interval_s
        self._unreached_retrace_s = unreached_retrace_s
        self._max_seconds = max_seconds
        self._on_path_change = on_path_change
        self._task: asyncio.Task | None = None
        # What the UI shows: a finished cycle, or a measurement still arriving.
        self._latest: TracedPath | None = None
        # The latest cycle that finished and whose path still stands; what stays
        # at the time limit.
        self._last_finished: TracedPath | None = None
        self._src: TraceEndpoint | None = None
        self._dst: TraceEndpoint | None = None
        # Set by notify_link_change() to wake the trace loop early.
        self._retrace_event = asyncio.Event()
        # Closes the cycle in flight: on stop, at the time limit, and when a link
        # on the shown path goes down. Each cycle gets its own.
        self._cycle_closed = threading.Event()
        # Orders writes to _latest between the trace threads and the event loop.
        self._show_lock = threading.Lock()
        self._expired = False
        self._expiry: asyncio.TimerHandle | None = None

    async def start(self, src: str, dst: str) -> None:
        """Start tracing between src and dst, replacing any running trace."""
        await self.stop()
        self._src = self._path_tracer.endpoint(src)
        self._dst = self._path_tracer.endpoint(dst)
        self._cycle_closed = threading.Event()
        self._expired = False
        self._expiry = asyncio.get_running_loop().call_later(self._max_seconds, self._expire)
        self._task = asyncio.create_task(self._trace_loop())

    async def stop(self) -> None:
        """Stop the trace loop and forget its result."""
        self._cancel_expiry()
        if self._task is not None:
            self._cycle_closed.set()
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._latest = None
        self._last_finished = None

    def notify_link_change(self, node_a: str, node_b: str, *, up: bool) -> None:
        """Wake the trace loop for a link change that touches the traced nodes.

        A link going down between two consecutive hops of the shown path means
        that path no longer exists: the cycle in flight is closed and the shown
        path is replaced at once by a new measurement.
        """
        if not self.active:
            return
        src, dst = self._require_endpoints()
        shown = self._latest
        watched = {src.node.node_id, dst.node.node_id}
        if shown is not None:
            watched |= set(shown.hops) | set(shown.reverse_hops)
        if node_a not in watched and node_b not in watched:
            return
        if (
            not up
            and shown is not None
            and (_joins(shown.hops, node_a, node_b) or _joins(shown.reverse_hops, node_a, node_b))
        ):
            with self._show_lock:
                self._cycle_closed.set()
                self._latest = self._path_tracer.measuring(src, dst, flow_id=TRACE_FLOW_ID)
                # The broken path is never shown again, at the time limit included.
                self._last_finished = None
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
        """The latest trace, including one that stopped at its time limit or on an error."""
        return self._latest

    def _expire(self) -> None:
        """The time limit: close the running traceroute and end the loop."""
        self._expired = True
        self._cycle_closed.set()
        self._retrace_event.set()

    def _cancel_expiry(self) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None

    async def _trace_loop(self) -> None:
        loop = asyncio.get_running_loop()
        # The last cycle whose two directions reached their destination with
        # every hop answering from a node; path changes compare against it.
        previous: TracedPath | None = None
        try:
            while not self._expired:
                with self._show_lock:
                    closed = threading.Event()
                    self._cycle_closed = closed
                    # The first cycle, and the cycle after the shown path broke,
                    # show their hops as they arrive; a later cycle replaces the
                    # shown result whole when it finishes.
                    streaming = self._latest is None or "running" in (
                        self._latest.state,
                        self._latest.reverse_state,
                    )
                # A link change during this cycle wakes the loop when it ends.
                self._retrace_event.clear()
                result = await loop.run_in_executor(None, self._trace_once, closed, streaming)
                if self._expired:
                    # The limit closed this cycle's traceroutes part way.
                    break
                if closed.is_set():
                    # A link on the shown path went down during this cycle.
                    continue
                with self._show_lock:
                    self._latest = result
                self._last_finished = result
                previous = self._report_path_changes(previous, result)

                both_reached = result.state == "reached" and result.reverse_state == "reached"
                interval = self._interval_s if both_reached else self._unreached_retrace_s
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._retrace_event.wait(), timeout=interval)
            self._latest = self._stopped_result("time_limit", self._last_complete())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The loop ends; the dialog shows the failure until the user stops
            # or restarts the trace.
            log.error(
                "Continuous trace %s -> %s stopped: %s", self.src, self.dst, exc, exc_info=exc
            )
            src, dst = self._require_endpoints()
            self._latest = self._stopped_result(
                "internal_error",
                self._path_tracer.failed(
                    src,
                    dst,
                    "The trace stopped on an internal error "
                    f"({type(exc).__name__}); see the VS-API log",
                    flow_id=TRACE_FLOW_ID,
                ),
            )
        finally:
            self._cancel_expiry()

    def _report_path_changes(
        self, previous: TracedPath | None, result: TracedPath
    ) -> TracedPath | None:
        """Report each direction whose node path differs from the last complete cycle.

        A cycle counts only when both directions reached their destination and
        every hop answered from a node; a silent hop, an unknown address or a
        short trace says nothing about which path packets took.
        """
        complete = (
            result.state == "reached"
            and result.reverse_state == "reached"
            and self._path_tracer.names_nodes(result.hops)
            and self._path_tracer.names_nodes(result.reverse_hops)
        )
        if not complete:
            return previous
        if previous is not None:
            if result.hops != previous.hops:
                self._on_path_change(result.src_node, result.dst_node, previous.hops, result.hops)
            if result.reverse_hops != previous.reverse_hops:
                self._on_path_change(
                    result.dst_node, result.src_node, previous.reverse_hops, result.reverse_hops
                )
        return result

    def _last_complete(self) -> TracedPath:
        """The latest finished cycle whose path still stands, or a failed result."""
        if self._last_finished is not None:
            return self._last_finished
        src, dst = self._require_endpoints()
        return self._path_tracer.failed(
            src,
            dst,
            "the trace reached its time limit before it measured a current path",
            flow_id=TRACE_FLOW_ID,
        )

    @staticmethod
    def _stopped_result(reason: TraceStopReason, result: TracedPath) -> TracedPath:
        return TracedPath.model_validate(
            {**result.model_dump(), "tracing": False, "stop_reason": reason}
        )

    def _trace_once(self, closed: threading.Event, streaming: bool) -> TracedPath:
        src, dst = self._require_endpoints()

        def show(progress: TracedPath) -> None:
            with self._show_lock:
                # A closed cycle's late hops never replace what replaced them.
                if not closed.is_set():
                    self._latest = progress

        return self._path_tracer.trace(
            src,
            dst,
            flow_id=TRACE_FLOW_ID,
            tracing=True,
            on_progress=show if streaming else None,
            stopped=closed,
        )

    def _require_endpoints(self) -> tuple[TraceEndpoint, TraceEndpoint]:
        if self._src is None or self._dst is None:
            raise RuntimeError("the tracer has no endpoints; start() sets them")
        return self._src, self._dst


def _joins(hops: list[str], node_a: str, node_b: str) -> bool:
    """Whether two consecutive hops are the two ends of a link."""
    return any({here, there} == {node_a, node_b} for here, there in itertools.pairwise(hops))
