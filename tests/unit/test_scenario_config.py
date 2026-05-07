"""Test scenario configuration models — standalone per PRD Appendix B.

Tests all 7 action types validate individually, invalid action type rejected,
missing required fields rejected, ScenarioConfig round-trip serialization,
discriminated union dispatches correctly.
"""

import pytest
from nodalarc.models.scenario import (
    InjectLinkDownStep,
    InjectLinkUpStep,
    InjectSatelliteLossStep,
    MeasureStep,
    ReconfigStep,
    RestoreSatelliteStep,
    ScenarioConfig,
    ScenarioStep,
    WaitConvergeStep,
    WaitStep,
)
from pydantic import TypeAdapter, ValidationError

step_adapter = TypeAdapter(ScenarioStep)


class TestActionTypeValidation:
    def test_wait_step(self):
        step = step_adapter.validate_python({"action": "wait", "duration_s": 30.0})
        assert isinstance(step, WaitStep)
        assert step.duration_s == 30.0

    def test_inject_link_down_step(self):
        step = step_adapter.validate_python(
            {
                "action": "inject_link_down",
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
            }
        )
        assert isinstance(step, InjectLinkDownStep)
        assert step.node_a == "sat-P00S00"
        assert step.node_b == "sat-P00S01"
        assert step.reason == "scenario_inject_down"

    def test_inject_link_up_step(self):
        step = step_adapter.validate_python(
            {
                "action": "inject_link_up",
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
            }
        )
        assert isinstance(step, InjectLinkUpStep)

    def test_inject_satellite_loss_step(self):
        step = step_adapter.validate_python(
            {
                "action": "inject_satellite_loss",
                "node": "sat-P02S03",
            }
        )
        assert isinstance(step, InjectSatelliteLossStep)
        assert step.node == "sat-P02S03"

    def test_restore_satellite_step(self):
        step = step_adapter.validate_python(
            {
                "action": "restore_satellite",
                "node": "sat-P02S03",
            }
        )
        assert isinstance(step, RestoreSatelliteStep)
        assert step.node == "sat-P02S03"

    def test_wait_converge_step(self):
        step = step_adapter.validate_python({"action": "wait_converge"})
        assert isinstance(step, WaitConvergeStep)
        assert step.timeout_s == 30.0

    def test_wait_converge_custom_timeout(self):
        step = step_adapter.validate_python(
            {
                "action": "wait_converge",
                "timeout_s": 60.0,
            }
        )
        assert step.timeout_s == 60.0

    def test_measure_step(self):
        step = step_adapter.validate_python({"action": "measure", "duration_s": 15.0})
        assert isinstance(step, MeasureStep)
        assert step.duration_s == 15.0

    def test_reconfig_step(self):
        step = step_adapter.validate_python(
            {
                "action": "reconfig",
                "target": "plane:3",
                "set_values": {"metric_type": "wide"},
            }
        )
        assert isinstance(step, ReconfigStep)
        assert step.target == "plane:3"
        assert step.set_values["metric_type"] == "wide"


class TestInvalidActionRejected:
    def test_unknown_action_type(self):
        with pytest.raises(ValidationError):
            step_adapter.validate_python({"action": "invalid_action"})

    def test_empty_action(self):
        with pytest.raises(ValidationError):
            step_adapter.validate_python({"action": ""})


class TestMissingRequiredFields:
    def test_inject_link_down_missing_node_a(self):
        with pytest.raises(ValidationError):
            step_adapter.validate_python(
                {
                    "action": "inject_link_down",
                    "node_b": "sat-P00S01",
                }
            )

    def test_inject_link_down_missing_node_b(self):
        with pytest.raises(ValidationError):
            step_adapter.validate_python(
                {
                    "action": "inject_link_down",
                    "node_a": "sat-P00S00",
                }
            )

    def test_inject_satellite_loss_missing_node(self):
        with pytest.raises(ValidationError):
            step_adapter.validate_python({"action": "inject_satellite_loss"})

    def test_wait_missing_duration(self):
        with pytest.raises(ValidationError):
            step_adapter.validate_python({"action": "wait"})

    def test_measure_missing_duration(self):
        with pytest.raises(ValidationError):
            step_adapter.validate_python({"action": "measure"})

    def test_reconfig_missing_target(self):
        with pytest.raises(ValidationError):
            step_adapter.validate_python({"action": "reconfig"})


class TestScenarioConfigRoundTrip:
    def test_round_trip_serialization(self):
        config = ScenarioConfig(
            name="test-scenario",
            description="A test scenario",
            steps=[
                WaitStep(action="wait", duration_s=10.0),
                InjectLinkDownStep(
                    action="inject_link_down",
                    node_a="sat-P00S00",
                    node_b="sat-P01S00",
                ),
                WaitConvergeStep(action="wait_converge", timeout_s=60.0),
                MeasureStep(action="measure", duration_s=30.0),
                InjectLinkUpStep(
                    action="inject_link_up",
                    node_a="sat-P00S00",
                    node_b="sat-P01S00",
                ),
                InjectSatelliteLossStep(
                    action="inject_satellite_loss",
                    node="sat-P02S03",
                ),
                RestoreSatelliteStep(
                    action="restore_satellite",
                    node="sat-P02S03",
                ),
                ReconfigStep(
                    action="reconfig",
                    target="all",
                    set_values={"metric_type": "wide"},
                ),
            ],
        )
        json_str = config.model_dump_json()
        restored = ScenarioConfig.model_validate_json(json_str)
        assert restored.name == config.name
        assert len(restored.steps) == 8
        assert isinstance(restored.steps[0], WaitStep)
        assert isinstance(restored.steps[1], InjectLinkDownStep)
        assert isinstance(restored.steps[2], WaitConvergeStep)
        assert isinstance(restored.steps[3], MeasureStep)
        assert isinstance(restored.steps[4], InjectLinkUpStep)
        assert isinstance(restored.steps[5], InjectSatelliteLossStep)
        assert isinstance(restored.steps[6], RestoreSatelliteStep)
        assert isinstance(restored.steps[7], ReconfigStep)


class TestDiscriminatedUnionDispatch:
    def test_all_7_types_dispatch_correctly(self):
        """Each action type dispatches to the correct model class."""
        cases = [
            ({"action": "wait", "duration_s": 1.0}, WaitStep),
            ({"action": "inject_link_down", "node_a": "a", "node_b": "b"}, InjectLinkDownStep),
            ({"action": "inject_link_up", "node_a": "a", "node_b": "b"}, InjectLinkUpStep),
            ({"action": "inject_satellite_loss", "node": "x"}, InjectSatelliteLossStep),
            ({"action": "restore_satellite", "node": "x"}, RestoreSatelliteStep),
            ({"action": "wait_converge"}, WaitConvergeStep),
            ({"action": "measure", "duration_s": 5.0}, MeasureStep),
            ({"action": "reconfig", "target": "all"}, ReconfigStep),
        ]
        for data, expected_type in cases:
            step = step_adapter.validate_python(data)
            assert isinstance(step, expected_type), (
                f"Expected {expected_type.__name__} for action={data['action']}, "
                f"got {type(step).__name__}"
            )

    def test_scenario_from_yaml_fixture(self):
        """Load the isl-failure scenario YAML and verify step dispatch."""
        from pathlib import Path

        import yaml

        fixture = Path(__file__).parent.parent.parent / "configs/scenarios/isl-failure.yaml"
        if not fixture.exists():
            pytest.skip("isl-failure.yaml not available")

        data = yaml.safe_load(fixture.read_text())
        config = ScenarioConfig.model_validate(data["scenario"])
        assert len(config.steps) == 7

        expected_types = [
            WaitStep,
            InjectLinkDownStep,
            WaitConvergeStep,
            MeasureStep,
            InjectLinkUpStep,
            WaitConvergeStep,
            MeasureStep,
        ]
        for step, expected in zip(config.steps, expected_types):
            assert isinstance(step, expected)
