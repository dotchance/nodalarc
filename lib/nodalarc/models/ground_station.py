# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Ground station configuration models.

Three formats are supported:
- Individual station file (top-level key: 'ground_station')
- Station set file (top-level key: 'ground_station_set')
- Monolithic legacy file (top-level keys: 'default_terminals', 'stations', etc.)
"""

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


class HysteresisParameters(BaseModel):
    """Parameters for ground segment handover dampening (hysteresis)."""

    discount_factor: float = 1.15  # Score multiplier for active links
    mask_fade_range_deg: float = 5.0  # Taper discount as elevation hits mask

    @field_validator("discount_factor")
    @classmethod
    def _positive_discount(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(f"discount_factor must be >= 1.0, got {v}")
        return v

    @field_validator("mask_fade_range_deg")
    @classmethod
    def _fade_range(cls, v: float) -> float:
        if not 0.0 < v <= 90.0:
            raise ValueError(f"mask_fade_range_deg must be in (0, 90], got {v}")
        return v


class GroundSegment(BaseModel):
    """Base class for all ground segment entities (Ground Stations, UTs).

    Aligns with NMTS PLATFORM_DEFINITION / NETWORK_NODE mapping.
    Every ground segment has a tenant ID, a reference body (default Earth),
    and a mobility class.
    """

    tenant_id: str = "default"
    reference_body: str = "earth"
    mobility: str = "fixed"  # "fixed", "terrestrial", "maritime", "aerial"
    service_class: Literal["gold", "silver"] = "silver"
    hysteresis: HysteresisParameters = HysteresisParameters()

    @field_validator("mobility")
    @classmethod
    def _valid_mobility(cls, v: str) -> str:
        valid_classes = ("fixed", "terrestrial", "maritime", "aerial")
        if v not in valid_classes:
            raise ValueError(f"mobility must be one of {valid_classes}, got {v!r}")
        return v

    @field_validator("reference_body")
    @classmethod
    def _valid_body(cls, v: str) -> str:
        valid_bodies = ("earth", "luna", "mars", "sun")
        if v not in valid_bodies:
            raise ValueError(f"reference_body must be one of {valid_bodies}, got {v!r}")
        return v


class TerrestrialPrefix(BaseModel):
    """Explicit terrestrial prefix for a ground station."""

    prefix: str  # CIDR notation, e.g. "172.16.100.0/24"
    metric: int

    @field_validator("metric")
    @classmethod
    def _non_negative_metric(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"metric must be non-negative, got {v}")
        return v


class TerrestrialPrefixTemplate(BaseModel):
    """Default terrestrial prefix template using {gs_index}."""

    ipv4_template: str = "172.16.{gs_index}.0/24"
    ipv6_template: str = "fd10::{gs_index}:0/112"
    metric: int = 10
    default_route: bool = False
    default_route_metric: int = 100

    @field_validator("metric")
    @classmethod
    def _non_negative_metric(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"metric must be non-negative, got {v}")
        return v


class GroundTerminalDef(BaseModel):
    """Terminal definition for ground stations."""

    type: str  # "optical" or "rf"
    count: int
    bandwidth_mbps: float
    tracking_capacity: int
    frequency_band: str | None = None  # For future environmental modeling

    @field_validator("count")
    @classmethod
    def _count_range(cls, v: int) -> int:
        if not 1 <= v <= 8:
            raise ValueError(f"terminal count must be 1-8, got {v}")
        return v


class GroundStationConfig(GroundSegment):
    """Configuration for a single ground station."""

    name: str
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0
    min_elevation_deg: float | None = None  # Override default
    scheduling_policy: str | None = None  # Override default
    terminals: list[GroundTerminalDef] | None = None  # Override default
    terrestrial_prefixes: list[TerrestrialPrefix] | None = None  # Override template

    @field_validator("lat_deg")
    @classmethod
    def _lat_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError(f"lat_deg must be -90 to 90, got {v}")
        return v

    @field_validator("lon_deg")
    @classmethod
    def _lon_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError(f"lon_deg must be -180 to 180, got {v}")
        return v

    @field_validator("min_elevation_deg")
    @classmethod
    def _elev_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 90:
            raise ValueError(f"min_elevation_deg must be 0-90, got {v}")
        return v


class GroundStationFile(BaseModel):
    """Top-level ground station configuration file."""

    default_terminals: list[GroundTerminalDef]
    default_min_elevation_deg: float = 25.0
    default_scheduling_policy: str = "highest-elevation"
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = None
    stations: list[GroundStationConfig]

    @model_validator(mode="after")
    def _validate_stations(self):
        if len(self.stations) == 0:
            raise ValueError("at least one station must be defined")
        names = [s.name for s in self.stations]
        if len(names) != len(set(names)):
            raise ValueError("duplicate station names found")
        return self

    @field_validator("default_min_elevation_deg")
    @classmethod
    def _elev_range(cls, v: float) -> float:
        if not 0 <= v <= 90:
            raise ValueError(f"default_min_elevation_deg must be 0-90, got {v}")
        return v


class GroundStationIndividualFile(BaseModel):
    """Individual ground station file (top-level key: 'ground_station')."""

    ground_station: GroundStationConfig


class GroundStationSetConfig(BaseModel):
    """Ground station set — a named collection of station references.

    Station names resolve to configs/ground-stations/stations/{name}.yaml.
    """

    name: str
    description: str | None = None
    stations: list[str]
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = None
    default_min_elevation_deg: float | None = None
    default_scheduling_policy: str | None = None

    @model_validator(mode="after")
    def _validate_set(self):
        if len(self.stations) == 0:
            raise ValueError("at least one station must be in the set")
        if len(self.stations) != len(set(self.stations)):
            raise ValueError("duplicate station references in set")
        return self


class GroundStationSetFile(BaseModel):
    """Ground station set file (top-level key: 'ground_station_set')."""

    ground_station_set: GroundStationSetConfig
