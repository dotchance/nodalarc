"""Scenario execution preflight tests."""

from __future__ import annotations

import asyncio

import pytest
import yaml

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


def test_async_runner_refuses_before_session_nats_or_scheduler_mutation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(yaml.safe_dump(_scenario_document({"action": "wait_converge"})))
    session_resolution_called = False
    nats_connect_called = False
    scheduler_command_called = False

    def resolve_session_id(_path: str) -> str:
        nonlocal session_resolution_called
        session_resolution_called = True
        return "session"

    async def connect(*_args, **_kwargs):
        nonlocal nats_connect_called
        nats_connect_called = True

    async def send_scheduler_cmd(*_args, **_kwargs):
        nonlocal scheduler_command_called
        scheduler_command_called = True

    monkeypatch.setattr(na_scenario, "_resolve_session_id", resolve_session_id)
    monkeypatch.setattr(na_scenario.nats, "connect", connect)
    monkeypatch.setattr(na_scenario, "_send_scheduler_cmd", send_scheduler_cmd)

    with pytest.raises(na_scenario.ScenarioRuntimeUnavailableError):
        asyncio.run(na_scenario.run_scenario_async(str(scenario_path), "unused-session.yaml"))

    assert session_resolution_called is False
    assert nats_connect_called is False
    assert scheduler_command_called is False


def test_cli_returns_deterministic_nonzero_for_unavailable_actions(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        yaml.safe_dump(
            _scenario_document(
                {"action": "wait_converge"},
                {"action": "measure", "duration_s": 1},
            )
        )
    )
    monkeypatch.setattr("nodalarc.platform_config.init_platform_config", lambda _path: None)

    exit_code = na_scenario.main(
        [
            "--scenario",
            str(scenario_path),
            "--session",
            "unused-session.yaml",
        ]
    )

    assert exit_code == 2
    assert "scenario.mi_unavailable" in caplog.text
    assert "unavailable scenario actions: measure, wait_converge" in caplog.text
