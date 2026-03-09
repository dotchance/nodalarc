"""Tests for Work Stream 4: Session and Scenario Library.

Verifies:
- Each session file loads without validation errors
- Each session's constellation, ground station set, and routing stack references resolve
- Each scenario file is valid YAML and parses correctly
- The manifest JSON is valid and references existing session/scenario files
"""

import json
from pathlib import Path

import pytest
import yaml

from nodalarc.models.session import SessionConfig
from nodalarc.models.scenario import ScenarioConfig
from ome.constellation_loader import load_constellation, load_ground_stations
from tests.conftest import CONFIGS_DIR

SESSIONS_DIR = CONFIGS_DIR / "sessions"
SCENARIOS_DIR = CONFIGS_DIR / "scenarios"
PROJECT_ROOT = CONFIGS_DIR.parent

# New sessions created in WS4
WS4_SESSIONS = [
    "iridium-66-isis-flat",
    "iridium-66-isis-striped",
    "iridium-66-ospf-flat",
    "iridium-small-36-isis-flat",
    "iridium-small-36-isis-striped",
    "iridium-small-36-ospf-flat",
    "oneweb-60-isis-flat",
    "oneweb-60-isis-striped",
    "oneweb-60-ospf-flat",
    "starlink-early-44-isis-flat",
    "starlink-early-44-isis-striped",
    "starlink-early-44-ospf-flat",
    "kuiper-50-isis-flat",
    "kuiper-50-isis-striped",
    "kuiper-50-ospf-flat",
]

# New scenario templates created in WS4
WS4_SCENARIO_TEMPLATES = [
    "compound-failure",
    "stress-test",
]

# Constellation-specific scenario variants
WS4_SCENARIO_VARIANTS = [
    "iridium-66-link-failure",
    "iridium-66-compound-failure",
    "iridium-66-satellite-loss",
    "iridium-small-36-link-failure",
    "iridium-small-36-compound-failure",
    "iridium-small-36-satellite-loss",
    "oneweb-60-link-failure",
    "oneweb-60-compound-failure",
    "oneweb-60-satellite-loss",
    "starlink-early-44-link-failure",
    "starlink-early-44-compound-failure",
    "starlink-early-44-satellite-loss",
    "kuiper-50-link-failure",
    "kuiper-50-compound-failure",
    "kuiper-50-satellite-loss",
]

ALL_WS4_SCENARIOS = WS4_SCENARIO_TEMPLATES + WS4_SCENARIO_VARIANTS


# ---------------------------------------------------------------------------
# Session tests
# ---------------------------------------------------------------------------

class TestSessionFilesLoad:
    """Each WS4 session file parses to a valid SessionConfig."""

    @pytest.mark.parametrize("session_name", WS4_SESSIONS)
    def test_session_loads(self, session_name):
        path = SESSIONS_DIR / f"{session_name}.yaml"
        assert path.exists(), f"Session file missing: {path}"
        data = yaml.safe_load(path.read_text())
        config = SessionConfig.model_validate(data)
        assert config.session.name == session_name

    @pytest.mark.parametrize("session_name", WS4_SESSIONS)
    def test_session_constellation_resolves(self, session_name):
        """The constellation referenced by each session can be loaded."""
        path = SESSIONS_DIR / f"{session_name}.yaml"
        data = yaml.safe_load(path.read_text())
        constellation_path = PROJECT_ROOT / data["constellation"]
        assert constellation_path.exists(), f"Constellation missing: {constellation_path}"
        config = load_constellation(constellation_path)
        assert config.name is not None

    @pytest.mark.parametrize("session_name", WS4_SESSIONS)
    def test_session_ground_stations_resolves(self, session_name):
        """The ground station set referenced by each session exists."""
        path = SESSIONS_DIR / f"{session_name}.yaml"
        data = yaml.safe_load(path.read_text())
        gs_ref = data["ground_stations"]
        gs_path = PROJECT_ROOT / gs_ref
        assert gs_path.exists(), f"Ground station file missing: {gs_path}"
        gs_file = load_ground_stations(gs_path)
        assert gs_file is not None

    @pytest.mark.parametrize("session_name", WS4_SESSIONS)
    def test_session_routing_stack_exists(self, session_name):
        """The routing stack directory referenced by each session exists."""
        path = SESSIONS_DIR / f"{session_name}.yaml"
        data = yaml.safe_load(path.read_text())
        stack_path = PROJECT_ROOT / data["routing"]["stack"]
        assert stack_path.exists(), f"Routing stack missing: {stack_path}"


class TestSessionMetadata:
    """Verify session configuration properties."""

    def test_count(self):
        assert len(WS4_SESSIONS) == 15

    @pytest.mark.parametrize("session_name", [s for s in WS4_SESSIONS if "isis-flat" in s])
    def test_isis_flat_uses_flat_strategy(self, session_name):
        data = yaml.safe_load((SESSIONS_DIR / f"{session_name}.yaml").read_text())
        assert data["routing"]["area_assignment"]["strategy"] == "flat"

    @pytest.mark.parametrize("session_name", [s for s in WS4_SESSIONS if "isis-striped" in s])
    def test_isis_striped_uses_stripe_strategy(self, session_name):
        data = yaml.safe_load((SESSIONS_DIR / f"{session_name}.yaml").read_text())
        assert data["routing"]["area_assignment"]["strategy"] == "stripe"

    @pytest.mark.parametrize("session_name", [s for s in WS4_SESSIONS if "ospf-flat" in s])
    def test_ospf_flat_uses_ospf_stack(self, session_name):
        data = yaml.safe_load((SESSIONS_DIR / f"{session_name}.yaml").read_text())
        assert "ospf" in data["routing"]["stack"]

    @pytest.mark.parametrize("session_name", [
        s for s in WS4_SESSIONS if any(p in s for p in ["iridium", "oneweb"])
    ])
    def test_polar_sessions_use_polar_emphasis(self, session_name):
        data = yaml.safe_load((SESSIONS_DIR / f"{session_name}.yaml").read_text())
        assert "polar-emphasis" in data["ground_stations"]

    @pytest.mark.parametrize("session_name", [
        s for s in WS4_SESSIONS if any(p in s for p in ["starlink-early", "kuiper"])
    ])
    def test_inclined_sessions_use_global(self, session_name):
        data = yaml.safe_load((SESSIONS_DIR / f"{session_name}.yaml").read_text())
        assert "global" in data["ground_stations"]


# ---------------------------------------------------------------------------
# Scenario tests
# ---------------------------------------------------------------------------

class TestScenarioFilesLoad:
    """Each WS4 scenario file parses to a valid ScenarioConfig."""

    @pytest.mark.parametrize("scenario_name", ALL_WS4_SCENARIOS)
    def test_scenario_loads(self, scenario_name):
        path = SCENARIOS_DIR / f"{scenario_name}.yaml"
        assert path.exists(), f"Scenario file missing: {path}"
        data = yaml.safe_load(path.read_text())
        config = ScenarioConfig.model_validate(data["scenario"])
        assert config.name == scenario_name

    @pytest.mark.parametrize("scenario_name", ALL_WS4_SCENARIOS)
    def test_scenario_has_steps(self, scenario_name):
        path = SCENARIOS_DIR / f"{scenario_name}.yaml"
        data = yaml.safe_load(path.read_text())
        config = ScenarioConfig.model_validate(data["scenario"])
        assert len(config.steps) > 0


class TestCompoundFailureScenario:
    def test_has_two_inject_link_down_steps(self):
        data = yaml.safe_load((SCENARIOS_DIR / "compound-failure.yaml").read_text())
        config = ScenarioConfig.model_validate(data["scenario"])
        downs = [s for s in config.steps if hasattr(s, "action") and s.action == "inject_link_down"]
        assert len(downs) == 2

    def test_has_two_inject_link_up_steps(self):
        data = yaml.safe_load((SCENARIOS_DIR / "compound-failure.yaml").read_text())
        config = ScenarioConfig.model_validate(data["scenario"])
        ups = [s for s in config.steps if hasattr(s, "action") and s.action == "inject_link_up"]
        assert len(ups) == 2

    def test_different_links_failed(self):
        data = yaml.safe_load((SCENARIOS_DIR / "compound-failure.yaml").read_text())
        config = ScenarioConfig.model_validate(data["scenario"])
        downs = [s for s in config.steps if s.action == "inject_link_down"]
        pairs = [(s.node_a, s.node_b) for s in downs]
        assert pairs[0] != pairs[1], "Compound failure must target different links"


class TestStressTestScenario:
    def test_has_traffic_flow(self):
        data = yaml.safe_load((SCENARIOS_DIR / "stress-test.yaml").read_text())
        config = ScenarioConfig.model_validate(data["scenario"])
        assert config.traffic_flows is not None
        assert len(config.traffic_flows) >= 1

    def test_chains_multiple_event_types(self):
        data = yaml.safe_load((SCENARIOS_DIR / "stress-test.yaml").read_text())
        config = ScenarioConfig.model_validate(data["scenario"])
        actions = {s.action for s in config.steps}
        # Must have: wait, measure, inject_link_down, inject_satellite_loss, inject_link_up, wait_converge
        assert "wait" in actions
        assert "measure" in actions
        assert "inject_link_down" in actions
        assert "inject_satellite_loss" in actions
        assert "inject_link_up" in actions
        assert "wait_converge" in actions


class TestConstellationSpecificVariants:
    """Constellation-specific scenarios use node IDs valid for their constellation."""

    @pytest.mark.parametrize("scenario_name,planes,sats_per_plane", [
        ("iridium-66-link-failure", 6, 11),
        ("iridium-66-compound-failure", 6, 11),
        ("iridium-66-satellite-loss", 6, 11),
        ("iridium-small-36-link-failure", 6, 6),
        ("iridium-small-36-compound-failure", 6, 6),
        ("iridium-small-36-satellite-loss", 6, 6),
        ("oneweb-60-link-failure", 6, 10),
        ("oneweb-60-compound-failure", 6, 10),
        ("oneweb-60-satellite-loss", 6, 10),
        ("starlink-early-44-link-failure", 4, 11),
        ("starlink-early-44-compound-failure", 4, 11),
        ("starlink-early-44-satellite-loss", 4, 11),
        ("kuiper-50-link-failure", 5, 10),
        ("kuiper-50-compound-failure", 5, 10),
        ("kuiper-50-satellite-loss", 5, 10),
    ])
    def test_node_ids_within_bounds(self, scenario_name, planes, sats_per_plane):
        """All node IDs referenced in steps must be valid for the constellation."""
        path = SCENARIOS_DIR / f"{scenario_name}.yaml"
        data = yaml.safe_load(path.read_text())
        config = ScenarioConfig.model_validate(data["scenario"])

        for step in config.steps:
            node_ids = []
            if hasattr(step, "node_a"):
                node_ids.append(step.node_a)
            if hasattr(step, "node_b"):
                node_ids.append(step.node_b)
            if hasattr(step, "node"):
                node_ids.append(step.node)

            for nid in node_ids:
                if nid.startswith("sat-"):
                    # Parse sat-PXXSYY
                    parts = nid.replace("sat-P", "").split("S")
                    plane = int(parts[0])
                    slot = int(parts[1])
                    assert 0 <= plane < planes, f"{nid}: plane {plane} >= {planes}"
                    assert 0 <= slot < sats_per_plane, f"{nid}: slot {slot} >= {sats_per_plane}"


# ---------------------------------------------------------------------------
# Manifest tests
# ---------------------------------------------------------------------------

class TestManifest:
    @pytest.fixture
    def manifest(self):
        path = SCENARIOS_DIR / "manifest.json"
        assert path.exists(), "manifest.json missing"
        return json.loads(path.read_text())

    def test_manifest_is_valid_json(self, manifest):
        assert isinstance(manifest, dict)

    def test_manifest_has_sessions_and_scenarios(self, manifest):
        assert "sessions" in manifest
        assert "scenarios" in manifest

    def test_manifest_session_count(self, manifest):
        assert len(manifest["sessions"]) == 15

    def test_manifest_scenario_count(self, manifest):
        # 8 templates + 15 constellation-specific = 23
        assert len(manifest["scenarios"]) == 23

    def test_all_ws4_sessions_in_manifest(self, manifest):
        manifest_ids = {s["id"] for s in manifest["sessions"]}
        for session_name in WS4_SESSIONS:
            assert session_name in manifest_ids, f"{session_name} not in manifest"

    def test_all_ws4_scenarios_in_manifest(self, manifest):
        manifest_ids = {s["id"] for s in manifest["scenarios"]}
        for scenario_name in ALL_WS4_SCENARIOS:
            assert scenario_name in manifest_ids, f"{scenario_name} not in manifest"

    def test_manifest_session_files_exist(self, manifest):
        for session in manifest["sessions"]:
            path = SESSIONS_DIR / f"{session['id']}.yaml"
            assert path.exists(), f"Session file missing for manifest entry: {session['id']}"

    def test_manifest_scenario_files_exist(self, manifest):
        for scenario in manifest["scenarios"]:
            path = SCENARIOS_DIR / f"{scenario['id']}.yaml"
            assert path.exists(), f"Scenario file missing for manifest entry: {scenario['id']}"

    def test_manifest_session_fields(self, manifest):
        required_fields = {"id", "name", "description", "constellation", "satellite_count",
                           "routing_stack", "ground_station_set", "tags"}
        for session in manifest["sessions"]:
            missing = required_fields - set(session.keys())
            assert not missing, f"Session {session['id']} missing fields: {missing}"

    def test_manifest_scenario_fields(self, manifest):
        required_fields = {"id", "name", "description", "compatible_sessions",
                           "duration_minutes", "tags"}
        for scenario in manifest["scenarios"]:
            missing = required_fields - set(scenario.keys())
            assert not missing, f"Scenario {scenario['id']} missing fields: {missing}"

    def test_compatible_sessions_reference_existing(self, manifest):
        session_ids = {s["id"] for s in manifest["sessions"]}
        for scenario in manifest["scenarios"]:
            for ref in scenario["compatible_sessions"]:
                if ref != "all":
                    assert ref in session_ids, (
                        f"Scenario {scenario['id']} references non-existent session: {ref}"
                    )
