"""Test constellation configuration models.

Proves discriminated union dispatch, validation rules, and round-trips.
"""

from pathlib import Path

import pytest
import yaml

from nodalarc.models.constellation import (
    ConstellationConfig,
    ExplicitConstellation,
    IslOverride,
    IslTerminal,
    ParametricConstellation,
    TerminalConfig,
    TLEConstellation,
)
from pydantic import TypeAdapter, ValidationError

from ome.constellation_loader import load_constellation
from tests.conftest import CONFIGS_DIR, FIXTURES_DIR

adapter = TypeAdapter(ConstellationConfig)


class TestDiscriminatedUnion:
    def test_parametric_dispatch(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        assert isinstance(config, ParametricConstellation)
        assert config.mode == "parametric"
        assert config.name == "starlink-mini"

    def test_explicit_dispatch(self):
        config = load_constellation(CONFIGS_DIR / "constellations/4-node-test.yaml")
        assert isinstance(config, ExplicitConstellation)
        assert config.mode == "explicit"
        assert len(config.satellites) == 4

    def test_tle_dispatch(self):
        data = {"mode": "tle", "name": "test-tle", "tle_file": "tle.txt",
                "default_terminals": {"isl": [{"type": "optical", "count": 2,
                "max_range_km": 5000, "bandwidth_mbps": 1000, "max_tracking_rate_deg_s": 3.0}]}}
        config = adapter.validate_python(data)
        assert isinstance(config, TLEConstellation)

    def test_unknown_mode_rejected(self):
        data = {"mode": "invalid", "name": "bad"}
        with pytest.raises(ValidationError):
            adapter.validate_python(data)

    def test_round_trip_parametric(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        json_str = config.model_dump_json()
        restored = adapter.validate_json(json_str)
        assert restored == config

    def test_round_trip_explicit(self):
        config = load_constellation(CONFIGS_DIR / "constellations/4-node-test.yaml")
        json_str = config.model_dump_json()
        restored = adapter.validate_json(json_str)
        assert restored == config


class TestParametricConstellation:
    def test_starlink_mini_loads(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        assert config.orbit.altitude_km == 550
        assert config.orbit.inclination_deg == 53
        assert config.orbit.pattern == "walker-delta"
        assert config.planes.count == 6
        assert config.planes.sats_per_plane == 10

    def test_polar_seam_demo_loads(self):
        config = load_constellation(CONFIGS_DIR / "constellations/polar-seam-demo.yaml")
        assert config.orbit.pattern == "walker-star"
        assert config.orbit.inclination_deg == 97.4
        assert config.polar_seam is not None
        assert config.polar_seam.enabled is True
        assert config.polar_seam.latitude_threshold_deg == 75


class TestExplicitConstellation:
    def test_four_node_loads(self):
        config = load_constellation(CONFIGS_DIR / "constellations/4-node-test.yaml")
        assert len(config.satellites) == 4
        planes = {s.plane for s in config.satellites}
        assert planes == {0, 1}
        # Each satellite has orbit
        for sat in config.satellites:
            assert sat.orbit.altitude_km == 550

    def test_terminal_count_from_default(self):
        config = load_constellation(CONFIGS_DIR / "constellations/4-node-test.yaml")
        assert config.default_terminals.isl[0].count == 2


class TestValidationRejections:
    def test_altitude_below_160(self):
        data = yaml.safe_load((FIXTURES_DIR / "invalid/bad-altitude.yaml").read_text())
        with pytest.raises(ValidationError, match="altitude_km must be >= 160"):
            adapter.validate_python(data)

    def test_terminal_count_exceeds_8(self):
        data = {
            "mode": "parametric", "name": "bad",
            "orbit": {"altitude_km": 550, "inclination_deg": 53, "pattern": "walker-delta"},
            "planes": {"count": 2, "raan_spacing_deg": 30, "sats_per_plane": 2, "phase_offset_deg": 6},
            "default_terminals": {"isl": [
                {"type": "optical", "count": 9, "max_range_km": 5000,
                 "bandwidth_mbps": 1000, "max_tracking_rate_deg_s": 3.0}
            ]},
        }
        with pytest.raises(ValidationError, match="terminal count must be 0-8"):
            adapter.validate_python(data)

    def test_negative_tracking_rate(self):
        data = {
            "mode": "parametric", "name": "bad",
            "orbit": {"altitude_km": 550, "inclination_deg": 53, "pattern": "walker-delta"},
            "planes": {"count": 2, "raan_spacing_deg": 30, "sats_per_plane": 2, "phase_offset_deg": 6},
            "default_terminals": {"isl": [
                {"type": "optical", "count": 2, "max_range_km": 5000,
                 "bandwidth_mbps": 1000, "max_tracking_rate_deg_s": -1.0}
            ]},
        }
        with pytest.raises(ValidationError, match="max_tracking_rate_deg_s must be positive"):
            adapter.validate_python(data)


class TestInvalidFixtures:
    def test_missing_terminals_rejected(self):
        """Parametric constellation without default_terminals is rejected."""
        data = yaml.safe_load((FIXTURES_DIR / "invalid/missing-terminals.yaml").read_text())
        with pytest.raises(ValidationError, match="default_terminals"):
            adapter.validate_python(data)

    def test_duplicate_slots_rejected(self):
        """Duplicate plane/slot in explicit mode now raises ValidationError."""
        from pydantic import ValidationError

        data = yaml.safe_load((FIXTURES_DIR / "invalid/duplicate-slots.yaml").read_text())
        with pytest.raises(ValidationError, match="Duplicate plane/slot"):
            adapter.validate_python(data)


class TestIslOverride:
    def test_override_loads(self):
        data = {
            "mode": "explicit", "name": "with-override",
            "default_terminals": {"isl": [
                {"type": "optical", "count": 2, "max_range_km": 5000,
                 "bandwidth_mbps": 1000, "max_tracking_rate_deg_s": 3.0}
            ]},
            "satellites": [
                {"plane": 0, "slot": 0, "orbit": {"altitude_km": 550, "inclination_deg": 53, "raan_deg": 0, "true_anomaly_deg": 0}},
                {"plane": 0, "slot": 1, "orbit": {"altitude_km": 550, "inclination_deg": 53, "raan_deg": 0, "true_anomaly_deg": 180}},
            ],
            "isl_overrides": [
                {"node": "sat-P00S00", "links": [
                    {"terminal": "isl0", "peer": "sat-P00S01"},
                ]},
            ],
        }
        config = adapter.validate_python(data)
        assert len(config.isl_overrides) == 1
        assert config.isl_overrides[0].node == "sat-P00S00"
        assert config.isl_overrides[0].links[0].terminal == "isl0"
        assert config.isl_overrides[0].links[0].peer == "sat-P00S01"
