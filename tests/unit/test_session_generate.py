"""Tests for session_generator — generate YAML, parse back, assert fields match."""

import pytest

import yaml

from nodalarc.models.session import SessionConfig
from nodalarc.session_generator import (
    ConstellationPreset,
    generate_session_yaml,
    load_constellation_presets,
)


class TestLoadPresets:
    def test_loads_all_presets(self):
        presets = load_constellation_presets()
        assert len(presets) >= 5
        assert "iridium-66" in presets
        assert "starlink-early-44" in presets

    def test_preset_fields(self):
        presets = load_constellation_presets()
        p = presets["iridium-66"]
        assert p.satellite_count == 66
        assert "iridium-66.yaml" in p.constellation
        assert p.ground_stations.endswith(".yaml")


class TestGenerateSessionYaml:
    """Generate YAML for every valid preset x protocol x extension combo."""

    @pytest.mark.parametrize("constellation", [
        "iridium-66", "iridium-small-36", "starlink-early-44", "oneweb-60", "kuiper-50",
    ])
    @pytest.mark.parametrize("protocol,extensions", [
        ("ospf", []),
        ("ospf", ["te"]),
        ("ospf", ["te", "mpls"]),
        ("isis", []),
        ("isis", ["sr"]),
        ("isis", ["te"]),
        ("isis", ["te", "mpls"]),
        ("ospf", ["sr"]),
        ("nodalpath", []),
    ])
    def test_generate_and_roundtrip(self, constellation, protocol, extensions):
        yaml_str, warnings = generate_session_yaml(
            constellation=constellation,
            protocol=protocol,
            extensions=extensions,
        )
        # Must parse back to valid SessionConfig
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)

        assert session.session.name == f"{constellation}-{protocol}-{'-'.join(extensions) or 'plain'}"
        assert session.routing.protocol == protocol
        assert session.routing.extensions == extensions
        assert session.routing.stack is None  # Wizard sessions use protocol, not stack

    @pytest.mark.parametrize("area_strategy", ["flat", "stripe", "per-plane"])
    def test_area_strategies(self, area_strategy):
        yaml_str, warnings = generate_session_yaml(
            constellation="iridium-small-36",
            protocol="ospf",
            extensions=[],
            area_strategy=area_strategy,
        )
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.area_assignment is not None
        assert session.routing.area_assignment.strategy == area_strategy


class TestGenerateInvalid:
    def test_unknown_constellation(self):
        with pytest.raises(ValueError, match="Unknown constellation"):
            generate_session_yaml("nonexistent", "ospf", [])

    def test_invalid_combo(self):
        with pytest.raises(ValueError, match="does not accept extensions"):
            generate_session_yaml("iridium-66", "nodalpath", ["sr"])


class TestNodalPathNoAreaAssignment:
    def test_no_area_assignment(self):
        yaml_str, _ = generate_session_yaml("iridium-66", "nodalpath", [])
        raw = yaml.safe_load(yaml_str)
        session = SessionConfig.model_validate(raw)
        assert session.routing.area_assignment is None
