"""Test constellation loader — expansion of configs to satellite nodes."""

import math

import pytest
import yaml
from pydantic import TypeAdapter

from ome.constellation_loader import (
    expand_constellation,
    expand_explicit,
    expand_parametric,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.constellation import (
    ConstellationConfig,
    ExplicitConstellation,
    ParametricConstellation,
    TLEConstellation,
)
from tests.conftest import CONFIGS_DIR

adapter = TypeAdapter(ConstellationConfig)


class TestParametricExpansion:
    def test_starlink_mini_count(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        sats = expand_constellation(config)
        assert len(sats) == 60  # 6 planes × 10 sats

    def test_starlink_mini_planes_and_slots(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        sats = expand_constellation(config)
        planes = {s.plane for s in sats}
        assert planes == {0, 1, 2, 3, 4, 5}
        for p in range(6):
            slots = {s.slot for s in sats if s.plane == p}
            assert slots == set(range(10))

    def test_starlink_mini_altitude(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        sats = expand_constellation(config)
        for sat in sats:
            alt = sat.elements.semi_major_axis_km - 6371.0
            assert abs(alt - 550.0) < 0.01

    def test_starlink_mini_raan_spacing(self):
        """RAAN increases by 30° per plane."""
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        sats = expand_constellation(config)
        for p in range(6):
            sat = next(s for s in sats if s.plane == p and s.slot == 0)
            expected_raan = math.radians(p * 30.0)
            assert abs(sat.elements.raan_rad - expected_raan) < 1e-10

    def test_starlink_mini_raan_spread(self):
        """Walker-delta: RAAN spread = 30° × 6 = 180° < 360°."""
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        assert isinstance(config, ParametricConstellation)
        raan_spread = config.planes.raan_spacing_deg * config.planes.count
        assert raan_spread == 180.0
        assert raan_spread < 360.0

    def test_polar_seam_demo_count(self):
        config = load_constellation(CONFIGS_DIR / "constellations/polar-seam-demo.yaml")
        sats = expand_constellation(config)
        assert len(sats) == 32  # 4 planes × 8 sats

    def test_polar_seam_raan_spread(self):
        """Walker-star: RAAN spread = 90° × 4 = 360°."""
        config = load_constellation(CONFIGS_DIR / "constellations/polar-seam-demo.yaml")
        assert isinstance(config, ParametricConstellation)
        raan_spread = config.planes.raan_spacing_deg * config.planes.count
        assert raan_spread == 360.0

    def test_terminal_counts(self):
        config = load_constellation(CONFIGS_DIR / "constellations/starlink-mini.yaml")
        sats = expand_constellation(config)
        for sat in sats:
            assert sat.isl_terminal_count == 4
            assert sat.ground_terminal_count == 1


class TestExplicitExpansion:
    def test_four_node_count(self):
        config = load_constellation(CONFIGS_DIR / "constellations/4-node-test.yaml")
        sats = expand_constellation(config)
        assert len(sats) == 4

    def test_four_node_planes(self):
        config = load_constellation(CONFIGS_DIR / "constellations/4-node-test.yaml")
        sats = expand_constellation(config)
        planes = {s.plane for s in sats}
        assert planes == {0, 1}

    def test_four_node_orbital_elements(self):
        config = load_constellation(CONFIGS_DIR / "constellations/4-node-test.yaml")
        sats = expand_constellation(config)
        p0s0 = next(s for s in sats if s.plane == 0 and s.slot == 0)
        assert abs(p0s0.elements.semi_major_axis_km - (6371.0 + 550.0)) < 0.01
        assert abs(p0s0.elements.inclination_rad - math.radians(53.0)) < 1e-10
        assert abs(p0s0.elements.raan_rad) < 1e-10
        assert abs(p0s0.elements.true_anomaly_rad) < 1e-10

    def test_four_node_terminal_counts(self):
        config = load_constellation(CONFIGS_DIR / "constellations/4-node-test.yaml")
        sats = expand_constellation(config)
        for sat in sats:
            assert sat.isl_terminal_count == 2
            assert sat.ground_terminal_count == 1


class TestTLEStub:
    def test_tle_raises_not_implemented(self):
        data = {
            "mode": "tle", "name": "test-tle", "tle_file": "tle.txt",
            "default_terminals": {"isl": [{
                "type": "optical", "count": 2,
                "max_range_km": 5000, "bandwidth_mbps": 1000,
                "max_tracking_rate_deg_s": 3.0,
            }]},
        }
        config = adapter.validate_python(data)
        with pytest.raises(NotImplementedError):
            expand_constellation(config)


class TestGroundStationLoading:
    def test_load_global_default(self):
        gs = load_ground_stations(CONFIGS_DIR / "ground-stations/global-default.yaml")
        assert len(gs.stations) == 7
        assert gs.default_min_elevation_deg == 25
