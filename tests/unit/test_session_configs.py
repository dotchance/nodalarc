"""Test session config YAML files validate against SessionConfig.

Loads session configs and verifies they parse through the Pydantic model.
Only validates configs that reference existing routing stacks and constellations.
"""

from pathlib import Path

import pytest
import yaml

from nodalarc.models.session import SessionConfig

SESSIONS_DIR = Path(__file__).parent.parent.parent / "configs" / "sessions"
PROJECT_ROOT = Path(__file__).parent.parent.parent


# Session configs that should fully validate
VALIDATABLE_SESSIONS = [
    "iridium-small-36-isis-flat.yaml",
    "starlink-early-44-isis-flat.yaml",
    "starlink-early-44-isis-striped.yaml",
    "starlink-early-44-ospf-flat.yaml",
    "kuiper-50-isis-flat.yaml",
    "kuiper-50-isis-striped.yaml",
    "kuiper-50-ospf-flat.yaml",
]


@pytest.mark.parametrize("filename", VALIDATABLE_SESSIONS)
class TestSessionConfigValidation:
    def test_loads_and_validates(self, filename: str):
        """YAML loads and validates through SessionConfig."""
        path = SESSIONS_DIR / filename
        if not path.exists():
            pytest.skip(f"{filename} not found")

        raw = yaml.safe_load(path.read_text())
        config = SessionConfig.model_validate(raw)
        assert config.session.name, "Session must have a name"
        assert config.constellation, "Session must reference a constellation"
        assert config.routing.stack, "Session must reference a routing stack"

    def test_referenced_files_exist(self, filename: str):
        """Constellation and GS config files referenced by session exist."""
        path = SESSIONS_DIR / filename
        if not path.exists():
            pytest.skip(f"{filename} not found")

        raw = yaml.safe_load(path.read_text())
        config = SessionConfig.model_validate(raw)

        constellation_path = PROJECT_ROOT / config.constellation
        assert constellation_path.exists(), (
            f"Constellation file not found: {config.constellation}"
        )

        gs_path = PROJECT_ROOT / config.ground_stations
        assert gs_path.exists(), (
            f"Ground station file not found: {config.ground_stations}"
        )

    def test_round_trip_serialization(self, filename: str):
        """Config survives JSON round-trip."""
        path = SESSIONS_DIR / filename
        if not path.exists():
            pytest.skip(f"{filename} not found")

        raw = yaml.safe_load(path.read_text())
        config = SessionConfig.model_validate(raw)
        json_str = config.model_dump_json()
        restored = SessionConfig.model_validate_json(json_str)
        assert restored.session.name == config.session.name


class TestNewSessionFiles:
    """Verify the component-model session config files exist."""

    NEW_FILES = [
        "iridium-small-36-isis-flat.yaml",
        "starlink-early-44-isis-flat.yaml",
        "starlink-early-44-isis-striped.yaml",
        "starlink-early-44-ospf-flat.yaml",
        "kuiper-50-isis-flat.yaml",
        "kuiper-50-isis-striped.yaml",
        "kuiper-50-ospf-flat.yaml",
    ]

    @pytest.mark.parametrize("filename", NEW_FILES)
    def test_file_exists(self, filename: str):
        path = SESSIONS_DIR / filename
        assert path.exists(), f"Missing session config: {filename}"


class TestISISStripedConfig:
    """Specific checks for the starlink-early-44-isis-striped reference session."""

    def test_uses_isis_sr_stack(self):
        raw = yaml.safe_load((SESSIONS_DIR / "starlink-early-44-isis-striped.yaml").read_text())
        config = SessionConfig.model_validate(raw)
        assert "isis" in config.routing.stack.lower()

    def test_uses_stripe_area_assignment(self):
        raw = yaml.safe_load((SESSIONS_DIR / "starlink-early-44-isis-striped.yaml").read_text())
        config = SessionConfig.model_validate(raw)
        assert config.routing.area_assignment.strategy == "stripe"
        assert config.routing.area_assignment.planes_per_stripe == 2

    def test_uses_discrete_event_mode(self):
        raw = yaml.safe_load((SESSIONS_DIR / "starlink-early-44-isis-striped.yaml").read_text())
        config = SessionConfig.model_validate(raw)
        assert config.time.mode == "discrete-event"


class TestOSPFFlatConfig:
    """Specific checks for the starlink-early-44-ospf-flat session."""

    def test_uses_ospf_stack(self):
        raw = yaml.safe_load((SESSIONS_DIR / "starlink-early-44-ospf-flat.yaml").read_text())
        config = SessionConfig.model_validate(raw)
        assert "ospf" in config.routing.stack.lower()

    def test_uses_flat_area_assignment(self):
        raw = yaml.safe_load((SESSIONS_DIR / "starlink-early-44-ospf-flat.yaml").read_text())
        config = SessionConfig.model_validate(raw)
        assert config.routing.area_assignment.strategy == "flat"
