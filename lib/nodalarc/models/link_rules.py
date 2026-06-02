# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Link rule grammar — the session-level wiring permission graph.

No cross-segment link exists by implication. ``link_rules`` declare which node
groups may form physical links; OME computes feasibility, allocation policy
schedules, and the Scheduler/Node Agent prove kernel state. This module is the
structural schema; selector cardinality, terminal compatibility, candidate
budgets, and protocol-boundary runtime support are semantic/runtime-support
checks owned by the resolver. See ``specs/plans/multi-segment-yaml-grammar.md``.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from nodalarc.models.ground_policy import SelectionPolicySpec
from nodalarc.models.segments import Identifier, LocalNodeId, TerminalMedium

LinkKind = Literal["access", "inter_constellation", "inter_body_relay", "relay"]
TerminalRole = Literal["ground", "isl", "relay"]


class NodeSelector(BaseModel):
    """Selects nodes inside one segment. All supplied filters are ANDed.

    ``planes``/``slots`` are valid only for constellation segments; ``names`` for
    ground segments; ``node_ids`` refer to local IDs before namespace expansion.
    A selector matching zero nodes is invalid (semantic validation).
    """

    model_config = ConfigDict(extra="forbid")

    segment: Identifier
    node_ids: list[LocalNodeId] | None = None
    node_tags: list[Identifier] | None = None
    planes: list[int] | None = None
    slots: list[int] | None = None
    names: list[Identifier] | None = None


class Endpoint(BaseModel):
    """One end of a link rule: a node set plus the terminal it links through."""

    model_config = ConfigDict(extra="forbid")

    selector: NodeSelector
    terminal_role: TerminalRole
    terminal_medium: TerminalMedium | None = None


# --- Topology (discriminated on ``mode``) ---


class VisibleCandidatesTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["visible_candidates"]


class NearestVisibleTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["nearest_visible"]


class NearestNTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["nearest_n"]
    n: int = Field(gt=0)


class ExplicitPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a: LocalNodeId
    b: LocalNodeId


class ExplicitPairsTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["explicit_pairs"]
    pairs: list[ExplicitPair] = Field(min_length=1)


LinkTopology = Annotated[
    VisibleCandidatesTopology | NearestVisibleTopology | NearestNTopology | ExplicitPairsTopology,
    Field(discriminator="mode"),
]


# --- Constraints + protocol boundary ---


class LinkRuleConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # int applies to every node; the map keys are segment IDs.
    max_links_per_node: int | dict[Identifier, int] | None = None
    max_range_km: float | None = None
    require_mutual_visibility: bool | None = None
    scheduling_policy: SelectionPolicySpec | None = None


class ProtocolBoundary(BaseModel):
    """Inter-domain boundary for inter-body relay rules.

    Only ``static_ip`` is MVP-supported; ``bgp``/``dtn_bundle``/``custom`` are
    structurally valid but rejected by runtime-support validation. A cislunar
    protocol boundary must not create an OSPF/ISIS adjacency.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    adapter: Literal["static_ip", "bgp", "dtn_bundle", "custom"]
    routing_domain_a: Identifier | None = None
    routing_domain_b: Identifier | None = None


class LinkRule(BaseModel):
    """A declared permission for two node groups to form physical links."""

    model_config = ConfigDict(extra="forbid")

    id: Identifier
    kind: LinkKind
    enabled: bool = True
    endpoints: tuple[Endpoint, Endpoint]
    topology: LinkTopology
    constraints: LinkRuleConstraints | None = None
    protocol_boundary: ProtocolBoundary | None = None
    tags: list[Identifier] | None = None
