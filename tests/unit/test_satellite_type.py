"""Tests for satellite type Pydantic model and YAML loading."""

import pytest
from nodalarc.constellation_loader import load_satellite_type, set_satellite_type_dir
from nodalarc.models.satellite_type import (
    GroundTerminalDef,
    IslTerminalDef,
    SatelliteTypeConfig,
)
from pydantic import ValidationError

from tests.conftest import CONFIGS_DIR

SAT_TYPE_DIR = CONFIGS_DIR / "satellite-types"


@pytest.fixture(autouse=True)
def _set_sat_type_dir():
    """Point satellite type loader at the real configs directory."""
    set_satellite_type_dir(SAT_TYPE_DIR)
    yield
    # Reset cache between tests
    load_satellite_type.cache_clear()


class TestIslTerminalDef:
    def test_valid_optical(self):
        t = IslTerminalDef(
            type="optical",
            count=4,
            max_range_km=5000,
            bandwidth_mbps=100,
            max_tracking_rate_deg_s=3.0,
        )
        assert t.field_of_regard_deg == 360.0  # default

    def test_valid_rf_with_band(self):
        t = IslTerminalDef(
            type="rf",
            band="Ka",
            count=2,
            role="intra-plane",
            max_range_km=4400,
            bandwidth_mbps=10,
            max_tracking_rate_deg_s=4.0,
            field_of_regard_deg=120,
        )
        assert t.band == "Ka"
        assert t.role == "intra-plane"

    def test_invalid_type(self):
        with pytest.raises(ValidationError, match="type must be"):
            IslTerminalDef(
                type="microwave",
                count=2,
                max_range_km=5000,
                bandwidth_mbps=100,
                max_tracking_rate_deg_s=3.0,
            )

    def test_count_too_high(self):
        with pytest.raises(ValidationError, match="terminal count must be 1-8"):
            IslTerminalDef(
                type="optical",
                count=9,
                max_range_km=5000,
                bandwidth_mbps=100,
                max_tracking_rate_deg_s=3.0,
            )

    def test_count_zero(self):
        with pytest.raises(ValidationError, match="terminal count must be 1-8"):
            IslTerminalDef(
                type="optical",
                count=0,
                max_range_km=5000,
                bandwidth_mbps=100,
                max_tracking_rate_deg_s=3.0,
            )

    def test_negative_range(self):
        with pytest.raises(ValidationError, match="max_range_km must be positive"):
            IslTerminalDef(
                type="optical",
                count=2,
                max_range_km=-100,
                bandwidth_mbps=100,
                max_tracking_rate_deg_s=3.0,
            )

    def test_negative_bandwidth(self):
        with pytest.raises(ValidationError, match="bandwidth_mbps must be positive"):
            IslTerminalDef(
                type="optical",
                count=2,
                max_range_km=5000,
                bandwidth_mbps=-10,
                max_tracking_rate_deg_s=3.0,
            )

    def test_negative_tracking_rate(self):
        with pytest.raises(ValidationError, match="max_tracking_rate_deg_s must be positive"):
            IslTerminalDef(
                type="optical",
                count=2,
                max_range_km=5000,
                bandwidth_mbps=100,
                max_tracking_rate_deg_s=-1.0,
            )

    def test_invalid_role(self):
        with pytest.raises(ValidationError, match="role must be"):
            IslTerminalDef(
                type="optical",
                count=2,
                role="diagonal",
                max_range_km=5000,
                bandwidth_mbps=100,
                max_tracking_rate_deg_s=3.0,
            )

    def test_field_of_regard_over_360(self):
        with pytest.raises(ValidationError, match="field_of_regard_deg must be 0-360"):
            IslTerminalDef(
                type="optical",
                count=2,
                max_range_km=5000,
                bandwidth_mbps=100,
                max_tracking_rate_deg_s=3.0,
                field_of_regard_deg=400,
            )

    def test_field_of_regard_negative(self):
        with pytest.raises(ValidationError, match="field_of_regard_deg must be 0-360"):
            IslTerminalDef(
                type="optical",
                count=2,
                max_range_km=5000,
                bandwidth_mbps=100,
                max_tracking_rate_deg_s=3.0,
                field_of_regard_deg=-10,
            )


class TestSatelliteTypeConfig:
    def test_total_isl_count_exceeds_8(self):
        with pytest.raises(ValidationError, match="total ISL terminal count must be 0-8"):
            SatelliteTypeConfig(
                name="too-many",
                isl_terminals=[
                    IslTerminalDef(
                        type="optical",
                        count=5,
                        max_range_km=5000,
                        bandwidth_mbps=100,
                        max_tracking_rate_deg_s=3.0,
                    ),
                    IslTerminalDef(
                        type="optical",
                        count=5,
                        max_range_km=5000,
                        bandwidth_mbps=100,
                        max_tracking_rate_deg_s=3.0,
                    ),
                ],
            )

    def test_total_ground_count_exceeds_4(self):
        with pytest.raises(ValidationError, match="total ground terminal count must be 0-4"):
            SatelliteTypeConfig(
                name="too-many-ground",
                isl_terminals=[
                    IslTerminalDef(
                        type="optical",
                        count=2,
                        max_range_km=5000,
                        bandwidth_mbps=100,
                        max_tracking_rate_deg_s=3.0,
                    ),
                ],
                ground_terminals=[
                    GroundTerminalDef(type="optical", count=3, bandwidth_mbps=1000),
                    GroundTerminalDef(type="optical", count=2, bandwidth_mbps=1000),
                ],
            )

    def test_empty_isl_is_valid(self):
        """Zero ISL terminals is valid (total count 0 is within 0-8)."""
        cfg = SatelliteTypeConfig(
            name="no-isl",
            isl_terminals=[],
        )
        assert sum(t.count for t in cfg.isl_terminals) == 0

    def test_empty_ground_is_valid(self):
        cfg = SatelliteTypeConfig(
            name="no-ground",
            isl_terminals=[
                IslTerminalDef(
                    type="optical",
                    count=2,
                    max_range_km=5000,
                    bandwidth_mbps=100,
                    max_tracking_rate_deg_s=3.0,
                ),
            ],
        )
        assert len(cfg.ground_terminals) == 0


class TestSatelliteTypeYAMLRoundTrip:
    """Load each satellite type YAML file and verify it round-trips."""

    @pytest.mark.parametrize(
        "name",
        [
            "iridium-next",
            "starlink-v2",
            "oneweb-gen2",
            "kuiper-v1",
            "generic-4isl",
        ],
    )
    def test_load_and_validate(self, name: str):
        sat_type = load_satellite_type(name)
        assert sat_type.name == name

    @pytest.mark.parametrize(
        "name",
        [
            "iridium-next",
            "starlink-v2",
            "oneweb-gen2",
            "kuiper-v1",
            "generic-4isl",
        ],
    )
    def test_round_trip_serialization(self, name: str):
        sat_type = load_satellite_type(name)
        # Serialize to dict and back
        data = sat_type.model_dump()
        restored = SatelliteTypeConfig.model_validate(data)
        assert restored == sat_type


class TestIridiumNext:
    def test_terminal_count(self):
        sat_type = load_satellite_type("iridium-next")
        total_isl = sum(t.count for t in sat_type.isl_terminals)
        assert total_isl == 4

    def test_two_intra_two_cross(self):
        sat_type = load_satellite_type("iridium-next")
        intra = [t for t in sat_type.isl_terminals if t.role == "intra-plane"]
        cross = [t for t in sat_type.isl_terminals if t.role == "cross-plane"]
        assert len(intra) == 1  # 1 entry with count=2
        assert intra[0].count == 2
        assert len(cross) == 1  # 1 entry with count=2
        assert cross[0].count == 2

    def test_tracking_rates(self):
        sat_type = load_satellite_type("iridium-next")
        intra = next(t for t in sat_type.isl_terminals if t.role == "intra-plane")
        cross = next(t for t in sat_type.isl_terminals if t.role == "cross-plane")
        assert intra.max_tracking_rate_deg_s == 4.0
        assert cross.max_tracking_rate_deg_s == 2.5

    def test_field_of_regard(self):
        sat_type = load_satellite_type("iridium-next")
        for t in sat_type.isl_terminals:
            assert t.field_of_regard_deg == 120

    def test_ground_terminal(self):
        sat_type = load_satellite_type("iridium-next")
        assert len(sat_type.ground_terminals) == 1
        assert sat_type.ground_terminals[0].bandwidth_mbps == 200

    def test_rf_type(self):
        sat_type = load_satellite_type("iridium-next")
        for t in sat_type.isl_terminals:
            assert t.type == "rf"
            assert t.band == "Ka"


class TestGeneric4Isl:
    def test_permissive_values(self):
        sat_type = load_satellite_type("generic-4isl")
        assert len(sat_type.isl_terminals) == 1
        t = sat_type.isl_terminals[0]
        assert t.count == 4
        assert t.max_range_km == 6000
        assert t.max_tracking_rate_deg_s == 5.0
        assert t.field_of_regard_deg == 160
        assert t.role is None  # pooled

    def test_optical_type(self):
        sat_type = load_satellite_type("generic-4isl")
        assert sat_type.isl_terminals[0].type == "optical"


class TestBeamFalloffExponent:
    def test_beam_falloff_default(self):
        t = GroundTerminalDef(type="rf", count=1, bandwidth_mbps=100)
        assert t.beam_falloff_exponent == 2.0

    def test_beam_falloff_explicit(self):
        t = GroundTerminalDef(type="rf", count=1, bandwidth_mbps=100, beam_falloff_exponent=3.5)
        assert t.beam_falloff_exponent == 3.5

    def test_beam_falloff_below_minimum_rejected(self):
        with pytest.raises(ValidationError, match="beam_falloff_exponent must be 1.0-8.0"):
            GroundTerminalDef(type="rf", count=1, bandwidth_mbps=100, beam_falloff_exponent=0.5)

    def test_beam_falloff_above_maximum_rejected(self):
        with pytest.raises(ValidationError, match="beam_falloff_exponent must be 1.0-8.0"):
            GroundTerminalDef(type="rf", count=1, bandwidth_mbps=100, beam_falloff_exponent=9.0)


class TestSatelliteTypeLoaderErrors:
    def test_nonexistent_type(self):
        with pytest.raises(FileNotFoundError, match="Satellite type file not found"):
            load_satellite_type("does-not-exist")
