# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground station configuration models.

Three formats are supported:
- Individual station file (top-level key: 'ground_station')
- Station set file (top-level key: 'ground_station_set')
- Monolithic legacy file (top-level keys: 'default_terminals', 'stations', etc.)
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodalarc.body_frames import SupportedSurfaceBody
from nodalarc.model_validation import NonEmptyReference
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


class GroundHandoverDefaultSurface(BaseModel):
    """Ground-source defaults for station handover behavior.

    These fields live with a ground station file or station set, not with the
    session. They are still only defaults: station-specific fields override
    them, and runtime resolution applies terminal capacity as the final truth
    constraint.
    """

    default_handover_mode: Literal["bbm", "mbb"] | None = None
    default_mbb_overlap_ticks: int | None = None
    default_mbb_reserve: int | None = None

    @field_validator("default_mbb_overlap_ticks")
    @classmethod
    def _valid_default_mbb_overlap_ticks(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"default_mbb_overlap_ticks must be >= 0, got {v}")
        return v

    @field_validator("default_mbb_reserve")
    @classmethod
    def _valid_default_mbb_reserve(cls, v: int | None) -> int | None:
        if v is not None and v not in (0, 1):
            raise ValueError(
                "default_mbb_reserve must be 0 or 1; higher values require future "
                "MBB-002 multi-overlap support"
            )
        return v

    @model_validator(mode="after")
    def _validate_default_handover_surface(self):
        if self.default_handover_mode == "mbb":
            if self.default_mbb_overlap_ticks is not None and self.default_mbb_overlap_ticks <= 0:
                raise ValueError(
                    "default_handover_mode='mbb' requires default_mbb_overlap_ticks > 0"
                )
            if self.default_mbb_reserve is not None and self.default_mbb_reserve <= 0:
                raise ValueError("default_handover_mode='mbb' requires default_mbb_reserve > 0")
        if self.default_handover_mode == "bbm":
            if self.default_mbb_overlap_ticks not in (None, 0):
                raise ValueError(
                    "default_handover_mode='bbm' must not set default_mbb_overlap_ticks"
                )
            if self.default_mbb_reserve not in (None, 0):
                raise ValueError("default_handover_mode='bbm' must not reserve MBB terminals")
        return self


class GroundSegment(BaseModel):
    """Base class for all ground segment entities (Ground Stations, UTs).

    Aligns with NMTS PLATFORM_DEFINITION / NETWORK_NODE mapping.
    Every ground segment has a tenant ID, an explicit reference body, and a
    mobility class.
    """

    tenant_id: NonEmptyReference = "default"
    reference_body: SupportedSurfaceBody
    mobility: NonEmptyReference = "fixed"  # "fixed", "terrestrial", "maritime", "aerial"
    service_priority: int = 10  # Lower = higher priority. Headroom: 1, 5, 10, 20...
    selection_policy: SelectionPolicySpec | None = None
    handover_policy: HandoverPolicySpec | None = None
    handover_mode: Literal["bbm", "mbb"] | None = None
    mbb_overlap_ticks: int | None = None
    mbb_reserve: int | None = None

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

    @field_validator("mbb_overlap_ticks")
    @classmethod
    def _valid_mbb_overlap_ticks(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"mbb_overlap_ticks must be >= 0, got {v}")
        return v

    @field_validator("mbb_reserve")
    @classmethod
    def _valid_mbb_reserve(cls, v: int | None) -> int | None:
        if v is not None and not 0 <= v <= 1:
            raise ValueError(
                "mbb_reserve must be 0 or 1; higher values require future MBB-002 "
                "multi-overlap allocator support"
            )
        return v

    @model_validator(mode="after")
    def _valid_mbb_surface(self):
        if self.handover_mode == "mbb":
            if self.mbb_overlap_ticks is not None and self.mbb_overlap_ticks <= 0:
                raise ValueError("handover_mode='mbb' requires mbb_overlap_ticks > 0")
            if self.mbb_reserve is not None and self.mbb_reserve <= 0:
                raise ValueError("handover_mode='mbb' requires mbb_reserve > 0")
        if self.handover_mode == "bbm" and self.mbb_reserve not in (None, 0):
            raise ValueError("handover_mode='bbm' must not reserve MBB terminals")
        return self


class TerrestrialPrefix(BaseModel):
    """Explicit terrestrial prefix for a ground station."""

    prefix: NonEmptyReference  # CIDR notation, e.g. "172.16.100.0/24"
    metric: int

    @field_validator("metric")
    @classmethod
    def _non_negative_metric(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"metric must be non-negative, got {v}")
        return v


class TerrestrialPrefixTemplate(BaseModel):
    """Default terrestrial prefix template using {gs_index}."""

    ipv4_template: NonEmptyReference = "172.16.{gs_index}.0/24"
    ipv6_template: NonEmptyReference = "fd10::{gs_index}:0/112"
    metric: int = 10
    default_route: bool = False
    default_route_metric: int = 100

    @field_validator("metric", "default_route_metric")
    @classmethod
    def _non_negative_metric(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"metric must be non-negative, got {v}")
        return v


class GroundTerminalDef(BaseModel):
    """Terminal definition for ground stations."""

    model_config = ConfigDict(allow_inf_nan=False)

    id: NonEmptyReference | None = None
    type: NonEmptyReference  # "optical" or "rf"
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
    frequency_band: NonEmptyReference | None = None  # For future environmental modeling
    band: NonEmptyReference | None = None  # Shorthand band identifier (Ka, Ku, E, V)
    tags: list[NonEmptyReference] | None = None
    gateway_beam_quota: int | None = None  # Declared for future per-beam allocation.
    user_terminal_beam_quota: int | None = None  # Declared for future per-beam allocation.

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

    model_config = ConfigDict(allow_inf_nan=False)

    name: NonEmptyReference
    source_name: NonEmptyReference | None = None
    site_id: NonEmptyReference | None = None
    site_node_id: NonEmptyReference | None = None
    display_name: str | None = None
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0
    min_elevation_deg: float | None = None  # Override default (validated below)
    terminals: list[GroundTerminalDef] | None = None  # Override default
    terrestrial_prefixes: list[TerrestrialPrefix] | None = None  # Override template
    tags: list[NonEmptyReference] | None = None

    # Physical antenna metadata (informational, not used in simulation).
    # NMTS migration target: these flat fields will be replaced by
    # ANTENNA_PATTERN + BAND_PROFILE models when ARCH-005 lands.
    # For now they document what physical hardware exists at the site.
    antennas: int | None = Field(default=None, gt=0)  # Number of physical antennas at site
    antenna_diameter_m: float | None = Field(default=None, gt=0)
    band: NonEmptyReference | None = None  # Primary frequency band (Ka, Ku, E, V)

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


class GroundSiteNodeConfig(GroundSegment):
    """One routable ground node inside a larger ground station site.

    A site node resolves into an ordinary runtime ground-station node so the
    mature OME/Scheduler/Operator path continues to own physics and actuation.
    The site id is grouping metadata; it does not imply an invisible live LAN.
    A node may own multiple terminal blocks; MBB/BBM is evaluated against the
    compatible terminal capacity on this node, not at the session level.
    """

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    id: NonEmptyReference
    display_name: str | None = None
    terminals: list[GroundTerminalDef] = Field(min_length=1)
    min_elevation_deg: float | None = None
    terrestrial_prefixes: list[TerrestrialPrefix] | None = None
    tags: list[NonEmptyReference] | None = None

    @field_validator("min_elevation_deg")
    @classmethod
    def _terminal_elev_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 90:
            raise ValueError(f"min_elevation_deg must be 0-90, got {v}")
        return v

    @model_validator(mode="after")
    def _terminal_blocks_are_named(self):
        terminal_ids = [terminal.id for terminal in self.terminals]
        if any(terminal_id is None for terminal_id in terminal_ids):
            raise ValueError(f"ground site node {self.id!r} requires terminal.id on every block")
        if len(terminal_ids) != len(set(terminal_ids)):
            raise ValueError(f"ground site node {self.id!r} has duplicate terminal ids")
        return self


class GroundSiteConfig(GroundSegment):
    """A user-facing ground station site containing routable ground nodes.

    The site is a facility/grouping object. Runtime packets flow through the
    resolved ground nodes; future site-LAN support must be an explicit runtime
    primitive, not inferred from colocated metadata.
    """

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    id: NonEmptyReference
    display_name: str | None = None
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0
    min_elevation_deg: float | None = None
    nodes: list[GroundSiteNodeConfig] = Field(min_length=1)
    tags: list[NonEmptyReference] | None = None

    @field_validator("lat_deg")
    @classmethod
    def _site_lat_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError(f"lat_deg must be -90 to 90, got {v}")
        return v

    @field_validator("lon_deg")
    @classmethod
    def _site_lon_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError(f"lon_deg must be -180 to 180, got {v}")
        return v

    @field_validator("min_elevation_deg")
    @classmethod
    def _site_elev_range(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 90:
            raise ValueError(f"min_elevation_deg must be 0-90, got {v}")
        return v

    @model_validator(mode="after")
    def _unique_site_nodes(self):
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError(f"ground site {self.id!r} has duplicate node ids")
        return self


class GroundStationFile(GroundHandoverDefaultSurface):
    """Top-level ground station configuration file."""

    default_terminals: list[GroundTerminalDef] = Field(default_factory=list)
    default_min_elevation_deg: float = 25.0
    default_selection_policy: SelectionPolicySpec | None = None
    default_handover_policy: HandoverPolicySpec | None = None
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = None
    stations: list[GroundStationConfig] = Field(default_factory=list)
    ground_sites: list[GroundSiteConfig] | None = None

    @model_validator(mode="before")
    @classmethod
    def _expand_ground_sites(cls, data):
        if not isinstance(data, dict):
            return data
        ground_sites = data.get("ground_sites")
        if not ground_sites:
            return data
        expanded = list(data.get("stations") or [])
        for raw_site in ground_sites:
            site = GroundSiteConfig.model_validate(raw_site)
            site_tags = list(site.tags or ())
            for site_node in site.nodes:
                terminal_tags: list[str] = []
                terminal_ids: list[str] = []
                for terminal in site_node.terminals:
                    terminal_ids.append(str(terminal.id))
                    terminal_tags.extend(str(tag) for tag in (terminal.tags or ()))
                node_tags = [
                    *site_tags,
                    *(site_node.tags or ()),
                    *terminal_tags,
                    site.id,
                    site_node.id,
                    *terminal_ids,
                ]
                station = {
                    "name": f"{site.id}-{site_node.id}",
                    "site_id": site.id,
                    "site_node_id": site_node.id,
                    "display_name": site_node.display_name
                    or f"{site.display_name or site.id} {site_node.id}",
                    "lat_deg": site.lat_deg,
                    "lon_deg": site.lon_deg,
                    "alt_m": site.alt_m,
                    "min_elevation_deg": site_node.min_elevation_deg
                    if site_node.min_elevation_deg is not None
                    else site.min_elevation_deg,
                    "terminals": [
                        terminal.model_dump(mode="python") for terminal in site_node.terminals
                    ],
                    "terrestrial_prefixes": [
                        prefix.model_dump(mode="python")
                        for prefix in (site_node.terrestrial_prefixes or ())
                    ]
                    or None,
                    "tenant_id": site.tenant_id,
                    "reference_body": site.reference_body,
                    "mobility": site.mobility,
                    "service_priority": site_node.service_priority,
                    "selection_policy": (
                        site_node.selection_policy.model_dump(mode="python")
                        if site_node.selection_policy is not None
                        else (
                            site.selection_policy.model_dump(mode="python")
                            if site.selection_policy is not None
                            else None
                        )
                    ),
                    "handover_policy": (
                        site_node.handover_policy.model_dump(mode="python")
                        if site_node.handover_policy is not None
                        else (
                            site.handover_policy.model_dump(mode="python")
                            if site.handover_policy is not None
                            else None
                        )
                    ),
                    "handover_mode": site_node.handover_mode or site.handover_mode,
                    "mbb_overlap_ticks": (
                        site_node.mbb_overlap_ticks
                        if site_node.mbb_overlap_ticks is not None
                        else site.mbb_overlap_ticks
                    ),
                    "mbb_reserve": (
                        site_node.mbb_reserve
                        if site_node.mbb_reserve is not None
                        else site.mbb_reserve
                    ),
                    "tags": node_tags,
                }
                expanded.append(station)
        next_data = dict(data)
        next_data["stations"] = expanded
        return next_data

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


class GroundStationSetConfig(GroundHandoverDefaultSurface):
    """Ground station set — a named collection of station references.

    Retained for tests that exercise the old ground-station model family.
    Runtime catalog sessions use site and site-set primitives resolved by
    `resolve_session()`.
    """

    name: NonEmptyReference
    description: str | None = None
    stations: list[NonEmptyReference]
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
