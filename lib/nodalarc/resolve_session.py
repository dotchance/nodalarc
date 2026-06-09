# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog session resolver.

This module is the single authority that turns the catalog configuration
language into immutable runtime truth. It rejects retired session shapes and
does not project catalog sessions back into the old session model.
"""

from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from nodalarc.catalog_paths import CatalogRoots, resolve_catalog_reference
from nodalarc.ephemeris_runtime import (
    EphemerisValidationError,
    runtime_config_from_resolved,
    session_epoch_unix,
    validate_ephemeris_manifest,
)
from nodalarc.link_rule_candidates import generate_declared_link_candidates
from nodalarc.models.catalog import validate_catalog_document, validate_catalog_value
from nodalarc.models.identity import IdentityMode
from nodalarc.models.link_rules import LinkRule, NodeSelector, TerminalSelector
from nodalarc.models.resolved_session import (
    ResolvedBodyFacts,
    ResolvedEndpoint,
    ResolvedEphemeris,
    ResolvedEphemerisKernel,
    ResolvedInterfaceAddress,
    ResolvedLinkCandidate,
    ResolvedLinkRule,
    ResolvedNode,
    ResolvedNodeInterfaces,
    ResolvedOrbitFacts,
    ResolvedRoutingDomain,
    ResolvedSession,
    ResolvedSurfacePosition,
    ResolvedTerminalBlock,
    ResolvedWanInterface,
    SidBlock,
    SourceContext,
)
from nodalarc.models.segment_session import Dispatch, RoutingTimers, SegmentSessionConfig
from nodalarc.models.segments import GroundOverride, GroundSegment, SegmentClock, SpaceSegment
from nodalarc.runtime_naming import validate_runtime_node_id
from nodalarc.runtime_support import (
    FeatureCategory,
    RuntimeSupport,
    UnsupportedFeature,
    UnsupportedFeatureError,
)

_NORMALIZE_RE = re.compile(r"[^a-z0-9-]+")
_DEFAULT_GENERATED_SPACE_LOOPBACK_IPV4_POOL = ipaddress.ip_network("100.64.0.0/10")
_DEFAULT_GENERATED_SPACE_LOOPBACK_IPV6_POOL = ipaddress.ip_network("fd00:6e0::/64")


class SessionResolutionError(ValueError):
    """Raised when a catalog session is structurally valid but semantically invalid."""


@dataclass(frozen=True)
class SessionResolution:
    """Resolved catalog session plus the parsed catalog root object."""

    resolved: ResolvedSession
    catalog_session: SegmentSessionConfig


@dataclass(frozen=True)
class _RuntimeNode:
    node: ResolvedNode
    plane: int | None = None
    slot: int | None = None
    body_facts: tuple[ResolvedBodyFacts, ...] = ()


def default_catalog_roots() -> CatalogRoots:
    return CatalogRoots.from_catalog_root("catalog/nodalarc")


def resolve_session(
    raw_session: dict[str, Any],
    *,
    catalog_roots: CatalogRoots | None = None,
    runtime_support: RuntimeSupport | None = None,
    source_context: SourceContext | None = None,
) -> ResolvedSession:
    return resolve_session_with_assets(
        raw_session,
        catalog_roots=catalog_roots,
        runtime_support=runtime_support,
        source_context=source_context,
    ).resolved


def resolve_session_with_assets(
    raw_session: dict[str, Any],
    *,
    catalog_roots: CatalogRoots | None = None,
    runtime_support: RuntimeSupport | None = None,
    source_context: SourceContext | None = None,
) -> SessionResolution:
    if not isinstance(raw_session, dict):
        raise SessionResolutionError("session YAML must parse to a mapping")
    if "segments" not in raw_session:
        raise SessionResolutionError("catalog session YAML requires top-level segments")
    for retired in ("constellation", "ground_stations", "satellite_type"):
        if retired in raw_session:
            raise SessionResolutionError(
                f"retired top-level session key is not supported: {retired}"
            )

    if source_context is not None and not isinstance(source_context, SourceContext):
        raise SessionResolutionError("source_context must be a SourceContext instance")

    roots = catalog_roots or default_catalog_roots()
    context = source_context or SourceContext(origin="resolve_session")
    try:
        cfg = SegmentSessionConfig.model_validate(raw_session)
    except ValidationError:
        raise
    except Exception as exc:
        raise SessionResolutionError(f"invalid catalog session: {exc}") from exc

    # The runtime-support gate is mandatory. Production always runs the
    # Earth-Luna profile; callers may only widen/narrow it explicitly. A None
    # here must never mean "skip the typed UnsupportedFeature layer".
    support = runtime_support or RuntimeSupport.earth_luna()
    _check_runtime_support(cfg, support, roots)

    runtime_nodes = _apply_addressing(cfg, _expand_segments(cfg, roots))
    resolved_nodes = tuple(item.node for item in runtime_nodes)
    _validate_allocator_wide_scheduling(resolved_nodes)
    _validate_orbit_default_propagator(cfg, resolved_nodes)
    body_facts = _collect_body_facts(runtime_nodes)
    _check_body_support(resolved_nodes, body_facts, support)
    ephemeris = _resolve_ephemeris(cfg, roots, resolved_nodes)
    link_rules = tuple(_resolve_link_rule(rule, runtime_nodes) for rule in cfg.link_rules or ())
    routing_domains = tuple(_resolve_routing_domains(cfg, runtime_nodes))
    _validate_routing_boundaries(cfg, routing_domains, link_rules)
    sid_blocks = tuple(_allocate_sid_blocks(routing_domains))
    dispatch = _resolve_dispatch(cfg)

    base_resolved = ResolvedSession(
        identity_mode=IdentityMode.SEGMENT_NAMESPACED,
        session=cfg.session,
        nodes=resolved_nodes,
        bodies=body_facts,
        link_rules=link_rules,
        routing_domains=routing_domains,
        sid_blocks=sid_blocks,
        simulation=cfg.simulation,
        routing=cfg.routing,
        dispatch=dispatch,
        addressing=cfg.addressing,
        ephemeris=ephemeris,
        time=cfg.time,
        source_context=context,
    )
    _enforce_declared_candidate_bounds(cfg, base_resolved)
    _validate_access_terminal_bindings(cfg, base_resolved)
    candidates = tuple(_resolve_link_candidates(base_resolved, cfg))
    _enforce_link_rule_constraints(base_resolved, candidates)
    resolved = ResolvedSession(
        identity_mode=base_resolved.identity_mode,
        session=base_resolved.session,
        nodes=base_resolved.nodes,
        bodies=base_resolved.bodies,
        link_rules=base_resolved.link_rules,
        link_candidates=candidates,
        routing_domains=base_resolved.routing_domains,
        sid_blocks=base_resolved.sid_blocks,
        simulation=base_resolved.simulation,
        routing=base_resolved.routing,
        dispatch=dispatch,
        addressing=base_resolved.addressing,
        ephemeris=base_resolved.ephemeris,
        time=base_resolved.time,
        source_context=base_resolved.source_context,
    )
    return SessionResolution(resolved=resolved, catalog_session=cfg)


def _resolve_dispatch(cfg: SegmentSessionConfig) -> Dispatch:
    if cfg.dispatch is not None:
        return cfg.dispatch
    return Dispatch(latency_authority="ome", max_latency_age_ticks=3)


def load_session_resolution_from_file(
    session_path: str | Path,
    *,
    catalog_roots: CatalogRoots | None = None,
    runtime_support: RuntimeSupport | None = None,
    origin: str = "file",
    run_id: str | None = None,
) -> SessionResolution:
    path = Path(session_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return resolve_session_with_assets(
        raw,
        catalog_roots=catalog_roots,
        runtime_support=runtime_support,
        source_context=SourceContext(origin=origin, session_path=str(path), run_id=run_id),
    )


def _check_runtime_support(
    cfg: SegmentSessionConfig, support: RuntimeSupport, roots: CatalogRoots
) -> None:
    unsupported = []
    for segment in cfg.segments:
        if isinstance(segment, GroundSegment):
            kind = "ground_set"
        elif isinstance(segment, SpaceSegment):
            # The segment class does not identify the source wrapper: a
            # SpaceSegment can carry a constellation, a single space_node, or
            # a space_node_set, and each is a distinct supported feature. The
            # gate must key on the loaded wrapper, not the segment class.
            wrapper, _ = _load_ref_or_object(segment.source, roots)
            kind = wrapper
        else:
            kind = "lagrange_point"
        if feature := support.check_segment_kind(kind):
            unsupported.append(feature)
    if cfg.ephemeris is not None and (
        feature := support.check_ephemeris_provider(cfg.ephemeris.provider)
    ):
        unsupported.append(feature)
    for domain in cfg.routing.domains if cfg.routing is not None else ():
        if feature := support.check_routing_protocol(domain.protocol):
            unsupported.append(feature)
    if cfg.addressing is not None:
        for pool_class in ("point_to_point", "terrestrial_prefixes"):
            if getattr(cfg.addressing, pool_class) and (
                feature := support.check_addressing_pool(pool_class)
            ):
                unsupported.append(feature)
        for assignment in cfg.addressing.loopbacks or ():
            if assignment.allocation not in (None, "by_node_order"):
                unsupported.append(
                    support._unsupported(
                        FeatureCategory.ADDRESSING_POOL,
                        f"allocation:{assignment.allocation}",
                        "address pool allocation strategy",
                    )
                )
    for segment in cfg.segments:
        clock = getattr(segment, "clock", None)
        if clock is not None and (feature := support.check_clock_model(clock.model)):
            unsupported.append(feature)
    for boundary in (
        cfg.routing.boundaries if cfg.routing is not None and cfg.routing.boundaries else ()
    ):
        if feature := support.check_protocol_adapter(boundary.adapter):
            unsupported.append(feature)
    if unsupported:
        raise UnsupportedFeatureError(unsupported)


def _check_body_support(
    nodes: tuple[ResolvedNode, ...],
    body_facts: tuple[ResolvedBodyFacts, ...],
    support: RuntimeSupport,
) -> None:
    """Gate resolved body usage against the runtime-support profile.

    Body names are also constrained by model Literals, but the support profile
    is the single typed authority for what the *selected* runtime implements —
    an Earth-only profile must reject a Luna session with a typed reason, not
    rely on schema width.
    """
    unsupported = []
    seen: set[tuple[str, str]] = set()

    def _add(feature: UnsupportedFeature | None) -> None:
        if feature is not None and (feature.category, feature.value) not in seen:
            seen.add((feature.category, feature.value))
            unsupported.append(feature)

    for node in nodes:
        if node.central_body is not None:
            _add(support.check_central_body(node.central_body))
        if node.reference_body is not None:
            _add(support.check_reference_body(node.reference_body))
    for facts in body_facts:
        _add(support.check_frame_body(facts.body_id))
    if unsupported:
        raise UnsupportedFeatureError(unsupported)


def _active_bodies(nodes: tuple[ResolvedNode, ...]) -> set[str]:
    active = {
        body
        for node in nodes
        for body in (node.central_body, node.reference_body)
        if body is not None
    }
    if not active:
        raise SessionResolutionError("resolved session contains no active body references")
    return active


def _body_facts_from_catalog(body: dict[str, Any]) -> ResolvedBodyFacts:
    return ResolvedBodyFacts(
        body_id=body["id"],
        display_name=body["display_name"],
        gravitational_parameter_km3_s2=float(body["gravitational_parameter_km3_s2"]),
        mean_radius_km=float(body["mean_radius_km"]),
        equatorial_radius_km=float(body["equatorial_radius_km"]),
        polar_radius_km=float(body["polar_radius_km"]),
        reference=body["reference"],
    )


def _collect_body_facts(runtime_nodes: tuple[_RuntimeNode, ...]) -> tuple[ResolvedBodyFacts, ...]:
    by_id: dict[str, ResolvedBodyFacts] = {}
    for item in runtime_nodes:
        for facts in item.body_facts:
            existing = by_id.get(facts.body_id)
            if existing is not None and existing != facts:
                raise SessionResolutionError(
                    f"body primitive {facts.body_id!r} resolves to conflicting physical facts"
                )
            by_id[facts.body_id] = facts
    if not by_id:
        raise SessionResolutionError("resolved session contains no body primitive facts")
    return tuple(by_id[body_id] for body_id in sorted(by_id))


def _resolve_ephemeris(
    cfg: SegmentSessionConfig,
    roots: CatalogRoots,
    nodes: tuple[ResolvedNode, ...],
) -> ResolvedEphemeris | None:
    active_bodies = _active_bodies(nodes)
    if cfg.ephemeris is None:
        missing = sorted(active_bodies - {"earth"})
        if missing:
            raise SessionResolutionError(
                "non-Earth session declares no ephemeris manifest for active body target(s): "
                + ", ".join(missing)
            )
        return None

    kernels: list[ResolvedEphemerisKernel] = []
    manifest_targets: set[str] = {"earth"}
    for kernel in cfg.ephemeris.kernels:
        targets = tuple(
            sorted({_ephemeris_target_body_id(target, roots) for target in kernel.targets})
        )
        manifest_targets.update(targets)
        kernels.append(
            ResolvedEphemerisKernel(
                id=kernel.id,
                path=kernel.path,
                sha256=kernel.sha256,
                targets=targets,
                frame=kernel.frame,
                coverage_start=kernel.coverage_start,
                coverage_end=kernel.coverage_end,
            )
        )

    missing_targets = sorted(active_bodies - manifest_targets)
    if missing_targets:
        raise SessionResolutionError(
            "ephemeris manifest is missing required body target(s): " + ", ".join(missing_targets)
        )

    resolved_ephemeris = ResolvedEphemeris(
        provider=cfg.ephemeris.provider,
        quality_tier=cfg.ephemeris.quality_tier,
        kernels=tuple(kernels),
    )

    # Manifest runtime validation happens at resolve time, not service
    # startup: kernel existence, sha256, declared coverage vs the session
    # epoch, and the single-kernel limit. A session whose ephemeris cannot
    # support it must fail at upload/deploy, not kill a pod later.
    try:
        epoch_unix = session_epoch_unix(cfg.time)
        validate_ephemeris_manifest(
            runtime_config_from_resolved(resolved_ephemeris),
            required_bodies=active_bodies - {"earth"},
            epoch_unix=epoch_unix,
        )
    except EphemerisValidationError as exc:
        raise SessionResolutionError(f"ephemeris manifest validation failed: {exc}") from exc

    return resolved_ephemeris


def _ephemeris_target_body_id(target: Any, roots: CatalogRoots) -> str:
    body = _load_expected(target, roots, "body")
    return str(body["id"])


@dataclass
class _SitePlacement:
    """One physical site and every ground segment (placement group) placing it.

    A site is a place, and a place exists once: placing a site in a segment
    enrolls it under that group label, it never mints a second copy of the
    site's routers. Group order is first-placement order.
    """

    site: dict[str, Any]
    segments: list[GroundSegment]


@dataclass(frozen=True)
class _SiteMarker:
    site_id: str


def _expand_segments(cfg: SegmentSessionConfig, roots: CatalogRoots) -> tuple[_RuntimeNode, ...]:
    ordered: list[_RuntimeNode | _SiteMarker] = []
    placements: dict[str, _SitePlacement] = {}
    for segment in cfg.segments:
        if isinstance(segment, SpaceSegment):
            ordered.extend(_expand_space_segment(segment, roots))
        elif isinstance(segment, GroundSegment):
            site_set = _load_expected(segment.placement.from_site_set, roots, "site_set")
            for site_ref in site_set["sites"]:
                site = _load_expected(site_ref, roots, "site")
                site_id = site["id"]
                placement = placements.get(site_id)
                if placement is None:
                    placements[site_id] = _SitePlacement(site=site, segments=[segment])
                    ordered.append(_SiteMarker(site_id))
                elif segment.id not in {s.id for s in placement.segments}:
                    placement.segments.append(segment)
        else:
            raise SessionResolutionError(
                f"segment {segment.id!r} uses runtime-unsupported lagrange placement"
            )
    nodes: list[_RuntimeNode] = []
    for entry in ordered:
        if isinstance(entry, _SiteMarker):
            nodes.extend(_expand_site_placement(placements[entry.site_id], roots))
        else:
            nodes.append(entry)
    if not nodes:
        raise SessionResolutionError("session resolves to zero runtime nodes")
    return tuple(nodes)


def _expand_space_segment(segment: SpaceSegment, roots: CatalogRoots) -> list[_RuntimeNode]:
    wrapper, source = _load_ref_or_object(segment.source, roots)
    if wrapper == "constellation":
        return _expand_constellation_segment(segment, source, roots)
    if wrapper == "space_node_set":
        expanded = []
        for entry in source["nodes"]:
            expanded.append(_space_node_from_entry(segment, entry, roots))
        return expanded
    if wrapper == "space_node":
        return [_space_node_from_entry(segment, source, roots)]
    raise SessionResolutionError(
        f"space segment {segment.id!r} source must be constellation, space_node, or space_node_set; got {wrapper!r}"
    )


def _expand_constellation_segment(
    segment: SpaceSegment,
    constellation: dict[str, Any],
    roots: CatalogRoots,
) -> list[_RuntimeNode]:
    node = _load_expected(constellation["node"], roots, "node")
    orbit = _load_expected(constellation["orbit"], roots, "orbit")
    body = _load_expected(orbit["central_body"], roots, "body")
    planes = int(constellation["planes"]["count"])
    slots = int(constellation["slots_per_plane"])
    phase_offset = float(constellation["phasing"].get("phase_offset_deg", 0.0))
    tag_rules = tuple(constellation.get("node_tags") or ())
    expanded: list[_RuntimeNode] = []

    for plane in range(planes):
        for slot in range(slots):
            local_id = f"sat-p{plane:02d}s{slot:02d}"
            runtime_id = _runtime_id(segment.id, local_id)
            tags = set(segment.tags or ())
            tags.update(constellation.get("tags") or ())
            tags.update(_node_tags_for(tag_rules, plane=plane, slot=slot, local_id=local_id))
            expanded.append(
                _RuntimeNode(
                    node=_resolved_space_node(
                        runtime_id=runtime_id,
                        local_id=local_id,
                        segment_id=segment.id,
                        source_node=node,
                        body=body,
                        orbit=_orbit_facts(
                            orbit,
                            body,
                            plane=plane,
                            slot=slot,
                            planes=planes,
                            slots_per_plane=slots,
                            raan_spacing_deg=float(constellation["planes"]["raan_spacing_deg"]),
                            phase_offset_deg=phase_offset,
                        ),
                        tags=tuple(sorted(tags)),
                        roots=roots,
                        clock=segment.clock,
                        plane=plane,
                        slot=slot,
                    ),
                    plane=plane,
                    slot=slot,
                    body_facts=(_body_facts_from_catalog(body),),
                )
            )
    return expanded


def _space_node_from_entry(
    segment: SpaceSegment, entry: dict[str, Any], roots: CatalogRoots
) -> _RuntimeNode:
    node = _load_expected(entry["node"], roots, "node")
    orbit = _load_expected(entry["orbit"], roots, "orbit") if "orbit" in entry else None
    if orbit is None:
        raise UnsupportedFeatureError(
            [
                UnsupportedFeature(
                    category=FeatureCategory.SEGMENT_KIND,
                    value="space_node:state_vector",
                    message=(
                        f"space node {entry['id']!r} uses raw state_vector placement; "
                        "the current runtime propagates orbit-element nodes only"
                    ),
                    support_note="future runtime capability",
                )
            ]
        )
    body = _load_expected(orbit["central_body"], roots, "body") if orbit is not None else None
    local_id = entry["id"]
    tags = tuple(sorted({*(segment.tags or ()), *(entry.get("tags") or ())}))
    return _RuntimeNode(
        node=_resolved_space_node(
            runtime_id=_runtime_id(segment.id, local_id),
            local_id=local_id,
            segment_id=segment.id,
            source_node=node,
            body=body,
            orbit=_orbit_facts(
                orbit,
                body,
                plane=None,
                slot=None,
                planes=None,
                slots_per_plane=None,
                raan_spacing_deg=0.0,
                phase_offset_deg=0.0,
            ),
            tags=tags,
            roots=roots,
            clock=segment.clock,
            plane=None,
            slot=None,
        ),
        body_facts=(_body_facts_from_catalog(body),),
    )


_ALLOCATOR_WIDE_SCHEDULING_FIELDS = (
    "ranking_order",
    "mbb_preemption",
    "successor_abort_policy",
    "cross_tenant_displacement",
    "bbm_acquire_timeout_ticks",
)


def _merge_ground_scheduling(
    site_value: dict[str, Any] | None, base: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Field-level merge: a site's scheduling overrides the placing group's
    apply/override values per field; unset fields inherit. A whole-object
    replace silently dropped group policy the site never mentioned."""
    if site_value is None:
        return base
    if base is None:
        return dict(site_value)
    merged = dict(base)
    merged.update({key: value for key, value in site_value.items() if value is not None})
    return merged


def _validate_orbit_default_propagator(
    cfg: SegmentSessionConfig, nodes: tuple[ResolvedNode, ...]
) -> None:
    """orbit.default_propagator is structurally inert — reject it.

    The grammar requires every orbit primitive to declare its propagator, so a
    session-level default can never apply, and one declared value cannot be
    honest for mixed-propagator sessions. A field that can never do anything
    must not validate as if it did.
    """
    if cfg.orbit is None or cfg.orbit.default_propagator is None:
        return
    actual = sorted({node.orbit.propagator for node in nodes if node.orbit is not None})
    raise SessionResolutionError(
        "orbit.default_propagator is inert: orbit primitives always declare their "
        f"propagator (this session resolves {actual}); remove the orbit block"
    )


def _validate_allocator_wide_scheduling(nodes: tuple[ResolvedNode, ...]) -> None:
    """Allocator-wide scheduling knobs must be uniform across every ground
    node at resolve time.

    The OME allocator is a single decision-maker; per-node divergence of these
    fields has no runtime meaning and previously died late with an untyped
    error at OME-input build.
    """
    baseline: dict[str, Any] | None = None
    baseline_node: str | None = None
    for node in nodes:
        if node.kind != "ground_station" or node.ground_scheduling is None:
            continue
        values = {
            field: getattr(node.ground_scheduling, field)
            for field in _ALLOCATOR_WIDE_SCHEDULING_FIELDS
        }
        if baseline is None:
            baseline, baseline_node = values, node.node_id
            continue
        diffs = sorted(field for field in values if values[field] != baseline[field])
        if diffs:
            raise SessionResolutionError(
                f"allocator-wide scheduling fields must be uniform across ground nodes; "
                f"{node.node_id!r} differs from {baseline_node!r} on: {', '.join(diffs)}"
            )


def _site_override_for(segment: GroundSegment, site_id: str) -> GroundOverride | None:
    matches = [override for override in (segment.overrides or ()) if override.match.site == site_id]
    if len(matches) > 1:
        raise SessionResolutionError(
            f"ground segment {segment.id!r} declares {len(matches)} overrides for "
            f"site {site_id!r}; at most one override per site"
        )
    return matches[0] if matches else None


def _effective_site_policy(
    segment: GroundSegment, site_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, tuple[str, ...]]:
    """One placing group's effective (scheduling, originated, tags) for a site.

    GroundOverride is the session author's per-site word and wins over the
    group-level apply. Returned scheduling/prefixes are plain dumps so they
    can be compared across placing groups.
    """
    override = _site_override_for(segment, site_id)
    apply = segment.apply
    scheduling = None
    if override is not None and override.scheduling is not None:
        scheduling = override.scheduling.model_dump(mode="python")
    elif apply is not None and apply.scheduling is not None:
        scheduling = apply.scheduling.model_dump(mode="python")
    originated = None
    if override is not None and override.originated_prefixes is not None:
        originated = override.originated_prefixes.model_dump(mode="python")
    elif apply is not None and apply.originated_prefixes is not None:
        originated = apply.originated_prefixes.model_dump(mode="python")
    tags: list[str] = list(segment.tags or ())
    if apply is not None and apply.tags:
        tags.extend(apply.tags)
    if override is not None and override.tags:
        tags.extend(override.tags)
    return scheduling, originated, tuple(tags)


def _expand_site_placement(placement: _SitePlacement, roots: CatalogRoots) -> list[_RuntimeNode]:
    site = placement.site
    site_id = site["id"]
    groups = tuple(segment.id for segment in placement.segments)

    # Effective per-site policy must agree across every placing group: a site
    # can join several groups, but it cannot run two scheduling policies or
    # originate two different prefix sets. Tags union; everything else is
    # identical-or-reject.
    policies = [_effective_site_policy(segment, site_id) for segment in placement.segments]
    base_scheduling, base_originated, _ = policies[0]
    for index in range(1, len(policies)):
        scheduling, originated, _ = policies[index]
        if scheduling != base_scheduling or originated != base_originated:
            raise SessionResolutionError(
                f"site {site_id!r} is placed by groups {list(groups)!r} with conflicting "
                f"apply/override policy (group {groups[index]!r} differs from {groups[0]!r}); "
                "tags may differ per group, scheduling and originated_prefixes must be identical"
            )
    tags: set[str] = set()
    for _, _, group_tags in policies:
        tags.update(group_tags)
    tags.update(site.get("tags") or ())

    frame = site["frame"]
    if "body_fixed" not in frame:
        frame_kind = next(iter(frame), "<empty>")
        raise UnsupportedFeatureError(
            [
                UnsupportedFeature(
                    category=FeatureCategory.SEGMENT_KIND,
                    value=f"site_frame:{frame_kind}",
                    message=(
                        f"site {site_id!r} uses a {frame_kind!r} frame; placed ground "
                        "nodes require a body_fixed surface frame on the current runtime"
                    ),
                    support_note="future runtime capability",
                )
            ]
        )
    body_ref = frame["body_fixed"]["body"]
    body = _load_expected(body_ref, roots, "body")
    if site.get("location") is None:
        raise SessionResolutionError(
            f"site {site_id!r} is not body-fixed; runtime support requires "
            "a fixed surface location for placed ground nodes"
        )

    expanded: list[_RuntimeNode] = []
    clock = next(
        (segment.clock for segment in placement.segments if segment.clock is not None),
        None,
    )
    for site_node in site["nodes"]:
        source_node = _load_expected(site_node["model"], roots, "node")
        # Ground identity is site-anchored: a node's name never depends on
        # which group(s) placed its site. local_node_id keeps the
        # site-qualified form so `node:` selectors stay unique.
        local_id = f"{site_id}-{site_node['id']}"
        runtime_id = _runtime_id(site_id, site_node["id"])
        _reject_unsupported_node_payloads(runtime_id, source_node, site_node)
        node_tags = set(tags)
        node_tags.update(site_node.get("tags") or ())
        scheduling = _merge_ground_scheduling(site_node.get("scheduling"), base_scheduling)
        originated = _merge_originated_prefixes(
            site_node.get("originated_prefixes"),
            base_originated,
        )
        expanded.append(
            _RuntimeNode(
                node=ResolvedNode(
                    node_id=runtime_id,
                    local_node_id=local_id,
                    segment_id=groups[0],
                    namespace=_normalize_token(site_id),
                    placement_groups=groups,
                    kind="ground_station",
                    frame_id=body["id"],
                    reference_body=body["id"],
                    tags=tuple(sorted(node_tags)),
                    tenant_id=site_node.get("tenant_id") or "default",
                    terminal_inventory=tuple(
                        _terminal_blocks_for_site_node(runtime_id, source_node, site_node, roots)
                    ),
                    interfaces=_interfaces_from_site_node(site_node),
                    wan_interfaces=tuple(
                        _wan_interfaces_for_site_node(runtime_id, source_node, site_node)
                    ),
                    surface_position=ResolvedSurfacePosition(
                        body=body["id"],
                        lat_deg=float(site["location"]["lat_deg"]),
                        lon_deg=float(site["location"]["lon_deg"]),
                        alt_m=float(site["location"]["alt_m"]),
                    ),
                    originated_prefixes=originated,
                    forwarding=source_node["forwarding"],
                    service_priority=site_node.get("service_priority"),
                    ground_scheduling=scheduling,
                    clock=clock or SegmentClock(),
                ),
                body_facts=(_body_facts_from_catalog(body),),
            )
        )
    return expanded


def _resolved_space_node(
    *,
    runtime_id: str,
    local_id: str,
    segment_id: str,
    source_node: dict[str, Any],
    body: dict[str, Any] | None,
    orbit: ResolvedOrbitFacts,
    tags: tuple[str, ...],
    roots: CatalogRoots,
    plane: int | None,
    slot: int | None,
    clock: SegmentClock | None = None,
) -> ResolvedNode:
    if body is None:
        raise SessionResolutionError(f"space node {runtime_id!r} has no resolved central body")
    _reject_unsupported_node_payloads(runtime_id, source_node, None)
    return ResolvedNode(
        node_id=runtime_id,
        local_node_id=local_id,
        segment_id=segment_id,
        namespace=segment_id,
        kind="satellite",
        frame_id=body["id"],
        central_body=body["id"],
        tags=tags,
        terminal_inventory=tuple(_terminal_blocks_for_node(runtime_id, source_node, None, roots)),
        wan_interfaces=tuple(_wan_interfaces_for_node(runtime_id, source_node)),
        orbit=orbit,
        forwarding=source_node["forwarding"],
        plane=plane,
        slot=slot,
        clock=clock or SegmentClock(),
    )


def _orbit_facts(
    orbit: dict[str, Any],
    body: dict[str, Any],
    *,
    plane: int | None,
    slot: int | None,
    planes: int | None,
    slots_per_plane: int | None,
    raan_spacing_deg: float,
    phase_offset_deg: float,
) -> ResolvedOrbitFacts:
    radius_km = float(body["equatorial_radius_km"])
    if orbit.get("elements") is not None:
        semi_major_axis_km = float(orbit["elements"]["semi_major_axis_km"])
        eccentricity = float(orbit["elements"]["eccentricity"])
    else:
        shape = orbit["shape"]
        if "altitude_km" in shape:
            semi_major_axis_km = radius_km + float(shape["altitude_km"])
            eccentricity = 0.0
        else:
            perigee_radius = radius_km + float(shape["perigee_altitude_km"])
            apogee_radius = radius_km + float(shape["apogee_altitude_km"])
            semi_major_axis_km = (perigee_radius + apogee_radius) / 2.0
            eccentricity = (apogee_radius - perigee_radius) / (apogee_radius + perigee_radius)

    orientation = orbit["orientation"]
    phase = orbit["phase"]
    raan_deg = float(orientation["raan_deg"])
    mean_anomaly_deg = float(phase["mean_anomaly_deg"])
    if plane is not None:
        raan_deg += plane * raan_spacing_deg
    if slot is not None and slots_per_plane:
        mean_anomaly_deg += slot * (360.0 / slots_per_plane)
    if plane is not None:
        mean_anomaly_deg += plane * phase_offset_deg
    _ = planes
    return ResolvedOrbitFacts(
        orbit_id=orbit["id"],
        central_body=body["id"],
        epoch=orbit["epoch"],
        propagator=orbit["propagator"],
        semi_major_axis_km=semi_major_axis_km,
        eccentricity=eccentricity,
        inclination_deg=float(orientation["inclination_deg"]),
        raan_deg=raan_deg % 360.0,
        argument_of_perigee_deg=float(orientation["argument_of_perigee_deg"]),
        mean_anomaly_deg=mean_anomaly_deg % 360.0,
    )


def _installed_mount_counts(
    runtime_id: str, source_node: dict[str, Any], installs: dict[str, Any] | None
) -> dict[str, int]:
    """The single derivation of how many terminals each mount has installed.

    ``installs`` is the site placement's terminal map. Absent (space nodes,
    no site customization surface) means the node model's mount counts apply.
    Present means it is exhaustive site truth: a mount without an entry has
    zero installed, an entry without installed_count takes the model count,
    and an entry naming an unknown mount is an authoring error.
    """
    mounts = {mount["id"]: int(mount["count"]) for mount in source_node["terminals"]}
    if installs is None:
        return mounts
    unknown = sorted(set(installs) - set(mounts))
    if unknown:
        raise SessionResolutionError(
            f"node {runtime_id!r} installs terminals for unknown mount(s): {unknown}; "
            f"node model declares {sorted(mounts)}"
        )
    return {
        mount_id: int((installs.get(mount_id) or {}).get("installed_count", model_count))
        if mount_id in installs
        else 0
        for mount_id, model_count in mounts.items()
    }


def _terminal_blocks_for_node(
    runtime_id: str,
    source_node: dict[str, Any],
    installs: dict[str, Any] | None,
    roots: CatalogRoots,
) -> list[ResolvedTerminalBlock]:
    blocks: list[ResolvedTerminalBlock] = []
    counts = _installed_mount_counts(runtime_id, source_node, installs)
    for mount in source_node["terminals"]:
        count = counts[mount["id"]]
        if count == 0:
            # Not installed at this placement — no inventory, no interfaces.
            continue
        terminal = _load_expected(mount["terminal"], roots, "terminal")
        installed = installs.get(mount["id"], {}) if installs is not None else {}
        capabilities = installed.get("capabilities") or {}
        limits = capabilities.get("limits") or terminal["limits"]
        bandwidth = capabilities.get("bandwidth_mbps") or terminal["bandwidth_mbps"]
        blocks.append(
            ResolvedTerminalBlock(
                terminal_id=mount["id"],
                owner_node_id=runtime_id,
                endpoint_role=mount["role"],
                medium=terminal["medium"],
                source_terminal_id=terminal["id"],
                count=count,
                tracking_capacity=int(
                    capabilities.get("tracking_capacity", terminal["tracking_capacity"])
                ),
                max_range_km=float(capabilities.get("max_range_km", terminal["max_range_km"])),
                min_elevation_deg=float(limits["elevation_deg"]["min"]),
                field_of_regard_deg=_field_of_regard_deg(limits),
                tracking_rate_deg_s=float(limits["max_tracking_rate_deg_s"]),
                # Slowest direction governs the usable link rate (codebase
                # convention) — the optimistic max overstated asymmetric pairs.
                bandwidth_mbps=float(min(bandwidth["transmit"], bandwidth["receive"])),
                source_ref=_catalog_source_ref(mount["terminal"], inline_id=terminal["id"]),
            )
        )
    return blocks


def _reject_unsupported_node_payloads(
    runtime_id: str, source_node: dict[str, Any], site_node: dict[str, Any] | None
) -> None:
    """Payloads are grammar-valid and runtime-future on every current profile.

    A node whose capability is authored via payloads must fail typed — it must
    never appear in the resolved session without that capability.
    """
    declared = bool(source_node.get("payloads")) or bool(site_node and site_node.get("payloads"))
    if declared:
        raise UnsupportedFeatureError(
            [
                UnsupportedFeature(
                    category=FeatureCategory.PAYLOAD,
                    value="payloads",
                    message=(
                        f"node {runtime_id!r} declares payloads; payload-provided "
                        "terminals and resource groups are not supported by the "
                        "current runtime"
                    ),
                    support_note="future runtime capability",
                )
            ]
        )


def _catalog_source_ref(value: Any, *, inline_id: str) -> str:
    if isinstance(value, str):
        return value
    return f"inline:{inline_id}"


def _field_of_regard_deg(limits: dict[str, Any]) -> float:
    az = limits["azimuth_deg"]
    el = limits["elevation_deg"]
    az_span = abs(float(az["max"]) - float(az["min"]))
    el_min = float(el["min"])
    el_span = abs(float(el["max"]) - el_min)
    if az_span >= 360.0:
        return min(360.0, max(0.0, 2.0 * (90.0 - el_min)))
    return min(360.0, max(az_span, el_span))


def _terminal_blocks_for_site_node(
    runtime_id: str, source_node: dict[str, Any], site_node: dict[str, Any], roots: CatalogRoots
) -> list[ResolvedTerminalBlock]:
    return _terminal_blocks_for_node(runtime_id, source_node, site_node["terminals"], roots)


def _wan_interfaces_for_node(
    runtime_id: str, source_node: dict[str, Any]
) -> list[ResolvedWanInterface]:
    interfaces: list[ResolvedWanInterface] = []
    isl_index = 0
    gnd_index = 0
    counts = _installed_mount_counts(runtime_id, source_node, None)
    for mount in source_node["terminals"]:
        for _ in range(counts[mount["id"]]):
            if mount["role"] == "access":
                name = f"gnd{gnd_index}"
                gnd_index += 1
            else:
                name = f"isl{isl_index}"
                isl_index += 1
            interfaces.append(
                ResolvedWanInterface(
                    name=name,
                    owner_node_id=runtime_id,
                    terminal_id=mount["id"],
                )
            )
    return interfaces


def _wan_interfaces_for_site_node(
    runtime_id: str, source_node: dict[str, Any], site_node: dict[str, Any]
) -> list[ResolvedWanInterface]:
    interfaces: list[ResolvedWanInterface] = []
    index = 0
    counts = _installed_mount_counts(runtime_id, source_node, site_node["terminals"])
    for mount in source_node["terminals"]:
        for _ in range(counts[mount["id"]]):
            interfaces.append(
                ResolvedWanInterface(
                    name=f"term{index}",
                    owner_node_id=runtime_id,
                    terminal_id=mount["id"],
                )
            )
            index += 1
    return interfaces


def _interfaces_from_site_node(site_node: dict[str, Any]) -> ResolvedNodeInterfaces:
    lo0 = site_node["interfaces"]["lo0"]
    terr0 = site_node["interfaces"]["terr0"]
    return ResolvedNodeInterfaces(
        lo0=_interface_address(lo0),
        terr0=_interface_address(terr0),
    )


def _interface_address(value: dict[str, str]) -> ResolvedInterfaceAddress:
    return ResolvedInterfaceAddress(ipv4=value.get("ipv4"), ipv6=value.get("ipv6"))


def _apply_addressing(
    cfg: SegmentSessionConfig,
    runtime_nodes: tuple[_RuntimeNode, ...],
) -> tuple[_RuntimeNode, ...]:
    nodes = list(runtime_nodes)
    if cfg.addressing is not None and cfg.addressing.loopbacks:
        for assignment in cfg.addressing.loopbacks:
            selected = _eval_node_selector(assignment.applies_to, tuple(nodes))
            if not selected:
                raise SessionResolutionError(
                    f"address pool assignment {assignment.id!r} matched zero nodes"
                )
            # Pool allocation is need-based and reservation-aware: a node that
            # already carries an authored address for a family keeps it and
            # consumes no pool slot, and every address already present in the
            # session (authored site lo0s included) is excluded from the pool
            # walk. Positional allocation that ignores authored addresses
            # mints duplicate router identities.
            reserved_ipv4, reserved_ipv6 = _existing_loopback_addresses(nodes)
            by_id = {item.node.node_id: item for item in selected}

            def _needs(item: _RuntimeNode, family: str) -> bool:
                interfaces = item.node.interfaces
                if interfaces is None:
                    return True
                return getattr(interfaces.lo0, family) is None

            ipv4_ids = (
                [nid for nid, item in by_id.items() if _needs(item, "ipv4")]
                if assignment.ipv4_pool is not None
                else []
            )
            ipv6_ids = (
                [nid for nid, item in by_id.items() if _needs(item, "ipv6")]
                if assignment.ipv6_pool is not None
                else []
            )
            ipv4_by_id = dict(
                zip(
                    ipv4_ids,
                    _allocate_pool_addresses(
                        assignment.ipv4_pool,
                        assignment.prefix_length,
                        count=len(ipv4_ids),
                        assignment_id=assignment.id,
                        reserved=reserved_ipv4,
                    ),
                    strict=True,
                )
                if ipv4_ids
                else ()
            )
            ipv6_by_id = dict(
                zip(
                    ipv6_ids,
                    _allocate_pool_addresses(
                        assignment.ipv6_pool,
                        assignment.prefix_length,
                        count=len(ipv6_ids),
                        assignment_id=assignment.id,
                        reserved=reserved_ipv6,
                    ),
                    strict=True,
                )
                if ipv6_ids
                else ()
            )
            allocated = {
                node_id: ResolvedInterfaceAddress(
                    ipv4=ipv4_by_id.get(node_id), ipv6=ipv6_by_id.get(node_id)
                )
                for node_id in by_id
                if node_id in ipv4_by_id or node_id in ipv6_by_id
            }
            next_nodes: list[_RuntimeNode] = []
            for item in nodes:
                loopback = allocated.get(item.node.node_id)
                if loopback is None:
                    next_nodes.append(item)
                    continue
                current = item.node.interfaces
                if current is not None:
                    merged_lo0 = _merge_loopback_assignment(
                        current.lo0,
                        loopback,
                        ipv4_pool=assignment.ipv4_pool,
                        ipv6_pool=assignment.ipv6_pool,
                        prefix_length=assignment.prefix_length,
                        assignment_id=assignment.id,
                        node_id=item.node.node_id,
                    )
                    next_nodes.append(
                        _RuntimeNode(
                            node=item.node.model_copy(
                                update={
                                    "interfaces": current.model_copy(update={"lo0": merged_lo0})
                                }
                            ),
                            plane=item.plane,
                            slot=item.slot,
                            body_facts=item.body_facts,
                        )
                    )
                    continue
                next_nodes.append(
                    _RuntimeNode(
                        node=item.node.model_copy(
                            update={"interfaces": ResolvedNodeInterfaces(lo0=loopback)}
                        ),
                        plane=item.plane,
                        slot=item.slot,
                        body_facts=item.body_facts,
                    )
                )
            nodes = next_nodes

    nodes = _apply_default_generated_space_loopbacks(nodes)

    missing = sorted(
        item.node.node_id
        for item in nodes
        if item.node.forwarding == "routed" and item.node.interfaces is None
    )
    if missing:
        raise SessionResolutionError(
            f"routed nodes require lo0 addressing from placement or addressing.loopbacks: {missing}"
        )
    return tuple(nodes)


def _apply_default_generated_space_loopbacks(nodes: list[_RuntimeNode]) -> list[_RuntimeNode]:
    """Assign resolver-owned loopbacks to generated routed space nodes.

    Placed ground routers get their loopback from the site placement. Generated
    constellation nodes do not have a site placement, but FRR still needs one
    stable loopback for unnumbered WAN interfaces and routing protocols. The
    resolver owns that runtime-only allocation and keeps it deterministic by
    walking the resolved node order. It does not mask missing placement data for
    non-space nodes.
    """

    existing_ipv4, existing_ipv6 = _existing_loopback_addresses(nodes)
    ipv4_iter = _available_host_addresses(
        _DEFAULT_GENERATED_SPACE_LOOPBACK_IPV4_POOL, existing_ipv4
    )
    ipv6_iter = _available_host_addresses(
        _DEFAULT_GENERATED_SPACE_LOOPBACK_IPV6_POOL, existing_ipv6
    )

    next_nodes: list[_RuntimeNode] = []
    for item in nodes:
        node = item.node
        if node.forwarding != "routed" or node.kind != "satellite" or node.interfaces is not None:
            next_nodes.append(item)
            continue
        loopback = ResolvedInterfaceAddress(
            ipv4=f"{next(ipv4_iter)}/32",
            ipv6=f"{next(ipv6_iter)}/128",
        )
        next_nodes.append(
            _RuntimeNode(
                node=node.model_copy(update={"interfaces": ResolvedNodeInterfaces(lo0=loopback)}),
                plane=item.plane,
                slot=item.slot,
                body_facts=item.body_facts,
            )
        )
    return next_nodes


def _existing_loopback_addresses(
    nodes: list[_RuntimeNode],
) -> tuple[set[ipaddress.IPv4Address], set[ipaddress.IPv6Address]]:
    ipv4: set[ipaddress.IPv4Address] = set()
    ipv6: set[ipaddress.IPv6Address] = set()
    for item in nodes:
        interfaces = item.node.interfaces
        if interfaces is None:
            continue
        if interfaces.lo0.ipv4 is not None:
            ipv4.add(ipaddress.ip_interface(interfaces.lo0.ipv4).ip)
        if interfaces.lo0.ipv6 is not None:
            ipv6.add(ipaddress.ip_interface(interfaces.lo0.ipv6).ip)
    return ipv4, ipv6


def _available_host_addresses(
    network: ipaddress.IPv4Network | ipaddress.IPv6Network,
    reserved: set[ipaddress.IPv4Address] | set[ipaddress.IPv6Address],
):
    for address in network.hosts():
        if address not in reserved:
            yield address
    raise SessionResolutionError(f"generated space loopback pool {network} is exhausted")


def _merge_loopback_assignment(
    current: ResolvedInterfaceAddress,
    allocated: ResolvedInterfaceAddress,
    *,
    ipv4_pool: str | None,
    ipv6_pool: str | None,
    prefix_length: int | None,
    assignment_id: str,
    node_id: str,
) -> ResolvedInterfaceAddress:
    _validate_existing_loopback_families_inside_pool(
        current,
        ipv4_pool=ipv4_pool,
        ipv6_pool=ipv6_pool,
        prefix_length=prefix_length,
        assignment_id=assignment_id,
        node_id=node_id,
    )
    return ResolvedInterfaceAddress(
        ipv4=current.ipv4 or allocated.ipv4,
        ipv6=current.ipv6 or allocated.ipv6,
    )


def _allocate_pool_addresses(
    pool: str,
    prefix_length: int | None,
    *,
    count: int,
    assignment_id: str,
    reserved: set[ipaddress.IPv4Address] | set[ipaddress.IPv6Address] = frozenset(),
) -> list[str]:
    if prefix_length is None:
        raise SessionResolutionError(
            f"address pool assignment {assignment_id!r} requires prefix_length"
        )
    network = ipaddress.ip_network(pool, strict=False)
    max_prefix = network.max_prefixlen
    if prefix_length < network.prefixlen or prefix_length > max_prefix:
        raise SessionResolutionError(
            f"address pool assignment {assignment_id!r} prefix_length {prefix_length} "
            f"is outside pool {pool}"
        )
    subnet_size = 1 << (max_prefix - prefix_length)
    available = network.num_addresses // subnet_size
    start = int(network.network_address)
    if prefix_length == max_prefix and network.num_addresses > 2:
        start += 1
        available -= 2
    addresses: list[str] = []
    offset = 0
    while len(addresses) < count:
        if offset >= available:
            raise SessionResolutionError(
                f"address pool assignment {assignment_id!r} needs {count} address(es), "
                f"but pool {pool}/{prefix_length} has only "
                f"{len(addresses)} free after reserved/authored addresses"
            )
        address = ipaddress.ip_address(start + offset * subnet_size)
        offset += 1
        if address not in network:
            raise SessionResolutionError(
                f"address pool assignment {assignment_id!r} allocated {address} outside {pool}"
            )
        if address in reserved:
            continue
        addresses.append(f"{address}/{prefix_length}")
    return addresses


def _validate_existing_loopback_families_inside_pool(
    current: ResolvedInterfaceAddress,
    *,
    ipv4_pool: str | None,
    ipv6_pool: str | None,
    prefix_length: int | None,
    assignment_id: str,
    node_id: str,
) -> None:
    for family, pool in (("ipv4", ipv4_pool), ("ipv6", ipv6_pool)):
        if pool is None:
            continue
        actual = getattr(current, family)
        if actual is None:
            continue
        pool_net = ipaddress.ip_network(pool, strict=False)
        actual_iface = ipaddress.ip_interface(actual)
        if actual_iface.ip not in pool_net:
            raise SessionResolutionError(
                f"address pool assignment {assignment_id!r} applies to {node_id!r}, "
                f"but authored lo0 {actual!r} is outside allocated pool family {family}"
            )
        if prefix_length is not None and actual_iface.network.prefixlen != prefix_length:
            raise SessionResolutionError(
                f"address pool assignment {assignment_id!r} applies to {node_id!r}, "
                f"but authored lo0 {actual!r} is not /{prefix_length}"
            )


def _merge_originated_prefixes(
    node_prefixes: dict[str, Any] | None, apply_prefixes: dict[str, Any] | None
):
    from nodalarc.models.segments import OriginatedPrefixes

    data: dict[str, list[str]] = {}
    for source in (apply_prefixes, node_prefixes):
        if not source:
            continue
        for family in ("ipv4", "ipv6"):
            if source.get(family):
                data.setdefault(family, []).extend(source[family])
    if not data:
        return None
    return OriginatedPrefixes.model_validate(data)


def _resolve_link_rule(rule: LinkRule, runtime_nodes: tuple[_RuntimeNode, ...]) -> ResolvedLinkRule:
    endpoints: list[ResolvedEndpoint] = []
    for endpoint in rule.endpoints:
        selected = _eval_node_selector(endpoint.select, runtime_nodes)
        if not selected:
            raise SessionResolutionError(f"link rule {rule.id!r} selector matched zero nodes")
        compatible = [
            item for item in selected if _node_has_terminal_matching(item.node, endpoint.terminal)
        ]
        if not compatible:
            raise SessionResolutionError(
                f"link rule {rule.id!r} terminal selector matched zero compatible mounts"
            )
        # Endpoint coherence: every selected node must share at least one
        # segment label. A node's labels are its segment plus its placement
        # groups — a shared site legitimately answers for every group that
        # placed it, so a {segment: leo_b_ground} endpoint may include a site
        # whose primary segment is leo_a_ground.
        label_sets = [{item.node.segment_id, *item.node.placement_groups} for item in compatible]
        common_labels = set.intersection(*label_sets)
        if not common_labels:
            spanned = sorted({item.node.segment_id for item in compatible})
            raise SessionResolutionError(
                f"link rule {rule.id!r} endpoint selector spans unrelated segments: {spanned}"
            )
        primary_segments = {item.node.segment_id for item in compatible}
        endpoint_segment = next(
            iter(sorted(common_labels & primary_segments) or sorted(common_labels))
        )
        endpoints.append(
            ResolvedEndpoint(
                segment_id=endpoint_segment,
                terminal_role=_endpoint_terminal_role(endpoint.terminal),
                terminal_medium=_endpoint_terminal_medium(endpoint.terminal),
                min_elevation_deg=endpoint.min_elevation_deg,
                node_ids=tuple(item.node.node_id for item in compatible),
            )
        )
    return ResolvedLinkRule(
        rule_id=rule.id,
        kind=rule.class_ or _derive_link_label(endpoints),
        enabled=rule.enabled,
        endpoints=(endpoints[0], endpoints[1]),
        topology=rule.topology,
        constraints=rule.constraints,
        tags=tuple(rule.tags or ()),
    )


def _resolve_routing_domains(
    cfg: SegmentSessionConfig,
    runtime_nodes: tuple[_RuntimeNode, ...],
) -> list[ResolvedRoutingDomain]:
    if cfg.routing is None:
        # Documented product default: one flat IS-IS domain over every ROUTED
        # node. Hosts/bridges/control-only nodes run no routing protocol and
        # must not be invented into one.
        routed = tuple(
            sorted(item.node.node_id for item in runtime_nodes if item.node.forwarding == "routed")
        )
        if not routed:
            raise SessionResolutionError(
                "session declares no routing and resolves zero routed nodes"
            )
        return [
            ResolvedRoutingDomain(
                domain_id="default_domain",
                protocol="isis",
                node_ids=routed,
                capabilities=(),
                area_assignment=None,
            )
        ]
    domains: list[ResolvedRoutingDomain] = []
    for domain in cfg.routing.domains:
        selected_ids: set[str] = set()
        for selector in domain.selectors:
            selected_ids.update(
                item.node.node_id for item in _eval_node_selector(selector, runtime_nodes)
            )
        if not selected_ids:
            raise SessionResolutionError(f"routing domain {domain.id!r} matched zero nodes")
        capabilities: list[str] = []
        if domain.capabilities is not None:
            if domain.capabilities.mpls is not None:
                capabilities.append("mpls")
            if domain.capabilities.segment_routing is not None:
                capabilities.append("segment_routing")
            if domain.capabilities.traffic_engineering is not None:
                capabilities.append("traffic_engineering")
        domains.append(
            ResolvedRoutingDomain(
                domain_id=domain.id,
                protocol=domain.protocol,
                timers=domain.timers or RoutingTimers(),
                node_ids=tuple(sorted(selected_ids)),
                capabilities=tuple(capabilities),
                area_assignment=domain.area_assignment,
            )
        )
    _validate_routing_domain_partition(domains, runtime_nodes)
    return domains


def _validate_routing_domain_partition(
    domains: list[ResolvedRoutingDomain],
    runtime_nodes: tuple[_RuntimeNode, ...],
) -> None:
    domain_ids_by_node: dict[str, list[str]] = {item.node.node_id: [] for item in runtime_nodes}
    for domain in domains:
        for node_id in domain.node_ids:
            if node_id in domain_ids_by_node:
                domain_ids_by_node[node_id].append(domain.domain_id)
    missing = [node_id for node_id, domain_ids in domain_ids_by_node.items() if not domain_ids]
    if missing:
        raise SessionResolutionError(
            "routing domains must cover every resolved node; missing: " + ", ".join(missing[:20])
        )
    overlaps = {
        node_id: domain_ids
        for node_id, domain_ids in domain_ids_by_node.items()
        if len(domain_ids) > 1
    }
    if overlaps:
        examples = ", ".join(
            f"{node_id}={domain_ids}" for node_id, domain_ids in sorted(overlaps.items())[:20]
        )
        raise SessionResolutionError(
            "routing domains must be disjoint; overlapping node membership: " + examples
        )


def _terminal_selectors_by_rule(
    cfg: SegmentSessionConfig,
) -> dict[str, tuple[TerminalSelector, TerminalSelector]]:
    return {
        rule.id: (rule.endpoints[0].terminal, rule.endpoints[1].terminal)
        for rule in cfg.link_rules or ()
    }


def _validate_access_terminal_bindings(
    cfg: SegmentSessionConfig, resolved: ResolvedSession
) -> None:
    """Ground terminal blocks bind to exactly one access rule.

    Terminal compatibility is authored, never inferred: the rule's terminal
    selector IS the binding declaration, and one ground terminal serving two
    constellations is an authoring ambiguity the allocator must never be
    asked to arbitrate. Satellite access terminals serve whichever ground
    station the allocator assigns and are deliberately not bound.
    """
    selectors = _terminal_selectors_by_rule(cfg)
    node_by_id = {node.node_id: node for node in resolved.nodes}
    bound: dict[tuple[str, str], str] = {}
    for rule in resolved.link_rules:
        if rule.kind != "access" or not rule.enabled:
            continue
        for endpoint, selector in zip(rule.endpoints, selectors[rule.rule_id], strict=True):
            for node_id in endpoint.node_ids:
                node = node_by_id[node_id]
                if node.kind != "ground_station":
                    continue
                for block in node.terminal_inventory:
                    if not _terminal_matches(block, selector):
                        continue
                    key = (node_id, block.terminal_id)
                    owner = bound.get(key)
                    if owner is not None and owner != rule.rule_id:
                        raise SessionResolutionError(
                            f"ground terminal {block.terminal_id!r} on {node_id!r} is "
                            f"bound by access rules {owner!r} and {rule.rule_id!r}; "
                            "terminal bindings must be disjoint — one terminal serves "
                            "one constellation"
                        )
                    bound[key] = rule.rule_id


def _resolve_link_candidates(
    resolved: ResolvedSession, cfg: SegmentSessionConfig
) -> list[ResolvedLinkCandidate]:
    pair_rank = _pair_rank_map(resolved)
    declared = generate_declared_link_candidates(resolved, pair_rank=pair_rank)
    candidates: list[ResolvedLinkCandidate] = []
    node_by_id = {node.node_id: node for node in resolved.nodes}
    selectors = _terminal_selectors_by_rule(cfg)
    rules_by_id = {rule.rule_id: rule for rule in resolved.link_rules}
    used_ifaces: dict[str, set[str]] = {}

    def _fixed_iface(node_id: str, rule_id: str) -> str:
        # The interface comes from the wan manifest entries whose owning
        # terminal block matches THIS rule's terminal selector — a candidate
        # must never claim an interface the manifest assigned to a different
        # mount (an rf link on the optical mount's interface is wire fiction).
        node = node_by_id[node_id]
        rule = rules_by_id[rule_id]
        side_selectors = [
            selector
            for endpoint, selector in zip(rule.endpoints, selectors[rule_id], strict=True)
            if node_id in endpoint.node_ids
        ]
        blocks_by_id = {block.terminal_id: block for block in node.terminal_inventory}
        eligible = [
            iface.name
            for iface in node.wan_interfaces
            if iface.terminal_id in blocks_by_id
            and any(
                _terminal_matches(blocks_by_id[iface.terminal_id], selector)
                for selector in side_selectors
            )
        ]
        used = used_ifaces.setdefault(node_id, set())
        for name in eligible:
            if name not in used:
                used.add(name)
                return name
        raise SessionResolutionError(
            f"link rule {rule_id!r} needs another fixed interface on {node_id!r}, but "
            f"every matching terminal interface is allocated ({sorted(eligible)})"
        )

    for candidate in declared:
        node_a, node_b = candidate.pair
        left = node_by_id[node_a]
        right = node_by_id[node_b]
        if candidate.kind == "access":
            iface_a, iface_b = _access_candidate_interfaces(left, right)
        else:
            iface_a = _fixed_iface(node_a, candidate.rule_id)
            iface_b = _fixed_iface(node_b, candidate.rule_id)
        bandwidth = _candidate_bandwidth_mbps(
            left,
            right,
            role=candidate.terminal_role,
            medium=candidate.terminal_medium,
        )
        candidates.append(
            ResolvedLinkCandidate(
                rule_id=candidate.rule_id,
                kind=candidate.kind,
                terminal_role=candidate.terminal_role,
                terminal_medium=candidate.terminal_medium,
                node_a=node_a,
                node_b=node_b,
                interface_a=iface_a,
                interface_b=iface_b,
                bandwidth_mbps=bandwidth,
                topology_mode=candidate.topology_mode,
                priority=candidate.priority,
                endpoint_segments=candidate.endpoint_segments,
            )
        )
    _validate_fixed_interface_capacity(candidates, node_by_id)
    _enforce_candidate_limits(candidates, resolved)
    return candidates


def _enforce_declared_candidate_bounds(
    cfg: SegmentSessionConfig, resolved: ResolvedSession
) -> None:
    """Bound the candidate graph BEFORE materializing it.

    Multi-segment sessions must declare candidate_limits — an all-by-all rule
    over composed segments is exactly the case the budget exists for, and an
    absent budget must not mean an absent bound. The static per-rule upper
    bound (mode-aware) is checked against the declared budget before any pair,
    interface, or rank is built.
    """
    limits = cfg.simulation.candidate_limits if cfg.simulation is not None else None
    if limits is None:
        if len(cfg.segments) > 1 and resolved.link_rules:
            raise SessionResolutionError(
                "multi-segment sessions with link rules must declare simulation.candidate_limits"
            )
        return
    for rule in resolved.link_rules:
        if not rule.enabled:
            continue
        left, right = rule.endpoints
        mode = rule.topology.mode
        if mode == "visible_candidates":
            bound = len(left.node_ids) * len(right.node_ids)
        elif mode == "nearest_n":
            bound = max(len(left.node_ids), len(right.node_ids)) * (rule.topology.n or 1)
        elif mode == "explicit_pairs":
            bound = len(rule.topology.pairs or ())
        else:
            continue  # nearest_visible rejects later with its own typed error
        if bound > limits.max_pairs_per_rule:
            raise SessionResolutionError(
                f"link rule {rule.rule_id!r} declares a static candidate upper bound of "
                f"{bound} pairs ({mode}), exceeding "
                f"simulation.candidate_limits.max_pairs_per_rule={limits.max_pairs_per_rule} "
                "before materialization"
            )


def _enforce_candidate_limits(
    candidates: list[ResolvedLinkCandidate],
    resolved: ResolvedSession,
) -> None:
    limits = resolved.simulation.candidate_limits if resolved.simulation is not None else None
    if limits is None:
        return
    counts_by_rule: dict[str, int] = {}
    for candidate in candidates:
        counts_by_rule[candidate.rule_id] = counts_by_rule.get(candidate.rule_id, 0) + 1
    oversized = {
        rule_id: count
        for rule_id, count in counts_by_rule.items()
        if count > limits.max_pairs_per_rule
    }
    if oversized:
        details = ", ".join(f"{rule_id}={count}" for rule_id, count in sorted(oversized.items()))
        raise SessionResolutionError(
            "declared link candidates exceed simulation.candidate_limits.max_pairs_per_rule "
            f"({limits.max_pairs_per_rule}): {details}"
        )
    total = len(candidates)
    if total > limits.max_pairs_per_tick:
        raise SessionResolutionError(
            "declared link candidates exceed simulation.candidate_limits.max_pairs_per_tick "
            f"({limits.max_pairs_per_tick}): {total}"
        )


def _access_candidate_interfaces(left: ResolvedNode, right: ResolvedNode) -> tuple[str, str]:
    left_ground = left.kind == "ground_station"
    right_ground = right.kind == "ground_station"
    if left_ground == right_ground:
        raise SessionResolutionError(
            f"access candidate requires exactly one ground station endpoint: "
            f"{left.node_id}<->{right.node_id}"
        )
    ground, satellite = (left, right) if left_ground else (right, left)
    # Ground access visibility is body-local: the GS body-fixed frame and the
    # satellite central-body frame must be the same body, or every range and
    # elevation number downstream is cross-frame garbage. Cross-body paths use
    # inter-body relays, never direct ground access.
    if ground.reference_body != satellite.central_body:
        raise SessionResolutionError(
            f"access link {ground.node_id}<->{satellite.node_id} is cross-body "
            f"(ground reference_body={ground.reference_body!r}, satellite "
            f"central_body={satellite.central_body!r}); ground access visibility is "
            "body-local — use an inter-body relay path instead"
        )
    return ("term0", "gnd0") if left_ground else ("gnd0", "term0")


def _candidate_bandwidth_mbps(
    left: ResolvedNode,
    right: ResolvedNode,
    *,
    role: str,
    medium: str | None,
) -> float:
    left_block = _first_matching_terminal_block(left, role=role, medium=medium)
    right_block = _first_matching_terminal_block(right, role=role, medium=medium)
    if left_block.bandwidth_mbps is None or right_block.bandwidth_mbps is None:
        raise SessionResolutionError(
            f"link candidate {left.node_id}<->{right.node_id} is missing bandwidth"
        )
    return min(float(left_block.bandwidth_mbps), float(right_block.bandwidth_mbps))


def _first_matching_terminal_block(
    node: ResolvedNode,
    *,
    role: str,
    medium: str | None,
) -> ResolvedTerminalBlock:
    matches = [
        block
        for block in node.terminal_inventory
        if block.endpoint_role == role and (medium is None or block.medium == medium)
    ]
    if not matches:
        raise SessionResolutionError(
            f"node {node.node_id!r} has no terminal block for role={role!r} medium={medium!r}"
        )
    return matches[0]


def _validate_fixed_interface_capacity(
    candidates: list[ResolvedLinkCandidate],
    node_by_id: dict[str, ResolvedNode],
) -> None:
    used: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.kind == "access":
            continue
        used.setdefault(candidate.node_a, set()).add(candidate.interface_a)
        used.setdefault(candidate.node_b, set()).add(candidate.interface_b)
    for node_id, interfaces in used.items():
        available = {
            iface.name
            for iface in node_by_id[node_id].wan_interfaces
            if iface.name.startswith("isl")
        }
        extra = sorted(interfaces - available)
        if extra:
            raise SessionResolutionError(
                f"node {node_id!r} needs fixed link interface(s) {extra}, "
                f"but only has {sorted(available)}"
            )


def _pair_rank_map(resolved: ResolvedSession) -> dict[tuple[str, str], float]:
    node_by_id = {node.node_id: node for node in resolved.nodes}
    radius_by_body = {facts.body_id: facts.mean_radius_km for facts in resolved.bodies}
    ranks: dict[tuple[str, str], float] = {}
    for rule in resolved.link_rules:
        for a in rule.endpoints[0].node_ids:
            for b in rule.endpoints[1].node_ids:
                if a == b:
                    continue
                pair = (a, b) if a < b else (b, a)
                ranks[pair] = _pair_static_rank(node_by_id[a], node_by_id[b], radius_by_body)
    return ranks


def _pair_static_rank(
    left: ResolvedNode, right: ResolvedNode, radius_by_body: dict[str, float]
) -> float:
    left_pos = _static_rank_position(left, radius_by_body)
    right_pos = _static_rank_position(right, radius_by_body)
    if left_pos is None or right_pos is None:
        raise SessionResolutionError(
            f"cannot rank pair {left.node_id}<->{right.node_id} by distance: a node "
            "has neither resolved orbit facts nor a surface position"
        )
    return math.dist(left_pos, right_pos)


def _static_rank_position(
    node: ResolvedNode, radius_by_body: dict[str, float]
) -> tuple[float, float, float] | None:
    """Epoch position for nearest-N ranking: orbital state for space nodes,
    body-fixed surface position for placed ground nodes (resolved body radius,
    never a hardcoded constant). A "nearest" rank derived from node-id
    character sums would be geometry theater."""
    orbital = _orbit_rank_position(node)
    if orbital is not None:
        return orbital
    if node.surface_position is None:
        return None
    body = node.surface_position
    radius = radius_by_body.get(body.body)
    if radius is None:
        raise SessionResolutionError(
            f"cannot rank {node.node_id!r}: no resolved body facts for {body.body!r}"
        )
    lat = math.radians(body.lat_deg)
    lon = math.radians(body.lon_deg)
    return (
        radius * math.cos(lat) * math.cos(lon),
        radius * math.cos(lat) * math.sin(lon),
        radius * math.sin(lat),
    )


def _orbit_rank_position(node: ResolvedNode) -> tuple[float, float, float] | None:
    if node.orbit is None:
        return None
    orbit = node.orbit
    mean_rad = math.radians(orbit.mean_anomaly_deg)
    eccentric_anomaly = mean_rad
    if orbit.eccentricity > 0:
        for _ in range(10):
            eccentric_anomaly -= (
                eccentric_anomaly - orbit.eccentricity * math.sin(eccentric_anomaly) - mean_rad
            ) / (1.0 - orbit.eccentricity * math.cos(eccentric_anomaly))
    true_anomaly = math.atan2(
        math.sqrt(1.0 - orbit.eccentricity**2) * math.sin(eccentric_anomaly),
        math.cos(eccentric_anomaly) - orbit.eccentricity,
    )
    radius = orbit.semi_major_axis_km * (1.0 - orbit.eccentricity * math.cos(eccentric_anomaly))
    raan = math.radians(orbit.raan_deg)
    inclination = math.radians(orbit.inclination_deg)
    argp = math.radians(orbit.argument_of_perigee_deg)
    u = argp + true_anomaly
    cos_raan = math.cos(raan)
    sin_raan = math.sin(raan)
    cos_i = math.cos(inclination)
    sin_i = math.sin(inclination)
    cos_u = math.cos(u)
    sin_u = math.sin(u)
    return (
        radius * (cos_raan * cos_u - sin_raan * cos_i * sin_u),
        radius * (sin_raan * cos_u + cos_raan * cos_i * sin_u),
        radius * sin_i * sin_u,
    )


def _eval_node_selector(
    selector: NodeSelector, runtime_nodes: tuple[_RuntimeNode, ...]
) -> list[_RuntimeNode]:
    universe = list(runtime_nodes)
    if selector.all is not None:
        current = {item.node.node_id for item in universe}
        for child in selector.all:
            current &= {item.node.node_id for item in _eval_node_selector(child, runtime_nodes)}
        return [item for item in universe if item.node.node_id in current]
    if selector.any is not None:
        current: set[str] = set()
        for child in selector.any:
            current |= {item.node.node_id for item in _eval_node_selector(child, runtime_nodes)}
        return [item for item in universe if item.node.node_id in current]
    if selector.not_ is not None:
        excluded = {item.node.node_id for item in _eval_node_selector(selector.not_, runtime_nodes)}
        return [item for item in universe if item.node.node_id not in excluded]
    if selector.segment is not None:
        return [
            item
            for item in universe
            if item.node.segment_id == selector.segment
            or selector.segment in item.node.placement_groups
        ]
    if selector.tag is not None:
        return [item for item in universe if selector.tag in item.node.tags]
    if selector.node is not None:
        return [item for item in universe if item.node.local_node_id == selector.node]
    if selector.plane is not None:
        return [item for item in universe if item.plane == selector.plane]
    if selector.slot is not None:
        return [item for item in universe if item.slot == selector.slot]
    raise AssertionError("unreachable node selector")


def _node_has_terminal_matching(node: ResolvedNode, selector: TerminalSelector) -> bool:
    return any(_terminal_matches(block, selector) for block in node.terminal_inventory)


def _terminal_matches(block: ResolvedTerminalBlock, selector: TerminalSelector) -> bool:
    if selector.all is not None:
        return all(_terminal_matches(block, child) for child in selector.all)
    if selector.any is not None:
        return any(_terminal_matches(block, child) for child in selector.any)
    if selector.not_ is not None:
        return not _terminal_matches(block, selector.not_)
    if selector.role is not None:
        return block.endpoint_role == selector.role
    if selector.medium is not None:
        return block.medium == selector.medium
    if selector.mount is not None:
        return block.terminal_id == selector.mount
    raise AssertionError("unreachable terminal selector")


def _collect_terminal_leaves(selector: TerminalSelector, attr: str) -> set[str]:
    """Collect positive leaf values for one selector attribute.

    ``not`` subtrees are exclusions — a role inside a negation is not the
    endpoint's role and must not be collected.
    """
    values: set[str] = set()
    leaf = getattr(selector, attr)
    if leaf is not None:
        values.add(leaf)
    for child in (selector.all or ()) + (selector.any or ()):
        values.update(_collect_terminal_leaves(child, attr))
    return values


def _endpoint_terminal_role(selector: TerminalSelector) -> str:
    roles = _collect_terminal_leaves(selector, "role")
    if len(roles) != 1:
        raise SessionResolutionError(
            "link endpoint terminal selector must name exactly one role; "
            f"got {sorted(roles) or 'none'}"
        )
    return next(iter(roles))


def _endpoint_terminal_medium(selector: TerminalSelector) -> str | None:
    mediums = _collect_terminal_leaves(selector, "medium")
    if len(mediums) > 1:
        raise SessionResolutionError(
            f"link endpoint terminal selector names multiple mediums: {sorted(mediums)}"
        )
    return next(iter(mediums)) if mediums else None


def _derive_link_label(endpoints: list[ResolvedEndpoint]) -> str:
    left, right = endpoints
    # An access-role endpoint makes the rule an access rule regardless of
    # segment arrangement — labeling it "isl" sends it down the wrong
    # interface/candidate path with a misleading failure.
    if left.terminal_role == "access" or right.terminal_role == "access":
        return "access"
    if left.segment_id == right.segment_id:
        return "isl"
    return "inter_body"


def _constraint_limit_for_node(limit: Any, node: ResolvedNode) -> int:
    if isinstance(limit, int):
        return limit
    labels = {node.segment_id, *node.placement_groups}
    matches = [value for segment_id, value in limit.items() if segment_id in labels]
    if not matches:
        raise SessionResolutionError(
            f"link_rule max_links_per_node map has no entry for segment(s) {sorted(labels)!r}"
        )
    return int(min(matches))


def _enforce_link_rule_constraints(resolved: ResolvedSession, candidates: tuple[Any, ...]) -> None:
    """Enforce the runtime-supported subset of link-rule constraints.

    ``max_links_per_node`` is a static graph constraint, enforceable here.
    Range/mutual-visibility constraints are dynamic OME semantics; accepting
    them before OME consumes them would be a lie, so they reject loudly.
    """
    nodes = {node.node_id: node for node in resolved.nodes}
    degree: dict[tuple[str, str], int] = {}
    for candidate in candidates:
        for node_id in (candidate.node_a, candidate.node_b):
            key = (candidate.rule_id, node_id)
            degree[key] = degree.get(key, 0) + 1

    for rule in resolved.link_rules:
        constraints = rule.constraints
        if constraints is None:
            continue
        unsupported = [
            name
            for name, value in (
                ("max_range_km", constraints.max_range_km),
                ("require_mutual_visibility", constraints.require_mutual_visibility),
            )
            if value is not None
        ]
        if unsupported:
            raise SessionResolutionError(
                f"link_rule {rule.rule_id!r} uses unsupported runtime constraint(s): "
                + ", ".join(unsupported)
            )
        if constraints.max_links_per_node is None:
            continue
        for (rule_id, node_id), count in sorted(degree.items()):
            if rule_id != rule.rule_id:
                continue
            limit = _constraint_limit_for_node(constraints.max_links_per_node, nodes[node_id])
            if count > limit:
                raise SessionResolutionError(
                    f"link_rule {rule.rule_id!r} declares {count} candidate links for "
                    f"{node_id!r}, exceeding max_links_per_node={limit}"
                )


def _validate_routing_boundaries(
    cfg: SegmentSessionConfig,
    domains: tuple[ResolvedRoutingDomain, ...],
    link_rules: tuple[ResolvedLinkRule, ...],
) -> None:
    """Boundary declarations must be materializable, and domain separation
    must be real.

    1. Every boundary's ``over`` names an existing, enabled, non-access rule
       whose endpoints land exactly in each export's from/to domains.
    2. Every non-access rule whose endpoints land in two different routing
       domains must be covered by a boundary — otherwise both ends would
       render live IGP interfaces and two declared-separate domains silently
       run as one.
    """
    domains_by_id = {domain.domain_id: domain for domain in domains}
    rules_by_id = {rule.rule_id: rule for rule in link_rules}
    domain_of_node = {
        node_id: domain.domain_id for domain in domains for node_id in domain.node_ids
    }
    boundary_rule_ids: set[str] = set()

    for boundary in cfg.routing.boundaries or () if cfg.routing is not None else ():
        rule = rules_by_id.get(boundary.over)
        if rule is None:
            raise SessionResolutionError(
                f"routing boundary over {boundary.over!r} names no declared link rule"
            )
        if not rule.enabled:
            raise SessionResolutionError(
                f"routing boundary over {boundary.over!r} names a disabled link rule"
            )
        if rule.kind == "access":
            raise SessionResolutionError(
                f"routing boundary over {boundary.over!r} names an access rule; "
                "boundaries run over fixed inter-domain links"
            )
        boundary_rule_ids.add(rule.rule_id)
        rule_domains = {
            domain_of_node[node_id]
            for endpoint in rule.endpoints
            for node_id in endpoint.node_ids
            if node_id in domain_of_node
        }
        for export in boundary.export:
            for domain_id in (export.from_, export.to):
                if domain_id not in domains_by_id:
                    raise SessionResolutionError(
                        f"routing boundary export references unknown domain {domain_id!r}"
                    )
            if export.from_ == export.to:
                raise SessionResolutionError(
                    f"routing boundary export from/to must differ; got {export.from_!r}"
                )
            if rule_domains != {export.from_, export.to}:
                raise SessionResolutionError(
                    f"routing boundary over {boundary.over!r} spans domains "
                    f"{sorted(rule_domains)} but export declares "
                    f"{sorted((export.from_, export.to))}"
                )

    for rule in link_rules:
        if rule.kind == "access" or not rule.enabled:
            continue
        rule_domains = {
            domain_of_node[node_id]
            for endpoint in rule.endpoints
            for node_id in endpoint.node_ids
            if node_id in domain_of_node
        }
        if len(rule_domains) > 1 and rule.rule_id not in boundary_rule_ids:
            raise SessionResolutionError(
                f"link rule {rule.rule_id!r} joins routing domains {sorted(rule_domains)} "
                "without a routing boundary; declare a boundary over it or keep the "
                "rule inside one domain"
            )


def _allocate_sid_blocks(domains: tuple[ResolvedRoutingDomain, ...]) -> list[SidBlock]:
    blocks: list[SidBlock] = []
    base = 1
    for domain in sorted(domains, key=lambda item: item.domain_id):
        if "segment_routing" not in domain.capabilities:
            continue
        count = len(domain.node_ids)
        blocks.append(
            SidBlock(
                domain_id=domain.domain_id,
                node_ids=tuple(sorted(domain.node_ids)),
                sid_start=base,
                sid_end=base + count - 1,
            )
        )
        base += max(count, 1)
    return blocks


def _node_tags_for(
    rules: tuple[dict[str, Any], ...], *, plane: int, slot: int, local_id: str
) -> set[str]:
    tags: set[str] = set()
    for rule in rules:
        if "planes" in rule and plane not in rule["planes"]:
            continue
        if "slots" in rule and slot not in rule["slots"]:
            continue
        if "node_ids" in rule and local_id not in rule["node_ids"]:
            continue
        tags.add(rule["tag"])
    return tags


def _runtime_id(segment_id: str, local_id: str) -> str:
    node_id = f"{_normalize_token(segment_id)}-{_normalize_token(local_id)}"
    validate_runtime_node_id(node_id)
    return node_id


def _normalize_token(value: str) -> str:
    token = _NORMALIZE_RE.sub("-", value.strip().lower()).strip("-")
    if not token:
        raise SessionResolutionError(f"cannot normalize empty runtime token from {value!r}")
    return token


def _load_ref_or_object(value: Any, roots: CatalogRoots) -> tuple[str, dict[str, Any]]:
    if isinstance(value, str):
        path = resolve_catalog_reference(value, roots)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    elif isinstance(value, dict):
        data = value
    else:
        raise SessionResolutionError(
            f"expected catalog reference or inline object, got {type(value)!r}"
        )
    try:
        wrapper, model = validate_catalog_document(data)
    except Exception as exc:
        raise SessionResolutionError(f"invalid catalog object: {exc}") from exc
    return wrapper, model.model_dump(mode="python", by_alias=True, exclude_none=True)


def _load_expected(ref: Any, roots: CatalogRoots, expected_wrapper: str) -> dict[str, Any]:
    if isinstance(ref, dict) and expected_wrapper not in ref:
        try:
            model = validate_catalog_value(expected_wrapper, ref)
        except Exception as exc:
            raise SessionResolutionError(
                f"invalid inline catalog object {expected_wrapper!r}: {exc}"
            ) from exc
        return model.model_dump(mode="python", by_alias=True, exclude_none=True)
    wrapper, body = _load_ref_or_object(ref, roots)
    if wrapper != expected_wrapper:
        raise SessionResolutionError(
            f"expected catalog object {expected_wrapper!r}, got {wrapper!r}"
        )
    return body
