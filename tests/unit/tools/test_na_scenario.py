"""Scenario execution preflight tests."""

from __future__ import annotations

import pytest

from tools import na_scenario


def _scenario_document(*steps: dict[str, object]) -> dict[str, object]:
    return {
        "scenario": {
            "name": "preflight",
            "description": "runtime availability preflight",
            "steps": list(steps),
        }
    }


def test_preflight_identifies_all_unavailable_mi_actions() -> None:
    scenario = na_scenario.ScenarioConfig.model_validate(
        _scenario_document(
            {"action": "wait_converge"},
            {"action": "measure", "duration_s": 5},
            {"action": "wait_converge", "timeout_s": 10},
        )["scenario"]
    )

    with pytest.raises(na_scenario.ScenarioRuntimeUnavailableError) as raised:
        na_scenario._preflight_scenario(scenario)

    assert raised.value.code == "scenario.mi_unavailable"
    assert raised.value.unavailable_actions == ("measure", "wait_converge")
    assert str(raised.value).endswith("unavailable scenario actions: measure, wait_converge")
