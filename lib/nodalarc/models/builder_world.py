# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""BuilderWorld — the read-only resolved-world wire view for the session builder.

The builder renders the resolver's expansion, never a builder-local one. This
model packages the two payloads the frontend world renderer already consumes
for a live session — node identity facts and ``SessionEphemeris`` — for a
session that has been resolved but not deployed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nodalarc.models.events import SessionEphemeris
from nodalarc.models.resolved_session import NodeKind, ResolvedSurfacePosition
from nodalarc.models.segment_session import SessionMeta


class BuilderWorldNode(BaseModel):
    """Identity facts for one resolved node, mirrored from ``ResolvedNode``.

    ``kind`` is carried explicitly so no consumer infers it from the
    ephemeris variant shape. Satellite placement (orbital elements, frames)
    lives only in the ephemeris. Ground placement is the resolver's
    ``surface_position``: the ephemeris only carries ground nodes that
    participate in space-link physics, while the world contains every
    resolved node — a gateway with no space links still exists.
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


class BuilderWorld(BaseModel):
    """One resolved session as a render-ready, read-only world."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: SessionMeta
    epoch_unix: float
    ephemeris: SessionEphemeris
    nodes: tuple[BuilderWorldNode, ...]
