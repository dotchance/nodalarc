# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Per-segment pacing telemetry for the OME.

The OME attributes its own time. Every tick records where its wall time
went — physics segments inside compute_step, publish segments in the
pacing loop, and sleep-vs-overrun against the pacing schedule — so that
"why is the engine slow" is answered by production data, never by
inference. The same instrument drives the perf test harness, the periodic
operator telemetry, and the achieved-rate honesty surface: one
measurement path, three consumers.

Overhead: a handful of perf_counter() calls per tick (sub-microsecond
each). The instrument is always on; conditional instrumentation would
mean test and production measure different code.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from statistics import median, quantiles
from time import perf_counter

from pydantic import BaseModel, ConfigDict

# Physics segments (recorded inside compute_step).
SEG_PROPAGATION = "propagation"
SEG_ISL = "isl"
SEG_VISIBILITY = "visibility"  # evaluate_ground_visibility INCLUDING dwell
SEG_DWELL = "dwell"  # the remaining-pass estimator inside visibility
SEG_ALLOCATOR = "allocator"
SEG_DIFF = "diff"  # event diffs + snapshot source + positions glue

# Pacing-loop segments (recorded in the run loop around publish work).
SEG_PUBLISH_AUTHORITY = "publish_authority"  # link + decision snapshots
SEG_PUBLISH_CHECKPOINT = "publish_checkpoint"
SEG_PUBLISH_EVENTS = "publish_events"  # visibility events + clock tick
SEG_OUTPUT_FILE = "output_file"  # optional JSONL writer

# Derived per tick: time spent sleeping (>= 0) or overrunning (< 0 means
# the tick missed its wall target by that much).
SEG_SLEEP = "sleep"

# Segments measured INSIDE another segment's span. They are reported for
# attribution but excluded from totals to avoid double counting.
NESTED_SEGMENTS = frozenset({SEG_DWELL})


@dataclass
class StepTimings:
    """Wall-time attribution for one tick, in seconds, by segment.

    Mutable accumulator: compute_step fills the physics segments, the
    pacing loop adds publish segments and sleep/overrun. ``measure`` is
    re-entrant per segment (times accumulate), so a segment touched in
    two places sums correctly.
    """

    segments: dict[str, float] = field(default_factory=dict)

    def add(self, segment: str, seconds: float) -> None:
        self.segments[segment] = self.segments.get(segment, 0.0) + seconds

    @contextmanager
    def measure(self, segment: str):
        start = perf_counter()
        try:
            yield
        finally:
            self.add(segment, perf_counter() - start)

    def compute_total(self) -> float:
        """Total attributed compute time, excluding sleep and nested segments."""
        return sum(
            v for k, v in self.segments.items() if k != SEG_SLEEP and k not in NESTED_SEGMENTS
        )


class PacingWindowStats(BaseModel):
    """Aggregated pacing telemetry over a recent window of ticks.

    Published periodically as OpsEvent details and logged at INFO. All
    times in milliseconds. ``achieved_ratio`` is the measured
    d(sim)/d(wall) over the window; ``requested_ratio`` is the commanded
    rate. The two diverging is the saturation signal the rate-honesty
    surface reports.
    """

    model_config = ConfigDict(frozen=True)

    window_ticks: int
    window_wall_s: float
    step_seconds: float
    requested_ratio: float
    achieved_ratio: float
    budget_ms_per_tick: float
    compute_p50_ms: float
    compute_p95_ms: float
    segments_p50_ms: dict[str, float]
    segments_p95_ms: dict[str, float]
    overrun_ticks: int  # ticks that missed their wall target


@dataclass
class PacingTelemetryWindow:
    """Sliding window of per-tick timings owned by the pacing loop."""

    maxlen: int = 600
    _ticks: deque[dict[str, float]] = field(init=False)
    _wall_marks: deque[float] = field(init=False)

    def __post_init__(self) -> None:
        self._ticks = deque(maxlen=self.maxlen)
        self._wall_marks = deque(maxlen=self.maxlen)

    def record(self, timings: StepTimings, *, wall_mark: float) -> None:
        self._ticks.append(dict(timings.segments))
        self._wall_marks.append(wall_mark)

    def clear(self) -> None:
        """Reset at pacing-reference resets (unpause, seek, rate change).

        Wall-mark gaps across those events are schedule discontinuities,
        not delivery failures; measuring across them would manufacture
        false rate-degradation alarms.
        """
        self._ticks.clear()
        self._wall_marks.clear()

    def __len__(self) -> int:
        return len(self._ticks)

    def achieved_ratio(self, *, step_seconds: float, last_n: int = 30) -> float | None:
        """Measured d(sim)/d(wall) over the most recent ticks; cheap per-tick read.

        None until enough ticks exist to be meaningful. This is the value
        the honesty surface publishes every tick — percentile aggregation
        stays in snapshot() at its slower cadence.
        """
        if len(self._wall_marks) < 3:
            return None
        marks = list(self._wall_marks)[-last_n:]
        window_wall_s = marks[-1] - marks[0]
        if window_wall_s <= 0:
            return None
        return ((len(marks) - 1) * step_seconds) / window_wall_s

    def snapshot(
        self,
        *,
        step_seconds: float,
        requested_ratio: float,
    ) -> PacingWindowStats | None:
        """Aggregate the window; None when too few ticks to be meaningful."""
        if len(self._ticks) < 5:
            return None

        ticks = list(self._ticks)
        marks = list(self._wall_marks)
        window_wall_s = marks[-1] - marks[0]
        # Sim time advanced = ticks-1 intervals between the recorded marks.
        achieved = ((len(ticks) - 1) * step_seconds) / window_wall_s if window_wall_s > 0 else 0.0
        budget_ms = (step_seconds / requested_ratio) * 1000 if requested_ratio > 0 else 0.0

        computes = [
            sum(v for k, v in tick.items() if k != SEG_SLEEP and k not in NESTED_SEGMENTS) * 1000
            for tick in ticks
        ]
        segment_names = sorted({k for tick in ticks for k in tick})
        seg_p50: dict[str, float] = {}
        seg_p95: dict[str, float] = {}
        for name in segment_names:
            values = [tick.get(name, 0.0) * 1000 for tick in ticks]
            seg_p50[name] = round(median(values), 3)
            seg_p95[name] = round(_p95(values), 3)

        overruns = sum(1 for tick in ticks if tick.get(SEG_SLEEP, 0.0) < 0.0)
        return PacingWindowStats(
            window_ticks=len(ticks),
            window_wall_s=round(window_wall_s, 3),
            step_seconds=step_seconds,
            requested_ratio=requested_ratio,
            achieved_ratio=round(achieved, 3),
            budget_ms_per_tick=round(budget_ms, 3),
            compute_p50_ms=round(median(computes), 3),
            compute_p95_ms=round(_p95(computes), 3),
            segments_p50_ms=seg_p50,
            segments_p95_ms=seg_p95,
            overrun_ticks=overruns,
        )


def _p95(values: list[float]) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    if len(values) < 20:
        return max(values)
    return quantiles(values, n=100)[94]
