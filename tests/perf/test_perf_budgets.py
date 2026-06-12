# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Budget-gated OME performance tests.

Run via `make perf-test` (sets NODALARC_PERF=1). Excluded from the
default unit suite: these runs take wall-clock minutes by design and
their budgets are calibrated for dedicated runs, not for a loaded CI
worker mid-suite.

Each scenario runs the production compute_step path through the harness,
emits a provenance-stamped artifact to perf-results/, prints a
human-readable report (tests/perf/report.py), and FAILS if the per-step
median exceeds the scenario budget. A budget failure is a performance
regression: attribute it with the artifact's segment breakdown before
touching the budget.
"""

from __future__ import annotations

import os
import platform

import pytest

from tests.perf.harness import latest_artifact, run_scenario
from tests.perf.report import format_scenario_report, format_summary_table
from tests.perf.scenarios import SCENARIOS

pytestmark = pytest.mark.skipif(
    os.environ.get("NODALARC_PERF") != "1",
    reason="perf budget runs are explicit: run via `make perf-test` (sets NODALARC_PERF=1)",
)

_summary_rows: list[tuple[str, float, float, float]] = []


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_scenario_within_budget(scenario, capsys):
    prior = latest_artifact(scenario.name, hostname=platform.node())
    result = run_scenario(
        scenario=scenario.name,
        session_path=scenario.session_path,
        steps=scenario.steps,
        repetitions=scenario.repetitions,
    )

    import json
    from pathlib import Path

    node_count = json.loads(Path(result.artifact_path).read_text()).get("node_count")

    with capsys.disabled():
        print()
        print(
            format_scenario_report(
                result,
                description=scenario.note,
                budget_ms=scenario.budget_step_p50_ms,
                node_count=node_count,
                prior=prior,
            )
        )

    _summary_rows.append(
        (scenario.name, result.step_p50_ms, result.step_p95_ms, scenario.budget_step_p50_ms)
    )
    if len(_summary_rows) == len(SCENARIOS):
        with capsys.disabled():
            print()
            print(format_summary_table(_summary_rows))

    assert result.step_p50_ms <= scenario.budget_step_p50_ms, (
        f"PERF REGRESSION: {scenario.name} typical step {result.step_p50_ms}ms exceeds "
        f"budget {scenario.budget_step_p50_ms}ms. Read the segment breakdown in "
        f"{result.artifact_path} to attribute the regression; do not raise the "
        f"budget without the measurement protocol's written justification."
    )
