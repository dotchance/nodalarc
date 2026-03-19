"""Test ground station configuration models."""

import pytest
import yaml
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    TerrestrialPrefix,
    TerrestrialPrefixTemplate,
)
from pydantic import ValidationError

from tests.conftest import CONFIGS_DIR, FIXTURES_DIR


class TestGroundStationFileLoading:
    def test_custom_example_loads(self):
        data = yaml.safe_load((CONFIGS_DIR / "ground-stations/custom-example.yaml").read_text())
        gs = GroundStationFile.model_validate(data)
        assert len(gs.stations) == 4
        assert gs.default_min_elevation_deg == 25
        assert gs.default_scheduling_policy == "highest-elevation"

    def test_station_names_unique(self):
        data = yaml.safe_load((CONFIGS_DIR / "ground-stations/custom-example.yaml").read_text())
        gs = GroundStationFile.model_validate(data)
        names = [s.name for s in gs.stations]
        assert len(names) == len(set(names))

    def test_round_trip(self):
        data = yaml.safe_load((CONFIGS_DIR / "ground-stations/custom-example.yaml").read_text())
        gs = GroundStationFile.model_validate(data)
        json_str = gs.model_dump_json()
        restored = GroundStationFile.model_validate_json(json_str)
        assert restored == gs


class TestDefaultPrefixTemplate:
    def test_template_present(self):
        data = yaml.safe_load((CONFIGS_DIR / "ground-stations/custom-example.yaml").read_text())
        gs = GroundStationFile.model_validate(data)
        assert gs.default_terrestrial_prefixes is not None
        tpl = gs.default_terrestrial_prefixes
        assert tpl.ipv4_template == "172.16.{gs_index}.0/24"
        assert tpl.ipv6_template == "fd10::{gs_index}:0/112"
        assert tpl.metric == 10

    def test_template_expansion(self):
        tpl = TerrestrialPrefixTemplate()
        # Expand for gs_index=3
        ipv4 = tpl.ipv4_template.format(gs_index=3)
        ipv6 = tpl.ipv6_template.format(gs_index=3)
        assert ipv4 == "172.16.3.0/24"
        assert ipv6 == "fd10::3:0/112"


class TestPolarStationOverrides:
    def test_polar_station(self):
        data = yaml.safe_load((CONFIGS_DIR / "ground-stations/custom-example.yaml").read_text())
        gs = GroundStationFile.model_validate(data)
        polar = next(s for s in gs.stations if s.name == "polar-station")

        # Per-station overrides
        assert polar.min_elevation_deg == 10
        assert polar.scheduling_policy == "longest-pass"

        # RF terminal override
        assert polar.terminals is not None
        assert len(polar.terminals) == 1
        assert polar.terminals[0].type == "rf"
        assert polar.terminals[0].bandwidth_mbps == 500
        assert polar.terminals[0].frequency_band == "Ka"

        # Per-station prefix override
        assert polar.terrestrial_prefixes is not None
        assert len(polar.terrestrial_prefixes) == 2
        assert polar.terrestrial_prefixes[0].prefix == "172.16.100.0/24"
        assert polar.terrestrial_prefixes[0].metric == 50


class TestValidationRejections:
    def test_negative_metric_rejected(self):
        with pytest.raises(ValidationError, match="metric must be non-negative"):
            TerrestrialPrefix(prefix="172.16.0.0/24", metric=-5)

    def test_duplicate_station_names_rejected(self):
        with pytest.raises(ValidationError, match="duplicate station names"):
            GroundStationFile(
                default_terminals=[
                    {"type": "optical", "count": 2, "bandwidth_mbps": 1000, "tracking_capacity": 1}
                ],
                stations=[
                    {"name": "dup", "lat_deg": 0, "lon_deg": 0},
                    {"name": "dup", "lat_deg": 1, "lon_deg": 1},
                ],
            )

    def test_empty_stations_rejected(self):
        with pytest.raises(ValidationError, match="at least one station"):
            GroundStationFile(
                default_terminals=[
                    {"type": "optical", "count": 2, "bandwidth_mbps": 1000, "tracking_capacity": 1}
                ],
                stations=[],
            )

    def test_invalid_latitude(self):
        with pytest.raises(ValidationError, match="lat_deg must be -90 to 90"):
            GroundStationConfig(name="bad", lat_deg=91, lon_deg=0)

    def test_invalid_longitude(self):
        with pytest.raises(ValidationError, match="lon_deg must be -180 to 180"):
            GroundStationConfig(name="bad", lat_deg=0, lon_deg=181)

    def test_invalid_elevation(self):
        with pytest.raises(ValidationError, match="min_elevation_deg must be 0-90"):
            GroundStationConfig(name="bad", lat_deg=0, lon_deg=0, min_elevation_deg=91)


class TestInvalidFixtures:
    def test_bad_prefix_negative_metric(self):
        """Ground station with negative terrestrial prefix metric is rejected."""
        data = yaml.safe_load((FIXTURES_DIR / "invalid/bad-prefix.yaml").read_text())
        with pytest.raises(ValidationError, match="metric must be non-negative"):
            GroundStationFile.model_validate(data)
