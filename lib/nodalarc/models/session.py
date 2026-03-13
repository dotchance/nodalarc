"""Session configuration models — top-level YAML schema."""

from typing import Any

from pydantic import BaseModel, model_validator

from nodalarc.models.ground_station import TerrestrialPrefixTemplate


class SessionMeta(BaseModel):
    """Session metadata."""

    name: str
    data_dir: str = "/var/nodalarc/sessions"


class AddressingConfig(BaseModel):
    """Addressing scheme overrides — all have defaults."""

    sat_id_template: str = "sat-P{plane:02d}S{slot:02d}"
    gs_id_template: str = "gs-{name}"
    ipv4_sat_template: str = "10.{plane}.{slot}.1"
    ipv4_gs_template: str = "10.255.{gs_index}.1"
    ipv6_sat_template: str = "fd00::{plane}:{slot}:1"
    ipv6_gs_template: str = "fd00::ff:{gs_index}:1"


class AreaMapping(BaseModel):
    """Area assignment for explicit strategy."""

    planes: list[int] | None = None
    ground_stations: str | list[str] | None = None  # "all" or list of names
    area_id: str


class AreaAssignmentConfig(BaseModel):
    """Routing area assignment configuration."""

    strategy: str  # "stripe", "per-plane", "flat", "explicit"
    planes_per_stripe: int | None = None  # Required for "stripe"
    assignments: list[AreaMapping] | None = None  # Required for "explicit"
    gs_area_id: str | None = None  # Area for ground stations

    @model_validator(mode="after")
    def _validate_strategy_fields(self):
        if self.strategy == "stripe" and (self.planes_per_stripe is None or self.planes_per_stripe <= 0):
            raise ValueError("strategy 'stripe' requires planes_per_stripe > 0")
        if self.strategy == "explicit" and not self.assignments:
            raise ValueError("strategy 'explicit' requires assignments list")
        return self


class RoutingConfig(BaseModel):
    """Routing configuration."""

    stack: str  # Path to routing stack directory
    compression_factor: int = 1
    config_overrides: dict[str, Any] = {}
    area_assignment: AreaAssignmentConfig | None = None


class TimeConfig(BaseModel):
    """Time configuration."""

    mode: str = "realtime"  # "realtime" or "discrete-event"
    compression: int = 1
    start_time: str | None = None  # ISO 8601 (default: now)
    step_seconds: int = 1
    latency_update_interval_seconds: int = 10


class TrafficFlowConfig(BaseModel):
    """Traffic flow configuration."""

    flow_id: str
    src: str
    dst: str
    protocol: str  # "udp" or "tcp"
    bandwidth_kbps: float
    probe_type: str  # "continuous" or "burst"


class ConvergenceConfig(BaseModel):
    """Convergence detection settings."""

    stability_period_s: float = 2.0
    timeout_s: float = 30.0
    probe_interval_ms: int = 100


class TerrestrialLinkConfig(BaseModel):
    """A static terrestrial link between two ground stations."""

    station_a: str
    station_b: str
    bandwidth_mbps: float = 10000.0
    latency_ms: float = 5.0
    loss_pct: float = 0.0


class SessionConfig(BaseModel):
    """Top-level session configuration — the single YAML file
    that defines an entire deployment."""

    session: SessionMeta
    constellation: str  # Path to constellation file
    ground_stations: str | list[str]  # Set name, path to GS file, or list of station names
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = None  # For direct station lists
    addressing: AddressingConfig = AddressingConfig()
    routing: RoutingConfig
    time: TimeConfig = TimeConfig()
    traffic_flows: list[TrafficFlowConfig] | None = None
    terrestrial_links: list[TerrestrialLinkConfig] | None = None
    convergence: ConvergenceConfig = ConvergenceConfig()
