"""Test all scenario YAML files validate against ScenarioConfig.

Loads every YAML in configs/scenarios/ and verifies it parses through
the Pydantic model. Also checks that each step's action is a valid
ScenarioStep discriminated union member.
"""

from pathlib import Path

import pytest
import yaml
from nodalarc.models.scenario import ScenarioConfig

SCENARIOS_DIR = Path(__file__).parent.parent.parent / "configs" / "scenarios"

SCENARIO_FILES = sorted(SCENARIOS_DIR.glob("*.yaml"))


@pytest.mark.parametrize(
    "yaml_path",
    SCENARIO_FILES,
    ids=[p.stem for p in SCENARIO_FILES],
)
class TestScenarioYAMLValidation:
    def test_loads_and_validates(self, yaml_path: Path):
        """YAML loads and validates through ScenarioConfig."""
        raw = yaml.safe_load(yaml_path.read_text())
        assert "scenario" in raw, f"{yaml_path.name} missing top-level 'scenario' key"
        config = ScenarioConfig.model_validate(raw["scenario"])
        assert config.name, "Scenario must have a name"
        assert config.description, "Scenario must have a description"
        assert len(config.steps) > 0, "Scenario must have at least one step"

    def test_all_steps_are_valid_actions(self, yaml_path: Path):
        """Each step action is a valid ScenarioStep type."""
        raw = yaml.safe_load(yaml_path.read_text())
        config = ScenarioConfig.model_validate(raw["scenario"])

        valid_actions = {
            "wait",
            "inject_link_down",
            "inject_link_up",
            "inject_satellite_loss",
            "restore_satellite",
            "wait_converge",
            "measure",
            "reconfig",
        }
        for i, step in enumerate(config.steps):
            assert step.action in valid_actions, (
                f"{yaml_path.name} step {i}: unknown action '{step.action}'"
            )

    def test_round_trip_serialization(self, yaml_path: Path):
        """Config survives JSON round-trip."""
        raw = yaml.safe_load(yaml_path.read_text())
        config = ScenarioConfig.model_validate(raw["scenario"])
        json_str = config.model_dump_json()
        restored = ScenarioConfig.model_validate_json(json_str)
        assert restored.name == config.name
        assert len(restored.steps) == len(config.steps)


class TestExpectedScenarioFiles:
    """Verify all 6 expected scenario files exist."""

    EXPECTED = [
        "isl-failure.yaml",
        "steady-state.yaml",
        "ground-handover.yaml",
        "satellite-loss.yaml",
        "polar-seam.yaml",
        "time-mode-validation.yaml",
    ]

    @pytest.mark.parametrize("filename", EXPECTED)
    def test_scenario_file_exists(self, filename: str):
        path = SCENARIOS_DIR / filename
        assert path.exists(), f"Missing scenario file: {filename}"

    def test_total_scenario_count(self):
        """At least 6 scenario files should exist."""
        assert len(SCENARIO_FILES) >= 6
