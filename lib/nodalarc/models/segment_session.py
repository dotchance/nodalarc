# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Top-level catalog session grammar."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.catalog_refs import BodyRef
from nodalarc.model_validation import (
    AwareTimestamp,
    Identifier,
    Ipv4Network,
    Ipv6Network,
    NonEmptyReference,
    NonNegativeInteger,
    PositiveFiniteFloat,
    PositiveInteger,
    RelativeAssetPath,
    Sha256Hex,
    StrictBoolean,
)
from nodalarc.models.link_rules import LinkRule, NodeSelector
from nodalarc.models.segments import Segment

RoutingProtocol = Literal["isis", "ospf", "bgp", "static"]
RoutingBoundaryAdapter = Literal["static_ip", "bgp", "dtn_bundle"]


class SessionMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Identifier
    display_name: str | None = None
    description: str | None = None


class AddressPoolAssignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    applies_to: NodeSelector
    ipv4_pool: Ipv4Network | None = None
    ipv6_pool: Ipv6Network | None = None
    prefix_length: PositiveInteger | None = None
    allocation: (
        Literal["by_node_order", "by_attach_index", "by_plane_slot", "by_ground_index"] | None
    ) = None

    @model_validator(mode="after")
    def _has_pool(self) -> AddressPoolAssignment:
        if self.ipv4_pool is None and self.ipv6_pool is None:
            raise ValueError("address pool assignment requires ipv4_pool and/or ipv6_pool")
        if self.prefix_length is not None:
            pools = (("ipv4_pool", self.ipv4_pool), ("ipv6_pool", self.ipv6_pool))
            for family, pool in pools:
                if pool is None:
                    continue
                network = ipaddress.ip_network(pool)
                if not network.prefixlen <= self.prefix_length <= network.max_prefixlen:
                    raise ValueError(
                        f"address pool assignment prefix_length {self.prefix_length} "
                        f"is outside {family} {pool}"
                    )
        return self


class Addressing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    loopbacks: tuple[AddressPoolAssignment, ...] | None = None
    point_to_point: tuple[AddressPoolAssignment, ...] | None = None
    terrestrial_prefixes: tuple[AddressPoolAssignment, ...] | None = None


class MplsCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SegmentRoutingCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data_plane: Literal["mpls"]


class TrafficEngineeringCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data_planes: tuple[Literal["mpls"], ...] | None = None


class RoutingCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mpls: MplsCapability | None = None
    segment_routing: SegmentRoutingCapability | None = None
    traffic_engineering: TrafficEngineeringCapability | None = None


RoutingAreaId = Annotated[
    str,
    Field(min_length=1, pattern=r"^[0-9a-f]+(?:\.[0-9a-f]+)*$"),
]


class AreaMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    planes: Annotated[tuple[NonNegativeInteger, ...], Field(min_length=1)] | None = None
    ground_stations: (
        Literal["all"] | Annotated[tuple[Identifier, ...], Field(min_length=1)] | None
    ) = None
    area_id: RoutingAreaId

    @model_validator(mode="after")
    def _targets_something(self) -> AreaMapping:
        if self.planes is None and self.ground_stations is None:
            raise ValueError("area mapping must target planes and/or ground_stations")
        if self.planes is not None:
            if not self.planes:
                raise ValueError("area mapping planes must not be empty")
            if any(plane < 0 for plane in self.planes):
                raise ValueError("area mapping planes must be non-negative")
            if len(set(self.planes)) != len(self.planes):
                raise ValueError("area mapping planes must not contain duplicates")
        if isinstance(self.ground_stations, tuple):
            if not self.ground_stations:
                raise ValueError("area mapping ground_stations must not be empty")
            if len(set(self.ground_stations)) != len(self.ground_stations):
                raise ValueError("area mapping ground_stations must not contain duplicates")
        return self


class AreaAssignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["flat", "per_plane", "stripe", "explicit"]
    gs_area_id: RoutingAreaId | None = None
    planes_per_stripe: PositiveInteger | None = None
    assignments: tuple[AreaMapping, ...] | None = None

    @model_validator(mode="after")
    def _variant_fields(self) -> AreaAssignment:
        if self.strategy in {"flat", "per_plane"}:
            if self.planes_per_stripe is not None or self.assignments is not None:
                raise ValueError(f"{self.strategy} area assignment must not carry variant fields")
        elif self.strategy == "stripe":
            if self.planes_per_stripe is None or self.assignments is not None:
                raise ValueError("stripe area assignment requires planes_per_stripe only")
        elif self.assignments is None or self.planes_per_stripe is not None:
            raise ValueError("explicit area assignment requires assignments only")
        return self


class SpfThrottle(BaseModel):
    """IETF SPF backoff values in milliseconds (IS-IS spf-delay-ietf; OSPF
    maps init/short/long onto its throttle triple). The IS-IS-only holddown
    and learning fields receive runtime defaults during resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    init_delay_ms: NonNegativeInteger = 50
    short_delay_ms: NonNegativeInteger = 200
    long_delay_ms: NonNegativeInteger = 1000
    holddown_ms: NonNegativeInteger | None = None
    time_to_learn_ms: NonNegativeInteger | None = None


class BfdConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: StrictBoolean = False
    detect_multiplier: PositiveInteger = 3
    rx_interval_ms: PositiveInteger = 300
    tx_interval_ms: PositiveInteger = 300


class RoutingTimers(BaseModel):
    """Per-domain IGP timer tuning. Protocol-neutral where the concept is
    shared; the renderer maps values per protocol. Engine defaults apply when
    a field (or the whole object) is omitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hello_interval_s: PositiveInteger = 1
    hold_interval_s: PositiveInteger = 3
    spf: SpfThrottle = SpfThrottle()
    bfd: BfdConfig = BfdConfig()

    @model_validator(mode="after")
    def _hold_exceeds_hello(self) -> RoutingTimers:
        if self.hold_interval_s <= self.hello_interval_s:
            raise ValueError(
                f"hold_interval_s ({self.hold_interval_s}) must be greater than "
                f"hello_interval_s ({self.hello_interval_s})"
            )
        return self


class RoutingDomain(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    protocol: RoutingProtocol
    capabilities: RoutingCapabilities | None = None
    selectors: tuple[NodeSelector, ...] = Field(min_length=1)
    area_assignment: AreaAssignment | None = None
    timers: RoutingTimers | None = None

    @model_validator(mode="after")
    def _protocol_specific_fields(self) -> RoutingDomain:
        if self.area_assignment is not None and self.protocol not in {"isis", "ospf"}:
            raise ValueError(
                f"routing domain {self.id!r} declares area_assignment on protocol "
                f"{self.protocol!r}; routing areas apply to isis/ospf domains only"
            )
        if self.area_assignment is not None:
            area_ids = [
                *(value for value in (self.area_assignment.gs_area_id,) if value is not None),
                *(mapping.area_id for mapping in self.area_assignment.assignments or ()),
            ]
            for area_id in area_ids:
                _validate_protocol_area_id(self.protocol, area_id)
        if self.timers is not None and self.protocol not in {"isis", "ospf"}:
            raise ValueError(
                f"routing domain {self.id!r} declares timers on protocol "
                f"{self.protocol!r}; IGP timers apply to isis/ospf domains only"
            )
        if self.protocol == "ospf" and self.timers is not None:
            unsupported = [
                field
                for field in ("holddown_ms", "time_to_learn_ms")
                if getattr(self.timers.spf, field) is not None
            ]
            if unsupported:
                raise ValueError(
                    f"routing domain {self.id!r} declares IS-IS-only SPF field(s) "
                    f"for OSPF: {', '.join(unsupported)}"
                )
        return self


def _validate_protocol_area_id(protocol: str, area_id: str) -> None:
    if protocol == "ospf":
        try:
            parsed = ipaddress.IPv4Address(area_id)
        except ipaddress.AddressValueError as exc:
            raise ValueError(
                f"OSPF area_id {area_id!r} must be a canonical dotted IPv4 address"
            ) from exc
        if str(parsed) != area_id:
            raise ValueError(f"OSPF area_id {area_id!r} must be a canonical dotted IPv4 address")
        return
    if protocol == "isis" and re.fullmatch(r"[0-9a-f]{2}(?:\.[0-9a-f]{4}){0,6}", area_id) is None:
        raise ValueError(
            f"IS-IS area_id {area_id!r} must be a canonical lowercase hex dotted area token"
        )


class AggregateOf(BaseModel):
    """Resolver-derived prefix set (grammar C046): the from-domain's
    originated prefixes, per address family the boundary can install."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    aggregate_of: Literal["originated"]


class ExportRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_: Identifier = Field(alias="from")
    to: Identifier
    prefixes: tuple[Ipv4Network | Ipv6Network, ...] | AggregateOf
    export_node_loopbacks: StrictBoolean | None = None
    install_via: Literal["peer_loopback"] | NonEmptyReference | None = None


class RoutingBoundary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    over: Identifier
    adapter: RoutingBoundaryAdapter
    export: tuple[ExportRule, ...] = Field(min_length=1)


class Routing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    domains: tuple[RoutingDomain, ...] = Field(min_length=1)
    boundaries: tuple[RoutingBoundary, ...] | None = None

    @model_validator(mode="after")
    def _unique_domains(self) -> Routing:
        ids = [domain.id for domain in self.domains]
        if len(set(ids)) != len(ids):
            raise ValueError("routing domain ids must be unique")
        return self


class CandidateLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_pairs_per_rule: PositiveInteger
    max_pairs_per_tick: PositiveInteger


class Simulation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_limits: CandidateLimits | None = None
    ground_link_model: Literal["geometry_only", "terminal_physics"] = "terminal_physics"
    acknowledge_geometry_only: StrictBoolean = False

    @model_validator(mode="after")
    def _geometry_only_is_explicit(self) -> Simulation:
        if self.ground_link_model == "geometry_only" and not self.acknowledge_geometry_only:
            raise ValueError(
                "simulation.ground_link_model='geometry_only' requires "
                "acknowledge_geometry_only: true"
            )
        if self.ground_link_model == "terminal_physics" and self.acknowledge_geometry_only:
            raise ValueError("simulation.acknowledge_geometry_only applies only to geometry_only")
        return self


class TimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start_time: AwareTimestamp
    step_seconds: PositiveFiniteFloat
    compression: PositiveFiniteFloat


class EphemerisKernel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    path: RelativeAssetPath
    sha256: Sha256Hex | None = None
    targets: tuple[BodyRef, ...] = Field(min_length=1)
    frame: Identifier
    coverage_start: AwareTimestamp | None = None
    coverage_end: AwareTimestamp | None = None

    @model_validator(mode="after")
    def _valid_coverage_window(self) -> EphemerisKernel:
        if (self.coverage_start is None) != (self.coverage_end is None):
            raise ValueError("ephemeris coverage_start and coverage_end must be declared together")
        if self.coverage_start is not None and self.coverage_end is not None:
            start = datetime.fromisoformat(self.coverage_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(self.coverage_end.replace("Z", "+00:00"))
            if end <= start:
                raise ValueError("ephemeris coverage_end must be later than coverage_start")
        return self


class Ephemeris(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["skyfield_bsp", "spice_kernel_stack", "operator_supplied_spk"]
    quality_tier: Identifier
    kernels: tuple[EphemerisKernel, ...] = Field(min_length=1)


class Dispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    latency_authority: Literal["ome"]
    max_latency_age_ticks: PositiveInteger


class SegmentSessionConfig(BaseModel):
    """Deployable catalog session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: SessionMeta
    segments: tuple[Segment, ...] = Field(min_length=1)
    link_rules: tuple[LinkRule, ...] | None = None
    addressing: Addressing | None = None
    routing: Routing | None = None
    simulation: Simulation | None = None
    time: TimeConfig
    ephemeris: Ephemeris | None = None
    dispatch: Dispatch | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> SegmentSessionConfig:
        segment_ids = [segment.id for segment in self.segments]
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("segment ids must be unique")
        if self.link_rules is not None:
            rule_ids = [rule.id for rule in self.link_rules]
            if len(set(rule_ids)) != len(rule_ids):
                raise ValueError("link rule ids must be unique")
        return self
