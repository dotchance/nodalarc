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


class BuilderWorld(BaseModel):
    """One resolved session as a render-ready, read-only world."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: SessionMeta
    epoch_unix: float
    ephemeris: SessionEphemeris
    nodes: tuple[BuilderWorldNode, ...]
    link_rules: tuple[BuilderLinkRule, ...] = ()
