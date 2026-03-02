"""Constellation configuration models.

Supports three modes via discriminated union on the `mode` field:
- parametric: Walker-delta/star expansion from orbital parameters
- explicit: Per-satellite orbital elements
- tle: TLE file with optional filtering
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Tag, field_validator, model_validator


class OrbitalElements(BaseModel):
    """Orbital elements for explicit-mode satellites."""

    altitude_km: float
    inclination_deg: float
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

    type: str  # "optical" or "rf"
    count: int
    max_range_km: float
    bandwidth_mbps: float
    max_tracking_rate_deg_s: float
    allocation: str = "auto"

    @field_validator("count")
    @classmethod
    def _count_range(cls, v: int) -> int:
        if not 0 <= v <= 8:
            raise ValueError(f"terminal count must be 0-8, got {v}")
        return v

    @field_validator("max_tracking_rate_deg_s")
    @classmethod
    def _positive_tracking_rate(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_tracking_rate_deg_s must be positive, got {v}")
        return v


class GroundTerminal(BaseModel):
    """Ground-link terminal specification for satellites."""

    type: str  # "optical" or "rf"
    count: int
    bandwidth_mbps: float

    @field_validator("count")
    @classmethod
    def _count_range(cls, v: int) -> int:
        if not 0 <= v <= 8:
            raise ValueError(f"terminal count must be 0-8, got {v}")
        return v


class TerminalConfig(BaseModel):
    """Terminal configuration for a satellite."""

    isl: list[IslTerminal]
    ground: list[GroundTerminal] = []


class OrbitParams(BaseModel):
    """Orbital parameters for parametric mode."""

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

    count: int
    raan_spacing_deg: float
    sats_per_plane: int
    phase_offset_deg: float


class PolarSeamConfig(BaseModel):
    """Polar seam configuration — hard latitude cutoff for cross-plane ISLs."""

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
    """Override terminal config for specific orbital planes."""

    planes: list[int]
    terminals: TerminalConfig


class SatelliteConfig(BaseModel):
    """Per-satellite config for explicit mode.

    Node ID is derived from plane/slot via AddressingScheme — never stored here.
    """

    plane: int
    slot: int
    orbit: OrbitalElements
    terminals: TerminalConfig | None = None  # None = use default_terminals


class TLEFilter(BaseModel):
    """Filter for TLE mode — select satellites from TLE file."""

    norad_ids: list[int] | None = None
    max_count: int | None = None


class ParametricConstellation(BaseModel):
    """Constellation defined by Walker-delta/star orbital parameters."""

    mode: Literal["parametric"]
    name: str
    orbit: OrbitParams
    planes: PlaneParams
    default_terminals: TerminalConfig
    polar_seam: PolarSeamConfig | None = None
    plane_overrides: list[PlaneOverride] | None = None
    isl_overrides: list[IslOverride] | None = None


class ExplicitConstellation(BaseModel):
    """Constellation with per-satellite orbital elements."""

    mode: Literal["explicit"]
    name: str
    satellites: list[SatelliteConfig]
    default_terminals: TerminalConfig
    isl_overrides: list[IslOverride] | None = None

    @model_validator(mode="after")
    def _no_duplicate_slots(self):
        pairs = [(s.plane, s.slot) for s in self.satellites]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Duplicate plane/slot pairs")
        return self


class TLEConstellation(BaseModel):
    """Constellation defined by a TLE file."""

    mode: Literal["tle"]
    name: str
    tle_file: str
    filter: TLEFilter | None = None
    default_terminals: TerminalConfig
    isl_overrides: list[IslOverride] | None = None


# Section 13.26: Discriminated union on `mode` field (non-negotiable)
ConstellationConfig = Annotated[
    Annotated[ParametricConstellation, Tag("parametric")]
    | Annotated[ExplicitConstellation, Tag("explicit")]
    | Annotated[TLEConstellation, Tag("tle")],
    Discriminator("mode"),
]
