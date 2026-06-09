# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Top-level catalog session grammar."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, model_validator

from nodalarc.model_validation import NonEmptyReference
from nodalarc.models.link_rules import LinkRule, NodeSelector
from nodalarc.models.segments import Identifier, Segment


class SessionMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Identifier
    display_name: str | None = None
    description: str | None = None


class AddressPoolAssignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    applies_to: NodeSelector
    ipv4_pool: NonEmptyReference | None = None
    ipv6_pool: NonEmptyReference | None = None
    prefix_length: PositiveInt | None = None
    allocation: (
        Literal["by_node_order", "by_attach_index", "by_plane_slot", "by_ground_index"] | None
    ) = None

    @model_validator(mode="after")
    def _has_pool(self) -> AddressPoolAssignment:
        if self.ipv4_pool is None and self.ipv6_pool is None:
            raise ValueError("address pool assignment requires ipv4_pool and/or ipv6_pool")
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


class AreaMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    planes: tuple[int, ...] | None = None
    ground_stations: Literal["all"] | tuple[Identifier, ...] | None = None
    area_id: Identifier

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
    gs_area_id: Identifier | None = None
    planes_per_stripe: PositiveInt | None = None
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


class RoutingDomain(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    protocol: Literal["isis", "ospf", "bgp", "static"]
    capabilities: RoutingCapabilities | None = None
    selectors: tuple[NodeSelector, ...] = Field(min_length=1)
    area_assignment: AreaAssignment | None = None
    timers: Identifier | None = None


class ExportRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_: Identifier = Field(alias="from")
    to: Identifier
    prefixes: tuple[NonEmptyReference, ...] | dict[str, Literal["originated"]]
    export_node_loopbacks: bool | None = None
    install_via: Literal["peer_loopback"] | NonEmptyReference | None = None


class RoutingBoundary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    over: Identifier
    adapter: Literal["static_ip", "bgp", "dtn_bundle"]
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

    max_pairs_per_rule: PositiveInt
    max_pairs_per_tick: PositiveInt


class Simulation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_limits: CandidateLimits | None = None


class TimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start_time: str
    step_seconds: PositiveFloat
    compression: PositiveFloat


class EphemerisKernel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    path: NonEmptyReference
    sha256: str | None = None
    targets: tuple[NonEmptyReference | dict, ...] = Field(min_length=1)
    frame: Identifier
    coverage_start: str | None = None
    coverage_end: str | None = None


class Ephemeris(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["skyfield_bsp", "spice_kernel_stack", "operator_supplied_spk"]
    quality_tier: Identifier
    kernels: tuple[EphemerisKernel, ...] = Field(min_length=1)


class OrbitDefaults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_propagator: Literal["two_body", "j2_mean_elements", "sgp4_tle"] | None = None


class Dispatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    latency_authority: Literal["ome"]
    max_latency_age_ticks: PositiveInt


class SegmentSessionConfig(BaseModel):
    """Deployable catalog session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: SessionMeta
    segments: tuple[Segment, ...] = Field(min_length=1)
    link_rules: tuple[LinkRule, ...] | None = None
    addressing: Addressing | None = None
    routing: Routing | None = None
    simulation: Simulation | None = None
    time: TimeConfig | None = None
    ephemeris: Ephemeris | None = None
    orbit: OrbitDefaults | None = None
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
