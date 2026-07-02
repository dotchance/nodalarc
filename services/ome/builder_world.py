# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Builder resolved-world — resolve a session and return its render-ready view.

Runs the exact chain the OME main loop runs at session start — resolver →
``build_ome_inputs_from_resolved`` → ``build_step_context`` →
``build_session_ephemeris`` — without deploying anything. Parity with the
live session-ephemeris stream is by construction: one code path, two callers.
"""

from __future__ import annotations

from typing import Any

import yaml
from nodalarc.catalog_paths import CatalogRoots, resolve_catalog_reference
from nodalarc.ephemeris_runtime import session_epoch_unix
from nodalarc.models.builder_world import BuilderWorld, BuilderWorldNode
from nodalarc.ome_inputs import build_ome_inputs_from_resolved
from nodalarc.resolve_session import (
    SourceContext,
    default_catalog_roots,
    resolve_session_with_assets,
)

from ome.event_stream import build_session_ephemeris, build_step_context


def build_builder_world(
    session_source: str | dict[str, Any],
    *,
    catalog_roots: CatalogRoots | None = None,
) -> BuilderWorld:
    """Resolve a session document and package its world for the builder.

    ``session_source`` is a ``nodalarc:<path>`` catalog reference to a session
    file, or an already-loaded session document. Resolution failures propagate
    typed — nothing is rendered on failure.
    """
    roots = catalog_roots or default_catalog_roots()
    if isinstance(session_source, str):
        path = resolve_catalog_reference(session_source, roots, label="builder session")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        raw = session_source
    if not raw:
        raise ValueError("builder session source is empty")

    resolution = resolve_session_with_assets(
        raw,
        catalog_roots=roots,
        source_context=SourceContext(origin="builder_world"),
    )
    resolved = resolution.resolved
    epoch_unix = session_epoch_unix(resolved.time)

    runtime = build_ome_inputs_from_resolved(resolved)
    ctx = build_step_context(
        satellites=runtime.satellites,
        addressing=runtime.addressing,
        gs_file=runtime.gs_file,
        neighbors=runtime.neighbors,
        propagator_id=runtime.propagator_id,
        ground_scheduling=runtime.ground_scheduling,
        ground_link_model=runtime.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=runtime.ground_candidate_satellites_by_gs,
        node_metadata=runtime.node_metadata,
        body_frames=runtime.body_frames,
        body_ephemeris=runtime.body_ephemeris,
        active_bodies=runtime.active_bodies,
    )
    ephemeris = build_session_ephemeris(ctx, epoch_unix, 0)

    nodes = tuple(
        BuilderWorldNode(
            node_id=node.node_id,
            local_node_id=node.local_node_id,
            segment_id=node.segment_id,
            namespace=node.namespace,
            kind=node.kind,
            plane=node.plane,
            slot=node.slot,
            tags=node.tags,
            surface_position=node.surface_position,
            forwarding=node.forwarding,
            terminal_inventory=node.terminal_inventory,
            interfaces=node.interfaces,
            originated_prefixes=node.originated_prefixes,
        )
        for node in resolved.nodes
    )
    return BuilderWorld(
        session=resolved.session,
        epoch_unix=epoch_unix,
        ephemeris=ephemeris,
        nodes=nodes,
    )
