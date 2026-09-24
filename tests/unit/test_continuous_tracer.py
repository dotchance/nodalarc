"""The live trace repeats a path trace until stopped and shows its latest result."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from vs_api.continuous_tracer import TRACE_FLOW_ID, ContinuousTracer
from vs_api.path_tracer import PathTracer

from tests.unit.test_path_tracer import REGISTRY, SIM_TIME, _Cluster, _output, _Stream


def _live(path_changes: list | None = None, *, max_seconds: float = 60.0) -> ContinuousTracer:
    return ContinuousTracer(
        path_tracer=PathTracer(
            node_registry=REGISTRY,
            namespace="nodalarc",
            core_v1=lambda: _Cluster({}),
            read_sim_time=lambda: SIM_TIME,
        ),
        interval_s=0.01,
        unreached_retrace_s=0.01,
        max_seconds=max_seconds,
        on_path_change=lambda *change: (path_changes if path_changes is not None else []).append(
            change
        ),
    )


def _streams(forward_outputs: list[list[str]], reverse_output: list[str]):
    """Each forward trace takes the next output; the last one repeats."""

    def next_stream(_exec, pod_name, _namespace, **_kwargs):
        if pod_name == "gs-beta":
            return _Stream(reverse_output)
        return _Stream(forward_outputs.pop(0) if len(forward_outputs) > 1 else forward_outputs[0])

    return next_stream


def test_the_first_cycle_streams_and_later_cycles_replace_the_result_whole() -> None:
    tracer = _live()
    progress_arguments: list = []
    real_trace = tracer._path_tracer.trace

    def recording_trace(*args, **kwargs):
        progress_arguments.append(kwargs["on_progress"])
        return real_trace(*args, **kwargs)

    async def run() -> None:
        with (
            patch(
                "kubernetes.stream.stream",
                side_effect=_streams(
                    [_output(" 1  10.2.1.1  9.0 ms")], _output(" 1  10.2.0.1  8.0 ms")
                ),
            ),
            patch.object(tracer._path_tracer, "trace", side_effect=recording_trace),
        ):
            await tracer.start("gs-alpha", "gs-beta")
            for _ in range(500):
                if len(progress_arguments) >= 2:
                    break
                await asyncio.sleep(0.01)
            await tracer.stop()

    asyncio.run(run())

    assert progress_arguments[0] is not None
    assert progress_arguments[1] is None


def test_an_internal_error_stops_the_loop_and_shows_the_failure() -> None:
    tracer = _live()

    async def run() -> None:
        with patch.object(tracer._path_tracer, "trace", side_effect=KeyError("boom")):
            await tracer.start("gs-alpha", "gs-beta")
            await asyncio.wait_for(tracer._task, timeout=5)

    asyncio.run(run())

    result = tracer.traced_path
    assert result is not None
    assert (result.flow_id, result.tracing) == (TRACE_FLOW_ID, False)
    assert (result.state, result.reverse_state) == ("failed", "failed")
    assert result.error == "The trace stopped on an internal error (KeyError); see the VS-API log"
    assert result.stop_reason == "internal_error"
    assert tracer.active is False


def test_a_path_change_is_recorded_when_the_forward_path_changes() -> None:
    changes: list = []
    tracer = _live(changes)
    streams = _streams(
        [
            _output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms"),
            _output(" 1  10.0.0.2  5.0 ms", " 2  10.2.1.1  9.0 ms"),
        ],
        _output(" 1  10.2.0.1  8.0 ms"),
    )

    async def run() -> None:
        with patch("kubernetes.stream.stream", side_effect=streams):
            await tracer.start("gs-alpha", "gs-beta")
            for _ in range(500):
                if changes:
                    break
                await asyncio.sleep(0.01)
            await tracer.stop()

    asyncio.run(run())

    assert changes == [
        (
            "gs-alpha",
            "gs-beta",
            ["gs-alpha", "sat-a", "gs-beta"],
            ["gs-alpha", "sat-b", "gs-beta"],
        )
    ]


async def _run_until(tracer: ContinuousTracer, streams, done) -> None:
    with patch("kubernetes.stream.stream", side_effect=streams):
        await tracer.start("gs-alpha", "gs-beta")
        for _ in range(500):
            if done():
                break
            await asyncio.sleep(0.01)
        await tracer.stop()


def test_a_silent_hop_is_not_a_path_change() -> None:
    changes: list = []
    tracer = _live(changes)
    cycles = [0]
    forward = [
        _output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms"),
        _output(" 1  *", " 2  10.2.1.1  9.0 ms"),
        _output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms"),
        _output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms"),
    ]

    def streams(_exec, pod_name, _namespace, **_kwargs):
        if pod_name == "gs-beta":
            return _Stream(_output(" 1  10.2.0.1  8.0 ms"))
        cycles[0] += 1
        return _Stream(forward.pop(0) if len(forward) > 1 else forward[0])

    asyncio.run(_run_until(tracer, streams, lambda: cycles[0] >= 4))

    assert cycles[0] >= 4
    assert changes == []


def test_a_reverse_path_change_is_recorded_from_the_destination() -> None:
    changes: list = []
    tracer = _live(changes)
    reverse = [
        _output(" 1  10.0.0.1  5.0 ms", " 2  10.2.0.1  8.0 ms"),
        _output(" 1  10.0.0.2  5.0 ms", " 2  10.2.0.1  8.0 ms"),
    ]

    def streams(_exec, pod_name, _namespace, **_kwargs):
        if pod_name == "gs-beta":
            return _Stream(reverse.pop(0) if len(reverse) > 1 else reverse[0])
        return _Stream(_output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms"))

    asyncio.run(_run_until(tracer, streams, lambda: bool(changes)))

    assert changes == [
        (
            "gs-beta",
            "gs-alpha",
            ["gs-beta", "sat-a", "gs-alpha"],
            ["gs-beta", "sat-b", "gs-alpha"],
        )
    ]


def test_the_trace_stops_at_its_time_limit_and_keeps_the_last_result() -> None:
    tracer = _live(max_seconds=0.3)
    streams = _streams(
        [_output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms")], _output(" 1  10.2.0.1  8.0 ms")
    )

    async def run() -> None:
        with patch("kubernetes.stream.stream", side_effect=streams):
            await tracer.start("gs-alpha", "gs-beta")
            await asyncio.wait_for(tracer._task, timeout=5)

    asyncio.run(run())

    result = tracer.traced_path
    assert result is not None
    assert (result.tracing, result.stop_reason) == (False, "time_limit")
    assert (result.state, result.hops) == ("reached", ["gs-alpha", "sat-a", "gs-beta"])
    assert tracer.active is False


def test_stopping_a_trace_signals_its_running_traceroutes() -> None:
    tracer = _live()
    signals: list = []
    real_trace = tracer._path_tracer.trace

    def recording_trace(*args, **kwargs):
        signals.append(kwargs["stopped"])
        return real_trace(*args, **kwargs)

    async def run() -> None:
        with (
            patch(
                "kubernetes.stream.stream",
                side_effect=_streams(
                    [_output(" 1  10.2.1.1  9.0 ms")], _output(" 1  10.2.0.1  8.0 ms")
                ),
            ),
            patch.object(tracer._path_tracer, "trace", side_effect=recording_trace),
        ):
            await tracer.start("gs-alpha", "gs-beta")
            for _ in range(500):
                if signals:
                    break
                await asyncio.sleep(0.01)
            await tracer.stop()

    asyncio.run(run())

    # The cycle in flight when the trace stopped was closed.
    assert signals and signals[-1].is_set()
    assert tracer.traced_path is None


class _OpenStream(_Stream):
    """A traceroute that keeps running until its exec is closed."""

    def __init__(self) -> None:
        super().__init__([])

    def is_open(self) -> bool:
        return not self.closed

    def update(self, timeout: int) -> None:
        time.sleep(0.005)


async def _until(done) -> None:
    for _ in range(500):
        if done():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never held")


VIA_SAT_A = ["gs-alpha", "sat-a", "gs-beta"]
VIA_SAT_B = ["gs-alpha", "sat-b", "gs-beta"]


def test_a_link_down_on_the_shown_path_replaces_it_and_streams_the_next_measurement() -> None:
    tracer = _live()
    forward = [
        lambda: _Stream(_output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms")),
        _OpenStream,
        lambda: _Stream(_output(" 1  10.0.0.2  5.0 ms", " 2  10.2.1.1  9.0 ms")),
    ]
    forward_cycles = [0]
    streaming: list[bool] = []
    shown_at_once: list = []
    real_trace = tracer._path_tracer.trace

    def recording_trace(*args, **kwargs):
        streaming.append(kwargs["on_progress"] is not None)
        return real_trace(*args, **kwargs)

    def streams(_exec, pod_name, _namespace, **_kwargs):
        if pod_name == "gs-beta":
            return _Stream(_output(" 1  10.0.0.1  4.0 ms", " 2  10.2.0.1  8.0 ms"))
        forward_cycles[0] += 1
        return (forward.pop(0) if len(forward) > 1 else forward[0])()

    async def run() -> None:
        with (
            patch("kubernetes.stream.stream", side_effect=streams),
            patch.object(tracer._path_tracer, "trace", side_effect=recording_trace),
        ):
            await tracer.start("gs-alpha", "gs-beta")
            # The first cycle finished via sat-a; the second is running and never ends.
            await _until(lambda: forward_cycles[0] >= 2)
            assert tracer.traced_path.hops == VIA_SAT_A
            tracer.notify_link_change("sat-a", "gs-alpha", up=False)
            shown_at_once.append(tracer.traced_path)
            await _until(
                lambda: (
                    tracer.traced_path.hops == VIA_SAT_B and tracer.traced_path.state == "reached"
                )
            )
            await tracer.stop()

    asyncio.run(run())

    measuring = shown_at_once[0]
    assert (measuring.hops, measuring.reverse_hops) == (["gs-alpha"], ["gs-beta"])
    assert (measuring.state, measuring.reverse_state, measuring.tracing) == (
        "running",
        "running",
        True,
    )
    # The first cycle streamed, the second did not, and the one after the break did.
    assert streaming[:3] == [True, False, True]


def test_only_a_link_down_between_consecutive_shown_hops_replaces_the_shown_path() -> None:
    tracer = _live()
    streams = _streams(
        [_output(" 1  10.0.0.1  5.0 ms", " 2  10.2.1.1  9.0 ms")],
        _output(" 1  10.0.0.1  4.0 ms", " 2  10.2.0.1  8.0 ms"),
    )
    shown: list = []

    async def run() -> None:
        with patch("kubernetes.stream.stream", side_effect=streams):
            await tracer.start("gs-alpha", "gs-beta")
            await _until(
                lambda: tracer.traced_path is not None and tracer.traced_path.state == "reached"
            )
            # Both ends are on the path but not linked in it; a link up is no break.
            tracer.notify_link_change("gs-alpha", "gs-beta", up=False)
            shown.append(tracer.traced_path.hops)
            tracer.notify_link_change("gs-alpha", "sat-a", up=True)
            shown.append(tracer.traced_path.hops)
            await tracer.stop()

    asyncio.run(run())

    assert shown == [VIA_SAT_A, VIA_SAT_A]
