# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground station configuration models.

Three formats are supported:
- Individual station file (top-level key: 'ground_station')
- Station set file (top-level key: 'ground_station_set')
- Monolithic legacy file (top-level keys: 'default_terminals', 'stations', etc.)
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodalarc.body_frames import SupportedSurfaceBody
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.terminal_physics import TerminalBoresight

MAX_GROUND_TERMINAL_COUNT = 128


class HysteresisParameters(BaseModel):
    """Parameters for ground segment handover dampening (hysteresis)."""

    model_config = ConfigDict(allow_inf_nan=False)

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
    reference_body: SupportedSurfaceBody = "earth"
    mobility: str = "fixed"  # "fixed", "terrestrial", "maritime", "aerial"
    service_priority: int = 10  # Lower = higher priority. Headroom: 1, 5, 10, 20...
    selection_policy: SelectionPolicySpec | None = None
    handover_policy: HandoverPolicySpec | None = None

    @field_validator("service_priority")
    @classmethod
    def _valid_priority(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"service_priority must be >= 1, got {v}")
        return v

    @field_validator("mobility")
    @classmethod
    def _valid_mobility(cls, v: str) -> str:
        valid_classes = ("fixed", "terrestrial", "maritime", "aerial")
        if v not in valid_classes:
            raise ValueError(f"mobility must be one of {valid_classes}, got {v!r}")
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
    max_range_km: float | None = None
    field_of_regard_deg: float | None = Field(
        default=None,
        description="Full apex angle, in degrees, of the ground-link field-of-regard cone.",
    )
    max_tracking_rate_deg_s: float | None = None
    boresight: TerminalBoresight | None = None
    frequency_band: str | None = None  # For future environmental modeling
    band: str | None = None  # Shorthand band identifier (Ka, Ku, E, V)
    gateway_beam_quota: int | None = None  # Accepted in Phase 3, not enforced yet.
    user_terminal_beam_quota: int | None = None  # Accepted in Phase 3, not enforced yet.

    @field_validator("count")
    @classmethod
    def _count_range(cls, v: int) -> int:
        if not 1 <= v <= MAX_GROUND_TERMINAL_COUNT:
            raise ValueError(f"terminal count must be 1-{MAX_GROUND_TERMINAL_COUNT}, got {v}")
        return v

    @field_validator("bandwidth_mbps")
    @classmethod
    def _positive_bandwidth(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"bandwidth_mbps must be positive, got {v}")
        return v

    @field_validator("tracking_capacity")
    @classmethod
    def _positive_tracking_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"tracking_capacity must be >= 1, got {v}")
        return v

    @field_validator("gateway_beam_quota", "user_terminal_beam_quota")
    @classmethod
    def _positive_future_quota(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError(f"future beam quota fields must be >= 1, got {v}")
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


class VerificationInfo(BaseModel):
    """Metadata about the data source for a ground station's coordinates."""

    source: str = "none"  # "FCC IBFS", "ITU", "none"
    filing: str | None = None  # Filing number (e.g., "E190023")
    confidence: str = "geocoded"  # "filing" = from regulatory filing, "geocoded" = approximation
    url: str | None = None  # Direct link to the filing
    notes: str | None = None


class GroundStationConfig(GroundSegment):
    """Configuration for a single ground station."""

    name: str
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0
    min_elevation_deg: float | None = None  # Override default
    terminals: list[GroundTerminalDef] | None = None  # Override default
    terrestrial_prefixes: list[TerrestrialPrefix] | None = None  # Override template

    # Physical antenna metadata (informational, not used in simulation).
    # NMTS migration target: these flat fields will be replaced by
    # ANTENNA_PATTERN + BAND_PROFILE models when ARCH-005 lands.
    # For now they document what physical hardware exists at the site.
    antennas: int | None = None  # Number of physical antennas at site
    antenna_diameter_m: float | None = None
    band: str | None = None  # Primary frequency band (Ka, Ku, E, V)

    # Data provenance
    verified: VerificationInfo | None = None

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
    default_selection_policy: SelectionPolicySpec | None = None
    default_handover_policy: HandoverPolicySpec | None = None
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
    default_terminals: list[GroundTerminalDef] | None = None
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = None
    default_min_elevation_deg: float | None = None
    default_selection_policy: SelectionPolicySpec | None = None
    default_handover_policy: HandoverPolicySpec | None = None

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
