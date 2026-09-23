# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""One measured trace of the path between two nodes.

A trace runs traceroute from each end toward the other end's loopback,
inside the nodes' own workloads, so the path it reports is the path real
packets took through the forwarding plane. Each direction reports what
answered at every hop and how the trace ended: reached, not_reached, or
failed with the reason it could not run. ``running`` marks a direction whose
traceroute is still printing hops.
"""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import kubernetes.client
import kubernetes.stream
from nodalarc.models.vs_api import TracedPath, TraceState
from nodalarc.workload_target import WorkloadTargetError, read_workload_target

from vs_api.resolved_runtime_views import TracerNode
from vs_api.traceroute import TracerouteHop, TracerouteOutputError, parse_traceroute

log = logging.getLogger(__name__)

SILENT_HOP = "*"

# BusyBox traceroute: ICMP, numeric, one probe per hop. The per-hop wait is an
# integer number of seconds (BusyBox rejects a fractional -w) and must exceed
# the cumulative round trip to the farthest hop, because traceroute stops early
# only when the destination answers: an Earth-Luna path sits near 3.1 s, so four
# seconds reaches the lunar hops. The hop limit fails a down path fast.
_TRACEROUTE_WAIT_S = 4
_TRACEROUTE_MAX_HOPS = 20
# Poll the exec stream at this interval so each printed hop is read within
# about two seconds.
_EXEC_POLL_S = 2


class UntraceableNodeError(ValueError):
    """A trace endpoint has no loopback address to trace to or from."""


@dataclass(frozen=True, slots=True)
class TraceEndpoint:
    """A traced node and the node whose workload runs its traceroute.

    TEMPORARY host-node stopgap: a host node has no routing daemon and no
    trace tooling, so its traceroute runs from the FRR gateway it attaches
    to. The reported path then omits the host-to-gateway LAN hop.
    """

    node: TracerNode
    runs_from: TracerNode


@dataclass(frozen=True, slots=True)
class _Direction:
    hops: tuple[str, ...]
    hop_rtts: tuple[float | None, ...]
    state: TraceState
    rtt_ms: float | None
    error: str | None


class PathTracer:
    """Traces the path between two nodes, one traceroute in each direction."""

    def __init__(
        self,
        *,
        node_registry: Mapping[str, TracerNode],
        namespace: str,
        core_v1: Callable[[], kubernetes.client.CoreV1Api],
        read_sim_time: Callable[[], str],
    ) -> None:
        self._node_registry = node_registry
        self._node_by_address: dict[str, str] = {}
        for node_id, node in node_registry.items():
            for address in node.addresses_ipv4:
                owner = self._node_by_address.setdefault(address, node_id)
                if owner != node_id:
                    raise ValueError(f"address {address} is assigned to {owner} and {node_id}")
        self._namespace = namespace
        self._core_v1 = core_v1
        self._read_sim_time = read_sim_time

    def endpoint(self, node_id: str) -> TraceEndpoint:
        """The node and the workload that traces for it; refuses a node it cannot trace."""
        node = self._node_registry.get(node_id)
        if node is None:
            raise UntraceableNodeError(f"{node_id} has no loopback address to trace")
        gateway_id = node.trace_gateway_node_id
        if gateway_id is None:
            return TraceEndpoint(node=node, runs_from=node)
        gateway = self._node_registry.get(gateway_id)
        if gateway is None:
            raise UntraceableNodeError(
                f"{node_id} is traced from its gateway {gateway_id}, which has no loopback address"
            )
        return TraceEndpoint(node=node, runs_from=gateway)

    def trace_between(self, src: str, dst: str, *, flow_id: str) -> TracedPath:
        """Trace once between two node ids. Nothing retraces the result."""
        return self.trace(self.endpoint(src), self.endpoint(dst), flow_id=flow_id, tracing=False)

    def trace(
        self,
        src: TraceEndpoint,
        dst: TraceEndpoint,
        *,
        flow_id: str,
        tracing: bool,
        on_progress: Callable[[TracedPath], None] | None = None,
    ) -> TracedPath:
        """Trace both directions concurrently and assemble the result.

        ``on_progress`` receives the result so far each time the forward
        traceroute prints a hop, with the reverse direction still running.
        """
        sim_time = self._read_sim_time()
        traced_at = datetime.now(UTC).isoformat()

        def assemble(forward: _Direction, reverse: _Direction) -> TracedPath:
            return self._assemble(
                src,
                dst,
                forward,
                reverse,
                flow_id=flow_id,
                tracing=tracing,
                traced_at=traced_at,
                sim_time=sim_time,
            )

        forward_progress = None
        if on_progress is not None:
            reverse_running = _Direction(
                hops=(dst.node.node_id,),
                hop_rtts=(None,),
                state="running",
                rtt_ms=None,
                error=None,
            )

            def forward_progress(forward: _Direction) -> None:
                on_progress(assemble(forward, reverse_running))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            forward_future = pool.submit(self._trace_direction, src, dst, forward_progress)
            reverse_future = pool.submit(self._trace_direction, dst, src, None)
            forward = forward_future.result()
            reverse = reverse_future.result()
        return assemble(forward, reverse)

    def failed(
        self, src: TraceEndpoint, dst: TraceEndpoint, error: str, *, flow_id: str
    ) -> TracedPath:
        """A result whose both directions failed with one reason; nothing retraces it."""
        return self._assemble(
            src,
            dst,
            self._failed(src, error),
            self._failed(dst, error),
            flow_id=flow_id,
            tracing=False,
            traced_at=datetime.now(UTC).isoformat(),
            sim_time=self._read_sim_time(),
        )

    def _trace_direction(
        self,
        origin: TraceEndpoint,
        target: TraceEndpoint,
        on_progress: Callable[[_Direction], None] | None,
    ) -> _Direction:
        target_address = target.runs_from.loopback_ipv4

        def progress(stdout: str) -> None:
            if on_progress is None:
                return
            complete = stdout[: stdout.rfind("\n") + 1]
            hops = parse_traceroute(complete)
            if hops:
                on_progress(self._direction(origin, hops, target_address, finished=False))

        try:
            stdout, error = self._run_traceroute(origin.runs_from.node_id, target_address, progress)
            if error is not None:
                return self._failed(origin, error)
            hops = parse_traceroute(stdout)
        except TracerouteOutputError as exc:
            return self._failed(origin, f"unreadable traceroute output: {exc}")
        return self._direction(origin, hops, target_address, finished=True)

    def _run_traceroute(
        self, node_id: str, target_address: str, on_output: Callable[[str], None]
    ) -> tuple[str, str | None]:
        """Run traceroute in a node's workload; return its output and any failure.

        The workload is read on every trace, so a replaced pod is traced
        where it runs now.
        """
        v1 = self._core_v1()
        try:
            workload = read_workload_target(v1, self._namespace, node_id)
        except WorkloadTargetError as exc:
            return "", str(exc)

        stdout = ""
        stderr = ""
        try:
            resp = kubernetes.stream.stream(
                v1.connect_get_namespaced_pod_exec,
                workload.pod_name,
                self._namespace,
                container=workload.container,
                command=[
                    "traceroute",
                    "-I",
                    "-n",
                    "-w",
                    str(_TRACEROUTE_WAIT_S),
                    "-q",
                    "1",
                    "-m",
                    str(_TRACEROUTE_MAX_HOPS),
                    target_address,
                ],
                stderr=True,
                stdout=True,
                stdin=False,
                tty=False,
                _preload_content=False,
            )
        except Exception as exc:
            # The exec boundary: whatever stops the command from starting is
            # this direction's failure, shown with its cause class.
            log.error("traceroute exec in %s failed: %s", workload.pod_name, exc, exc_info=exc)
            return "", f"traceroute could not start in {workload.pod_name} ({type(exc).__name__})"
        try:
            # One read after the stream closes collects output that arrived
            # with the close.
            closed = False
            while not closed:
                closed = not resp.is_open()
                if not closed:
                    resp.update(timeout=_EXEC_POLL_S)
                chunk = resp.read_stdout()
                if chunk:
                    stdout += chunk
                    on_output(stdout)
                stderr += resp.read_stderr()
            returncode = resp.returncode
        finally:
            resp.close()
        if returncode != 0:
            detail = stderr.strip().splitlines()[-1] if stderr.strip() else "no error output"
            return stdout, f"traceroute exited {returncode} in {workload.pod_name}: {detail}"
        return stdout, None

    def _direction(
        self,
        origin: TraceEndpoint,
        hops: tuple[TracerouteHop, ...],
        target_address: str,
        *,
        finished: bool,
    ) -> _Direction:
        labels = [origin.node.node_id]
        rtts: list[float | None] = [None]
        for hop in hops:
            labels.append(self._hop_label(hop))
            rtts.append(hop.rtt_ms)
        last = hops[-1] if hops else None
        reached = last is not None and last.address == target_address
        state: TraceState
        if not finished:
            state = "running"
        elif reached:
            state = "reached"
        else:
            state = "not_reached"
        return _Direction(
            hops=tuple(labels),
            hop_rtts=tuple(rtts),
            state=state,
            rtt_ms=last.rtt_ms if state == "reached" and last is not None else None,
            error=None,
        )

    def _hop_label(self, hop: TracerouteHop) -> str:
        if hop.address is None:
            return SILENT_HOP
        return self._node_by_address.get(hop.address, hop.address)

    @staticmethod
    def _failed(origin: TraceEndpoint, error: str) -> _Direction:
        return _Direction(
            hops=(origin.node.node_id,),
            hop_rtts=(None,),
            state="failed",
            rtt_ms=None,
            error=error,
        )

    def _assemble(
        self,
        src: TraceEndpoint,
        dst: TraceEndpoint,
        forward: _Direction,
        reverse: _Direction,
        *,
        flow_id: str,
        tracing: bool,
        traced_at: str,
        sim_time: str,
    ) -> TracedPath:
        return TracedPath(
            flow_id=flow_id,
            src_node=src.node.node_id,
            dst_node=dst.node.node_id,
            hops=list(forward.hops),
            hop_rtts=list(forward.hop_rtts),
            state=forward.state,
            rtt_ms=forward.rtt_ms,
            error=forward.error,
            reverse_hops=list(reverse.hops),
            reverse_hop_rtts=list(reverse.hop_rtts),
            reverse_state=reverse.state,
            reverse_rtt_ms=reverse.rtt_ms,
            reverse_error=reverse.error,
            asymmetry_detected=self._asymmetry(forward, reverse),
            tracing=tracing,
            traced_at=traced_at,
            sim_time=sim_time,
        )

    def _asymmetry(self, forward: _Direction, reverse: _Direction) -> bool | None:
        """Whether the two directions crossed different nodes between the ends.

        Known only when both directions reached their destination and every
        hop between the ends answered from a node's address. The ends are left
        out: each direction starts at its own endpoint and ends at the other
        endpoint's traceroute host.
        """
        if forward.state != "reached" or reverse.state != "reached":
            return None
        forward_between = forward.hops[1:-1]
        reverse_between = reverse.hops[1:-1]
        if not all(hop in self._node_registry for hop in forward_between + reverse_between):
            return None
        return forward_between != tuple(reversed(reverse_between))
