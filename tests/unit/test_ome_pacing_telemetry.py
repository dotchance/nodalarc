# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for the OME per-segment pacing telemetry instrument.

The instrument is the foundation of the measurement protocol: every
performance claim about the OME must be answerable from these segments.
These tests pin (1) the accumulator semantics, (2) the window aggregation
math (achieved rate, overruns, percentiles), and (3) that compute_step
actually attributes its physics segments.
"""

from __future__ import annotations

import json
import time

from ome.event_stream import build_step_context, compute_step
from ome.telemetry import (
    SEG_ALLOCATOR,
    SEG_DIFF,
    SEG_DWELL,
    SEG_ISL,
    SEG_PROPAGATION,
    SEG_SLEEP,
    SEG_VISIBILITY,
    PacingTelemetryWindow,
    StepTimings,
)

from tests.conftest import load_runtime_ome_test_inputs


class TestStepTimings:
    def test_measure_records_elapsed(self):
        t = StepTimings()
        with t.measure(SEG_PROPAGATION):
            time.sleep(0.005)
        assert t.segments[SEG_PROPAGATION] >= 0.004

    def test_measure_accumulates_across_reentry(self):
        t = StepTimings()
        with t.measure(SEG_DIFF):
            time.sleep(0.002)
        with t.measure(SEG_DIFF):
            time.sleep(0.002)
        assert t.segments[SEG_DIFF] >= 0.003

    def test_total_excludes_sleep_and_nested_segments(self):
        t = StepTimings()
        t.add(SEG_PROPAGATION, 0.010)
        t.add(SEG_VISIBILITY, 0.020)  # includes dwell's span
        t.add(SEG_DWELL, 0.015)  # nested inside visibility
        t.add(SEG_SLEEP, 0.500)
        assert abs(t.compute_total() - 0.030) < 1e-9

    def test_overrun_recorded_as_negative_sleep(self):
        t = StepTimings()
        t.add(SEG_SLEEP, -0.004)
        assert t.segments[SEG_SLEEP] < 0
        assert t.compute_total() == 0.0


class TestPacingTelemetryWindow:
    def _window(self, *, ticks: int, compute_s: float, sleep_s: float, spacing_s: float):
        w = PacingTelemetryWindow()
        for i in range(ticks):
            t = StepTimings()
            t.add(SEG_PROPAGATION, compute_s)
            t.add(SEG_SLEEP, sleep_s)
            w.record(t, wall_mark=i * spacing_s)
        return w

    def test_too_few_ticks_yields_none(self):
        w = self._window(ticks=3, compute_s=0.001, sleep_s=0.01, spacing_s=0.0167)
        assert w.snapshot(step_seconds=1.0, requested_ratio=60.0) is None

    def test_achieved_ratio_reflects_wall_spacing(self):
        # Ticks of 1 sim-second spaced 1/60 wall-second apart = 60x delivered.
        w = self._window(ticks=61, compute_s=0.001, sleep_s=0.01, spacing_s=1.0 / 60.0)
        stats = w.snapshot(step_seconds=1.0, requested_ratio=60.0)
        assert stats is not None
        assert abs(stats.achieved_ratio - 60.0) < 0.5
        assert stats.overrun_ticks == 0

    def test_saturation_shows_in_achieved_ratio_and_overruns(self):
        # Commanded 60x but ticks actually spaced a full second apart = 1x,
        # every tick missing its wall target (negative sleep).
        w = self._window(ticks=30, compute_s=0.9, sleep_s=-0.85, spacing_s=1.0)
        stats = w.snapshot(step_seconds=1.0, requested_ratio=60.0)
        assert stats is not None
        assert stats.achieved_ratio < 1.5
        assert stats.requested_ratio == 60.0
        assert stats.overrun_ticks == 30
        assert stats.compute_p50_ms >= 850

    def test_stats_payload_is_json_serializable(self):
        w = self._window(ticks=10, compute_s=0.002, sleep_s=0.01, spacing_s=0.1)
        stats = w.snapshot(step_seconds=1.0, requested_ratio=10.0)
        assert stats is not None
        payload = json.loads(stats.model_dump_json())
        assert payload["requested_ratio"] == 10.0
        assert SEG_PROPAGATION in payload["segments_p50_ms"]


class TestComputeStepAttribution:
    def test_compute_step_attributes_physics_segments(self):
        session, _resolved, gs_file, sats, addressing, neighbors, candidates = (
            load_runtime_ome_test_inputs(origin="test.pacing_telemetry")
        )
        ctx = build_step_context(
            satellites=sats,
            addressing=addressing,
            gs_file=gs_file,
            neighbors=neighbors,
            propagator_id=session.orbit.propagator,
            ground_scheduling=session.scheduling.ground,
            ground_candidate_satellites_by_gs=candidates,
            ground_link_model=session.ground_link_model,
            body_frames=session.body_frames,
        )
        result = compute_step(ctx, 1704067200.0, 0, int(session.time.step_seconds), 0.0, {}, {})

        segments = result.timings.segments
        for name in (SEG_PROPAGATION, SEG_ISL, SEG_VISIBILITY, SEG_ALLOCATOR, SEG_DIFF):
            assert name in segments, f"missing segment {name}: {sorted(segments)}"
            assert segments[name] >= 0.0
        assert result.timings.compute_total() > 0.0


class TestAchievedRatio:
    def test_warmup_returns_none(self):
        w = PacingTelemetryWindow()
        t = StepTimings()
        t.add(SEG_PROPAGATION, 0.001)
        w.record(t, wall_mark=0.0)
        w.record(t, wall_mark=1.0)
        assert w.achieved_ratio(step_seconds=1.0) is None

    def test_measures_delivered_rate_not_commanded(self):
        w = PacingTelemetryWindow()
        t = StepTimings()
        t.add(SEG_PROPAGATION, 0.001)
        # 1 sim-second ticks spaced 1 wall-second apart = 1x, regardless
        # of what was commanded.
        for i in range(10):
            w.record(t, wall_mark=float(i))
        achieved = w.achieved_ratio(step_seconds=1.0)
        assert achieved is not None
        assert abs(achieved - 1.0) < 0.01

    def test_clear_resets_judgment_after_schedule_discontinuity(self):
        w = PacingTelemetryWindow()
        t = StepTimings()
        t.add(SEG_PROPAGATION, 0.001)
        for i in range(10):
            w.record(t, wall_mark=float(i))
        w.clear()
        # Post-reset: too few ticks to judge — no false degradation alarm
        # from the wall-mark gap a pause/seek/rate-change leaves behind.
        assert w.achieved_ratio(step_seconds=1.0) is None
        assert w.snapshot(step_seconds=1.0, requested_ratio=60.0) is None


class TestClockTickHonestyFields:
    def test_clock_tick_carries_achieved_and_degraded(self):
        from datetime import UTC, datetime

        from nodalarc.models.events import ClockTick

        tick = ClockTick(
            sim_time=datetime(2026, 6, 8, tzinfo=UTC),
            wall_time=datetime(2026, 6, 10, tzinfo=UTC),
            compression_ratio=60.0,
            achieved_ratio=1.2,
            pacing_degraded=True,
        )
        decoded = ClockTick.model_validate_json(tick.model_dump_json())
        assert decoded.achieved_ratio == 1.2
        assert decoded.pacing_degraded is True

    def test_clock_tick_defaults_are_wire_compatible(self):
        from datetime import UTC, datetime

        from nodalarc.models.events import ClockTick

        legacy = ClockTick(
            sim_time=datetime(2026, 6, 8, tzinfo=UTC),
            wall_time=datetime(2026, 6, 10, tzinfo=UTC),
            compression_ratio=1.0,
        )
        decoded = ClockTick.model_validate_json(
            legacy.model_dump_json(exclude={"achieved_ratio", "pacing_degraded"})
        )
        assert decoded.achieved_ratio is None
        assert decoded.pacing_degraded is False
