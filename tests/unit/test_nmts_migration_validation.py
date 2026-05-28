# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
import pytest
from nodalarc.models.ground_station import (
    GroundStationConfig,
    HysteresisParameters,
)
from nodalarc.models.satellite_type import IslTerminalDef, SatelliteTypeConfig
from pydantic import ValidationError


def test_ground_segment_nmts_fields():
    """Verify new NMTS-aligned fields in GroundStationConfig."""
    gs = GroundStationConfig(
        name="test-gs",
        lat_deg=45.0,
        lon_deg=-75.0,
        tenant_id="tenant-a",
        reference_body="luna",
        mobility="maritime",
        service_priority=1,
    )
    assert gs.tenant_id == "tenant-a"
    assert gs.reference_body == "luna"
    assert gs.mobility == "maritime"
    assert gs.service_priority == 1


def test_ground_segment_validation():
    """Verify validation for mobility, service_class, and reference_body."""
    # Invalid mobility
    with pytest.raises(ValidationError, match="mobility must be one of"):
        GroundStationConfig(name="gs", lat_deg=0, lon_deg=0, mobility="warp-speed")

    # Invalid service_priority
    with pytest.raises(ValidationError, match="service_priority must be >= 1"):
        GroundStationConfig(name="gs", lat_deg=0, lon_deg=0, service_priority=0)

    # Invalid reference_body
    with pytest.raises(ValidationError, match="Input should be"):
        GroundStationConfig(name="gs", lat_deg=0, lon_deg=0, reference_body="pluto")

    # Lagrange labels are not surface bodies and are rejected by the same schema
    # boundary as any unsupported reference_body.
    with pytest.raises(ValidationError, match="Input should be"):
        GroundStationConfig(name="gs", lat_deg=0, lon_deg=0, reference_body="eml2")


def test_hysteresis_parameters_validation():
    """Verify range validation for hysteresis parameters."""
    # discount_factor < 1.0
    with pytest.raises(ValidationError, match="discount_factor must be >= 1.0"):
        HysteresisParameters(discount_factor=0.9)

    # mask_fade_range_deg <= 0
    with pytest.raises(ValidationError, match="mask_fade_range_deg must be in"):
        HysteresisParameters(mask_fade_range_deg=0)

    # mask_fade_range_deg > 90
    with pytest.raises(ValidationError, match="mask_fade_range_deg must be in"):
        HysteresisParameters(mask_fade_range_deg=91)


def test_satellite_type_ut_capacity():
    """Verify ut_serving_capacity field and validation."""
    isl = IslTerminalDef(
        type="optical",
        count=2,
        max_range_km=5000,
        bandwidth_mbps=10000,
        max_tracking_rate_deg_s=3.0,
    )

    # Valid
    st = SatelliteTypeConfig(
        name="test-sat", tenant_id="tenant-b", ut_serving_capacity=500, isl_terminals=[isl]
    )
    assert st.ut_serving_capacity == 500
    assert st.tenant_id == "tenant-b"

    # Invalid capacity
    with pytest.raises(ValidationError, match="ut_serving_capacity must be at least 1"):
        SatelliteTypeConfig(name="test-sat", ut_serving_capacity=0, isl_terminals=[isl])
