# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Satellite type configuration models.

A satellite type defines the terminal hardware carried by each satellite
in a constellation. Satellite type files live in configs/satellite-types/
and are referenced by name from constellation definitions.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodalarc.model_validation import NonEmptyReference
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight


class IslTerminalDef(BaseModel):
    """ISL terminal definition within a satellite type."""

    model_config = ConfigDict(allow_inf_nan=False)

    type: NonEmptyReference  # "optical" or "rf"
    band: NonEmptyReference | None = None  # Frequency band for RF terminals
    count: int
    role: NonEmptyReference | None = None  # "intra-plane", "cross-plane", or None (pool)
    max_range_km: float
    bandwidth_mbps: float
    max_tracking_rate_deg_s: float
    field_of_regard_deg: float = 360.0

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in ("optical", "rf"):
            raise ValueError(f"type must be 'optical' or 'rf', got {v!r}")
        return v

    @field_validator("count")
    @classmethod
    def _count_range(cls, v: int) -> int:
        if not 1 <= v <= 8:
            raise ValueError(f"terminal count must be 1-8, got {v}")
        return v

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ("intra-plane", "cross-plane"):
            raise ValueError(f"role must be 'intra-plane', 'cross-plane', or None, got {v!r}")
        return v

    @field_validator("max_range_km")
    @classmethod
    def _positive_range(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_range_km must be positive, got {v}")
        return v

    @field_validator("bandwidth_mbps")
    @classmethod
    def _positive_bandwidth(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"bandwidth_mbps must be positive, got {v}")
        return v

    @field_validator("max_tracking_rate_deg_s")
    @classmethod
    def _positive_tracking_rate(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_tracking_rate_deg_s must be positive, got {v}")
        return v

    @field_validator("field_of_regard_deg")
    @classmethod
    def _for_range(cls, v: float) -> float:
        if not 0 <= v <= 360:
            raise ValueError(f"field_of_regard_deg must be 0-360, got {v}")
        return v


class GroundTerminalDef(BaseModel):
    """Ground terminal definition within a satellite type."""

    model_config = ConfigDict(allow_inf_nan=False)

    type: NonEmptyReference  # "optical" or "rf"
    band: NonEmptyReference | None = None  # Frequency band for RF terminals
    count: int
    bandwidth_mbps: float
    max_range_km: float | None = None
    field_of_regard_deg: float | None = Field(
        default=None,
        description="Full apex angle, in degrees, of the ground-link field-of-regard cone.",
    )
    max_tracking_rate_deg_s: float | None = None
    boresight: SatGroundTerminalBoresight | None = None
    beam_falloff_exponent: float = 2.0
    gateway_beam_quota: int | None = None  # Declared for future per-beam allocation.
    user_terminal_beam_quota: int | None = None  # Declared for future per-beam allocation.

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in ("optical", "rf"):
            raise ValueError(f"type must be 'optical' or 'rf', got {v!r}")
        return v

    @field_validator("count")
    @classmethod
    def _count_range(cls, v: int) -> int:
        if not 1 <= v <= 8:
            raise ValueError(f"terminal count must be 1-8, got {v}")
        return v

    @field_validator("bandwidth_mbps")
    @classmethod
    def _positive_bandwidth(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"bandwidth_mbps must be positive, got {v}")
        return v

    @field_validator("max_range_km")
    @classmethod
    def _positive_ground_range(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"max_range_km must be positive, got {v}")
        return v

    @field_validator("field_of_regard_deg")
    @classmethod
    def _ground_for_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 < v <= 180:
            raise ValueError(f"field_of_regard_deg must be in (0, 180], got {v}")
        return v

    @field_validator("max_tracking_rate_deg_s")
    @classmethod
    def _positive_ground_tracking_rate(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"max_tracking_rate_deg_s must be positive, got {v}")
        return v

    @field_validator("gateway_beam_quota", "user_terminal_beam_quota")
    @classmethod
    def _positive_future_quota(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError(f"future beam quota fields must be >= 1, got {v}")
        return v

    @field_validator("beam_falloff_exponent")
    @classmethod
    def _falloff_range(cls, v: float) -> float:
        if not 1.0 <= v <= 8.0:
            raise ValueError(f"beam_falloff_exponent must be 1.0-8.0, got {v}")
        return v


class SatelliteTypeConfig(BaseModel):
    """Satellite type configuration — terminal hardware for a satellite platform.

    Referenced by name from constellation YAML files. The name is the
    filename without extension (e.g., 'iridium-next.yaml' → 'iridium-next').
    """

    model_config = ConfigDict(allow_inf_nan=False)

    name: NonEmptyReference
    tenant_id: NonEmptyReference = "default"
    description: str | None = None
    ut_serving_capacity: int = 100  # Number of logical UTs this sat can serve
    isl_terminals: list[IslTerminalDef]
    ground_terminals: list[GroundTerminalDef] = []

    @field_validator("ut_serving_capacity")
    @classmethod
    def _ut_capacity_range(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"ut_serving_capacity must be at least 1, got {v}")
        return v

    @model_validator(mode="after")
    def _validate_terminal_counts(self):
        total_isl = sum(t.count for t in self.isl_terminals)
        if total_isl > 8:
            raise ValueError(f"total ISL terminal count must be 0-8, got {total_isl}")
        total_ground = sum(t.count for t in self.ground_terminals)
        if total_ground > 4:
            raise ValueError(f"total ground terminal count must be 0-4, got {total_ground}")
        return self
