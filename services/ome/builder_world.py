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
from datetime import UTC, datetime
from typing import Any

import yaml
from nodalarc.catalog_browse import flatten_user_references, rehydrate_user_references
from nodalarc.catalog_paths import CatalogRoots, resolve_catalog_reference
from nodalarc.ephemeris_runtime import body_states_at, session_epoch_unix
from nodalarc.models.builder_world import (
    BuilderLinkCandidate,
    BuilderLinkEndpoint,
    BuilderLinkRule,
    BuilderPreviewPair,
    BuilderPreviewReasonCount,
    BuilderResolveCheck,
    BuilderRulePreview,
    BuilderSaveArtifact,
    BuilderWorld,
    BuilderWorldNode,
    BuilderWorldSegment,
)
from nodalarc.models.events import SessionEphemeris
from nodalarc.models.resolved_session import ResolvedLinkRule, ResolvedNode, ResolvedSession
from nodalarc.ome_inputs import build_ome_inputs_from_resolved, resolved_body_frames_at_epoch
from nodalarc.resolve_session import (
    SessionResolution,
    SessionResolutionError,
    SourceContext,
    default_catalog_roots,
    link_rule_interface_facts,
    resolve_session_with_assets,
    segment_display_names,
)
from nodalarc.session_validator import validate_session_readiness

from ome.event_stream import build_session_ephemeris, build_step_context
from ome.propagation_engine import propagate_satellites
from ome.visibility import check_ground_visibility, check_isl_visibility


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


# --- Rule preview: server-computed frozen-epoch visibility (P5a) -------------
#
# The builder renders these facts; it never runs a second physics engine. Each
# rule's geometry is computed through the SAME OME visibility composites the
# runtime uses, at the resolved epoch (dt=0), with the motion-only gates
# (tracking rate, polar seam) disabled — a preview is one frozen instant, not a
# simulation. The universe is bounded and the report is honest about the bound.

#: Deterministic per-rule cap on how many pairs geometry runs over. A preview
#: PREVIEWS; OME simulates. Beyond this the preview is an honest partial.
_PREVIEW_TESTED_BUDGET = 4000
#: Cap on drawn pairs shipped per rule — a dense-enough sample to read the
#: pattern, never the runtime-scale full set.
_PREVIEW_DRAW_CAP = 800


def _preview_body_of(node: ResolvedNode) -> str | None:
    """The body a node sits on — ``central_body`` for satellites,
    ``reference_body`` for ground; a relay may carry either."""
    if node.kind == "satellite":
        return node.central_body
    if node.kind == "ground_station":
        return node.reference_body
    return node.reference_body or node.central_body


def _preview_scope(rule: ResolvedLinkRule, node_by_id: dict[str, ResolvedNode]) -> str:
    """Whether a rule's geometry is computed, and if not, the typed wall —
    decided from body/kind/enabled facts alone, no OME. A disabled rule ships no
    geometry; a rule with no satellite endpoint is static-terrestrial (the
    runtime does not visibility-compute it); a rule whose endpoint members
    straddle bodies is inter-body (same-body composites cannot judge it)."""
    if not rule.enabled:
        return "disabled"
    members = [
        node_by_id[nid]
        for endpoint in rule.endpoints
        for nid in endpoint.node_ids
        if nid in node_by_id
    ]
    if not any(node.kind == "satellite" for node in members):
        return "terrestrial_pending"
    bodies = {b for b in (_preview_body_of(node) for node in members) if b is not None}
    if len(bodies) > 1:
        return "inter_body_pending"
    return "computed"


def _closed_pair_count(members_a: tuple[str, ...], members_b: tuple[str, ...]) -> int:
    """The deduped canonical unordered pair count ``|A||B| - I - C(I,2)`` (I =
    |A∩B|) — by FORMULA, never by materializing the pair set. That O(N^2) build
    on the debounced edit loop is exactly the runtime burden a preview must not
    carry."""
    a = set(members_a)
    b = set(members_b)
    inter = len(a & b)
    return len(a) * len(b) - inter - inter * (inter - 1) // 2


def _canonical_pairs(members_a: tuple[str, ...], members_b: tuple[str, ...]):
    """Lazily yield the endpoint cross product as canonical unordered pairs
    (a==b skipped, each unordered pair once), oriented (endpoint-0 member,
    endpoint-1 member), in authored member order. A generator, so the tested
    budget stops it without building the whole universe."""
    seen: set[tuple[str, str]] = set()
    for na in members_a:
        for nb in members_b:
            if na == nb:
                continue
            key = (na, nb) if na <= nb else (nb, na)
            if key in seen:
                continue
            seen.add(key)
            yield na, nb


def _isl_limits(
    node_a: str,
    node_b: str,
    candidate: Any,
    ctx: Any,
    rule: ResolvedLinkRule,
) -> tuple[float | None, float]:
    """``(max_range_km, field_of_regard_deg)`` for an ISL pair, min-reduced
    across the two endpoints' terminals — the reduction the runtime and the
    coverage preview both apply. A fixed pair uses the terminals the allocator
    assigned (its interfaces); a visible pair, which has no assignment, reduces
    across each node's ISL terminals. FoR defaults to 360 (gate off) when no
    terminal limit is known.

    Each node is keyed to ITS OWN allocated interface by node id (never by
    argument position), so orientation can never re-pair a node with the other
    node's interface."""
    constraints = ctx.sat_isl_terminal_constraints
    node_iface: dict[str, str] = {}
    if candidate is not None:
        node_iface[candidate.node_a] = candidate.interface_a
        node_iface[candidate.node_b] = candidate.interface_b

    def _node_limit(node_id: str) -> tuple[float, float] | None:
        by_iface = constraints.get(node_id) or {}
        interface = node_iface.get(node_id)
        if interface is not None and interface in by_iface:
            term = by_iface[interface]
            return float(term.max_range_km), float(term.field_of_regard_deg)
        if not by_iface:
            return None
        return (
            min(float(t.max_range_km) for t in by_iface.values()),
            min(float(t.field_of_regard_deg) for t in by_iface.values()),
        )

    la = _node_limit(node_a)
    lb = _node_limit(node_b)
    if la is None or lb is None:
        rng = rule.constraints.max_range_km if rule.constraints else None
        return (float(rng) if rng is not None else None), 360.0
    return min(la[0], lb[0]), min(la[1], lb[1])


def _isl_terminal_type(node_id: str, candidate: Any, ctx: Any) -> str | None:
    """The allocated ISL terminal's type for a node in a FIXED pair, resolved by
    node identity — or None for a visible pair (no allocation) or a missing
    lookup. Keyed by identity so orientation can never cross the wires."""
    if candidate is None:
        return None
    if node_id == getattr(candidate, "node_a", None):
        interface = candidate.interface_a
    elif node_id == getattr(candidate, "node_b", None):
        interface = candidate.interface_b
    else:
        return None
    term = (ctx.sat_isl_terminal_constraints.get(node_id) or {}).get(interface)
    return term.terminal_type if term is not None else None


def _isl_verdict(
    node_a: str,
    node_b: str,
    candidate: Any,
    sat_states: dict[str, Any],
    ctx: Any,
    rule: ResolvedLinkRule,
    central_body: str,
) -> str:
    """The frozen-epoch ISL verdict — LOS, range, and field-of-regard via the
    visibility composite, plus the one allocator-layer gate the runtime applies
    before geometry: terminal-type compatibility. The runtime
    (evaluate_isl_feasibility) refuses to bring up a pair between incompatible
    ISL terminal types, so the preview reports terminal_type_mismatch and draws
    no line rather than a false-positive candidate. (terminal_role_mismatch and
    the motion gates stay out — role compatibility is enforced upstream by the
    allocated interfaces, and the motion gates are time semantics a frozen epoch
    does not judge.)"""
    state_a = sat_states.get(node_a)
    state_b = sat_states.get(node_b)
    if state_a is None or state_b is None:
        return "no_geometry"
    body_frame = ctx.body_frames.get(central_body)
    if body_frame is None:
        return "no_geometry"
    type_a = _isl_terminal_type(node_a, candidate, ctx)
    type_b = _isl_terminal_type(node_b, candidate, ctx)
    if type_a is not None and type_b is not None and type_a != type_b:
        return "terminal_type_mismatch"
    max_range, fov = _isl_limits(node_a, node_b, candidate, ctx, rule)
    if max_range is None:
        return "no_geometry"
    result = check_isl_visibility(
        state_a.position_ecef_km,
        state_a.velocity_ecef_km_s,
        state_b.position_ecef_km,
        state_b.velocity_ecef_km_s,
        max_range_km=max_range,
        body_frame=body_frame,
        max_tracking_rate_deg_s=None,  # motion gate — off for a frozen epoch
        field_of_regard_deg=fov,
        polar_seam_enabled=False,  # motion gate — off for a frozen epoch
        geo_a=state_a.geodetic,
        geo_b=state_b.geodetic,
    )
    return "ok" if result.visible else result.reason


def _ground_verdict(
    gs_node: ResolvedNode,
    sat_state: Any,
    ctx: Any,
    gs_min_elev: dict[str, float],
) -> str:
    if sat_state is None:
        return "no_geometry"
    gs_pos = ctx.gs_positions.get(gs_node.node_id)
    if gs_pos is None:
        return "no_geometry"
    gs_ecef, gs_geo = gs_pos
    reference_body = ctx.gs_reference_bodies.get(gs_node.node_id)
    body_frame = ctx.body_frames.get(reference_body) if reference_body else None
    if body_frame is None:
        return "no_geometry"
    # The effective cross-rule mask the runtime enforces at this GS — never the
    # rule's own endpoint mask (geometry_only carries no boresight/FoR/range).
    min_elev = gs_min_elev.get(gs_node.node_id, 25.0)
    gv = check_ground_visibility(
        gs_ecef,
        gs_geo,
        sat_state.position_ecef_km,
        min_elev,
        body_frame=body_frame,
    )
    return "ok" if gv.visible else gv.reject_reason


def _pair_verdict(
    node_a: str,
    node_b: str,
    candidate: Any,
    node_by_id: dict[str, ResolvedNode],
    sat_states: dict[str, Any],
    ctx: Any,
    rule: ResolvedLinkRule,
    gs_min_elev: dict[str, float],
) -> str:
    left = node_by_id.get(node_a)
    right = node_by_id.get(node_b)
    if left is None or right is None:
        return "no_geometry"
    a_sat = left.kind == "satellite"
    b_sat = right.kind == "satellite"
    if a_sat and b_sat:
        return _isl_verdict(node_a, node_b, candidate, sat_states, ctx, rule, left.central_body)
    if a_sat != b_sat:
        gs_node = right if a_sat else left
        sat_id = node_a if a_sat else node_b
        return _ground_verdict(gs_node, sat_states.get(sat_id), ctx, gs_min_elev)
    # Ground-ground inside a computed rule: no visibility to compute.
    return "no_geometry"


def _computed_preview(
    rule: ResolvedLinkRule,
    node_by_id: dict[str, ResolvedNode],
    sat_states: dict[str, Any],
    ctx: Any,
    gs_min_elev: dict[str, float],
    fixed: dict[str, list[Any]],
) -> BuilderRulePreview:
    mode = rule.topology.mode
    if mode in ("explicit_pairs", "nearest_n"):
        candidates = fixed.get(rule.rule_id, [])
        pairs_total = len(candidates)
        pair_iter: Any = ((c.node_a, c.node_b, c) for c in candidates)
        fixed_pairs = True
    elif mode == "visible_candidates":
        members_a = rule.endpoints[0].node_ids
        members_b = rule.endpoints[1].node_ids
        pairs_total = _closed_pair_count(members_a, members_b)
        pair_iter = ((na, nb, None) for na, nb in _canonical_pairs(members_a, members_b))
        fixed_pairs = False
    else:
        # nearest_visible is rejected upstream; nothing else is computable.
        pairs_total = 0
        pair_iter = iter(())
        fixed_pairs = False

    tested = min(pairs_total, _PREVIEW_TESTED_BUDGET)
    ep0 = set(rule.endpoints[0].node_ids)
    ep1 = set(rule.endpoints[1].node_ids)
    reason_counts: dict[str, int] = {}
    drawn: list[BuilderPreviewPair] = []
    passing = 0
    for index, (na, nb, candidate) in enumerate(pair_iter):
        if index >= tested:
            break
        # The verdict is computed on the pair's OWN node order — for a fixed
        # pair, node_a carries interface_a and node_b carries interface_b, so
        # each node is judged against its own allocated terminal. Orientation is
        # a DISPLAY concern only (which endpoint each node belongs to); swapping
        # nodes for the drawable must never re-pair a node with the other's
        # interface.
        display_a, display_b = na, nb
        if fixed_pairs:
            forward = na in ep0 and nb in ep1
            reverse = na in ep1 and nb in ep0
            if not (forward or reverse):
                # An allocated pair matching neither endpoint orientation is an
                # engine inconsistency, not an authoring fact — fail closed on
                # the rule's wall channel rather than normalize it to zero lines.
                raise SessionResolutionError(
                    f"link rule {rule.rule_id!r}: allocated pair ({na}, {nb}) "
                    "matches neither endpoint orientation",
                    subject_kind="link",
                    subject_id=rule.rule_id,
                )
            if not forward:
                display_a, display_b = nb, na
        verdict = _pair_verdict(na, nb, candidate, node_by_id, sat_states, ctx, rule, gs_min_elev)
        if verdict == "ok":
            passing += 1
            if len(drawn) < _PREVIEW_DRAW_CAP:
                drawn.append(
                    BuilderPreviewPair(
                        rule_id=rule.rule_id,
                        kind=rule.kind,
                        node_a=display_a,
                        node_b=display_b,
                    )
                )
        else:
            reason_counts[verdict] = reason_counts.get(verdict, 0) + 1

    capped = tested < pairs_total or passing > _PREVIEW_DRAW_CAP
    return BuilderRulePreview(
        rule_id=rule.rule_id,
        kind=rule.kind,
        preview_scope="computed",
        pairs_total=pairs_total,
        pairs_tested=tested,
        pairs_drawn=len(drawn),
        capped=capped,
        reason_counts=tuple(
            BuilderPreviewReasonCount(reason=reason, count=count)
            for reason, count in sorted(reason_counts.items())
        ),
        drawable_pairs=tuple(drawn),
    )


def _builder_rule_previews(
    resolved: ResolvedSession,
    ctx: Any,
    epoch_unix: float,
) -> tuple[BuilderRulePreview, ...]:
    """Frozen-epoch visibility verdicts for every rule — the server's preview,
    computed once per resolve through the runtime's own composites."""
    node_by_id = {node.node_id: node for node in resolved.nodes}
    scopes = {rule.rule_id: _preview_scope(rule, node_by_id) for rule in resolved.link_rules}

    def _scope_only(rule: ResolvedLinkRule) -> BuilderRulePreview:
        return BuilderRulePreview(
            rule_id=rule.rule_id,
            kind=rule.kind,
            preview_scope=scopes[rule.rule_id],
            pairs_total=0,
            pairs_tested=0,
            pairs_drawn=0,
            capped=False,
        )

    # No rule needs geometry — skip the propagation entirely.
    if not any(scope == "computed" for scope in scopes.values()):
        return tuple(_scope_only(rule) for rule in resolved.link_rules)

    # One propagation at the resolved epoch (dt=0) — the exact runtime state.
    body_states = body_states_at(ctx.body_ephemeris, set(ctx.active_bodies), epoch_unix)
    sat_states = propagate_satellites(
        satellites=ctx.satellites,
        addressing=ctx.addressing,
        epoch_unix=epoch_unix,
        dt=0.0,
        propagator_id=ctx.propagator_id,
        body_states=body_states,
        body_frames=ctx.body_frames,
    )
    gs_min_elev = resolved.effective_ground_min_elevation_by_gs()
    fixed: dict[str, list[Any]] = {}
    for candidate in resolved.link_candidates:
        fixed.setdefault(candidate.rule_id, []).append(candidate)

    previews: list[BuilderRulePreview] = []
    for rule in resolved.link_rules:
        if scopes[rule.rule_id] == "computed":
            previews.append(
                _computed_preview(rule, node_by_id, sat_states, ctx, gs_min_elev, fixed)
            )
        else:
            previews.append(
                BuilderRulePreview(
                    rule_id=rule.rule_id,
                    kind=rule.kind,
                    preview_scope=scopes[rule.rule_id],
                    pairs_total=0,
                    pairs_tested=0,
                    pairs_drawn=0,
                    capped=False,
                )
            )
    return tuple(previews)


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


def _satellite_less_world_view(
    resolved: ResolvedSession, epoch_unix: float
) -> tuple[SessionEphemeris, tuple[BuilderRulePreview, ...]]:
    """The ephemeris and rule previews for a grammar-valid session with no
    satellite — the OME preview precondition is a runtime-readiness gate, not a
    grammar rule, so the session RESOLVES and RENDERS rather than walling.

    The ephemeris carries no node ephemerides (there is nothing to propagate and
    OME cannot be built) but POPULATED body frames, so render scale still anchors
    on a body. Every rule is accurately terrestrial/inter_body_pending — with no
    space segment there is nothing to visibility-compute, and a per-rule "add a
    satellite" note would be a false state for a static terrestrial link."""
    ephemeris = SessionEphemeris(
        epoch_id=0,
        sim_time=datetime.fromtimestamp(epoch_unix, tz=UTC),
        epoch_unix=epoch_unix,
        nodes={},
        body_frames=resolved_body_frames_at_epoch(resolved, epoch_unix),
    )
    node_by_id = {node.node_id: node for node in resolved.nodes}
    previews = tuple(
        BuilderRulePreview(
            rule_id=rule.rule_id,
            kind=rule.kind,
            preview_scope=_preview_scope(rule, node_by_id),
            pairs_total=0,
            pairs_tested=0,
            pairs_drawn=0,
            capped=False,
        )
        for rule in resolved.link_rules
    )
    return ephemeris, previews


def _world_from_resolution(
    resolution: SessionResolution, roots: CatalogRoots, raw: dict[str, Any]
) -> BuilderWorld:
    resolved = resolution.resolved
    catalog_session = resolution.catalog_session
    epoch_unix = session_epoch_unix(resolved.time)

    # A satellite-less session cannot build OME inputs (the runtime requires a
    # satellite), but it is valid grammar that resolves and renders. Guard the
    # OME chain on satellite presence; the rest of the world is identical.
    if any(node.kind == "satellite" for node in resolved.nodes):
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
        rule_previews = _builder_rule_previews(resolved, ctx, epoch_unix)
    else:
        ephemeris, rule_previews = _satellite_less_world_view(resolved, epoch_unix)

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
        rule_previews=rule_previews,
    )
