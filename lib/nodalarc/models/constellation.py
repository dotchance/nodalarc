# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Constellation configuration models.

Supports three modes via discriminated union on the `mode` field:
- parametric: Walker-delta/star expansion from orbital parameters
- explicit: Per-satellite orbital elements
- tle: TLE file with optional filtering
"""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)

from nodalarc.models.terminal_physics import SatGroundTerminalBoresight


class OrbitalElements(BaseModel):
    """Orbital elements for explicit-mode satellites."""

    model_config = ConfigDict(allow_inf_nan=False)

    altitude_km: float
    inclination_deg: float = Field(ge=0, le=180)
    # raan/true-anomaly are finite (allow_inf_nan=False); not range-bounded because
    # callers may pass non-canonical or computed (pre-mod-360) angles.
    raan_deg: float
    true_anomaly_deg: float

    @field_validator("altitude_km")
    @classmethod
    def _altitude_min(cls, v: float) -> float:
        if v < 160:
            raise ValueError(f"altitude_km must be >= 160, got {v}")
        return v


class IslTerminal(BaseModel):
    """ISL terminal specification."""

    model_config = ConfigDict(allow_inf_nan=False)

    type: str  # "optical" or "rf"
    count: int
    role: str | None = None  # "intra-plane", "cross-plane", or None for pooled terminals
    max_range_km: float = Field(gt=0)
    bandwidth_mbps: float = Field(gt=0)
    max_tracking_rate_deg_s: float
    field_of_regard_deg: float = Field(default=360.0, ge=0, le=360)

    @field_validator("count")
    @classmethod
    def _count_range(cls, v: int) -> int:
        if not 1 <= v <= 8:
            raise ValueError(f"terminal count must be 1-8, got {v}")
        return v

    @field_validator("max_tracking_rate_deg_s")
    @classmethod
    def _positive_tracking_rate(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_tracking_rate_deg_s must be positive, got {v}")
        return v

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ("intra-plane", "cross-plane"):
            raise ValueError(f"role must be 'intra-plane', 'cross-plane', or None, got {v!r}")
        return v


class GroundTerminal(BaseModel):
    """Ground-link terminal specification for satellites."""

    model_config = ConfigDict(allow_inf_nan=False)

    type: str  # "optical" or "rf"
    count: int
    bandwidth_mbps: float
    max_range_km: float | None = None
    field_of_regard_deg: float | None = Field(
        default=None,
        description="Full apex angle, in degrees, of the ground-link field-of-regard cone.",
    )
    max_tracking_rate_deg_s: float | None = None
    boresight: SatGroundTerminalBoresight | None = None
    gateway_beam_quota: int | None = None  # Accepted in Phase 3, not enforced.
    user_terminal_beam_quota: int | None = None  # Accepted in Phase 3, not enforced.

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
    def _positive_range(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"max_range_km must be positive, got {v}")
        return v

    @field_validator("field_of_regard_deg")
    @classmethod
    def _for_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 < v <= 180:
            raise ValueError(f"field_of_regard_deg must be in (0, 180], got {v}")
        return v

    @field_validator("max_tracking_rate_deg_s")
    @classmethod
    def _positive_tracking_rate(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"max_tracking_rate_deg_s must be positive, got {v}")
        return v

    @field_validator("gateway_beam_quota", "user_terminal_beam_quota")
    @classmethod
    def _positive_future_quota(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError(f"future beam quota fields must be >= 1, got {v}")
        return v


class TerminalConfig(BaseModel):
    """Terminal configuration for a satellite."""

    isl: list[IslTerminal]
    ground: list[GroundTerminal] = []


class OrbitParams(BaseModel):
    """Orbital parameters for parametric mode."""

    model_config = ConfigDict(allow_inf_nan=False)

    altitude_km: float
    inclination_deg: float
    pattern: str  # "walker-delta" or "walker-star"

    @field_validator("altitude_km")
    @classmethod
    def _altitude_min(cls, v: float) -> float:
        if v < 160:
            raise ValueError(f"altitude_km must be >= 160, got {v}")
        return v

    @field_validator("inclination_deg")
    @classmethod
    def _inclination_range(cls, v: float) -> float:
        if not 0 <= v <= 180:
            raise ValueError(f"inclination_deg must be 0-180, got {v}")
        return v


class PlaneParams(BaseModel):
    """Orbital plane parameters for parametric mode."""

    model_config = ConfigDict(allow_inf_nan=False)

    count: int = Field(gt=0)
    raan_spacing_deg: float = Field(ge=0)
    sats_per_plane: int = Field(gt=0)
    phase_offset_deg: float


class PolarSeamConfig(BaseModel):
    """Polar seam configuration — hard latitude cutoff for cross-plane ISLs."""

    model_config = ConfigDict(allow_inf_nan=False)

    enabled: bool = False
    latitude_threshold_deg: float = 70.0

    @field_validator("latitude_threshold_deg")
    @classmethod
    def _threshold_range(cls, v: float) -> float:
        if not 0 <= v <= 90:
            raise ValueError(f"latitude_threshold_deg must be 0-90, got {v}")
        return v


class IslLink(BaseModel):
    """Single ISL terminal-to-peer mapping within an override."""

    terminal: str  # e.g. "isl0"
    peer: str  # e.g. "sat-P01S00"


class IslOverride(BaseModel):
    """ISL override for a specific node — manually assigns terminals to peers."""

    node: str
    links: list[IslLink]


class PlaneOverride(BaseModel):
    """Override terminal config for specific orbital planes.

    Can reference a satellite type by name or provide inline terminals.
    """

    planes: list[int]
    satellite_type: str | None = None  # Reference to satellite type file
    terminals: TerminalConfig | None = None  # Deprecated: inline terminals

    @model_validator(mode="after")
    def _require_one_source(self):
        if self.satellite_type is None and self.terminals is None:
            raise ValueError("PlaneOverride must specify satellite_type or terminals")
        return self


class SatelliteConfig(BaseModel):
    """Per-satellite config for explicit mode.

    Node ID is derived from plane/slot via AddressingScheme — never stored here.
    """

    plane: int = Field(ge=0)
    slot: int = Field(ge=0)
    orbit: OrbitalElements
    satellite_type: str | None = None  # Override satellite type for this node
    terminals: TerminalConfig | None = None  # Deprecated: inline terminals


class TLEFilter(BaseModel):
    """Filter for TLE mode — select satellites from TLE file."""

    norad_ids: list[int] | None = None
    max_count: int | None = Field(default=None, gt=0)


class ParametricConstellation(BaseModel):
    """Constellation defined by Walker-delta/star orbital parameters."""

    mode: Literal["parametric"]
    name: str
    orbit: OrbitParams
    planes: PlaneParams
    satellite_type: str | None = None  # Reference to satellite type file
    default_terminals: TerminalConfig | None = None  # Deprecated: inline terminals
    polar_seam: PolarSeamConfig | None = None
    plane_overrides: list[PlaneOverride] | None = None
    isl_overrides: list[IslOverride] | None = None

    @model_validator(mode="after")
    def _require_terminal_source(self):
        if self.satellite_type is None and self.default_terminals is None:
            raise ValueError("Must specify satellite_type or default_terminals")
        return self


class ExplicitConstellation(BaseModel):
    """Constellation with per-satellite orbital elements."""

    mode: Literal["explicit"]
    name: str
    satellites: list[SatelliteConfig]
    satellite_type: str | None = None  # Reference to satellite type file
    default_terminals: TerminalConfig | None = None  # Deprecated: inline terminals
    isl_overrides: list[IslOverride] | None = None

    @model_validator(mode="after")
    def _validate(self):
        pairs = [(s.plane, s.slot) for s in self.satellites]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Duplicate plane/slot pairs")
        if self.satellite_type is None and self.default_terminals is None:
            raise ValueError("Must specify satellite_type or default_terminals")
        return self


class TLEConstellation(BaseModel):
    """Constellation defined by a TLE file."""

    mode: Literal["tle"]
    name: str
    tle_file: str
    filter: TLEFilter | None = None
    satellite_type: str | None = None  # Reference to satellite type file
    default_terminals: TerminalConfig | None = None  # Deprecated: inline terminals
    isl_overrides: list[IslOverride] | None = None

    @model_validator(mode="after")
    def _require_terminal_source(self):
        if self.satellite_type is None and self.default_terminals is None:
            raise ValueError("Must specify satellite_type or default_terminals")
        return self


# Section 13.26: Discriminated union on `mode` field (non-negotiable)
ConstellationConfig = Annotated[
    Annotated[ParametricConstellation, Tag("parametric")]
    | Annotated[ExplicitConstellation, Tag("explicit")]
    | Annotated[TLEConstellation, Tag("tle")],
    Discriminator("mode"),
]
