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

    ``document_yaml`` is the pane/resolution document — the server-serialized
    form of the session that resolved, in its authoring shape. It is not the
    save artifact: a save flattens user-library references first, so the pane
    YAML and a saved file's bytes are distinct forms of the same session.
    ``artifact_sha256`` identifies the save artifact — the sha256 of the
    canonical flattened YAML bytes a save of this document writes
    (hypothetical on a resolve check, exact on a save). ``document`` is the
    same session as a parsed mapping for the client's draft importer (it has
    no YAML parser of its own) — in the AUTHORING form when the caller asked
    for rehydration (hermetically inlined user-library objects re-referenced
    while their content still matches the library; identical semantics under
    the loader contract). Its shape is owned by the session grammar, not
    this envelope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    world: BuilderWorld
    document: dict[str, Any]
    document_yaml: str
    artifact_sha256: str
    # Runtime-readiness (Q3), the NODE-COUNT-INDEPENDENT subset the UI can gate
    # on: a session may resolve and save (Q1) yet be unable to start on the
    # cluster. NECESSARY, not sufficient — the switch endpoint runs the
    # operator's full, node-count-aware validator and is authoritative.
    deploy_ready: bool = True
    deploy_blockers: tuple[str, ...] = ()


class BuilderSaveArtifact(BaseModel):
    """Grammar-only save result: what a save writes, without a preview world.

    The save path answers only Q1 (grammar-valid): a session that resolves
    canonicalizes and is written, even if its preview world (Q2) fails or
    refuses to build. So this carries the canonical bytes and their hash plus
    the two values the save endpoint reports — the session name and node count
    — read from the ResolvedSession, never from a built world. It deliberately
    omits ``world``: building it is the resolve-check's job, not the save's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_yaml: str
    artifact_sha256: str
    session_name: str
    node_count: int


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
    them.

    SUPERSEDED by ``BuilderRulePreview.drawable_pairs``, which carries the same
    pair identities plus the server's frozen-epoch visibility verdict. Kept
    beside the previews during the client cutover; the reader moves off it and
    this field is deleted in a later, reader-free step."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    node_a: str
    node_b: str


#: How a rule's preview geometry was resolved. Only ``computed`` carries reason
#: counts and drawn pairs; the other three are typed walls the client renders
#: verbatim instead of deciding client-side. A module-level alias so the
#: vocabulary can be pinned against the TS twin (``get_args`` reaches it).
PreviewScope = Literal[
    "computed",
    "inter_body_pending",
    "terrestrial_pending",
    "disabled",
]

#: The reasons a tested preview pair drew no line, keyed VERBATIM on the
#: runtime's own visibility reject_reason — never a renamed dialect — plus the
#: one server-only bucket ``no_geometry`` (an allocated pair with no computable
#: geometry at the frozen epoch, the old client N30 silent skip). The
#: motion-only gates (tracking_exceeded, polar_seam) cannot appear: the preview
#: is one frozen epoch and calls the composites with those gates disabled.
BuilderPreviewRejectReason = Literal[
    "los_blocked",
    "range_exceeded",
    "elevation_below_min",
    "field_of_regard",
    "no_geometry",
]


class BuilderPreviewPair(BaseModel):
    """One drawn preview pair: a runtime node pair whose frozen-epoch geometry
    passed every armed gate, oriented to the rule's endpoints server-side. P5b's
    canvas draws these directly and never re-derives pair identities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    kind: str
    node_a: str
    node_b: str


class BuilderPreviewReasonCount(BaseModel):
    """How many TESTED pairs one reject reason accounts for. ``reason`` is the
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
    not, why (``inter_body_pending``/``terrestrial_pending``/``disabled`` are
    typed walls). Only ``computed`` carries reason counts and drawn pairs.

    The preview is BOUNDED, not a simulation. ``pairs_total`` is the candidate
    universe size (a closed-form count, never a materialized pair set);
    ``pairs_tested`` is the deterministic subset geometry actually ran on
    (``min(pairs_total, budget)``, first pairs in authored/node-id order — never
    distance-ranked); ``pairs_drawn`` is how many tested pairs passed (the first
    such, capped to the draw cap). ``capped`` is true when the preview is partial
    on either axis (``pairs_tested < pairs_total`` or more passed than were
    drawn). Reason counts and drawn pairs describe the TESTED subset only."""

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
    rule_previews: tuple[BuilderRulePreview, ...] = ()
