# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Session configuration models — top-level YAML schema."""

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

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
        if self.strategy == "stripe" and (
            self.planes_per_stripe is None or self.planes_per_stripe <= 0
        ):
            raise ValueError("strategy 'stripe' requires planes_per_stripe > 0")
        if self.strategy == "explicit" and not self.assignments:
            raise ValueError("strategy 'explicit' requires assignments list")
        return self


class RoutingConfig(BaseModel):
    """Routing configuration.

    Either ``stack`` (legacy path to a routing-stack directory) or
    ``protocol`` (resolved via stack_resolver) must be set.
    """

    protocol: str | None = None  # "ospf" | "isis" | "static" | "nodalpath"
    extensions: list[str] = []  # ["te", "mpls", "sr"]
    stack: str | None = None  # Legacy path — bypass resolution
    compression_factor: int = 1
    config_overrides: dict[str, Any] = {}
    area_assignment: AreaAssignmentConfig | None = None

    @model_validator(mode="after")
    def _require_stack_or_protocol(self):
        if self.stack is None and self.protocol is None:
            raise ValueError("Either 'stack' or 'protocol' must be set")
        return self


class TimeConfig(BaseModel):
    """Time configuration."""

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
    """Convergence detection settings for MI probe measurement."""

    stability_period_s: float = 2.0
    timeout_s: float = 30.0
    probe_interval_ms: int = 100


class MiConfig(BaseModel):
    """Measurement Infrastructure configuration. Disabled by default.

    When enabled, MI runs protocol adapters, probe daemons, and a
    convergence gate for measuring routing convergence after link events.
    When disabled (default), no MI processes start and no MI ports bind.
    """

    enabled: bool = False
    adapter: str | None = None  # e.g. "frr_isis_adapter"
    convergence: ConvergenceConfig = ConvergenceConfig()


class TerrestrialLinkConfig(BaseModel):
    """A static terrestrial link between two ground stations."""

    station_a: str
    station_b: str
    bandwidth_mbps: float = 10000.0
    latency_ms: float = 5.0
    loss_pct: float = 0.0


class PlacementConfig(BaseModel):
    """Pod placement policy for multi-node deployment.

    allOnOne: all pods on a single node (default, backward compatible).
    planePerNode: one orbital plane per K3s node. Intra-plane ISLs are
        LOCAL (direct veth), cross-plane ISLs are CROSS_NODE (VXLAN).
    planeGroupPerNode: multiple adjacent planes per node, round-robin.
    """

    model_config = ConfigDict(frozen=True)

    policy: str = "allOnOne"  # allOnOne | planePerNode | planeGroupPerNode
    planes_per_group: int | None = None  # For planeGroupPerNode


class SessionConfig(BaseModel):
    """Top-level session configuration — the single YAML file
    that defines an entire deployment.

    ``constellation`` accepts either a file path (str) or an inline
    constellation definition (dict).  Same for ``ground_stations``
    which additionally accepts a list of station name strings.

    ``satellite_type`` is the wizard's independent satellite-type
    selection.  When set and ``constellation`` is a file path, the
    deployer merges the two at session-creation time.  When
    ``constellation`` is already an inline dict it is assumed to
    contain the intended satellite type and this field is ignored.
    """

    session: SessionMeta
    constellation: str | dict  # Path to constellation file OR inline definition
    ground_stations: str | list[str] | dict  # Set name, path, station list, OR inline GS definition
    satellite_type: str | None = None  # Override satellite type (independent of constellation)
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = (
        None  # For direct station lists
    )
    addressing: AddressingConfig = AddressingConfig()
    routing: RoutingConfig
    time: TimeConfig = TimeConfig()
    traffic_flows: list[TrafficFlowConfig] | None = None
    terrestrial_links: list[TerrestrialLinkConfig] | None = None
    placement: PlacementConfig = PlacementConfig()
    mi: MiConfig = MiConfig()
    convergence: ConvergenceConfig = ConvergenceConfig()  # backward compat — use mi.convergence
