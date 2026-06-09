# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog segment grammar."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    TypeAdapter,
    model_validator,
)

from nodalarc.model_validation import NonEmptyReference

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
RuntimeNodeId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]
TerminalMedium = Literal["rf", "optical"]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonNegativeFiniteFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
LagrangePoint = Literal["l1", "l2", "l3", "l4", "l5"]

CatalogObject = NonEmptyReference | dict[str, Any]


class TagsMixin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @staticmethod
    def _validate_unique_tags(tags: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if tags is not None and len(set(tags)) != len(tags):
            raise ValueError("tags must not contain duplicates")
        return tags


class SegmentClock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: Literal["session", "affine"] = "session"
    offset_s: FiniteFloat | None = None
    rate: PositiveFiniteFloat | None = None

    @model_validator(mode="after")
    def _validate_clock(self) -> SegmentClock:
        if self.model == "session":
            if self.offset_s is not None or self.rate is not None:
                raise ValueError("session clock must not set offset_s or rate")
        elif self.rate is None:
            raise ValueError("affine clock requires rate")
        return self


class OriginatedPrefixes(BaseModel):
    """Routing injection intent for a placed node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ipv4: tuple[NonEmptyReference, ...] | None = None
    ipv6: tuple[NonEmptyReference, ...] | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> OriginatedPrefixes:
        if not self.ipv4 and not self.ipv6:
            raise ValueError("originated_prefixes must include ipv4 and/or ipv6")
        return self


class HighestElevationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    highest_elevation: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _empty(self) -> HighestElevationPolicy:
        if self.highest_elevation:
            raise ValueError("highest_elevation policy takes no parameters")
        return self


class LowestElevationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lowest_elevation: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _empty(self) -> LowestElevationPolicy:
        if self.lowest_elevation:
            raise ValueError("lowest_elevation policy takes no parameters")
        return self


class LongestRemainingPassParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lookahead_horizon_ticks: PositiveInt


class LongestRemainingPassPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    longest_remaining_pass: LongestRemainingPassParams


SelectionPolicy = HighestElevationPolicy | LowestElevationPolicy | LongestRemainingPassPolicy


class HysteresisParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    discount_factor: PositiveFiniteFloat
    mask_fade_range_deg: NonNegativeFiniteFloat


class HysteresisPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hysteresis: HysteresisParams


class HardReleasePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hard_release: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _empty(self) -> HardReleasePolicy:
        if self.hard_release:
            raise ValueError("hard_release policy takes no parameters")
        return self


HandoverPolicy = HysteresisPolicy | HardReleasePolicy

RankingComponent = Literal[
    "service_priority",
    "selection_score",
    "per_gs_rank",
    "satellite_ground_terminal_capacity",
    "lex_pair",
]


class GroundScheduling(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_policy: SelectionPolicy | None = None
    handover_policy: HandoverPolicy | None = None
    handover_mode: Literal["mbb", "bbm"] | None = None
    mbb_overlap_ticks: NonNegativeInt | None = None
    mbb_reserve: NonNegativeInt | None = None
    handover_concurrency: Literal["one_at_a_time", "all_at_once"] | None = None
    ranking_order: tuple[RankingComponent, ...] | None = None
    mbb_preemption: Literal["off"] | None = None
    successor_abort_policy: Literal["hard_release", "soft_retain"] | None = None
    cross_tenant_displacement: Literal["off"] | None = None
    bbm_acquire_timeout_ticks: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _validate_scheduling(self) -> GroundScheduling:
        if self.ranking_order is not None:
            if len(set(self.ranking_order)) != len(self.ranking_order):
                raise ValueError("ranking_order must not contain duplicates")
            if self.ranking_order[-1] != "lex_pair":
                raise ValueError("ranking_order must end with lex_pair")
        return self


class GroundApply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scheduling: GroundScheduling | None = None
    originated_prefixes: OriginatedPrefixes | None = None
    tags: tuple[Identifier, ...] | None = None

    @model_validator(mode="after")
    def _unique_tags(self) -> GroundApply:
        TagsMixin._validate_unique_tags(self.tags)
        return self


class GroundOverrideMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    site: Identifier


class GroundOverride(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    match: GroundOverrideMatch
    tags: tuple[Identifier, ...] | None = None
    scheduling: GroundScheduling | None = None
    originated_prefixes: OriginatedPrefixes | None = None

    @model_validator(mode="after")
    def _unique_tags(self) -> GroundOverride:
        TagsMixin._validate_unique_tags(self.tags)
        return self


class GroundPlacement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_site_set: CatalogObject


class SpaceSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    display_name: str | None = None
    tags: tuple[Identifier, ...] | None = None
    clock: SegmentClock | None = None
    source: CatalogObject

    @model_validator(mode="after")
    def _unique_tags(self) -> SpaceSegment:
        TagsMixin._validate_unique_tags(self.tags)
        return self


class GroundSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    display_name: str | None = None
    tags: tuple[Identifier, ...] | None = None
    clock: SegmentClock | None = None
    placement: GroundPlacement
    apply: GroundApply | None = None
    overrides: tuple[GroundOverride, ...] | None = None

    @model_validator(mode="after")
    def _validate_ground(self) -> GroundSegment:
        TagsMixin._validate_unique_tags(self.tags)
        if self.overrides is not None:
            seen: set[str] = set()
            for override in self.overrides:
                site = override.match.site
                if site in seen:
                    raise ValueError(f"duplicate ground override for site {site!r}")
                seen.add(site)
        return self


class StateVector(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    epoch: str
    frame: Identifier
    position_km: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    velocity_km_s: tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class ConfiguredStateLagrange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    configured_state: StateVector


class ApproximateLagrange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lagrange_approximation: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _empty(self) -> ApproximateLagrange:
        if self.lagrange_approximation:
            raise ValueError("lagrange_approximation takes no parameters")
        return self


class ExternalEphemerisLagrange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    external_ephemeris: dict[str, NonEmptyReference]

    @model_validator(mode="after")
    def _path_only(self) -> ExternalEphemerisLagrange:
        if set(self.external_ephemeris) != {"path"}:
            raise ValueError("external_ephemeris requires only path")
        return self


LagrangeEphemeris = ConfiguredStateLagrange | ApproximateLagrange | ExternalEphemerisLagrange
_LAGRANGE_EPHEMERIS_ADAPTER = TypeAdapter(LagrangeEphemeris)


class LagrangeFrame(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lagrange: dict[str, Any]

    @model_validator(mode="after")
    def _validate_frame(self) -> LagrangeFrame:
        required = {"primary_body", "secondary_body", "point", "ephemeris"}
        keys = set(self.lagrange)
        if keys != required:
            raise ValueError(f"lagrange frame keys must be {sorted(required)}")
        if self.lagrange["point"] not in {"l1", "l2", "l3", "l4", "l5"}:
            raise ValueError("lagrange point must be l1, l2, l3, l4, or l5")
        _LAGRANGE_EPHEMERIS_ADAPTER.validate_python(self.lagrange["ephemeris"])
        return self


class LagrangeSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Identifier
    display_name: str | None = None
    tags: tuple[Identifier, ...] | None = None
    clock: SegmentClock | None = None
    node: CatalogObject
    frame: LagrangeFrame

    @model_validator(mode="after")
    def _unique_tags(self) -> LagrangeSegment:
        TagsMixin._validate_unique_tags(self.tags)
        return self


Segment = SpaceSegment | GroundSegment | LagrangeSegment
