# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Builder resolved-world — resolve a session and return its render-ready view.

Runs the exact chain the OME main loop runs at session start — resolver →
``build_ome_inputs_from_resolved`` → ``build_step_context`` →
``build_session_ephemeris`` — without deploying anything. Parity with the
live session-ephemeris stream is by construction: one code path, two callers.
"""

from __future__ import annotations

import hashlib
from typing import Any

import yaml
from nodalarc.catalog_browse import flatten_user_references, rehydrate_user_references
from nodalarc.catalog_paths import CatalogRoots, resolve_catalog_reference
from nodalarc.ephemeris_runtime import session_epoch_unix
from nodalarc.models.builder_world import (
    BuilderLinkCandidate,
    BuilderLinkEndpoint,
    BuilderLinkRule,
    BuilderResolveCheck,
    BuilderSaveArtifact,
    BuilderWorld,
    BuilderWorldNode,
    BuilderWorldSegment,
)
from nodalarc.models.resolved_session import ResolvedLinkRule, ResolvedSession
from nodalarc.ome_inputs import build_ome_inputs_from_resolved
from nodalarc.resolve_session import (
    SessionResolution,
    SourceContext,
    default_catalog_roots,
    link_rule_interface_facts,
    resolve_session_with_assets,
    segment_display_names,
)
from nodalarc.session_validator import validate_session_readiness

from ome.event_stream import build_session_ephemeris, build_step_context


def _builder_link_rule(
    rule: ResolvedLinkRule,
    local_to_runtime: dict[tuple[str, str], str],
) -> BuilderLinkRule:
    """Flatten one resolved rule's topology union into display facts.

    Explicit pairs survive resolution in the AUTHORED segment-local id space
    (endpoint memberships are runtime ids). The wire contract carries runtime
    ids everywhere, so pairs are joined here through the resolver's own
    (segment, local id) facts — ambiguity is fatal, never guessed.
    """
    topology = rule.topology
    explicit_pairs: tuple[tuple[str, str], ...] = ()
    if topology.mode == "explicit_pairs":
        rule_segments = sorted({endpoint.segment_id for endpoint in rule.endpoints})

        def _runtime_id(local: str) -> str:
            matches = {
                local_to_runtime[(segment, local)]
                for segment in rule_segments
                if (segment, local) in local_to_runtime
            }
            if len(matches) != 1:
                raise ValueError(
                    f"link rule {rule.rule_id!r}: explicit pair id {local!r} resolves to "
                    f"{len(matches)} nodes across segments {rule_segments}"
                )
            return next(iter(matches))

        explicit_pairs = tuple(
            (_runtime_id(pair.a), _runtime_id(pair.b)) for pair in topology.pairs
        )
    return BuilderLinkRule(
        rule_id=rule.rule_id,
        kind=rule.kind,
        enabled=rule.enabled,
        endpoints=tuple(
            BuilderLinkEndpoint(
                segment_id=endpoint.segment_id,
                terminal_role=endpoint.terminal_role,
                terminal_medium=endpoint.terminal_medium,
                min_elevation_deg=endpoint.min_elevation_deg,
                node_ids=endpoint.node_ids,
            )
            for endpoint in rule.endpoints
        ),
        topology_mode=topology.mode,
        topology_n=getattr(topology, "n", None),
        explicit_pairs=explicit_pairs,
        max_range_km=rule.constraints.max_range_km if rule.constraints else None,
    )


def _canonical_session_yaml(document: Any) -> str:
    """The one canonical YAML dump for session documents.

    The pane/resolution document and the flattened save artifact are
    different documents; both serialize through this function so the dump
    style can never drift between them.
    """
    return yaml.dump(document, default_flow_style=False, sort_keys=False)


def build_builder_resolve_check(
    session_source: str | dict[str, Any],
    *,
    catalog_roots: CatalogRoots | None = None,
    rehydrate: bool = False,
) -> BuilderResolveCheck:
    """Resolve a session document and return the world with its canonical YAML.

    ``rehydrate`` returns ``document`` in the AUTHORING form: inline objects
    a hermetic save flattened are re-referenced into the user library when
    their content still matches (semantics identical either way — the
    resolution and the YAML always use the file's own content). Pass it when
    loading a saved/running session for editing; a client-posted document
    already carries its references.

    ``artifact_sha256`` hashes the canonical FLATTENED form — what a save of
    this document writes. Flattening is idempotent, so on the save path
    (which flattens before calling here) the hash equals the written bytes.
    """
    roots = catalog_roots or default_catalog_roots()
    raw = _load_session_source(session_source, roots)
    flattened = flatten_user_references(raw, roots=roots)
    resolution = resolve_session_with_assets(
        raw,
        catalog_roots=roots,
        source_context=SourceContext(origin="builder_world"),
    )
    deploy_ready, deploy_blockers = _deploy_readiness(resolution.resolved)
    return BuilderResolveCheck(
        world=_world_from_resolution(resolution, roots, raw),
        document=rehydrate_user_references(raw, roots=roots) if rehydrate else raw,
        document_yaml=_canonical_session_yaml(raw),
        artifact_sha256=hashlib.sha256(
            _canonical_session_yaml(flattened).encode("utf-8")
        ).hexdigest(),
        deploy_ready=deploy_ready,
        deploy_blockers=deploy_blockers,
    )


def _deploy_readiness(
    resolved: ResolvedSession, *, available_node_count: int = 1
) -> tuple[bool, tuple[str, ...]]:
    """Deploy-readiness (Q3): can this resolved session start on the cluster?

    Refuses on no satellites (the session cannot start) or any readiness ERROR
    (zero-candidate link rules, disconnected routing members, SR index gaps,
    MBB capacity shortfalls) — the same conditions the operator raises on. The
    only node-count-dependent check the validator runs is a WARNING, never an
    error, so the refusal set is cluster-fact-free: the UI gate calls this with
    the default node count and gets exactly the same verdict the switch guard
    gets with the live count. The switch guard passes the live count so it runs
    the operator's full validator with real facts and stays authoritative.
    """
    blockers: list[str] = []
    satellite_count = sum(1 for node in resolved.nodes if node.kind == "satellite")
    if satellite_count < 1:
        blockers.append("no satellites — the session cannot start on the cluster")
    for result in validate_session_readiness(resolved, available_node_count=available_node_count):
        if result.level == "error":
            blockers.append(f"[{result.code}] {result.message}")
    return (not blockers, tuple(blockers))


def deploy_readiness_for_source(
    session_source: str | dict[str, Any],
    *,
    catalog_roots: CatalogRoots | None = None,
    available_node_count: int = 1,
) -> tuple[bool, tuple[str, ...]]:
    """Resolve a session (Q1) and return its deploy-readiness (Q3).

    The switch guard's authoritative check: it runs BEFORE any CR mutation, so
    a session that cannot start on the cluster never reaches the switch that
    would delete the running ConstellationSpec CR before the operator's late
    raise. Resolution failures propagate typed.
    """
    roots = catalog_roots or default_catalog_roots()
    raw = _load_session_source(session_source, roots)
    resolution = resolve_session_with_assets(
        raw,
        catalog_roots=roots,
        source_context=SourceContext(origin="builder_world"),
    )
    return _deploy_readiness(resolution.resolved, available_node_count=available_node_count)


def build_builder_save_artifact(
    session_source: str | dict[str, Any],
    *,
    catalog_roots: CatalogRoots | None = None,
) -> BuilderSaveArtifact:
    """Grammar-only save path (Q1): resolve → canonicalize → hash.

    Deliberately does NOT build the preview world (Q2 —
    ``_world_from_raw``/``build_ome_inputs_from_resolved``), so a
    grammar-valid session whose world build would fail or refuse (for
    instance a satellite-less, ground-only session) still saves: save depends
    on Q1 alone. The session name and node count come from the
    ``ResolvedSession``, not a built world.
    """
    roots = catalog_roots or default_catalog_roots()
    raw = _load_session_source(session_source, roots)
    flattened = flatten_user_references(raw, roots=roots)
    resolution = resolve_session_with_assets(
        raw,
        catalog_roots=roots,
        source_context=SourceContext(origin="builder_world"),
    )
    resolved = resolution.resolved
    return BuilderSaveArtifact(
        document_yaml=_canonical_session_yaml(raw),
        artifact_sha256=hashlib.sha256(
            _canonical_session_yaml(flattened).encode("utf-8")
        ).hexdigest(),
        session_name=resolved.session.name,
        node_count=len(resolved.nodes),
    )


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
    return _world_from_raw(_load_session_source(session_source, roots), roots)


def _load_session_source(
    session_source: str | dict[str, Any], roots: CatalogRoots
) -> dict[str, Any]:
    if isinstance(session_source, str):
        path = resolve_catalog_reference(session_source, roots, label="builder session")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        raw = session_source
    if not raw:
        raise ValueError("builder session source is empty")
    return raw


def _world_from_raw(raw: dict[str, Any], roots: CatalogRoots) -> BuilderWorld:
    resolution = resolve_session_with_assets(
        raw,
        catalog_roots=roots,
        source_context=SourceContext(origin="builder_world"),
    )
    return _world_from_resolution(resolution, roots, raw)


def _world_from_resolution(
    resolution: SessionResolution, roots: CatalogRoots, raw: dict[str, Any]
) -> BuilderWorld:
    resolved = resolution.resolved
    catalog_session = resolution.catalog_session
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

    local_to_runtime = {
        (node.segment_id, node.local_node_id): node.node_id for node in resolved.nodes
    }
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
    # The world tree speaks the user's names: segment display names read by
    # the resolver from the authored sources, ordered as authored. Runtime
    # ids stay the identity; the name is presentation.
    names = segment_display_names(raw, roots=roots)
    seen_segments: list[str] = []
    for node in resolved.nodes:
        if node.segment_id not in seen_segments:
            seen_segments.append(node.segment_id)
    return BuilderWorld(
        session=resolved.session,
        epoch_unix=epoch_unix,
        ephemeris=ephemeris,
        nodes=nodes,
        link_rules=tuple(
            _builder_link_rule(rule, local_to_runtime) for rule in resolved.link_rules
        ),
        segments=tuple(
            BuilderWorldSegment(segment_id=seg, display_name=names.get(seg, seg))
            for seg in seen_segments
        ),
        # Capacity truth is computed once, by the allocator, and shipped:
        # displays report what allocation did, never re-derive what it might.
        allocations=tuple(link_rule_interface_facts(resolved, catalog_session)),
        link_candidates=tuple(
            BuilderLinkCandidate(
                rule_id=candidate.rule_id,
                node_a=candidate.node_a,
                node_b=candidate.node_b,
            )
            for candidate in resolved.link_candidates
        ),
    )
