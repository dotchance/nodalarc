# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""BuilderWorld — the read-only resolved-world wire view for the session builder.

The builder renders the resolver's expansion, never a builder-local one. This
model packages the two payloads the frontend world renderer already consumes
for a live session — node identity facts and ``SessionEphemeris`` — for a
session that has been resolved but not deployed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from nodalarc.models.catalog import MountRole
from nodalarc.models.events import NodePosition, SessionEphemeris
from nodalarc.models.link_rules import LinkLabel
from nodalarc.models.resolved_session import (
    NodeKind,
    ResolvedNodeInterfaces,
    ResolvedSurfacePosition,
    ResolvedTerminalBlock,
)
from nodalarc.models.segment_session import SessionMeta
from nodalarc.models.segments import OriginatedPrefixes


class BuilderLinkEndpoint(BaseModel):
    """One resolved link-rule endpoint, flattened for builder display."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str
    terminal_role: MountRole
    terminal_medium: str | None = None
    min_elevation_deg: float | None = None
    node_ids: tuple[str, ...]


class BuilderLinkRule(BaseModel):
    """Display projection of one ``ResolvedLinkRule``.

    The resolved rule's topology is a discriminated union; the builder needs
    the flat facts (mode, n, explicit pairs, range cap) to preview candidate
    geometry. This is a projection for display — candidate truth at runtime
    stays with OME.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    kind: LinkLabel
    enabled: bool
    endpoints: tuple[BuilderLinkEndpoint, BuilderLinkEndpoint]
    topology_mode: str
    topology_n: int | None = None
    explicit_pairs: tuple[tuple[str, str], ...] = ()
    max_range_km: float | None = None


class BuilderWorldNode(BaseModel):
    """One resolved node's facts for builder display, mirrored from ``ResolvedNode``.

    ``kind`` is carried explicitly so no consumer infers it from the
    ephemeris variant shape. Satellite placement (orbital elements, frames)
    lives in the ephemeris; ``epoch_position`` is the OME-propagated state
    used to seed renderers that cannot propagate that ephemeris variant
    locally. Ground placement is the resolver's
    ``surface_position``: the ephemeris only carries ground nodes that
    participate in space-link physics, while the world contains every
    resolved node — a gateway with no space links still exists.

    Hardware and network facts reuse the resolved models verbatim — they are
    the wire truth; the builder never re-shapes them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    local_node_id: str
    segment_id: str
    namespace: str | None = None
    kind: NodeKind
    plane: int | None = None
    slot: int | None = None
    tags: tuple[str, ...] = ()
    surface_position: ResolvedSurfacePosition | None = None
    epoch_position: NodePosition | None = None
    forwarding: Literal["routed", "host", "bridge", "control_only"] | None = None
    terminal_inventory: tuple[ResolvedTerminalBlock, ...] = ()
    interfaces: ResolvedNodeInterfaces | None = None
    originated_prefixes: OriginatedPrefixes | None = None


class BuilderNodeInterfaceFacts(BaseModel):
    """One node's fixed-interface capacity for one rule, as allocated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    segment_id: str
    matching: int
    free: int


class BuilderRuleAllocation(BaseModel):
    """The allocator's own outcome for one rule — the single capacity truth
    every display reports instead of re-deriving.

    For access rules nothing is consumed at resolve time (the runtime
    schedules access within terminal capacity): ``allocated_pairs`` counts
    the declared candidate universe and ``free`` always mirrors
    ``matching``. Displays must not present access facts as fixed
    allocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    kind: str
    allocated_pairs: int
    per_node: tuple[BuilderNodeInterfaceFacts, ...] = ()


#: How a rule's preview geometry was resolved. Only ``computed`` carries reason
#: counts and drawn pairs; the client renders the other three statuses without
#: deciding them client-side. A module-level alias lets tests compare the
#: vocabulary with the TypeScript union through ``get_args``.
PreviewScope = Literal[
    "computed",
    "inter_body_pending",
    "terrestrial_pending",
    "disabled",
]

#: The reasons a tested preview pair drew no line, keyed directly on the
#: runtime's own visibility reject_reason — never a renamed dialect — plus the
#: one server-only bucket ``no_geometry`` (an allocated pair with no computable
#: geometry at the frozen epoch, the old client silent skip). The
#: motion-only gates (tracking_exceeded, polar_seam) cannot appear: the preview
#: is one frozen epoch and calls the composites with those gates disabled.
#: ``terminal_type_mismatch`` is the one allocator-layer gate the preview does
#: replicate — the runtime refuses to bring up an ISL pair between incompatible
#: terminal types, so drawing it would be a false-positive line.
BuilderPreviewRejectReason = Literal[
    "los_blocked",
    "range_exceeded",
    "elevation_below_min",
    "field_of_regard",
    "terminal_type_mismatch",
    "no_geometry",
]


class BuilderPreviewPair(BaseModel):
    """One drawn preview pair: a runtime node pair whose frozen-epoch geometry
    passed every armed gate, oriented to the rule's endpoints server-side. canvas draws these directly and never re-derives pair identities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    kind: str
    node_a: str
    node_b: str


class BuilderPreviewReasonCount(BaseModel):
    """How many tested pairs one reject reason accounts for. ``reason`` is the
    runtime's reject_reason verbatim (or ``no_geometry``); counts sum over
    ``pairs_tested`` — never an untested remainder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: BuilderPreviewRejectReason
    count: int


class BuilderRulePreview(BaseModel):
    """The server's frozen-epoch visibility verdict for one link rule.

    NodalArc computes preview geometry through the same OME visibility
    composites the runtime uses; the builder renders these facts and never runs
    a second physics engine. ``preview_scope`` says whether geometry ran and, if
    not, why. Only ``computed`` carries reason counts and drawn pairs.

    The preview is bounded, not a simulation. ``pairs_total`` is the candidate
    universe size (a closed-form count, never a materialized pair set);
    ``pairs_tested`` is the deterministic subset geometry actually ran on
    (``min(pairs_total, budget)``, first pairs in authored/node-id order — never
    distance-ranked); ``pairs_drawn`` is how many tested pairs passed (the first
    such, capped to the draw cap). ``capped`` is true when the preview is partial
    on either axis (``pairs_tested < pairs_total`` or more passed than were
    drawn). Reason counts and drawn pairs describe the tested subset only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    kind: str
    preview_scope: PreviewScope
    pairs_total: int
    pairs_tested: int
    pairs_drawn: int
    capped: bool
    reason_counts: tuple[BuilderPreviewReasonCount, ...] = ()
    drawable_pairs: tuple[BuilderPreviewPair, ...] = ()


class BuilderWorldSegment(BaseModel):
    """One segment as the user named it — the world tree speaks their words,
    never bare runtime ids."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str
    display_name: str


class BuilderWorld(BaseModel):
    """One resolved session as a render-ready, read-only world."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: SessionMeta
    epoch_unix: float
    ephemeris: SessionEphemeris
    nodes: tuple[BuilderWorldNode, ...]
    link_rules: tuple[BuilderLinkRule, ...] = ()
    segments: tuple[BuilderWorldSegment, ...] = ()
    allocations: tuple[BuilderRuleAllocation, ...] = ()
    rule_previews: tuple[BuilderRulePreview, ...] = ()
