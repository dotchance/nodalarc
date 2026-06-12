# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Human-readable rendering of perf harness results.

The JSON artifacts are for machines and trend tooling; this module is for
the person reading `make perf-test` output. Plain words over jargon:
"typical step" is the median, "worst 1-in-20" is the 95th percentile, and
segments get names a human can act on.
"""

from __future__ import annotations

from tests.perf.harness import PerfRunResult

# Segment key -> what a human should read. Order here is display order.
SEGMENT_LABELS: tuple[tuple[str, str], ...] = (
    ("dwell", "remaining-pass dwell estimator"),
    ("visibility", "ground visibility checks"),
    ("propagation", "orbit propagation"),
    ("isl", "inter-satellite link evaluation"),
    ("allocator", "ground link allocation"),
    ("diff", "event diffs + snapshot prep"),
    ("publish_authority", "snapshot publishing"),
    ("publish_checkpoint", "checkpoint publishing"),
    ("publish_events", "event publishing"),
    ("output_file", "JSONL file output"),
)


# Product speed ladder at the ratified 1 Hz system tick (owner,
# 2026-06-10): these are the speeds the UI offers. At Nx compression one
# tick must finish inside 1000/N ms of wall time. These are requirements,
# not alarms - a scenario that misses a speed is shown failing it even
# while the regression gate passes.
PRODUCT_SPEEDS = (1, 5, 10, 30, 60, 120)


def speed_marks(step_ms: float) -> str:
    """'1x ok  5x ok  10x FAIL ...' for the product speed ladder."""
    parts = []
    for speed in PRODUCT_SPEEDS:
        budget = 1000.0 / speed
        parts.append(f"{speed}x {'ok' if step_ms <= budget else 'FAIL'}")
    return "  ".join(parts)


def max_sustainable_speed(step_ms: float) -> float:
    """Highest compression one tick of this cost can honestly sustain."""
    return 1000.0 / step_ms if step_ms > 0 else float("inf")


def _pct(part_ms: float, whole_ms: float) -> str:
    if whole_ms <= 0:
        return "0%"
    return f"{(part_ms / whole_ms) * 100:.0f}%"


def format_scenario_report(
    result: PerfRunResult,
    *,
    description: str,
    budget_ms: float,
    node_count: int | None = None,
    prior: dict | None = None,
) -> str:
    """One scenario as a short human-readable block."""
    lines: list[str] = []
    step = result.step_p50_ms
    budget_used = (step / budget_ms) * 100 if budget_ms > 0 else 0.0
    status = "PASS" if step <= budget_ms else "FAIL"

    nodes = f"{node_count} nodes — " if node_count else ""
    lines.append(f"  {result.scenario}: {nodes}{description}")
    lines.append(
        f"    one simulation tick takes ~{_fmt_ms(step)} "
        f"(worst 1-in-20: {_fmt_ms(result.step_p95_ms)})"
    )
    lines.append(
        f"    regression alarm: {status} — {budget_used:.0f}% of the "
        f"{_fmt_ms(budget_ms)} ceiling (the ceiling tracks the known "
        f"baseline; it answers 'did we get worse', not 'is this good')"
    )
    sustainable = max_sustainable_speed(step)
    lines.append(
        f"    speed ladder at the 1 Hz tick: {speed_marks(step)}"
        f"   (this step cost can sustain ~{sustainable:.0f}x)"
    )

    # Where the time goes: top segments by share of the step, with the
    # dwell estimator shown as the part of visibility it actually is.
    segs = dict(result.segments_p50_ms)
    dwell = segs.pop("dwell", 0.0)
    parts: list[str] = []
    for key, label in SEGMENT_LABELS:
        if key == "dwell":
            continue
        value = segs.get(key, 0.0)
        if value < 0.01 or value / step < 0.02:
            continue
        text = f"{label} {_fmt_ms(value)} ({_pct(value, step)})"
        if key == "visibility" and dwell > 0:
            text += f", of which the dwell estimator is {_fmt_ms(dwell)}"
        parts.append(text)
    if parts:
        lines.append("    where the time goes: " + "; ".join(parts))

    if prior is not None:
        prev = prior.get("step_p50_ms")
        prev_sha = prior.get("git_sha", "?")
        if isinstance(prev, (int, float)) and prev > 0:
            delta = ((step - prev) / prev) * 100
            direction = "slower" if delta > 0 else "faster"
            if abs(delta) < 3:
                trend = f"unchanged vs last run on this host ({_fmt_ms(prev)} @ {prev_sha})"
            else:
                trend = (
                    f"{abs(delta):.0f}% {direction} than last run on this host "
                    f"({_fmt_ms(prev)} @ {prev_sha})"
                )
            lines.append(f"    trend: {trend}")

    lines.append(f"    artifact: {result.artifact_path}")
    return "\n".join(lines)


def format_summary_table(rows: list[tuple[str, float, float, float]]) -> str:
    """Final at-a-glance table: (scenario, step_ms, p95_ms, budget_ms)."""
    speed_headers = "   ".join(f"{s}x" for s in PRODUCT_SPEEDS)
    lines = [
        f"  scenario       typical step   regression       {speed_headers}   max honest",
        "  ------------   ------------   --------------   "
        + "-" * (len(speed_headers) + 2)
        + "   ----------",
    ]
    for name, step, _p95, budget in rows:
        gate = "OK" if step <= budget else "WORSE"
        marks = "   ".join(
            f"{'ok' if step <= 1000.0 / speed else '--':>{len(str(speed)) + 1}}"
            for speed in PRODUCT_SPEEDS
        )
        sustainable = max_sustainable_speed(step)
        cap = f"~{sustainable:.0f}x" if sustainable < 10000 else ">9999x"
        lines.append(
            f"  {name:<12}   {_fmt_ms(step):>12}   {gate:<5}({_fmt_ms(budget):>7})   {marks}   {cap:>8}"
        )
    lines.append("")
    lines.append("  ok = one tick fits inside that speed's wall budget at the 1 Hz tick;")
    lines.append(
        "  -- = it does not (the engine would silently lag — the honesty banner shows it)."
    )
    return "\n".join(lines)


def _fmt_ms(value_ms: float) -> str:
    if value_ms >= 1000:
        return f"{value_ms / 1000:.2f} s"
    if value_ms >= 100:
        return f"{value_ms:.0f} ms"
    if value_ms >= 1:
        return f"{value_ms:.1f} ms"
    return f"{value_ms:.2f} ms"
