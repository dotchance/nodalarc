"""The live trace repeats a path trace until stopped and shows its latest result."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from vs_api.continuous_tracer import TRACE_FLOW_ID, ContinuousTracer
from vs_api.path_tracer import PathTracer

from tests.unit.test_path_tracer import REGISTRY, SIM_TIME, _Cluster, _output, _Stream


def _live(path_changes: list | None = None) -> ContinuousTracer:
    return ContinuousTracer(
        path_tracer=PathTracer(
            node_registry=REGISTRY,
            namespace="nodalarc",
            core_v1=lambda: _Cluster({}),
            read_sim_time=lambda: SIM_TIME,
        ),
        interval_s=0.01,
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
