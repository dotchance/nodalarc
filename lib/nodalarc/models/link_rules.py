# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog link-rule grammar.

Link rules declare which resolved node sets may form physical links. They do
not create links directly; OME still computes feasibility from geometry,
terminal limits, and current runtime state.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from nodalarc.frozen import FrozenDict
from nodalarc.models.segments import FiniteFloat, Identifier, PositiveFiniteFloat, TerminalMedium

MountRole = Literal["access", "isl", "crosslink", "backbone"]
LinkLabel = Literal["access", "isl", "relay", "backbone", "inter_body"]
LinkRelation = Literal["intra_segment", "inter_segment", "inter_body"]
LinkMedium = Literal["rf", "optical", "terrestrial", "mixed"]

# Transitional type names retained for callers while the resolver/runtime are
# moved to the catalog vocabulary. The values are the catalog mount/link labels,
# not the retired ground|isl|relay endpoint role set.
TerminalRole = MountRole
LinkKind = LinkLabel


class NodeSelector(BaseModel):
    """Set expression over resolved nodes.

    Exactly one field is allowed. ``all`` is intersection, ``any`` is union, and
    ``not`` is complement relative to the selector universe.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    all: tuple[NodeSelector, ...] | None = Field(default=None, min_length=1)
    any: tuple[NodeSelector, ...] | None = Field(default=None, min_length=1)
    not_: NodeSelector | None = Field(default=None, alias="not")
    segment: Identifier | None = None
    tag: Identifier | None = None
    node: Identifier | None = None
    plane: NonNegativeInt | None = None
    slot: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _exactly_one_operator(self) -> NodeSelector:
        present = [
            field
            for field in ("all", "any", "not_", "segment", "tag", "node", "plane", "slot")
            if getattr(self, field) is not None
        ]
        if len(present) != 1:
            raise ValueError("node selector must contain exactly one set operator or predicate")
        return self


class TerminalSelector(BaseModel):
    """Set expression over terminal mounts on the already-selected node set."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    all: tuple[TerminalSelector, ...] | None = Field(default=None, min_length=1)
    any: tuple[TerminalSelector, ...] | None = Field(default=None, min_length=1)
    not_: TerminalSelector | None = Field(default=None, alias="not")
    role: MountRole | None = None
    medium: TerminalMedium | None = None
    mount: Identifier | None = None

    @model_validator(mode="after")
    def _exactly_one_operator(self) -> TerminalSelector:
        present = [
            field
            for field in ("all", "any", "not_", "role", "medium", "mount")
            if getattr(self, field) is not None
        ]
        if len(present) != 1:
            raise ValueError("terminal selector must contain exactly one set operator or predicate")
        return self


class Endpoint(BaseModel):
    """One endpoint of a link rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    select: NodeSelector
    terminal: TerminalSelector
    min_elevation_deg: FiniteFloat | None = None


class VisibleCandidatesTopology(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["visible_candidates"]


class NearestVisibleTopology(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["nearest_visible"]


class NearestNTopology(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["nearest_n"]
    n: PositiveInt


class ExplicitPair(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    a: Identifier
    b: Identifier

    @model_validator(mode="after")
    def _no_self_pair(self) -> ExplicitPair:
        if self.a == self.b:
            raise ValueError("explicit link pair endpoints must differ")
        return self


class ExplicitPairsTopology(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["explicit_pairs"]
    pairs: tuple[ExplicitPair, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_undirected_pairs(self) -> ExplicitPairsTopology:
        seen: set[frozenset[str]] = set()
        for pair in self.pairs:
            key = frozenset((pair.a, pair.b))
            if key in seen:
                raise ValueError(f"duplicate explicit pair: {sorted(key)}")
            seen.add(key)
        return self


LinkTopology = Annotated[
    VisibleCandidatesTopology | NearestVisibleTopology | NearestNTopology | ExplicitPairsTopology,
    Field(discriminator="mode"),
]


class LinkRuleConstraints(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_links_per_node: (
        PositiveInt | Annotated[dict[Identifier, PositiveInt], AfterValidator(FrozenDict)] | None
    ) = None
    max_range_km: PositiveFiniteFloat | None = None
    require_mutual_visibility: bool | None = None

    @model_validator(mode="after")
    def _validate_per_segment_caps(self) -> LinkRuleConstraints:
        if isinstance(self.max_links_per_node, FrozenDict):
            if not self.max_links_per_node:
                raise ValueError("max_links_per_node map must not be empty")
            for segment_id, value in self.max_links_per_node.items():
                if not isinstance(segment_id, str) or not segment_id:
                    raise ValueError("max_links_per_node segment ids must be non-empty strings")
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    raise ValueError("max_links_per_node values must be positive integers")
        return self


class LinkRule(BaseModel):
    """A declared permission for two node groups to form physical links."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: Identifier
    enabled: bool = True
    endpoints: tuple[Endpoint, Endpoint]
    topology: LinkTopology
    constraints: LinkRuleConstraints | None = None
    class_: LinkLabel | None = Field(default=None, alias="class")
    tags: tuple[Identifier, ...] | None = None

    @model_validator(mode="after")
    def _unique_tags(self) -> LinkRule:
        if self.tags is not None and len(set(self.tags)) != len(self.tags):
            raise ValueError("link rule tags must not contain duplicates")
        return self


NodeSelector.model_rebuild()
TerminalSelector.model_rebuild()
