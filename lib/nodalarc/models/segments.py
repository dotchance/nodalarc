# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Segment grammar — building blocks that produce runtime nodes.

Structural schema for the segment-based session grammar. This layer is permissive
on cross-object/identity-mode rules (those belong to semantic + runtime-support
validation); it is strict on object shape and local field meaning. Runtime-future
segment kinds (``space_node_set``, ``lagrange_point``) are defined here so
future-looking YAML validates structurally, then fails runtime-support validation
with a typed reason until the runtime implements them.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator, model_validator

from nodalarc.body_frames import FrameBodyName, SupportedSurfaceBody
from nodalarc.model_validation import NonEmptyReference
from nodalarc.models.constellation import ConstellationConfig, OrbitalElements
from nodalarc.models.ground_policy import (
    CrossTenantDisplacementPolicy,
    HandoverPolicySpec,
    MbbPreemptionPolicy,
    RankingComponent,
    SelectionPolicySpec,
    SuccessorAbortPolicy,
)

# --- Grammar primitives (lexical types shared across the grammar modules) ---

# Identifier ::= /[a-z][a-z0-9-]*/
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
# LocalNodeId ::= string without whitespace (source IDs before namespace expansion)
LocalNodeId = Annotated[str, Field(pattern=r"^\S+$")]
# Namespace prefixes runtime node IDs in segment_namespaced mode.
Namespace = Identifier
# TerminalMedium ::= "rf" | "optical"
TerminalMedium = Literal["rf", "optical"]
# LagrangePoint ::= "L1" | "L2" | "L3" | "L4" | "L5"
LagrangePoint = Literal["L1", "L2", "L3", "L4", "L5"]
# Non-finite geometry/physics inputs (NaN/Inf) are banned at the grammar layer.
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]

SegmentKind = Literal[
    "constellation",
    "ground_set",
    "space_node",
    "space_node_set",
    "lagrange_point",
]


class SegmentClock(BaseModel):
    """Optional, future-facing per-segment clock offset/rate from session time.

    OME remains responsible for aligning all computed facts to the session master
    timeline; per-node time is metadata for propagation and future relativistic
    modeling. Link decisions and events still carry session master sim time.

    Frozen: it is embedded in the frozen ``ResolvedNode`` and is parsed once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: Literal["session", "affine"] = "session"
    offset_s: FiniteFloat | None = None
    rate: PositiveFiniteFloat | None = None

    @model_validator(mode="after")
    def _validate_clock(self) -> SegmentClock:
        if self.model == "affine":
            if self.rate is None:
                raise ValueError("clock.model='affine' requires a positive rate")
        else:  # session
            if self.offset_s is not None or self.rate is not None:
                raise ValueError("clock.model='session' must not set offset_s or rate")
        return self


# --- Constellation segment ---


class InternalIslConfig(BaseModel):
    """In-segment ISL behavior. In-segment ISLs never permit cross-segment links."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    scope: Literal["intra_segment"] = "intra_segment"


class InternalLinkConfig(BaseModel):
    """Ordinary in-segment links produced by a constellation segment."""

    model_config = ConfigDict(extra="forbid")

    isl: InternalIslConfig | None = None


class ConstellationSegment(BaseModel):
    """A segment that references or inlines a constellation file."""

    model_config = ConfigDict(extra="forbid")

    id: Identifier
    kind: Literal["constellation"]
    # A path string to a constellation file, or an inline constellation. The
    # inline form is the typed ConstellationConfig union so JSON Schema validates
    # its shape; catalog/cross-file checks remain resolver semantics.
    source: NonEmptyReference | ConstellationConfig
    # Required in segment_namespaced; forbidden in legacy_compatible. Enforced by
    # the resolver per the session identity mode, not at this structural layer.
    namespace: Namespace | None = None
    # Required after resolution in multi-segment sessions (semantic validation).
    central_body: FrameBodyName | None = None
    satellite_type: Identifier | None = None
    internal_links: InternalLinkConfig | None = None
    display_name: str | None = None
    tags: list[Identifier] | None = None
    clock: SegmentClock | None = None


# --- Ground segment ---


class GroundSchedulingPolicy(BaseModel):
    """All-optional ground scheduling override surface.

    Mirrors the fields of ``session.GroundSchedulingConfig`` but every field is
    optional: it is a partial override merged by the resolver in the order
    station > segment > source-set default > explicit session default. The
    resolver fails validation if the effective policy is incomplete after merge;
    it never falls through to hidden code defaults.
    """

    model_config = ConfigDict(extra="forbid")

    selection_policy: SelectionPolicySpec | None = None
    handover_policy: HandoverPolicySpec | None = None
    ranking_order: tuple[RankingComponent, ...] | None = None
    handover_mode: Literal["bbm", "mbb"] | None = None
    mbb_overlap_ticks: NonNegativeInt | None = None
    mbb_reserve: Annotated[int, Field(ge=0, le=1)] | None = None
    mbb_preemption: MbbPreemptionPolicy | None = None
    successor_abort_policy: SuccessorAbortPolicy | None = None
    cross_tenant_displacement: CrossTenantDisplacementPolicy | None = None
    bbm_acquire_timeout_ticks: Literal[1] | None = None

    @field_validator("ranking_order")
    @classmethod
    def _validate_ranking_order(
        cls, value: tuple[RankingComponent, ...] | None
    ) -> tuple[RankingComponent, ...] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("ground segment scheduling.ranking_order must not be empty")
        if value[-1] != "lex_pair":
            raise ValueError("ground segment scheduling.ranking_order must end with 'lex_pair'")
        if len(value) == 1:
            raise ValueError(
                "ground segment scheduling.ranking_order must include at least one "
                "decision component before 'lex_pair'"
            )
        if len(set(value)) != len(value):
            raise ValueError("ground segment scheduling.ranking_order must not contain duplicates")
        return value

    @model_validator(mode="after")
    def _validate_partial_mbb_surface(self) -> GroundSchedulingPolicy:
        if self.handover_mode == "mbb":
            if self.mbb_overlap_ticks is not None and self.mbb_overlap_ticks <= 0:
                raise ValueError("MBB ground segment override requires mbb_overlap_ticks > 0")
            if self.mbb_reserve is not None and self.mbb_reserve <= 0:
                raise ValueError("MBB ground segment override requires mbb_reserve > 0")
        return self


class GroundSegment(BaseModel):
    """A segment that references a ground station set or inline ground config."""

    model_config = ConfigDict(extra="forbid")

    id: Identifier
    kind: Literal["ground_set"]
    # A path string, or an inline ground source. Inline ground sources are
    # genuinely heterogeneous (a station-set file, a monolithic station file, or
    # a name list per ``load_ground_stations``), so their structural validation is
    # resolver-owned (the single ground-loading authority) rather than expressed
    # as one lossy structural union here. See the grammar doc, "Ground Segment".
    source: NonEmptyReference | dict
    # Required after resolution in multi-body sessions (semantic validation).
    reference_body: SupportedSurfaceBody | None = None
    namespace: Namespace | None = None
    display_name: str | None = None
    tags: list[Identifier] | None = None
    scheduling: GroundSchedulingPolicy | None = None


# --- Space node segment (MVP runtime: Luna milestone) ---


class StateVector(BaseModel):
    """Explicit position/velocity state for a space node, in a named frame."""

    model_config = ConfigDict(extra="forbid")

    frame: Identifier
    position_km: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    velocity_km_s: tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class ExplicitSpaceNode(BaseModel):
    """One explicitly placed relay/spacecraft node."""

    model_config = ConfigDict(extra="forbid")

    id: Identifier
    satellite_type: Identifier | None = None
    state: StateVector | OrbitalElements
    tags: list[Identifier] | None = None
    clock: SegmentClock | None = None


class SpaceNodeSegment(BaseModel):
    """A single explicitly placed relay/spacecraft node (MVP: one node)."""

    model_config = ConfigDict(extra="forbid")

    id: Identifier
    kind: Literal["space_node"]
    namespace: Namespace
    satellite_type: Identifier
    node: ExplicitSpaceNode
    tags: list[Identifier] | None = None


class SpaceNodeSetSegment(BaseModel):
    """Explicit group of space nodes. Structurally valid but runtime-future."""

    model_config = ConfigDict(extra="forbid")

    id: Identifier
    kind: Literal["space_node_set"]
    namespace: Namespace
    satellite_type: Identifier | None = None
    nodes: list[ExplicitSpaceNode] = Field(min_length=1)
    tags: list[Identifier] | None = None


# --- Lagrange point segment (runtime-future) ---


class ConfiguredStateLagrangeEphemeris(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["configured_state"]
    state: StateVector


class LagrangeApproximationEphemeris(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["lagrange_approximation"]


class ExternalLagrangeEphemeris(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["external_ephemeris"]
    source: NonEmptyReference


LagrangeEphemeris = Annotated[
    ConfiguredStateLagrangeEphemeris | LagrangeApproximationEphemeris | ExternalLagrangeEphemeris,
    Field(discriminator="model"),
]


class LagrangeFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_body: FrameBodyName
    secondary_body: FrameBodyName
    point: LagrangePoint
    ephemeris: LagrangeEphemeris


class LagrangePointSegment(BaseModel):
    """One relay node anchored to a Lagrange point. Structurally valid, runtime-future."""

    model_config = ConfigDict(extra="forbid")

    id: Identifier
    kind: Literal["lagrange_point"]
    namespace: Namespace
    frame: LagrangeFrame
    satellite_type: Identifier
    display_name: str | None = None
    tags: list[Identifier] | None = None
    clock: SegmentClock | None = None


# --- The Segment discriminated union ---

Segment = Annotated[
    ConstellationSegment
    | GroundSegment
    | SpaceNodeSegment
    | SpaceNodeSetSegment
    | LagrangePointSegment,
    Field(discriminator="kind"),
]

# Segment kinds that produce runtime nodes (all of them, today).
NODE_PRODUCING_KINDS: frozenset[str] = frozenset(
    {"constellation", "ground_set", "space_node", "space_node_set", "lagrange_point"}
)
