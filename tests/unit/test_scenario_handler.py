"""Test scenario command parsing — pure protocol validation.

Tests parse_scenario_command: valid commands produce typed models,
malformed JSON is rejected, unknown actions are rejected.
"""

from __future__ import annotations

import json

import pytest
from scheduler.scenario_handler import (
    ClearAllOverrides,
    InjectLinkDown,
    InjectSatelliteLoss,
    ReleaseLinkOverride,
    RestoreSatellite,
    parse_scenario_command,
)


class TestParseValidCommands:
    def test_inject_link_down_default_reason(self):
        cmd = parse_scenario_command(
            json.dumps(
                {"action": "inject_link_down", "node_a": "sat-P00S00", "node_b": "sat-P00S01"}
            ).encode()
        )
        assert isinstance(cmd, InjectLinkDown)
        assert cmd.node_a == "sat-P00S00"
        assert cmd.node_b == "sat-P00S01"
        assert cmd.reason == "scenario_inject_down"

    def test_inject_link_down_custom_reason(self):
        cmd = parse_scenario_command(
            json.dumps(
                {
                    "action": "inject_link_down",
                    "node_a": "sat-P00S00",
                    "node_b": "sat-P00S01",
                    "reason": "link_degradation",
                }
            ).encode()
        )
        assert isinstance(cmd, InjectLinkDown)
        assert cmd.reason == "link_degradation"

    def test_inject_satellite_loss(self):
        cmd = parse_scenario_command(
            json.dumps({"action": "inject_satellite_loss", "node": "sat-P03S07"}).encode()
        )
        assert isinstance(cmd, InjectSatelliteLoss)
        assert cmd.node == "sat-P03S07"

    def test_inject_link_up(self):
        cmd = parse_scenario_command(
            json.dumps(
                {"action": "inject_link_up", "node_a": "gs-ashburn", "node_b": "sat-P00S00"}
            ).encode()
        )
        assert isinstance(cmd, ReleaseLinkOverride)
        assert cmd.node_a == "gs-ashburn"
        assert cmd.node_b == "sat-P00S00"

    def test_restore_satellite(self):
        cmd = parse_scenario_command(
            json.dumps({"action": "restore_satellite", "node": "sat-P03S07"}).encode()
        )
        assert isinstance(cmd, RestoreSatellite)
        assert cmd.node == "sat-P03S07"

    def test_clear_overrides(self):
        cmd = parse_scenario_command(json.dumps({"action": "clear_overrides"}).encode())
        assert isinstance(cmd, ClearAllOverrides)


class TestParseInvalidCommands:
    def test_malformed_json(self):
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_scenario_command(b"not json at all")

    def test_not_an_object(self):
        with pytest.raises(ValueError, match="expected JSON object"):
            parse_scenario_command(json.dumps([1, 2, 3]).encode())

    def test_missing_action(self):
        with pytest.raises(ValueError, match="missing 'action' field"):
            parse_scenario_command(json.dumps({"node_a": "sat-P00S00"}).encode())

    def test_unknown_action(self):
        with pytest.raises(ValueError, match="invalid command"):
            parse_scenario_command(json.dumps({"action": "detonate_satellite"}).encode())

    def test_inject_link_down_missing_node_b(self):
        with pytest.raises(ValueError, match="invalid command"):
            parse_scenario_command(
                json.dumps({"action": "inject_link_down", "node_a": "sat-P00S00"}).encode()
            )

    def test_inject_satellite_loss_missing_node(self):
        with pytest.raises(ValueError, match="invalid command"):
            parse_scenario_command(json.dumps({"action": "inject_satellite_loss"}).encode())

    def test_empty_bytes(self):
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_scenario_command(b"")
