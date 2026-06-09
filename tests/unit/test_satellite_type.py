"""Tests for satellite type Pydantic model and YAML loading."""

import pytest
from nodalarc.models.satellite_type import (
    GroundTerminalDef,
    IslTerminalDef,
    SatelliteTypeConfig,
)
from pydantic import ValidationError


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
