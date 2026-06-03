# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session configuration models — top-level YAML schema."""

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    field_validator,
    model_validator,
)

from nodalarc.frozen import FrozenDict, ImmutableStrDict
from nodalarc.model_validation import NonEmptyReference, NonEmptyString, nonempty_unique
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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NonEmptyString
    run_id: NonEmptyReference | None = None
    data_dir: NonEmptyReference = "/var/nodalarc/sessions"

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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    sat_id_template: NonEmptyReference = "sat-P{plane:02d}S{slot:02d}"
    gs_id_template: NonEmptyReference = "gs-{name}"
    ipv4_sat_template: NonEmptyReference = "10.{plane}.{slot}.1"
    ipv4_gs_template: NonEmptyReference = "10.255.{gs_index}.1"
    ipv6_sat_template: NonEmptyReference = "fd00::{plane}:{slot}:1"
    ipv6_gs_template: NonEmptyReference = "fd00::ff:{gs_index}:1"


class AreaMapping(BaseModel):
    """One explicit area mapping. Only explicit strategy owns this shape."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    planes: tuple[NonNegativeInt, ...] | None = None
    ground_stations: Literal["all"] | tuple[NonEmptyReference, ...] | None = None
    area_id: NonEmptyReference

    @field_validator("planes")
    @classmethod
    def _valid_planes(cls, v: tuple[int, ...] | None) -> tuple[int, ...] | None:
        return nonempty_unique(v)

    @field_validator("ground_stations")
    @classmethod
    def _valid_ground_stations(cls, v):
        # "all" is the only scalar keyword; specific stations must be a non-empty
        # list/tuple so a lone typo cannot masquerade as a keyword.
        if isinstance(v, (list, tuple)):
            return nonempty_unique(v)
        return v

    @model_validator(mode="after")
    def _targets_something(self):
        if self.planes is None and self.ground_stations is None:
            raise ValueError("AreaMapping must target planes and/or ground_stations")
        return self


class FlatAreaAssignmentConfig(BaseModel):
    """All nodes share one routing area."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    strategy: Literal["flat"]
    gs_area_id: NonEmptyReference | None = None


class PerPlaneAreaAssignmentConfig(BaseModel):
    """Each orbital plane gets a deterministic routing area."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    strategy: Literal["per-plane"]
    gs_area_id: NonEmptyReference | None = None


class StripeAreaAssignmentConfig(BaseModel):
    """Adjacent planes are grouped into fixed-size area stripes."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    strategy: Literal["stripe"]
    planes_per_stripe: int = Field(gt=0)
    gs_area_id: NonEmptyReference | None = None


class ExplicitAreaAssignmentConfig(BaseModel):
    """Only explicit strategy may carry per-plane/per-GS mappings."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    strategy: Literal["explicit"]
    assignments: tuple[AreaMapping, ...] = Field(min_length=1)
    gs_area_id: NonEmptyReference | None = None

    @model_validator(mode="after")
    def _validate_unique_targets(self):
        seen_planes: set[int] = set()
        seen_gs: set[str] = set()
        has_all_gs = False
        for mapping in self.assignments:
            for plane in mapping.planes or ():
                if plane in seen_planes:
                    raise ValueError(f"plane {plane} is mapped by more than one assignment")
                seen_planes.add(plane)
            if mapping.ground_stations == "all":
                if has_all_gs:
                    raise ValueError("ground_stations='all' is mapped by more than one assignment")
                has_all_gs = True
            elif mapping.ground_stations is not None:
                for name in mapping.ground_stations:
                    if name in seen_gs:
                        raise ValueError(
                            f"ground station {name!r} is mapped by more than one assignment"
                        )
                    seen_gs.add(name)
        if has_all_gs and seen_gs:
            raise ValueError(
                "explicit area assignment mixes ground_stations='all' with specific "
                "station mappings (ambiguous)"
            )
        return self


AreaAssignmentConfig = Annotated[
    FlatAreaAssignmentConfig
    | PerPlaneAreaAssignmentConfig
    | StripeAreaAssignmentConfig
    | ExplicitAreaAssignmentConfig,
    Field(discriminator="strategy"),
]


class RoutingConfig(BaseModel):
    """Routing configuration.

    Runtime routing authority is ``protocol`` plus normalized ``extensions``.
    ``stack`` is rejected because it creates divergent routing truth across
    services.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    protocol: NonEmptyReference | None = None  # resolved by stack_resolver
    # Normalized to the canonical {te, sr, mpls} the stack resolver consumes;
    # known long-form aliases are accepted, unknown values rejected (never
    # silently dropped). See _normalize_extensions.
    extensions: tuple[str, ...] = ()
    stack: NonEmptyReference | None = None  # Unsupported split routing path; rejected below.
    compression_factor: int = Field(default=1, gt=0)
    config_overrides: ImmutableStrDict = Field(default_factory=FrozenDict)
    area_assignment: AreaAssignmentConfig | None = None

    # BFD — cross-protocol, independent of IS-IS/OSPF choice
    bfd: bool = False
    bfd_detect_multiplier: int = Field(default=3, gt=0)
    bfd_rx_interval: int = Field(default=300, gt=0)  # ms
    bfd_tx_interval: int = Field(default=300, gt=0)  # ms

    # IS-IS timers (used when protocol=isis)
    isis_hello_interval: int = Field(default=1, gt=0)  # seconds
    isis_hello_multiplier: int = Field(default=3, gt=0)
    spf_init_delay: int = Field(default=50, ge=0)  # ms — IETF SPF backoff algorithm
    spf_short_delay: int = Field(default=200, ge=0)  # ms
    spf_long_delay: int = Field(default=1000, ge=0)  # ms
    spf_holddown: int = Field(default=2000, ge=0)  # ms
    spf_time_to_learn: int = Field(default=500, ge=0)  # ms

    # OSPF timers (used when protocol=ospf)
    ospf_hello_interval: int = Field(default=1, gt=0)  # seconds
    ospf_dead_interval: int = Field(default=3, gt=0)  # seconds
    ospf_spf_delay: int = Field(default=50, ge=0)  # ms — SPF throttle
    ospf_spf_initial_hold: int = Field(default=200, ge=0)  # ms
    ospf_spf_max_hold: int = Field(default=1000, ge=0)  # ms

    @field_validator("extensions")
    @classmethod
    def _normalize_extensions(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        # Single source of truth for the extension vocabulary lives in the stack
        # resolver, which is the API that actually consumes these.
        from nodalarc.stack_resolver import normalize_extensions

        return normalize_extensions(v)

    @model_validator(mode="after")
    def _require_single_runtime_authority(self):
        # The current Operator/Scheduler runtime resolves protocol/extensions and does
        # not honor routing.stack. Accepting stack would create different routing truth
        # in different services, so fail here instead of preserving a split-brain path.
        if self.stack is not None:
            raise ValueError(
                "routing.stack is not supported by the current runtime; use "
                "routing.protocol with routing.extensions"
            )
        if self.protocol is None:
            raise ValueError("routing.protocol must be set")
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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

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


class CandidateLimits(BaseModel):
    """Bounds on link-rule candidate generation (segment grammar).

    Multi-segment sessions must declare ``candidate_limits`` so a link rule whose
    static endpoint-pair upper bound exceeds the limit fails semantic validation
    before OME starts, rather than materializing an unbounded all-by-all matrix.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    max_pairs_per_rule: int
    max_pairs_per_tick: int | None = None

    @field_validator("max_pairs_per_rule")
    @classmethod
    def _positive_per_rule(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("simulation.candidate_limits.max_pairs_per_rule must be > 0")
        return value

    @field_validator("max_pairs_per_tick")
    @classmethod
    def _positive_per_tick(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("simulation.candidate_limits.max_pairs_per_tick must be > 0")
        return value


class SimulationConfig(BaseModel):
    """Simulation contract fields exposed to session YAML."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: int = 2
    ground_link_model: Literal["geometry_only", "terminal_physics"] = "terminal_physics"
    acknowledge_geometry_only: bool = False
    acknowledge_bbm_handover_gap: bool = False
    actuation: ActuationConfig = Field(default_factory=ActuationConfig)
    # Required for segment sessions; optional on internal one-constellation projections.
    candidate_limits: CandidateLimits | None = None

    @field_validator("schema_version")
    @classmethod
    def _schema_version_supported(cls, value: int) -> int:
        if value != 2:
            raise ValueError("simulation.schema_version must be 2")
        return value


class OrbitConfig(BaseModel):
    """Orbit propagation model selection."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    selection_policy: SelectionPolicySpec = Field(default_factory=SelectionPolicySpec)
    handover_policy: HandoverPolicySpec = Field(
        default_factory=lambda: HandoverPolicySpec(
            name="hysteresis",
            params=HysteresisParameters().model_dump(),
        )
    )
    ranking_order: tuple[RankingComponent, ...] = Field(
        default_factory=lambda: (
            "service_priority",
            "selection_score",
            "satellite_ground_terminal_capacity",
            "lex_pair",
        )
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
    def _bounded_single_overlap_reserve(cls, value: int) -> int:
        if value < 0:
            raise ValueError("scheduling.ground.mbb_reserve must be >= 0")
        # BIG HONESTY NOTE / MBB-002:
        # `mbb_reserve > 1` reads like "this ground station may run two or more
        # simultaneous make-before-break overlaps." The current allocator DOES NOT
        # implement that. It serializes active MBB overlap per GS through
        # `mbb_overlap_locked`, so accepting reserve=2 would reserve capacity the
        # engine cannot use and would make the model look stronger than reality.
        # Do not relax this validator until MBB-002 implements true multi-overlap
        # state, terminal accounting, attribution, and tests.
        if value > 1:
            raise ValueError(
                "scheduling.ground.mbb_reserve > 1 requires future MBB-002 "
                "multi-overlap allocator support; current implementation supports "
                "at most one concurrent MBB overlap per ground station"
            )
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

    @model_validator(mode="before")
    @classmethod
    def _normalize_handover_params(cls, data):
        # Fill hysteresis defaults before the frozen HandoverPolicySpec is built,
        # so the spec is never mutated after construction. SelectionPolicySpec
        # self-normalizes its own params.
        if not isinstance(data, dict):
            return data
        handover = data.get("handover_policy")
        if isinstance(handover, HandoverPolicySpec):
            handover = handover.model_dump()
        if isinstance(handover, Mapping) and handover.get("name") == "hysteresis":
            normalized = HysteresisParameters(**dict(handover.get("params") or {})).model_dump()
            data = {**data, "handover_policy": {**handover, "params": normalized}}
        return data

    @model_validator(mode="after")
    def _resolve_policy_surface(self):
        # selection_policy and handover_policy validate/normalize themselves; this
        # enforces only the cross-field ground-scheduling rules.
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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    ground: GroundSchedulingConfig = Field(default_factory=GroundSchedulingConfig)


class SubstrateCompensationConfig(BaseModel):
    """Substrate latency compensation settings."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    measurement_source: Literal["node-agent-rtt"] = "node-agent-rtt"
    rtt_to_one_way: Literal["half-rtt"] = "half-rtt"


class DispatchConfig(BaseModel):
    """Dispatch authority, latency freshness, and kernel-proof cadence."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    flow_id: NonEmptyReference
    src: NonEmptyReference
    dst: NonEmptyReference
    protocol: Literal["udp", "tcp"]
    bandwidth_kbps: float = Field(gt=0)
    probe_type: Literal["continuous", "burst"]

    @model_validator(mode="after")
    def _distinct_endpoints(self):
        if self.src == self.dst:
            raise ValueError("traffic flow src and dst must differ")
        return self


class ConvergenceConfig(BaseModel):
    """Convergence detection settings for MI probe measurement."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    stability_period_s: float = Field(default=2.0, gt=0)
    timeout_s: float = Field(default=30.0, gt=0)
    probe_interval_ms: int = Field(default=100, gt=0)


class MiConfig(BaseModel):
    """Measurement Infrastructure configuration. Disabled by default.

    When enabled, MI runs protocol adapters, probe daemons, and a
    convergence gate for measuring routing convergence after link events.
    When disabled (default), no MI processes start and no MI ports bind.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    enabled: bool = False
    adapter: NonEmptyReference | None = None  # e.g. "frr_isis_adapter"
    convergence: ConvergenceConfig = ConvergenceConfig()


class TerrestrialLinkConfig(BaseModel):
    """A static terrestrial link between two ground stations."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    station_a: NonEmptyReference
    station_b: NonEmptyReference
    bandwidth_mbps: float = Field(default=10000.0, gt=0)
    latency_ms: float = Field(default=5.0, ge=0)
    loss_pct: float = Field(default=0.0, ge=0, le=100)

    @model_validator(mode="after")
    def _distinct_endpoints(self):
        if self.station_a == self.station_b:
            raise ValueError("terrestrial link endpoints must be distinct stations")
        return self


class DecisionTraceConfig(BaseModel):
    """User-facing audit trace retention settings."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    decision_trace: DecisionTraceConfig = Field(default_factory=DecisionTraceConfig)


class AllOnOnePlacementConfig(BaseModel):
    """All pods land on the first available K3s node."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    policy: Literal["allOnOne"]


class PlanePerNodePlacementConfig(BaseModel):
    """One orbital plane per K3s node; ground stations use deterministic spread."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    policy: Literal["planePerNode"] = "planePerNode"


class PlaneGroupPerNodePlacementConfig(BaseModel):
    """Adjacent planes grouped by an explicit group size."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    policy: Literal["planeGroupPerNode"]
    planes_per_group: int = Field(gt=0)


PlacementConfig = Annotated[
    AllOnOnePlacementConfig | PlanePerNodePlacementConfig | PlaneGroupPerNodePlacementConfig,
    Field(discriminator="policy"),
]


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

    # Not frozen: this is the resolver's internal runtime projection. Product
    # session YAML uses SegmentSessionConfig; the authoritative frozen runtime
    # contract is ResolvedSession, not this model.
    model_config = ConfigDict(extra="forbid")

    session: SessionMeta
    constellation: NonEmptyReference | dict  # Path to constellation file OR inline definition
    ground_stations: (
        NonEmptyReference | list[NonEmptyReference] | dict
    )  # Set name, path, station list, OR inline GS definition
    satellite_type: NonEmptyReference | None = (
        None  # Override satellite type (independent of constellation)
    )
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
    placement: PlacementConfig = Field(default_factory=PlanePerNodePlacementConfig)
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
