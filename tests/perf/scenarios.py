# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Performance scenario matrix and budgets.

Scenarios cover the shapes that matter (per the scale-plan scenario
matrix): a small LEO baseline, the mixed-regime multi-body flagship with
dwell policies active (the measured pathological shape), a dwell-policy
GEO session (continuously visible pairs force full-horizon walks every
tick), and a larger LEO constellation.

BUDGETS: hard ceilings on per-step p50, set ~2-3x above the measured
baseline on the reference dev host so they catch order-of-magnitude
regressions on any machine while tolerating host variance. Precision
trend-tracking comes from the artifacts (perf-results/), which are
host-fingerprinted — budgets are the alarm, artifacts are the record.
Tighten budgets as the scale-plan phases land; loosening one is a
regression and requires the measurement protocol's written justification.

Baseline (2026-06-10, AMD Ryzen 7 7840HS, scalar engine, pre-P1):
  flagship      ~950-1450 ms/step (dwell estimator = 99.6%)
  geo-tdrs      dwell-active, small constellation
  leo-simple    ~5-15 ms/step (no dwell policy)
  leo-walker    ~30-80 ms/step (176 sats, no dwell policy)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PerfScenario:
    name: str
    session_path: str
    steps: int
    repetitions: int
    budget_step_p50_ms: float
    note: str


SCENARIOS: tuple[PerfScenario, ...] = (
    PerfScenario(
        name="leo-simple",
        session_path="catalog/nodalarc/sessions/earth-leo-simple.yaml",
        steps=10,
        repetitions=3,
        budget_step_p50_ms=5.0,
        note="36-satellite LEO ring, no dwell policy — the small-session floor "
        "(re-baselined 2026-06-11: measured 1.4 ms after the batch kernel)",
    ),
    PerfScenario(
        name="leo-walker",
        session_path="catalog/nodalarc/sessions/earth-leo-walker.yaml",
        steps=8,
        repetitions=3,
        budget_step_p50_ms=20.0,
        note="176-satellite Walker constellation, no dwell policy "
        "(re-baselined 2026-06-11: measured 5.9 ms after the batch kernel)",
    ),
    PerfScenario(
        name="geo-dwell",
        session_path="catalog/nodalarc/sessions/earth-geo-tdrs.yaml",
        steps=8,
        repetitions=3,
        budget_step_p50_ms=3.0,
        note=(
            "GEO with a longest-remaining-pass policy: satellites never set — "
            "the dwell frontier memo answers in O(1) (re-baselined 2026-06-11: "
            "measured 0.62 ms; the pre-frontier full-horizon walk was ~947 ms)"
        ),
    ),
    PerfScenario(
        name="flagship",
        session_path="catalog/nodalarc/sessions/earth-leo-heo-geo-luna-reachability.yaml",
        steps=6,
        repetitions=2,
        budget_step_p50_ms=16.0,
        note=(
            "mixed LEO/MEO/HEO/GEO + Luna with dwell policies on HEO+GEO "
            "(re-baselined 2026-06-11: measured 5.2 ms after dwell frontier, "
            "allocator index, deferred wire build, and the batch kernel; 16 ms "
            "is also the 60x wall budget at the 1 Hz tick)"
        ),
    ),
)


def scenario_by_name(name: str) -> PerfScenario:
    for scenario in SCENARIOS:
        if scenario.name == name:
            return scenario
    raise KeyError(f"Unknown perf scenario {name!r}; known: {[s.name for s in SCENARIOS]}")
