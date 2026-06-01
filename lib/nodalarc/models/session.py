# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session configuration models — top-level YAML schema."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodalarc.models.ground_policy import (
    CrossTenantDisplacementPolicy,
    HandoverPolicySpec,
    MbbPreemptionPolicy,
    RankingComponent,
    SelectionPolicySpec,
    SuccessorAbortPolicy,
)
from nodalarc.models.ground_station import HysteresisParameters, TerrestrialPrefixTemplate


class SessionMeta(BaseModel):
    """Session metadata."""

    model_config = ConfigDict(extra="forbid")

    name: str
    run_id: str | None = None
    data_dir: str = "/var/nodalarc/sessions"

    @field_validator("run_id")
    @classmethod
    def _valid_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from nodalarc.nats_channels import sanitize_session_id

        clean = sanitize_session_id(value)
        if clean != value:
            raise ValueError("session.run_id must already be a valid runtime subject segment")
        return clean


class AddressingConfig(BaseModel):
    """Addressing scheme overrides — all have defaults."""

    model_config = ConfigDict(extra="forbid")

    sat_id_template: str = "sat-P{plane:02d}S{slot:02d}"
    gs_id_template: str = "gs-{name}"
    ipv4_sat_template: str = "10.{plane}.{slot}.1"
    ipv4_gs_template: str = "10.255.{gs_index}.1"
    ipv6_sat_template: str = "fd00::{plane}:{slot}:1"
    ipv6_gs_template: str = "fd00::ff:{gs_index}:1"


class AreaMapping(BaseModel):
    """Area assignment for explicit strategy."""

    model_config = ConfigDict(extra="forbid")

    planes: list[int] | None = None
    ground_stations: str | list[str] | None = None  # "all" or list of names
    area_id: str


class AreaAssignmentConfig(BaseModel):
    """Routing area assignment configuration."""

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    protocol: str | None = None  # "ospf" | "isis" | "static" | "nodalpath"
    extensions: list[str] = []  # ["te", "mpls", "sr"]
    stack: str | None = None  # Legacy path — bypass resolution
    compression_factor: int = 1
    config_overrides: dict[str, Any] = {}
    area_assignment: AreaAssignmentConfig | None = None

    # BFD — cross-protocol, independent of IS-IS/OSPF choice
    bfd: bool = False
    bfd_detect_multiplier: int = 3
    bfd_rx_interval: int = 300  # ms
    bfd_tx_interval: int = 300  # ms

    # IS-IS timers (used when protocol=isis)
    isis_hello_interval: int = 1  # seconds
    isis_hello_multiplier: int = 3
    spf_init_delay: int = 50  # ms — IETF SPF backoff algorithm
    spf_short_delay: int = 200  # ms
    spf_long_delay: int = 1000  # ms
    spf_holddown: int = 2000  # ms
    spf_time_to_learn: int = 500  # ms

    # OSPF timers (used when protocol=ospf)
    ospf_hello_interval: int = 1  # seconds
    ospf_dead_interval: int = 3  # seconds
    ospf_spf_delay: int = 50  # ms — SPF throttle
    ospf_spf_initial_hold: int = 200  # ms
    ospf_spf_max_hold: int = 1000  # ms

    @model_validator(mode="after")
    def _require_stack_or_protocol(self):
        if self.stack is None and self.protocol is None:
            raise ValueError("Either 'stack' or 'protocol' must be set")
        return self


class ActuationConfig(BaseModel):
    """Wall-clock actuation-latency contract for the in_flight -> faulted decision.

    NOT sim-time: measured in real wall-clock from the Scheduler committing a desired
    link change and dispatching the actuator op to confirmed kernel proof. A paused or
    time-compressed sim does not pause real actuation latency. ``expected_latency_ms``
    is the target (instrumentation / percentile reference); ``fault_after_ms`` is the
    threshold past which a desired-vs-actual divergence is faulted instead of calm
    in_flight. A platform/session contract, not a frontend constant; defaults validated
    against measured single-pair actuation (~25-37 ms p99 on the reference cluster).
    """

    model_config = ConfigDict(extra="forbid")

    expected_latency_ms: float = 250.0
    fault_after_ms: float = 1200.0

    @field_validator("expected_latency_ms", "fault_after_ms")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("simulation.actuation latency bounds must be > 0 ms")
        return value

    @model_validator(mode="after")
    def _fault_exceeds_expected(self):
        if self.fault_after_ms <= self.expected_latency_ms:
            raise ValueError("simulation.actuation.fault_after_ms must exceed expected_latency_ms")
        return self


class SimulationConfig(BaseModel):
    """Simulation contract fields exposed to session YAML."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    ground_link_model: Literal["geometry_only", "terminal_physics"] = "terminal_physics"
    acknowledge_geometry_only: bool = False
    acknowledge_bbm_handover_gap: bool = False
    actuation: ActuationConfig = Field(default_factory=ActuationConfig)

    @field_validator("schema_version")
    @classmethod
    def _schema_version_supported(cls, value: int) -> int:
        if value != 2:
            raise ValueError("simulation.schema_version must be 2")
        return value


class OrbitConfig(BaseModel):
    """Orbit propagation model selection."""

    model_config = ConfigDict(extra="forbid")

    propagator: Literal["keplerian-circular", "j2-mean-elements", "sgp4-tle"]
    tle_max_age_days: float | None = None

    @property
    def fidelity_label(self) -> Literal["synthetic-keplerian", "j2-mean-elements", "sgp4-tle"]:
        """User-facing fidelity label derived from the selected propagator."""
        if self.propagator == "keplerian-circular":
            return "synthetic-keplerian"
        return self.propagator

    @field_validator("tle_max_age_days")
    @classmethod
    def _positive_tle_age_window(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("orbit.tle_max_age_days must be > 0")
        return value

    @model_validator(mode="after")
    def _validate_tle_age_scope(self):
        if self.propagator == "sgp4-tle" and self.tle_max_age_days is None:
            raise ValueError("orbit.tle_max_age_days is required when propagator is 'sgp4-tle'")
        if self.propagator != "sgp4-tle" and self.tle_max_age_days is not None:
            raise ValueError("orbit.tle_max_age_days is only valid when propagator is 'sgp4-tle'")
        return self


class GroundSchedulingConfig(BaseModel):
    """Ground handover and allocation behavior.

    Phase 3 keeps mechanism and policy separate. This model is only the
    operator-configured policy surface; the OME allocator consumes the resolved
    specs and dispatches to registered pure policy hooks.
    """

    model_config = ConfigDict(extra="forbid")

    selection_policy: SelectionPolicySpec = Field(default_factory=SelectionPolicySpec)
    handover_policy: HandoverPolicySpec = Field(
        default_factory=lambda: HandoverPolicySpec(
            name="hysteresis",
            params=HysteresisParameters().model_dump(),
        )
    )
    ranking_order: list[RankingComponent] = Field(
        default_factory=lambda: ["service_priority", "selection_score", "lex_pair"]
    )

    handover_mode: Literal["bbm", "mbb"] = "bbm"
    mbb_overlap_ticks: int = 3
    mbb_reserve: int = 0
    mbb_preemption: MbbPreemptionPolicy = "off"
    successor_abort_policy: SuccessorAbortPolicy = "hard_release"
    cross_tenant_displacement: CrossTenantDisplacementPolicy = "off"
    bbm_acquire_timeout_ticks: int = 1

    @field_validator("mbb_overlap_ticks")
    @classmethod
    def _positive_overlap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("scheduling.ground.mbb_overlap_ticks must be >= 0")
        return value

    @field_validator("mbb_reserve")
    @classmethod
    def _non_negative_reserve(cls, value: int) -> int:
        if value < 0:
            raise ValueError("scheduling.ground.mbb_reserve must be >= 0")
        return value

    @field_validator("bbm_acquire_timeout_ticks")
    @classmethod
    def _strict_bbm_acquire_timeout(cls, value: int) -> int:
        if value != 1:
            raise ValueError(
                "scheduling.ground.bbm_acquire_timeout_ticks values other than 1 are "
                "reserved extension points; Phase 3 has no specified multi-tick BBMGap "
                "wait-state algorithm"
            )
        return value

    @model_validator(mode="after")
    def _resolve_policy_surface(self):
        selection_params = dict(self.selection_policy.params)
        if self.selection_policy.name in ("highest-elevation", "lowest-elevation"):
            if selection_params:
                raise ValueError(
                    f"selection_policy.name={self.selection_policy.name!r} requires empty params"
                )
        elif self.selection_policy.name == "longest-remaining-pass":
            extra = sorted(set(selection_params) - {"lookahead_horizon_ticks"})
            if extra:
                raise ValueError(
                    "selection_policy.name='longest-remaining-pass' received unsupported "
                    f"params: {', '.join(extra)}"
                )
            horizon = selection_params.get("lookahead_horizon_ticks")
            if horizon is None or int(horizon) <= 0:
                raise ValueError(
                    "longest-remaining-pass requires "
                    "scheduling.ground.selection_policy.params.lookahead_horizon_ticks > 0"
                )
            selection_params["lookahead_horizon_ticks"] = int(horizon)
            self.selection_policy.params = selection_params
        else:  # pragma: no cover - Literal should make this unreachable.
            raise ValueError(f"Unknown selection_policy.name={self.selection_policy.name!r}")

        if self.handover_policy.name == "none":
            if self.handover_policy.params:
                raise ValueError("handover_policy.name='none' requires empty params")
        elif self.handover_policy.name == "hysteresis":
            self.handover_policy.params = HysteresisParameters(
                **self.handover_policy.params
            ).model_dump()
        else:  # pragma: no cover - Literal should make this unreachable.
            raise ValueError(f"Unknown handover_policy.name={self.handover_policy.name!r}")

        if not self.ranking_order:
            raise ValueError("scheduling.ground.ranking_order must not be empty")
        if self.ranking_order[-1] != "lex_pair":
            raise ValueError("scheduling.ground.ranking_order must end with 'lex_pair'")
        if len(self.ranking_order) == 1:
            raise ValueError(
                "scheduling.ground.ranking_order must include at least one decision "
                "component before 'lex_pair'"
            )
        if len(set(self.ranking_order)) != len(self.ranking_order):
            raise ValueError("scheduling.ground.ranking_order must not contain duplicates")

        if self.handover_mode == "mbb":
            if self.mbb_overlap_ticks <= 0:
                raise ValueError("MBB handover requires mbb_overlap_ticks > 0")
            if self.mbb_reserve <= 0:
                raise ValueError("MBB handover requires mbb_reserve > 0")
        return self


class SchedulingConfig(BaseModel):
    """Scheduling policy surface."""

    model_config = ConfigDict(extra="forbid")

    ground: GroundSchedulingConfig = Field(default_factory=GroundSchedulingConfig)


class SubstrateCompensationConfig(BaseModel):
    """Substrate latency compensation settings."""

    model_config = ConfigDict(extra="forbid")

    measurement_source: Literal["node-agent-rtt"] = "node-agent-rtt"
    rtt_to_one_way: Literal["half-rtt"] = "half-rtt"


class DispatchConfig(BaseModel):
    """Dispatch authority, latency freshness, and kernel-proof cadence."""

    model_config = ConfigDict(extra="forbid")

    latency_authority: Literal["ome"] = "ome"
    max_latency_age_ticks: int = 1
    clean_kernel_audit_interval_s: float = 60.0
    substrate_compensation: SubstrateCompensationConfig = Field(
        default_factory=SubstrateCompensationConfig
    )

    @field_validator("max_latency_age_ticks")
    @classmethod
    def _positive_latency_age(cls, value: int) -> int:
        if value < 1:
            raise ValueError("dispatch.max_latency_age_ticks must be >= 1")
        return value

    @field_validator("clean_kernel_audit_interval_s")
    @classmethod
    def _positive_clean_kernel_audit_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("dispatch.clean_kernel_audit_interval_s must be > 0")
        return value


class TimeConfig(BaseModel):
    """Time configuration."""

    model_config = ConfigDict(extra="forbid")

    compression: int = 1
    start_time: str | None = None  # ISO 8601 (default: now per R-OME-005)
    step_seconds: int = 1

    @field_validator("compression", "step_seconds")
    @classmethod
    def _positive_time_values(cls, value: int) -> int:
        if value < 1:
            raise ValueError("time.compression and time.step_seconds must be >= 1")
        return value


def resolve_session_epoch(time_config: TimeConfig) -> float:
    """Resolve session epoch to Unix timestamp (seconds).

    Per R-OME-005: when start_time is null/omitted, the session starts at
    wall-clock now. OME is the single authoritative resolver.  This function
    is called exactly once at session start — the resolved value is the
    sim-time basis for every event emitted during the session.
    """
    import time
    from datetime import datetime

    if time_config.start_time:
        return datetime.fromisoformat(time_config.start_time).timestamp()
    return time.time()


class TrafficFlowConfig(BaseModel):
    """Traffic flow configuration."""

    model_config = ConfigDict(extra="forbid")

    flow_id: str
    src: str
    dst: str
    protocol: str  # "udp" or "tcp"
    bandwidth_kbps: float
    probe_type: str  # "continuous" or "burst"


class ConvergenceConfig(BaseModel):
    """Convergence detection settings for MI probe measurement."""

    model_config = ConfigDict(extra="forbid")

    stability_period_s: float = 2.0
    timeout_s: float = 30.0
    probe_interval_ms: int = 100


class MiConfig(BaseModel):
    """Measurement Infrastructure configuration. Disabled by default.

    When enabled, MI runs protocol adapters, probe daemons, and a
    convergence gate for measuring routing convergence after link events.
    When disabled (default), no MI processes start and no MI ports bind.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    adapter: str | None = None  # e.g. "frr_isis_adapter"
    convergence: ConvergenceConfig = ConvergenceConfig()


class TerrestrialLinkConfig(BaseModel):
    """A static terrestrial link between two ground stations."""

    model_config = ConfigDict(extra="forbid")

    station_a: str
    station_b: str
    bandwidth_mbps: float = 10000.0
    latency_ms: float = 5.0
    loss_pct: float = 0.0


class DecisionTraceConfig(BaseModel):
    """User-facing audit trace retention settings."""

    model_config = ConfigDict(extra="forbid")

    active_links: Literal["always"] = "always"
    rejected_candidates_retention: Literal["none", "bounded", "full"] = "bounded"
    retention_ticks: int = 300

    @field_validator("retention_ticks")
    @classmethod
    def _positive_retention(cls, value: int) -> int:
        if value < 1:
            raise ValueError("observability.decision_trace.retention_ticks must be >= 1")
        return value


class ObservabilityConfig(BaseModel):
    """Observability and provenance knobs exposed in session YAML."""

    model_config = ConfigDict(extra="forbid")

    decision_trace: DecisionTraceConfig = Field(default_factory=DecisionTraceConfig)


class PlacementConfig(BaseModel):
    """Pod placement policy for multi-node deployment.

    allOnOne: all pods on the first available node; explicit single-node/debug policy.
    planePerNode: one orbital plane per K3s node. Intra-plane ISLs are
        LOCAL (direct veth), cross-plane ISLs are CROSS_NODE (VXLAN). This is
        the default so multi-node deployments exercise the real substrate.
    planeGroupPerNode: multiple adjacent planes per node, round-robin.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: str = "planePerNode"  # allOnOne | planePerNode | planeGroupPerNode
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

    model_config = ConfigDict(extra="forbid")

    session: SessionMeta
    constellation: str | dict  # Path to constellation file OR inline definition
    ground_stations: str | list[str] | dict  # Set name, path, station list, OR inline GS definition
    satellite_type: str | None = None  # Override satellite type (independent of constellation)
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = (
        None  # For direct station lists
    )
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    orbit: OrbitConfig
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    dispatch: DispatchConfig = Field(default_factory=DispatchConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    addressing: AddressingConfig = AddressingConfig()
    routing: RoutingConfig
    time: TimeConfig = TimeConfig()
    traffic_flows: list[TrafficFlowConfig] | None = None
    terrestrial_links: list[TerrestrialLinkConfig] | None = None
    placement: PlacementConfig = PlacementConfig()
    mi: MiConfig = MiConfig()
    convergence: ConvergenceConfig = ConvergenceConfig()  # backward compat — use mi.convergence

    @model_validator(mode="after")
    def _require_explicit_ground_policy_surface(self):
        missing: list[str] = []
        if (
            "scheduling" not in self.model_fields_set
            or "ground" not in self.scheduling.model_fields_set
        ):
            missing.extend(
                [
                    "scheduling.ground.selection_policy",
                    "scheduling.ground.handover_policy",
                ]
            )
        else:
            ground_fields = self.scheduling.ground.model_fields_set
            if "selection_policy" not in ground_fields:
                missing.append("scheduling.ground.selection_policy")
            if "handover_policy" not in ground_fields:
                missing.append("scheduling.ground.handover_policy")
        if missing:
            raise ValueError(
                "Ground scheduling policy must be explicit; missing "
                + ", ".join(missing)
                + ". Phase 3 does not silently choose candidate selection or "
                "incumbent handover policy."
            )
        return self
