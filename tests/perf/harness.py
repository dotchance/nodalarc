# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME performance measurement harness.

Drives the production compute_step path for a scenario session and
reports per-segment timings using the SAME instrument the live engine
records (ome.telemetry.StepTimings) — test and production measure
identical code.

Every run emits a provenance-stamped artifact (JSON) so performance
claims are citable and comparable over time:

    {scenario, git sha + dirty flag, host fingerprint (CPU model, python,
     numpy version), pinning status, steps, repetition spread,
     per-segment p50/p95 ms, per-step p50/p95 ms}

Methodology rules enforced here:
- warmup steps are discarded;
- medians with spread are reported, never single samples;
- the profiler is never enabled in budget runs (profiler-on numbers must
  not be quoted as wall numbers);
- artifacts record enough environment to refuse apples-to-oranges
  comparisons (numbers from different hosts are trends, not deltas).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "perf-results"


@dataclass(frozen=True)
class PerfRunResult:
    """Aggregated result of one scenario run (all repetitions)."""

    scenario: str
    steps_per_rep: int
    repetitions: int
    step_p50_ms: float
    step_p95_ms: float
    rep_p50_spread_ms: tuple[float, float]  # (min, max) of per-rep medians
    segments_p50_ms: dict[str, float]
    artifact_path: str


def _git_provenance() -> dict[str, object]:
    def _run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    sha = _run("rev-parse", "--short=12", "HEAD") or "unknown"
    dirty = bool(_run("status", "--porcelain"))
    return {"git_sha": sha, "dirty_tree": dirty}


def _host_fingerprint() -> dict[str, object]:
    cpu_model = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        cpu_model = platform.processor()
    try:
        import numpy

        numpy_version = numpy.__version__
    except ImportError:
        numpy_version = "absent"
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    return {
        "hostname": platform.node(),
        "cpu_model": cpu_model,
        "python": platform.python_version(),
        "numpy": numpy_version,
        "cpu_affinity": affinity,
        "pinned_single_core": len(affinity) == 1,
    }


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    if len(ordered) < 2:
        return ordered[0] if ordered else 0.0
    index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def run_scenario(
    *,
    scenario: str,
    session_path: str,
    steps: int,
    warmup: int = 2,
    repetitions: int = 3,
) -> PerfRunResult:
    """Run `steps` compute_step ticks against the session, `repetitions` times.

    Each repetition rebuilds carried state from scratch (fresh isl/gs
    state and associations) so repetitions are independent; the step
    context is built once (immutable inputs, mirrors a live session).
    """
    from ome.event_stream import build_step_context, compute_step
    from ome.main import _effective_ground_scheduling_for_runtime, _load_session_config

    cfg = _load_session_config(REPO_ROOT / session_path, run_id=f"perf-{scenario}")
    session = cfg.resolved
    step_seconds = int(session.time.step_seconds)

    from nodalarc.models.session import resolve_session_epoch

    epoch_unix = resolve_session_epoch(session.time)
    step_ctx = build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        propagator_id=cfg.propagator_id,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_scheduling=_effective_ground_scheduling_for_runtime(cfg.ground_scheduling),
        ground_link_model=cfg.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=cfg.ground_candidate_satellites_by_gs,
        node_metadata=cfg.node_metadata,
        body_frames=cfg.body_frames,
        body_ephemeris=cfg.body_ephemeris,
        active_bodies=cfg.active_bodies,
    )

    rep_step_ms: list[list[float]] = []
    rep_segments: list[list[dict[str, float]]] = []
    for _rep in range(repetitions):
        isl_state: dict = {}
        gs_state: dict = {}
        associations: dict = {}
        teardowns: dict = {}
        dwell_state: dict = {}
        step_ms: list[float] = []
        segments: list[dict[str, float]] = []
        for step in range(steps + warmup):
            wall_start = time.perf_counter()
            result = compute_step(
                step_ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                isl_state,
                gs_state,
                associations,
                teardowns,
                dwell_state=dwell_state,
            )
            elapsed_ms = (time.perf_counter() - wall_start) * 1000
            associations = result.associations
            teardowns = result.pending_teardowns
            if step >= warmup:
                step_ms.append(elapsed_ms)
                segments.append({k: v * 1000 for k, v in result.timings.segments.items()})
        rep_step_ms.append(step_ms)
        rep_segments.append(segments)

    all_steps = [ms for rep in rep_step_ms for ms in rep]
    all_segments = [seg for rep in rep_segments for seg in rep]
    segment_names = sorted({name for seg in all_segments for name in seg})
    seg_p50 = {
        name: round(median([seg.get(name, 0.0) for seg in all_segments]), 3)
        for name in segment_names
    }
    seg_p95 = {
        name: round(_p95([seg.get(name, 0.0) for seg in all_segments]), 3) for name in segment_names
    }
    rep_medians = [median(rep) for rep in rep_step_ms]

    artifact = {
        "kind": "ome-perf-run",
        "scenario": scenario,
        "session_path": session_path,
        "recorded_at_unix": time.time(),
        **_git_provenance(),
        "host": _host_fingerprint(),
        "steps_per_rep": steps,
        "warmup_discarded": warmup,
        "repetitions": repetitions,
        "step_p50_ms": round(median(all_steps), 3),
        "step_p95_ms": round(_p95(all_steps), 3),
        "rep_median_min_ms": round(min(rep_medians), 3),
        "rep_median_max_ms": round(max(rep_medians), 3),
        "segments_p50_ms": seg_p50,
        "segments_p95_ms": seg_p95,
        "node_count": len(cfg.satellites) + len(cfg.gs_file.stations),
        "step_seconds": step_seconds,
    }

    ARTIFACT_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    artifact_path = ARTIFACT_DIR / f"{scenario}-{stamp}-{artifact['git_sha']}.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n")
    index_path = ARTIFACT_DIR / "index.jsonl"
    with index_path.open("a") as fh:
        fh.write(json.dumps(artifact) + "\n")

    return PerfRunResult(
        scenario=scenario,
        steps_per_rep=steps,
        repetitions=repetitions,
        step_p50_ms=artifact["step_p50_ms"],
        step_p95_ms=artifact["step_p95_ms"],
        rep_p50_spread_ms=(artifact["rep_median_min_ms"], artifact["rep_median_max_ms"]),
        segments_p50_ms=seg_p50,
        artifact_path=str(artifact_path),
    )


def latest_artifact(scenario: str, *, hostname: str) -> dict | None:
    """Most recent prior artifact for (scenario, host), for trend context."""
    index_path = ARTIFACT_DIR / "index.jsonl"
    if not index_path.exists():
        return None
    latest: dict | None = None
    for line in index_path.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("scenario") == scenario and entry.get("host", {}).get("hostname") == hostname:
            latest = entry
    return latest
