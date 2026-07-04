# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""BuilderWorld — the read-only resolved-world wire view for the session builder.

The builder renders the resolver's expansion, never a builder-local one. This
model packages the two payloads the frontend world renderer already consumes
for a live session — node identity facts and ``SessionEphemeris`` — for a
session that has been resolved but not deployed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from nodalarc.models.events import SessionEphemeris
from nodalarc.models.resolved_session import (
    LinkKind,
    NodeKind,
    ResolvedNodeInterfaces,
    ResolvedSurfacePosition,
    ResolvedTerminalBlock,
    TerminalRole,
)
from nodalarc.models.segment_session import SessionMeta
from nodalarc.models.segments import OriginatedPrefixes
from nodalarc.runtime_support import UnsupportedFeature


class BuilderResolveCheck(BaseModel):
    """Resolve-check result: the world plus the canonical session document.

    ``document_yaml`` is the server-serialized form of the session document
    that resolved — one serializer (the same dump the deploy path writes), so
    the YAML pane, the saved file, and the wire never diverge. ``document``
    is the same session as a parsed mapping for the client's draft importer
    (it has no YAML parser of its own) — in the AUTHORING form when the
    caller asked for rehydration (hermetically inlined user-library objects
    re-referenced while their content still matches the library; identical
    semantics under the loader contract). Its shape is owned by the session
    grammar, not this envelope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    world: BuilderWorld
    document: dict[str, Any]
    document_yaml: str


class BuilderCatalogEntry(BaseModel):
    """One catalog primitive as a library listing row.

    ``error`` carries the validation failure verbatim when the file does not
    satisfy its family's grammar — a broken entry is shown, never hidden.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    family: str
    id: str | None = None
    display_name: str | None = None
    notes: str | None = None
    summary: str | None = None
    error: str | None = None


class BuilderLinkEndpoint(BaseModel):
    """One resolved link-rule endpoint, flattened for builder display."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    segment_id: str
    terminal_role: TerminalRole
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
    kind: LinkKind
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
    lives only in the ephemeris. Ground placement is the resolver's
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


class BuilderLinkCandidate(BaseModel):
    """One allocated fixed pair — the preview draws these, never re-derives
    them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    node_a: str
    node_b: str


class BuilderErrorSubject(BaseModel):
    """The document object a refusal is about: its kind plus the id the
    client's own serializer emitted — draft-addressable without prose
    parsing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    id: str


class BuilderResolveRefusal(BaseModel):
    """The 422 envelope for a session document the resolver refused.

    ``error`` is the resolver's message verbatim. Scope fields ride along
    when the refusal carries them: ``subject``/``segment_id``/``node_id``
    from ``SessionResolutionError``, ``features`` from
    ``UnsupportedFeatureError``. This model OWNS the envelope schema — the
    HTTP layer serializes it and the frontend twin is pinned to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: str
    subject: BuilderErrorSubject | None = None
    segment_id: str | None = None
    node_id: str | None = None
    features: tuple[UnsupportedFeature, ...] | None = None


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
    link_candidates: tuple[BuilderLinkCandidate, ...] = ()
