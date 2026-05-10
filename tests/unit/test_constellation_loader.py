"""Test constellation loader — expansion of configs to satellite nodes."""

import math

from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.constellation import (
    ConstellationConfig,
    ParametricConstellation,
)
from pydantic import TypeAdapter

from tests.conftest import CONFIGS_DIR, FIXTURES_DIR

adapter = TypeAdapter(ConstellationConfig)


class TestParametricExpansion:
    def test_starlink_early_count(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        sats = expand_constellation(config)
        assert len(sats) == 44  # 4 planes × 11 sats

    def test_starlink_early_planes_and_slots(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        sats = expand_constellation(config)
        planes = {s.plane for s in sats}
        assert planes == {0, 1, 2, 3}
        for p in range(4):
            slots = {s.slot for s in sats if s.plane == p}
            assert slots == set(range(11))

    def test_starlink_early_altitude(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        sats = expand_constellation(config)
        for sat in sats:
            alt = sat.elements.semi_major_axis_km - 6371.0
            assert abs(alt - 550.0) < 0.01

    def test_starlink_early_raan_spacing(self):
        """RAAN increases by 45° per plane."""
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        sats = expand_constellation(config)
        for p in range(4):
            sat = next(s for s in sats if s.plane == p and s.slot == 0)
            expected_raan = math.radians(p * 45.0)
            assert abs(sat.elements.raan_rad - expected_raan) < 1e-10

    def test_starlink_early_raan_spread(self):
        """Walker-delta: RAAN spread = 45° × 4 = 180° < 360°."""
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        assert isinstance(config, ParametricConstellation)
        raan_spread = config.planes.raan_spacing_deg * config.planes.count
        assert raan_spread == 180.0
        assert raan_spread < 360.0

    def test_iridium_66_count(self):
        config = load_constellation(CONFIGS_DIR / "constellations/iridium-66.yaml")
        sats = expand_constellation(config)
        assert len(sats) == 66  # 6 planes × 11 sats

    def test_iridium_66_raan_spread(self):
        """Walker-star: RAAN spread = 31.6° × 6 = 189.6° < 360°."""
        config = load_constellation(CONFIGS_DIR / "constellations/iridium-66.yaml")
        assert isinstance(config, ParametricConstellation)
        raan_spread = config.planes.raan_spacing_deg * config.planes.count
        assert abs(raan_spread - 189.6) < 0.1
        assert raan_spread < 360.0

    def test_terminal_counts(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")
        sats = expand_constellation(config)
        for sat in sats:
            assert sat.isl_terminal_count == 4
            assert sat.ground_terminal_count == 1


class TestExplicitExpansion:
    def test_custom_example_count(self):
        config = load_constellation(CONFIGS_DIR / "constellations/custom-example.yaml")
        sats = expand_constellation(config)
        assert len(sats) == 4

    def test_custom_example_planes(self):
        config = load_constellation(CONFIGS_DIR / "constellations/custom-example.yaml")
        sats = expand_constellation(config)
        planes = {s.plane for s in sats}
        assert planes == {0, 1}

    def test_custom_example_orbital_elements(self):
        config = load_constellation(CONFIGS_DIR / "constellations/custom-example.yaml")
        sats = expand_constellation(config)
        p0s0 = next(s for s in sats if s.plane == 0 and s.slot == 0)
        assert abs(p0s0.elements.semi_major_axis_km - (6371.0 + 550.0)) < 0.01
        assert abs(p0s0.elements.inclination_rad - math.radians(53.0)) < 1e-10
        assert abs(p0s0.elements.raan_rad) < 1e-10
        assert abs(p0s0.elements.true_anomaly_rad) < 1e-10

    def test_custom_example_terminal_counts(self):
        config = load_constellation(CONFIGS_DIR / "constellations/custom-example.yaml")
        sats = expand_constellation(config)
        for sat in sats:
            assert sat.isl_terminal_count == 2
            assert sat.ground_terminal_count == 1


class TestTLEExpansion:
    def test_tle_expands_records_with_original_lines(self):
        data = {
            "mode": "tle",
            "name": "test-tle",
            "tle_file": str(FIXTURES_DIR / "tles/sample.tle"),
            "default_terminals": {
                "isl": [
                    {
                        "type": "optical",
                        "count": 2,
                        "max_range_km": 5000,
                        "bandwidth_mbps": 1000,
                        "max_tracking_rate_deg_s": 3.0,
                    }
                ],
                "ground": [{"type": "rf", "count": 1, "bandwidth_mbps": 1000}],
            },
            "filter": {"norad_ids": [25544, 23455]},
        }
        config = adapter.validate_python(data)
        sats = expand_constellation(config)

        assert len(sats) == 2
        assert [sat.norad_id for sat in sats] == [25544, 23455]
        assert [sat.slot for sat in sats] == [0, 1]
        assert all(sat.plane == 0 for sat in sats)
        assert all(sat.tle_line_1 and sat.tle_line_1.startswith("1 ") for sat in sats)
        assert all(sat.tle_line_2 and sat.tle_line_2.startswith("2 ") for sat in sats)
        assert all(sat.isl_terminal_count == 2 for sat in sats)
        assert all(sat.ground_terminal_count == 1 for sat in sats)
        assert all(sat.elements.semi_major_axis_km > 6500 for sat in sats)


class TestGroundStationLoading:
    def test_load_global_set(self):
        gs = load_ground_stations(CONFIGS_DIR / "ground-stations/sets/global.yaml")
        assert len(gs.stations) == 7
