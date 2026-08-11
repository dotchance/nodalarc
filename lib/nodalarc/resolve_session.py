# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog session resolver.

This module is the single authority that turns the canonical catalog
configuration language into immutable runtime truth.
"""

from __future__ import annotations

import ipaddress
import math
import re
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodalarc.body_frames import BodyFrame, body_runtime_support_for
from nodalarc.catalog_paths import CatalogRoots, resolve_catalog_reference
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import validate_referenced_configuration_document
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.ephemeris_runtime import (
    EphemerisValidationError,
    runtime_config_from_resolved,
    session_epoch_unix,
    validate_ephemeris_manifest,
)
from nodalarc.link_rule_candidates import generate_declared_link_candidates
from nodalarc.models.catalog import EnvValueFrom, Profile, ResolvedEnvEntry
from nodalarc.models.identity import IdentityMode
from nodalarc.models.link_rules import LinkRule, NodeSelector, TerminalSelector
from nodalarc.models.resolved_session import (
    ResolvedBodyFacts,
    ResolvedEndpoint,
    ResolvedEphemeris,
    ResolvedEphemerisKernel,
    ResolvedHostAttachment,
    ResolvedInterfaceAddress,
    ResolvedLinkCandidate,
    ResolvedLinkRule,
    ResolvedNode,
    ResolvedEthernetSegment,
    ResolvedNodeInterfaces,
    ResolvedOriginatedPrefixes,
    ResolvedSegmentMember,
    ResolvedOrbitFacts,
    ResolvedRoutingDomain,
    ResolvedSession,
    ResolvedSurfacePosition,
    ResolvedTerminalBlock,
    ResolvedWanInterface,
    SidBlock,
    SourceContext,
)
from nodalarc.models.segment_session import (
    Dispatch,
    RoutingDomain,
    RoutingTimers,
    SegmentSessionConfig,
)
from nodalarc.models.segments import GroundOverride, GroundSegment, SegmentClock, SpaceSegment
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
from nodalarc.propagator import propagate_sgp4_tle
from nodalarc.runtime_naming import validate_runtime_node_id
from nodalarc.runtime_support import (
    FeatureCategory,
    RuntimeSupport,
    UnsupportedFeature,
    UnsupportedFeatureError,
    adapter_renders,
    adapter_renders_routing,
)
from nodalarc.tle import tle_mean_elements

_NORMALIZE_RE = re.compile(r"[^a-z0-9-]+")
_DEFAULT_GENERATED_SPACE_LOOPBACK_IPV4_POOL = ipaddress.ip_network("100.64.0.0/10")
_DEFAULT_GENERATED_SPACE_LOOPBACK_IPV6_POOL = ipaddress.ip_network("fd00:6e0::/64")


class SessionResolutionError(ValueError):
    """Raised when a catalog session is structurally valid but semantically invalid.

    A raise site that knows which authored object failed may attach its
    persisted object or segment identity so authoring clients can associate the
    refusal with the responsible input. Raise sites without that context remain
    valid unscoped semantic failures.
    """

    def __init__(
        self,
        message: str,
        *,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        segment_id: str | None = None,
        node_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.subject_kind = subject_kind
        self.subject_id = subject_id
        self.segment_id = segment_id
        self.node_id = node_id


@dataclass(frozen=True)
class SessionResolution:
    """Resolved catalog session plus the parsed catalog root object."""

    resolved: ResolvedSession
    catalog_session: SegmentSessionConfig
    # The admitted catalog profiles the resolved nodes reference, keyed by
    # reference token. Deployment consumes these; it never reloads them.
    workload_profiles: Mapping[str, Profile] = field(default_factory=dict)


@dataclass(frozen=True)
class _RuntimeNode:
    node: ResolvedNode
    plane: int | None = None
    slot: int | None = None
    body_facts: tuple[ResolvedBodyFacts, ...] = ()
    # The effective profile's adapter name; None when the profile has none.
    # A node is a router exactly when this adapter renders routing.
    profile_adapter: str | None = None
    # Ethernet attachment facts pending allocation: one entry per interface
    # the node's environment joins, (interface_name, site_id, segment_id).
    # A site-installed node carries one entry per bound port; a mounted
    # payload carries exactly one, named by its mount's attach port.
    ethernet_bindings: tuple[tuple[str, str, str], ...] = ()
    # Symbolic origination intent, resolved to concrete prefixes once
    # segment subnets are allocated.
    origination_targets: Any = None


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
    if source_context is not None and not isinstance(source_context, SourceContext):
        raise SessionResolutionError("source_context must be a SourceContext instance")

    roots = catalog_roots or default_catalog_roots()
    context = source_context or SourceContext(origin="resolve_session")
    cfg = SegmentSessionConfig.model_validate(raw_session)
    # The runtime-support gate is mandatory. Production always runs the
    # Earth-Luna profile; callers may only widen/narrow it explicitly. A None
    # here must never mean "skip the typed UnsupportedFeature layer".
    support = runtime_support or RuntimeSupport.earth_luna()
    _check_runtime_support(cfg, support, roots)

    allocated_nodes, ethernet_segments = _allocate_segment_addressing(
        list(_expand_segments(cfg, roots))
    )
    runtime_nodes = _apply_addressing(cfg, tuple(allocated_nodes))
    runtime_nodes = _derive_host_attachments(runtime_nodes)
    resolved_nodes = tuple(item.node for item in runtime_nodes)
    _check_ground_scheduling_support(resolved_nodes, support)
    body_facts = _collect_body_facts(runtime_nodes)
    _check_body_support(resolved_nodes, body_facts, support)
    _check_propagator_support(resolved_nodes, support)
    _check_workload_adapter_support(runtime_nodes, support)
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
        ethernet_segments=ethernet_segments,
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
        ethernet_segments=base_resolved.ethernet_segments,
        sid_blocks=base_resolved.sid_blocks,
        simulation=base_resolved.simulation,
        routing=base_resolved.routing,
        dispatch=dispatch,
        addressing=base_resolved.addressing,
        ephemeris=base_resolved.ephemeris,
        time=base_resolved.time,
        source_context=base_resolved.source_context,
    )
    _validate_access_ground_scheduling(resolved)
    _validate_allocator_wide_scheduling(resolved)
    workload_profiles = {
        reference: Profile.model_validate(_load_expected(reference, roots, "profile"))
        for reference in sorted({node.profile for node in resolved_nodes})
    }
    _check_profile_env(workload_profiles, resolved_nodes)
    return SessionResolution(
        resolved=resolved,
        catalog_session=cfg,
        workload_profiles=workload_profiles,
    )


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
    raw = load_configuration_yaml(path.read_text(encoding="utf-8"))
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
    explicit_node_clocks: list[dict[str, Any]] = []
    for segment in cfg.segments:
        if isinstance(segment, GroundSegment):
            kind = "ground_set"
        elif isinstance(segment, SpaceSegment):
            # The segment class does not identify the source wrapper: a
            # SpaceSegment can carry a constellation or space_node_set, and
            # each is a distinct supported feature. The gate must key on the
            # loaded wrapper, not the segment class.
            with _segment_scope(segment.id):
                wrapper, source = _load_ref_or_object(segment.source, roots)
            kind = wrapper
            if wrapper == "space_node_set":
                explicit_node_clocks.extend(
                    entry["clock"] for entry in source["nodes"] if entry.get("clock") is not None
                )
            # Onboard execution on space placements (bus ports, payload
            # mounts) is structural grammar ahead of the runtime chain.
            space_node_refs: set[str] = set()
            if wrapper == "constellation":
                space_node_refs.add(source["node"])
            elif wrapper == "space_node_set":
                space_node_refs.update(entry["node"] for entry in source["nodes"])
            for node_ref in sorted(space_node_refs):
                with _segment_scope(segment.id):
                    node_document = _load_expected(node_ref, roots, "node")
                onboard = bool(node_document.get("ethernet")) or bool(
                    node_document.get("payloads")
                )
                if feature := support.check_payloads(onboard):
                    unsupported.append(feature)
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
        if domain.capabilities is not None:
            for capability in ("mpls", "segment_routing", "traffic_engineering"):
                if getattr(domain.capabilities, capability) is not None and (
                    feature := support.check_routing_capability(domain.protocol, capability)
                ):
                    unsupported.append(feature)
    if cfg.addressing is not None:
        for pool_class in ("loopbacks", "point_to_point", "terrestrial_prefixes"):
            assignments = getattr(cfg.addressing, pool_class) or ()
            if assignments and (feature := support.check_addressing_pool(pool_class)):
                unsupported.append(feature)
            for assignment in assignments:
                allocation = assignment.allocation or "by_node_order"
                if feature := support.check_address_allocation(allocation):
                    unsupported.append(feature)
    for rule in cfg.link_rules or ():
        if feature := support.check_link_topology(rule.topology.mode):
            unsupported.append(feature)
        if rule.constraints is not None:
            for constraint in (
                "max_links_per_node",
                "max_range_km",
                "require_mutual_visibility",
            ):
                if getattr(rule.constraints, constraint) is not None and (
                    feature := support.check_link_constraint(constraint)
                ):
                    unsupported.append(feature)
    for segment in cfg.segments:
        clock = getattr(segment, "clock", None)
        if clock is not None and (feature := support.check_clock_model(clock.model)):
            unsupported.append(feature)
    for clock in explicit_node_clocks:
        if feature := support.check_clock_model(clock["model"]):
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


def _check_propagator_support(
    nodes: tuple[ResolvedNode, ...],
    support: RuntimeSupport,
) -> None:
    """Gate orbit propagators against the runtime-support profile.

    The grammar carries propagators the runtime cannot fly yet (e.g. "crtbp"
    three-body NRHO/halo trajectories). Those must fail here with a typed
    reason — propagating them through Kepler machinery would emit
    plausible-looking but physically false trajectories.
    """
    unsupported = []
    seen: set[str] = set()
    for node in nodes:
        if node.orbit is None:
            continue
        propagator = node.orbit.propagator
        if propagator in seen:
            continue
        seen.add(propagator)
        if feature := support.check_propagator(propagator):
            unsupported.append(feature)
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


@contextmanager
def _segment_scope(segment_id: str):
    """Address refusals raised while expanding one segment: a wall that
    names no owner lands on the segment being expanded (refusals are
    addressed mail — an unscoped catalog-validation message would otherwise
    surface as raw prose far from the object that caused it)."""
    try:
        yield
    except SessionResolutionError as exc:
        if exc.subject_id or exc.segment_id or exc.node_id:
            raise
        raise SessionResolutionError(
            str(exc),
            subject_kind="segment",
            subject_id=segment_id,
            segment_id=segment_id,
        ) from exc


def _expand_segments(cfg: SegmentSessionConfig, roots: CatalogRoots) -> tuple[_RuntimeNode, ...]:
    ordered: list[_RuntimeNode | _SiteMarker] = []
    placements: dict[str, _SitePlacement] = {}
    for segment in cfg.segments:
        with _segment_scope(segment.id):
            if isinstance(segment, SpaceSegment):
                ordered.extend(_expand_space_segment(segment, roots))
            elif isinstance(segment, GroundSegment):
                site_set = _load_expected(segment.placement.from_site_set, roots, "site_set")
                sites = tuple(
                    _load_expected(site_ref, roots, "site") for site_ref in site_set["sites"]
                )
                placed_site_ids = {site["id"] for site in sites}
                unknown_override_sites = sorted(
                    override.match.site
                    for override in segment.overrides or ()
                    if override.match.site not in placed_site_ids
                )
                if unknown_override_sites:
                    raise SessionResolutionError(
                        f"ground segment {segment.id!r} override targets site id(s) absent "
                        f"from its selected site set: {unknown_override_sites}"
                    )
                for site in sites:
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
        for source_slot, entry in enumerate(source["nodes"]):
            expanded.extend(
                _space_node_from_entry(
                    segment,
                    entry,
                    roots,
                    source_slot=source_slot,
                )
            )
        return expanded
    raise SessionResolutionError(
        f"space segment {segment.id!r} source must be constellation or space_node_set; "
        f"got {wrapper!r}"
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
    profile_ref, profile_level, profile_adapter = _effective_profile(
        described=f"generated nodes in segment {segment.id!r}",
        placed=None,
        segment_profile=segment.profile,
        definition=node.get("profile"),
        roots=roots,
    )
    expanded: list[_RuntimeNode] = []

    for plane in range(planes):
        for slot in range(slots):
            local_id = f"sat-p{plane:02d}s{slot:02d}"
            runtime_id = _runtime_id(segment.id, local_id)
            tags = set(segment.tags or ())
            tags.update(constellation.get("tags") or ())
            tags.update(_node_tags_for(tag_rules, plane=plane, slot=slot, local_id=local_id))
            carrier = _RuntimeNode(
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
                    profile=profile_ref,
                    profile_level=profile_level,
                ),
                plane=plane,
                slot=slot,
                body_facts=(_body_facts_from_catalog(body),),
                profile_adapter=profile_adapter,
                ethernet_bindings=_space_carrier_bindings(node, runtime_id),
                origination_targets=_node_origination_targets(node),
            )
            expanded.append(carrier)
            expanded.extend(
                _expand_space_payload_members(
                    carrier=carrier,
                    source_node=node,
                    segment_profile=segment.profile,
                    roots=roots,
                )
            )
    return expanded


def _node_origination_targets(source_node: dict[str, Any]):
    declared = source_node.get("originated_prefixes")
    if not declared:
        return None
    from nodalarc.models.segments import OriginatedPrefixes

    return OriginatedPrefixes.model_validate(declared)


def _space_node_from_entry(
    segment: SpaceSegment,
    entry: dict[str, Any],
    roots: CatalogRoots,
    *,
    source_slot: int,
) -> list[_RuntimeNode]:
    node = _load_expected(entry["node"], roots, "node")
    orbit = _load_expected(entry["orbit"], roots, "orbit") if "orbit" in entry else None
    tle = entry.get("sgp4_tle")
    if orbit is None and tle is None:
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
    if tle is not None:
        body = _load_expected(tle["central_body"], roots, "body")
        orbit_facts = _tle_orbit_facts(entry["id"], tle, body)
        plane = 0
        slot = source_slot
    else:
        body = _load_expected(orbit["central_body"], roots, "body")
        orbit_facts = _orbit_facts(
            orbit,
            body,
            plane=None,
            slot=None,
            planes=None,
            slots_per_plane=None,
            raan_spacing_deg=0.0,
            phase_offset_deg=0.0,
        )
        plane = None
        slot = None
    local_id = entry["id"]
    profile_ref, profile_level, profile_adapter = _effective_profile(
        described=f"space node {local_id!r} in segment {segment.id!r}",
        placed=entry.get("profile"),
        segment_profile=segment.profile,
        definition=node.get("profile"),
        roots=roots,
    )
    tags = tuple(sorted({*(segment.tags or ()), *(entry.get("tags") or ())}))
    entry_clock = (
        SegmentClock.model_validate(entry["clock"]) if entry.get("clock") is not None else None
    )
    runtime_id = _runtime_id(segment.id, local_id)
    carrier = _RuntimeNode(
        node=_resolved_space_node(
            runtime_id=runtime_id,
            local_id=local_id,
            segment_id=segment.id,
            source_node=node,
            body=body,
            orbit=orbit_facts,
            tags=tags,
            roots=roots,
            clock=entry_clock or segment.clock,
            plane=plane,
            slot=slot,
            profile=profile_ref,
            profile_level=profile_level,
        ),
        body_facts=(_body_facts_from_catalog(body),),
        profile_adapter=profile_adapter,
        ethernet_bindings=_space_carrier_bindings(node, runtime_id),
        origination_targets=_node_origination_targets(node),
    )
    return [
        carrier,
        *_expand_space_payload_members(
            carrier=carrier,
            source_node=node,
            segment_profile=segment.profile,
            roots=roots,
        ),
    ]


def _tle_orbit_facts(
    node_id: str,
    tle: dict[str, Any],
    body: dict[str, Any],
) -> ResolvedOrbitFacts:
    if body["id"] != "earth":
        raise SessionResolutionError(
            f"space node {node_id!r} uses sgp4_tle with central body {body['id']!r}; "
            "SGP4/TLE placement is Earth-only"
        )
    line_1 = tle["line_1"]
    line_2 = tle["line_2"]
    elements = tle_mean_elements(
        line_1,
        line_2,
        gravitational_parameter_km3_s2=float(body["gravitational_parameter_km3_s2"]),
    )
    epoch = datetime.fromtimestamp(elements.epoch_unix, UTC).isoformat().replace("+00:00", "Z")
    return ResolvedOrbitFacts(
        orbit_id=f"{node_id}-sgp4-tle",
        central_body="earth",
        epoch=epoch,
        propagator="sgp4_tle",
        semi_major_axis_km=elements.semi_major_axis_km,
        eccentricity=elements.eccentricity,
        inclination_deg=elements.inclination_deg,
        raan_deg=elements.raan_deg,
        argument_of_perigee_deg=elements.argument_of_perigee_deg,
        mean_anomaly_deg=elements.mean_anomaly_deg,
        tle_line_1=line_1,
        tle_line_2=line_2,
        norad_id=elements.norad_id,
    )


_ALLOCATOR_WIDE_SCHEDULING_FIELDS = (
    "ranking_order",
    "handover_concurrency",
    "mbb_preemption",
    "successor_abort_policy",
    "cross_tenant_displacement",
    "bbm_acquire_timeout_ticks",
)

_REQUIRED_ACCESS_GROUND_SCHEDULING_FIELDS = (
    "selection_policy",
    "handover_policy",
    "handover_mode",
    "mbb_overlap_ticks",
    "mbb_reserve",
    "handover_concurrency",
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


def _check_ground_scheduling_support(
    nodes: tuple[ResolvedNode, ...], support: RuntimeSupport
) -> None:
    unsupported: list[UnsupportedFeature] = []
    seen: set[tuple[FeatureCategory, str]] = set()

    def add(feature: UnsupportedFeature | None) -> None:
        if feature is None or (feature.category, feature.value) in seen:
            return
        seen.add((feature.category, feature.value))
        unsupported.append(feature)

    for node in nodes:
        scheduling = node.ground_scheduling
        if scheduling is None:
            continue
        if scheduling.handover_concurrency is not None:
            add(support.check_ground_handover_concurrency(scheduling.handover_concurrency))
        if scheduling.mbb_reserve is not None:
            add(support.check_ground_mbb_reserve(scheduling.mbb_reserve))
        if scheduling.bbm_acquire_timeout_ticks is not None:
            add(
                support.check_ground_bbm_acquire_timeout_ticks(scheduling.bbm_acquire_timeout_ticks)
            )
    if unsupported:
        raise UnsupportedFeatureError(unsupported)


def _validate_access_ground_scheduling(resolved: ResolvedSession) -> None:
    required_ground_ids = set(resolved.ground_candidate_satellites_by_gs())
    nodes = {node.node_id: node for node in resolved.nodes}
    for node_id in sorted(required_ground_ids):
        scheduling = nodes[node_id].ground_scheduling
        if scheduling is None:
            raise SessionResolutionError(
                f"ground station {node_id!r} participates in access candidates and "
                "requires explicit ground scheduling"
            )
        missing = [
            field
            for field in _REQUIRED_ACCESS_GROUND_SCHEDULING_FIELDS
            if getattr(scheduling, field) is None
        ]
        if missing:
            raise SessionResolutionError(
                f"ground station {node_id!r} participates in access candidates and has "
                f"incomplete ground scheduling; missing: {', '.join(missing)}"
            )
        mode = scheduling.handover_mode
        overlap_ticks = scheduling.mbb_overlap_ticks
        reserve = scheduling.mbb_reserve
        if mode is None or overlap_ticks is None or reserve is None:
            raise SessionResolutionError(
                f"ground station {node_id!r} has incomplete handover scheduling"
            )
        if mode == "bbm":
            if overlap_ticks != 0 or reserve != 0:
                raise SessionResolutionError(
                    f"ground station {node_id!r} uses BBM and requires "
                    "mbb_overlap_ticks=0 and mbb_reserve=0"
                )
        elif overlap_ticks <= 0 or reserve <= 0:
            raise SessionResolutionError(
                f"ground station {node_id!r} uses MBB and requires positive "
                "mbb_overlap_ticks and mbb_reserve"
            )


def _validate_allocator_wide_scheduling(resolved: ResolvedSession) -> None:
    """Allocator-wide scheduling knobs must be uniform across every ground
    node at resolve time.

    The OME allocator is a single decision-maker for ground nodes that
    participate in access candidates. Per-node divergence of these fields has
    no runtime meaning and previously died late at OME-input build.
    """
    baseline: dict[str, Any] | None = None
    baseline_node: str | None = None
    required_ground_ids = set(resolved.ground_candidate_satellites_by_gs())
    for node in resolved.nodes:
        if node.node_id not in required_ground_ids or node.ground_scheduling is None:
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
    clocks = tuple(segment.clock or SegmentClock() for segment in placement.segments)
    base_clock = clocks[0]
    for index in range(1, len(policies)):
        scheduling, originated, _ = policies[index]
        if (
            scheduling != base_scheduling
            or originated != base_originated
            or clocks[index] != base_clock
        ):
            raise SessionResolutionError(
                f"site {site_id!r} is placed by groups {list(groups)!r} with conflicting "
                f"apply/override policy (group {groups[index]!r} differs from {groups[0]!r}); "
                "tags may differ per group, but clock, scheduling, and "
                "originated_prefixes must be identical"
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

    # A shared site is instantiated once; segment profile statements must
    # agree across every placing group, like scheduling and clock.
    declared_segment_profiles = {
        str(placing.profile) for placing in placement.segments if placing.profile is not None
    }
    if len(declared_segment_profiles) > 1:
        raise SessionResolutionError(
            f"site {site_id!r} is placed by ground segments with conflicting "
            f"profile statements: {sorted(declared_segment_profiles)}"
        )
    segment_profile = next(iter(declared_segment_profiles), None)

    expanded: list[_RuntimeNode] = []
    for site_node in site["nodes"]:
        source_node = _load_expected(site_node["node"], roots, "node")
        # Ground identity is site-anchored: a node's name never depends on
        # which group(s) placed its site. local_node_id keeps the
        # site-qualified form so `node:` selectors stay unique.
        local_id = f"{site_id}-{site_node['id']}"
        runtime_id = _runtime_id(site_id, site_node["id"])
        bindings = _validate_port_bindings(runtime_id, source_node, site_node, site)
        _validate_payload_installations(runtime_id, source_node, site_node)
        node_tags = set(tags)
        node_tags.update(site_node.get("tags") or ())
        scheduling = _merge_ground_scheduling(site_node.get("scheduling"), base_scheduling)
        # Ground scheduling governs access-contact allocation; a node with
        # no terminal mounts (a processing host) participates in no access
        # rule and holds no scheduling state.
        if not site_node.get("terminals"):
            scheduling = None
        originated = _merge_originated_prefixes(
            site_node.get("originated_prefixes"),
            source_node.get("originated_prefixes"),
            base_originated,
        )
        profile_ref, profile_level, profile_adapter = _effective_profile(
            described=f"ground node {runtime_id!r}",
            placed=site_node.get("profile"),
            segment_profile=segment_profile,
            definition=source_node.get("profile"),
            roots=roots,
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
                        _terminal_blocks_for_site_node(
                            runtime_id,
                            source_node,
                            site_node,
                            roots,
                            body_id=body["id"],
                        )
                    ),
                    interfaces=None,
                    wan_interfaces=tuple(
                        _wan_interfaces_for_site_node(runtime_id, source_node, site_node)
                    ),
                    surface_position=ResolvedSurfacePosition(
                        body=body["id"],
                        lat_deg=float(site["location"]["lat_deg"]),
                        lon_deg=float(site["location"]["lon_deg"]),
                        alt_m=float(site["location"]["alt_m"]),
                    ),
                    originated_prefixes=None,
                    forwarding=source_node["forwarding"],
                    profile=profile_ref,
                    profile_level=profile_level,
                    service_priority=site_node.get("service_priority"),
                    ground_scheduling=scheduling,
                    clock=base_clock,
                ),
                body_facts=(_body_facts_from_catalog(body),),
                profile_adapter=profile_adapter,
                ethernet_bindings=tuple(
                    (port, site_id, segment) for port, segment in sorted(bindings.items())
                ),
                origination_targets=originated,
            )
        )
        expanded.extend(
            _expand_ground_payload_members(
                carrier_runtime_id=runtime_id,
                carrier_local_id=local_id,
                site_id=site_id,
                site=site,
                site_node=site_node,
                source_node=source_node,
                bindings=bindings,
                groups=groups,
                body=body,
                node_tags=node_tags,
                segment_profile=segment_profile,
                base_clock=base_clock,
                roots=roots,
            )
        )
    return expanded


def _validate_port_bindings(
    runtime_id: str,
    source_node: dict[str, Any],
    site_node: dict[str, Any],
    site: dict[str, Any],
) -> dict[str, str]:
    """Every Ethernet port the node declares binds to exactly one declared
    site segment; a binding naming an unknown port refuses."""

    declared_ports = [port["id"] for port in source_node.get("ethernet", ())]
    bindings = dict(site_node.get("interfaces") or {})
    unknown_ports = sorted(set(bindings) - set(declared_ports))
    if unknown_ports:
        raise SessionResolutionError(
            f"ground node {runtime_id!r} binds undeclared Ethernet port(s): {unknown_ports}; "
            f"the node definition declares {sorted(declared_ports)}"
        )
    unbound_ports = sorted(set(declared_ports) - set(bindings))
    if unbound_ports:
        raise SessionResolutionError(
            f"ground node {runtime_id!r} leaves declared Ethernet port(s) unbound: "
            f"{unbound_ports}; every declared port binds to one site segment"
        )
    declared_segments = {segment["id"] for segment in site.get("ethernet", ())}
    unknown_segments = sorted(set(bindings.values()) - declared_segments)
    if unknown_segments:
        raise SessionResolutionError(
            f"ground node {runtime_id!r} binds unknown site segment(s): {unknown_segments}"
        )
    return bindings


def _expand_ground_payload_members(
    *,
    carrier_runtime_id: str,
    carrier_local_id: str,
    site_id: str,
    site: dict[str, Any],
    site_node: dict[str, Any],
    source_node: dict[str, Any],
    bindings: dict[str, str],
    groups: tuple[str, ...],
    body: dict[str, Any],
    node_tags: set[str],
    segment_profile: str | None,
    base_clock: Any,
    roots: CatalogRoots,
) -> list[_RuntimeNode]:
    """Expand a site-installed node's populated payload mounts into runtime
    members: real environments attached to the segment the mount's port
    binds to, carried by the installing node."""

    members: list[_RuntimeNode] = []
    installations = site_node.get("payloads") or {}
    mounts = {mount["id"]: mount for mount in source_node.get("payloads", ())}
    for mount_id in sorted(installations):
        installation = installations[mount_id]
        mount = mounts[mount_id]
        payload = _load_expected(mount["payload"], roots, "payload")
        attach = mount["attach"]
        segment = bindings[attach]
        member_tags = set(node_tags)
        member_tags.update(mount.get("tags") or ())
        member_tags.update(installation.get("tags") or ())
        for ordinal in range(1, int(installation["installed_count"]) + 1):
            suffix = mount_id if ordinal == 1 else f"{mount_id}{ordinal}"
            member_local = f"{carrier_local_id}-{suffix}"
            member_runtime_id = _runtime_id(site_id, f"{site_node['id']}-{suffix}")
            profile_ref, profile_level, profile_adapter = _effective_profile(
                described=f"payload member {member_runtime_id!r}",
                placed=mount.get("profile"),
                segment_profile=segment_profile,
                definition=payload.get("profile"),
                roots=roots,
            )
            members.append(
                _RuntimeNode(
                    node=ResolvedNode(
                        node_id=member_runtime_id,
                        local_node_id=member_local,
                        segment_id=groups[0],
                        namespace=_normalize_token(site_id),
                        placement_groups=groups,
                        kind="ground_station",
                        frame_id=body["id"],
                        reference_body=body["id"],
                        tags=tuple(sorted(member_tags)),
                        tenant_id=site_node.get("tenant_id") or "default",
                        terminal_inventory=(),
                        interfaces=None,
                        wan_interfaces=(),
                        surface_position=ResolvedSurfacePosition(
                            body=body["id"],
                            lat_deg=float(site["location"]["lat_deg"]),
                            lon_deg=float(site["location"]["lon_deg"]),
                            alt_m=float(site["location"]["alt_m"]),
                        ),
                        originated_prefixes=None,
                        forwarding=payload["forwarding"],
                        profile=profile_ref,
                        profile_level=profile_level,
                        ground_scheduling=None,
                        clock=base_clock,
                    ),
                    body_facts=(_body_facts_from_catalog(body),),
                    profile_adapter=profile_adapter,
                    ethernet_bindings=((attach, site_id, segment),),
                )
            )
    return members


def _space_carrier_bindings(source_node: dict[str, Any], carrier_id: str) -> tuple:
    """A space node's declared ports are the buses it carries: one carrier
    interface per port, each its own segment scoped to this placed copy."""

    return tuple(
        (port["id"], carrier_id, port["id"]) for port in source_node.get("ethernet", ())
    )


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
    profile: str,
    profile_level: str,
    clock: SegmentClock | None = None,
) -> ResolvedNode:
    if body is None:
        raise SessionResolutionError(f"space node {runtime_id!r} has no resolved central body")
    return ResolvedNode(
        node_id=runtime_id,
        local_node_id=local_id,
        segment_id=segment_id,
        namespace=segment_id,
        kind="satellite",
        frame_id=body["id"],
        central_body=body["id"],
        tags=tags,
        terminal_inventory=tuple(
            _terminal_blocks_for_node(
                runtime_id,
                source_node,
                None,
                roots,
                owner_kind="satellite",
                body_id=body["id"],
            )
        ),
        wan_interfaces=tuple(_wan_interfaces_for_node(runtime_id, source_node)),
        orbit=orbit,
        forwarding=source_node["forwarding"],
        profile=profile,
        profile_level=profile_level,
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
    zero installed, each entry requires an explicit installed_count, and an
    entry naming an unknown mount is an authoring error.
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
    for mount_id, installation in installs.items():
        installed_count = int(installation.get("installed_count", mounts[mount_id]))
        if installed_count > mounts[mount_id]:
            raise SessionResolutionError(
                f"node {runtime_id!r} installs {installed_count} terminals for mount "
                f"{mount_id!r}, but the referenced node model declares {mounts[mount_id]}"
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
    *,
    owner_kind: str,
    body_id: str,
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
        _validate_terminal_capability_narrowing(
            runtime_id,
            mount["id"],
            terminal,
            capabilities,
        )
        limits = capabilities.get("limits") or terminal["limits"]
        bandwidth = capabilities.get("bandwidth_mbps") or terminal["bandwidth_mbps"]
        boresight = _effective_terminal_boresight(
            runtime_id=runtime_id,
            mount=mount,
            capabilities=capabilities,
            owner_kind=owner_kind,
            body_id=body_id,
        )
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
                field_of_regard_deg=_field_of_regard_deg(
                    limits,
                    access=mount["role"] == "access",
                ),
                tracking_rate_deg_s=float(limits["max_tracking_rate_deg_s"]),
                # Slowest direction governs the usable link rate (codebase
                # convention) — the optimistic max overstated asymmetric pairs.
                bandwidth_mbps=float(min(bandwidth["transmit"], bandwidth["receive"])),
                boresight=boresight,
                source_ref=str(mount["terminal"]),
            )
        )
    return blocks


def _validate_terminal_capability_narrowing(
    runtime_id: str,
    mount_id: str,
    terminal: dict[str, Any],
    capabilities: dict[str, Any],
) -> None:
    bandwidth = capabilities.get("bandwidth_mbps")
    if bandwidth is not None:
        for direction in ("transmit", "receive"):
            if float(bandwidth[direction]) > float(terminal["bandwidth_mbps"][direction]):
                raise SessionResolutionError(
                    f"node {runtime_id!r} terminal mount {mount_id!r} {direction} bandwidth "
                    "override exceeds the referenced terminal capability"
                )

    tracking_capacity = capabilities.get("tracking_capacity")
    if tracking_capacity is not None and int(tracking_capacity) > int(
        terminal["tracking_capacity"]
    ):
        raise SessionResolutionError(
            f"node {runtime_id!r} terminal mount {mount_id!r} tracking_capacity override "
            "exceeds the referenced terminal capability"
        )

    max_range_km = capabilities.get("max_range_km")
    if max_range_km is not None and float(max_range_km) > float(terminal["max_range_km"]):
        raise SessionResolutionError(
            f"node {runtime_id!r} terminal mount {mount_id!r} max_range_km override "
            "exceeds the referenced terminal capability"
        )

    limits = capabilities.get("limits")
    if limits is None:
        return
    terminal_limits = terminal["limits"]
    for axis in ("azimuth_deg", "elevation_deg"):
        if float(limits[axis]["min"]) < float(terminal_limits[axis]["min"]):
            raise SessionResolutionError(
                f"node {runtime_id!r} terminal mount {mount_id!r} {axis}.min override "
                "widens the referenced terminal limits"
            )
        if float(limits[axis]["max"]) > float(terminal_limits[axis]["max"]):
            raise SessionResolutionError(
                f"node {runtime_id!r} terminal mount {mount_id!r} {axis}.max override "
                "widens the referenced terminal limits"
            )
    if float(limits["max_tracking_rate_deg_s"]) > float(terminal_limits["max_tracking_rate_deg_s"]):
        raise SessionResolutionError(
            f"node {runtime_id!r} terminal mount {mount_id!r} max_tracking_rate_deg_s "
            "override exceeds the referenced terminal limits"
        )


def _validate_payload_installations(
    runtime_id: str,
    source_node: dict[str, Any],
    site_node: dict[str, Any],
) -> None:
    mounts = {mount["id"]: int(mount["count"]) for mount in source_node.get("payloads", ())}
    installations = site_node.get("payloads") or {}
    unknown = sorted(set(installations) - set(mounts))
    if unknown:
        raise SessionResolutionError(
            f"node {runtime_id!r} installs payloads for unknown mount(s): {unknown}; "
            f"node model declares {sorted(mounts)}"
        )
    for mount_id, installation in installations.items():
        installed_count = int(installation["installed_count"])
        if installed_count > mounts[mount_id]:
            raise SessionResolutionError(
                f"node {runtime_id!r} installs {installed_count} payloads for mount "
                f"{mount_id!r}, but the referenced node model declares {mounts[mount_id]}"
            )


def _expand_space_payload_members(
    *,
    carrier: _RuntimeNode,
    source_node: dict[str, Any],
    segment_profile: str | None,
    roots: CatalogRoots,
) -> list[_RuntimeNode]:
    """Expand a space carrier's payload mounts into runtime members.

    Every mount is fully populated on a space placement (there is no
    installation map in space, exactly as space terminals install at their
    declared counts). Members ride the carrier: same segment, same orbit
    facts, same plane and slot, attached to the bus segment the mount's
    port names, with carrier-qualified identity.
    """

    members: list[_RuntimeNode] = []
    node = carrier.node
    for mount in sorted(source_node.get("payloads", ()), key=lambda entry: entry["id"]):
        payload = _load_expected(mount["payload"], roots, "payload")
        attach = mount["attach"]
        for ordinal in range(1, int(mount["count"]) + 1):
            suffix = mount["id"] if ordinal == 1 else f"{mount['id']}{ordinal}"
            member_local = f"{node.local_node_id}-{suffix}"
            member_runtime_id = _runtime_id(node.segment_id, member_local)
            profile_ref, profile_level, profile_adapter = _effective_profile(
                described=f"payload member {member_runtime_id!r}",
                placed=mount.get("profile"),
                segment_profile=segment_profile,
                definition=payload.get("profile"),
                roots=roots,
            )
            member_tags = set(node.tags)
            member_tags.update(mount.get("tags") or ())
            members.append(
                _RuntimeNode(
                    node=ResolvedNode(
                        node_id=member_runtime_id,
                        local_node_id=member_local,
                        segment_id=node.segment_id,
                        namespace=node.namespace,
                        kind=node.kind,
                        frame_id=node.frame_id,
                        central_body=node.central_body,
                        tags=tuple(sorted(member_tags)),
                        terminal_inventory=(),
                        wan_interfaces=(),
                        orbit=node.orbit,
                        forwarding=payload["forwarding"],
                        profile=profile_ref,
                        profile_level=profile_level,
                        plane=node.plane,
                        slot=node.slot,
                        clock=node.clock,
                    ),
                    plane=carrier.plane,
                    slot=carrier.slot,
                    body_facts=carrier.body_facts,
                    profile_adapter=profile_adapter,
                    ethernet_bindings=((attach, node.node_id, attach),),
                )
            )
    return members


def _ground_terminal_boresight(value: dict[str, Any]) -> TerminalBoresight:
    mode = value["mode"]
    if mode == "local_vertical":
        return TerminalBoresight(mode=mode)
    if mode == "configured_topocentric":
        return TerminalBoresight(
            mode=mode,
            configured_az_deg=float(value["azimuth_deg"]),
            configured_el_deg=float(value["elevation_deg"]),
        )
    azimuth = value["azimuth_deg"]
    elevation = value["elevation_deg"]
    return TerminalBoresight(
        mode=mode,
        min_az_deg=float(azimuth["min"]),
        max_az_deg=float(azimuth["max"]),
        min_el_deg=float(elevation["min"]),
        max_el_deg=float(elevation["max"]),
    )


def _effective_terminal_boresight(
    *,
    runtime_id: str,
    mount: dict[str, Any],
    capabilities: dict[str, Any],
    owner_kind: str,
    body_id: str,
) -> TerminalBoresight | SatGroundTerminalBoresight | None:
    declared = mount.get("boresight")
    installed = capabilities.get("boresight")
    if mount["role"] != "access":
        if declared is not None or installed is not None:
            raise SessionResolutionError(
                f"node {runtime_id!r} non-access terminal mount {mount['id']!r} "
                "must not declare an access boresight"
            )
        return None
    if declared is None and owner_kind == "satellite":
        raise SessionResolutionError(
            f"satellite {runtime_id!r} access terminal mount {mount['id']!r} "
            "requires a spacecraft boresight"
        )
    if owner_kind == "ground_station":
        if declared is not None:
            raise SessionResolutionError(
                f"ground node {runtime_id!r} access terminal mount {mount['id']!r} "
                "must declare boresight on the site installation, not the node mount"
            )
        if installed is None:
            raise SessionResolutionError(
                f"ground node {runtime_id!r} access terminal mount {mount['id']!r} "
                "requires a site installation boresight"
            )
        return _ground_terminal_boresight(installed)
    if installed is not None:
        raise SessionResolutionError(
            f"satellite {runtime_id!r} access terminal mount {mount['id']!r} "
            "must not use a ground installation boresight"
        )
    return SatGroundTerminalBoresight(target_body=body_id, mode=declared["mode"])


def _field_of_regard_deg(limits: dict[str, Any], *, access: bool) -> float:
    az = limits["azimuth_deg"]
    el = limits["elevation_deg"]
    az_span = abs(float(az["max"]) - float(az["min"]))
    el_min = float(el["min"])
    el_span = abs(float(el["max"]) - el_min)
    result = max(0.0, 2.0 * (90.0 - el_min)) if az_span >= 360.0 else max(az_span, el_span)
    return min(180.0 if access else 360.0, result)


def _terminal_blocks_for_site_node(
    runtime_id: str,
    source_node: dict[str, Any],
    site_node: dict[str, Any],
    roots: CatalogRoots,
    *,
    body_id: str,
) -> list[ResolvedTerminalBlock]:
    return _terminal_blocks_for_node(
        runtime_id,
        source_node,
        site_node["terminals"],
        roots,
        owner_kind="ground_station",
        body_id=body_id,
    )


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


_SEGMENT_SUBNET_IPV4_POOL = ipaddress.ip_network("172.16.0.0/12")
_SEGMENT_SUBNET_IPV6_POOL = ipaddress.ip_network("fd00:da7a::/32")
_SEGMENT_IPV4_PREFIX = 24
_SEGMENT_IPV6_PREFIX = 64


def _allocate_segment_addressing(
    nodes: list[_RuntimeNode],
) -> tuple[list[_RuntimeNode], tuple[ResolvedEthernetSegment, ...]]:
    """Allocate every Ethernet segment subnet and member address.

    Deterministic by stable ids: segments order by (site, segment id) and
    receive consecutive subnets from the resolver-owned pools; within a
    segment, routed environments order before hosts, each class sorted by
    node id, and receive consecutive host addresses. Loopbacks for
    ground-placed environments come from the same resolver-owned loopback
    pools the generated space nodes use. Symbolic origination resolves
    against the allocated subnets; a target naming a segment the node is
    not attached to refuses.
    """

    segment_keys = sorted(
        {(site_id, segment) for item in nodes for _, site_id, segment in item.ethernet_bindings}
    )
    if not segment_keys:
        return nodes, ()

    ipv4_subnets = _SEGMENT_SUBNET_IPV4_POOL.subnets(new_prefix=_SEGMENT_IPV4_PREFIX)
    ipv6_subnets = _SEGMENT_SUBNET_IPV6_POOL.subnets(new_prefix=_SEGMENT_IPV6_PREFIX)
    subnet_by_key: dict[tuple[str, str], tuple[Any, Any]] = {}
    for key in segment_keys:
        try:
            subnet_by_key[key] = (next(ipv4_subnets), next(ipv6_subnets))
        except StopIteration:  # pragma: no cover - 4096 /24s deep
            raise SessionResolutionError(
                f"segment subnet pool exhausted allocating {key[0]}/{key[1]}"
            ) from None

    def _segment_order(item: _RuntimeNode) -> tuple[int, str]:
        return (0 if item.node.forwarding == "routed" else 1, item.node.node_id)

    members_by_key: dict[tuple[str, str], list[_RuntimeNode]] = {}
    for item in nodes:
        for _, site_id, segment in item.ethernet_bindings:
            members_by_key.setdefault((site_id, segment), []).append(item)

    address_by_node_segment: dict[tuple[str, tuple[str, str]], tuple[str, str]] = {}
    for key, members in members_by_key.items():
        ipv4_net, ipv6_net = subnet_by_key[key]
        ipv4_hosts = ipv4_net.hosts()
        ipv6_hosts = ipv6_net.hosts()
        for item in sorted(members, key=_segment_order):
            try:
                ipv4_host = next(ipv4_hosts)
                ipv6_host = next(ipv6_hosts)
            except StopIteration:
                raise SessionResolutionError(
                    f"segment {key[0]}/{key[1]} has more members than its "
                    f"allocated subnet holds"
                ) from None
            address_by_node_segment[(item.node.node_id, key)] = (
                f"{ipv4_host}/{_SEGMENT_IPV4_PREFIX}",
                f"{ipv6_host}/{_SEGMENT_IPV6_PREFIX}",
            )

    existing_ipv4, existing_ipv6 = _existing_loopback_addresses(nodes)
    lo_ipv4 = _available_host_addresses(
        _DEFAULT_GENERATED_SPACE_LOOPBACK_IPV4_POOL, existing_ipv4
    )
    lo_ipv6 = _available_host_addresses(
        _DEFAULT_GENERATED_SPACE_LOOPBACK_IPV6_POOL, existing_ipv6
    )

    next_nodes: list[_RuntimeNode] = []
    for item in sorted(nodes, key=lambda entry: entry.node.node_id):
        if not item.ethernet_bindings:
            next_nodes.append(item)
            continue
        ethernet: dict[str, ResolvedInterfaceAddress] = {}
        bound_segments: dict[str, tuple[str, str]] = {}
        for interface, site_id, segment in item.ethernet_bindings:
            key = (site_id, segment)
            ipv4_address, ipv6_address = address_by_node_segment[(item.node.node_id, key)]
            ethernet[interface] = ResolvedInterfaceAddress(
                ipv4=ipv4_address, ipv6=ipv6_address
            )
            bound_segments[segment] = key
        interfaces = ResolvedNodeInterfaces(
            lo0=ResolvedInterfaceAddress(
                ipv4=f"{next(lo_ipv4)}/32", ipv6=f"{next(lo_ipv6)}/128"
            ),
            ethernet=ethernet,
        )
        originated = _resolve_origination(
            item.node.node_id,
            item.origination_targets,
            bound_segments,
            subnet_by_key,
        )
        next_nodes.append(
            replace(
                item,
                node=item.node.model_copy(
                    update={"interfaces": interfaces, "originated_prefixes": originated}
                ),
            )
        )
    order = {item.node.node_id: index for index, item in enumerate(nodes)}
    next_nodes.sort(key=lambda entry: order[entry.node.node_id])

    segment_records: list[ResolvedEthernetSegment] = []
    for key in segment_keys:
        ipv4_net, ipv6_net = subnet_by_key[key]
        segment_records.append(
            ResolvedEthernetSegment(
                scope_id=key[0],
                segment_id=key[1],
                ipv4_subnet=str(ipv4_net),
                ipv6_subnet=str(ipv6_net),
                members=tuple(
                    ResolvedSegmentMember(node_id=item.node.node_id, interface=interface)
                    for item in sorted(members_by_key[key], key=_segment_order)
                    for interface, site_id, segment in item.ethernet_bindings
                    if (site_id, segment) == key
                ),
            )
        )
    return next_nodes, tuple(segment_records)


def _resolve_origination(
    node_id: str,
    targets: Any,
    bound_segments: dict[str, tuple[str, str]],
    subnet_by_key: dict[tuple[str, str], tuple[Any, Any]],
) -> ResolvedOriginatedPrefixes | None:
    if targets is None:
        return None
    resolved: dict[str, list[str]] = {}
    defaults = {"ipv4": "0.0.0.0/0", "ipv6": "::/0"}
    for family_index, family in enumerate(("ipv4", "ipv6")):
        entries = getattr(targets, family, None)
        if not entries:
            continue
        prefixes: list[str] = []
        for entry in entries:
            if entry == "default":
                prefixes.append(defaults[family])
                continue
            key = bound_segments.get(entry)
            if key is None:
                raise SessionResolutionError(
                    f"node {node_id!r} originates segment {entry!r}, but is not "
                    "bound or attached to it"
                )
            prefixes.append(str(subnet_by_key[key][family_index]))
        seen: set[str] = set()
        resolved[family] = [
            prefix for prefix in prefixes if not (prefix in seen or seen.add(prefix))
        ]
    if not resolved:
        return None
    return ResolvedOriginatedPrefixes.model_validate(resolved)


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
            # An explicit assignment owns the families it pools: every
            # selected node's pooled family is allocated from the pool,
            # replacing any resolver default. Addresses already present
            # elsewhere in the session are excluded from the pool walk so
            # no router identity is minted twice.
            reserved_ipv4, reserved_ipv6 = _existing_loopback_addresses(
                [item for item in nodes if item.node.node_id not in
                 {sel.node.node_id for sel in selected}]
            )
            by_id = {item.node.node_id: item for item in selected}

            def _needs(item: _RuntimeNode, family: str) -> bool:
                del item, family
                return True

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
                        replace(
                            item,
                            node=item.node.model_copy(
                                update={
                                    "interfaces": current.model_copy(update={"lo0": merged_lo0})
                                }
                            ),
                        )
                    )
                    continue
                next_nodes.append(
                    replace(
                        item,
                        node=item.node.model_copy(
                            update={"interfaces": ResolvedNodeInterfaces(lo0=loopback)}
                        ),
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
            replace(
                item,
                node=node.model_copy(update={"interfaces": ResolvedNodeInterfaces(lo0=loopback)}),
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
    """An explicit assignment owns the families it pools.

    Every loopback in the session is resolver-allocated; a family the
    assignment pools replaces the resolver default, and a family it does
    not pool keeps the default.
    """
    del prefix_length, assignment_id, node_id
    return ResolvedInterfaceAddress(
        ipv4=allocated.ipv4 if ipv4_pool is not None else current.ipv4,
        ipv6=allocated.ipv6 if ipv6_pool is not None else current.ipv6,
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


def _merge_originated_prefixes(*sources):
    """Combine symbolic origination intent from any authoring levels.

    Sources are dicts or None; entries combine additively and repeats
    collapse at resolution.
    """
    from nodalarc.models.segments import OriginatedPrefixes

    data: dict[str, list[str]] = {}
    for source in reversed(sources):
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
    endpoint_nodes: list[tuple[ResolvedNode, ...]] = []
    for endpoint in rule.endpoints:
        terminal_role = _endpoint_terminal_role(endpoint.terminal)
        terminal_medium = _endpoint_terminal_medium(endpoint.terminal)
        terminal_mount = _endpoint_terminal_mount(endpoint.terminal)
        selected = _eval_node_selector(endpoint.select, runtime_nodes)
        if not selected:
            raise SessionResolutionError(f"link rule {rule.id!r} selector matched zero nodes")
        matching_blocks = {
            item.node.node_id: tuple(
                block
                for block in item.node.terminal_inventory
                if _terminal_matches(block, endpoint.terminal)
            )
            for item in selected
        }
        compatible = [item for item in selected if matching_blocks[item.node.node_id]]
        if not compatible:
            raise SessionResolutionError(
                f"link rule {rule.id!r} terminal selector matched zero compatible mounts"
            )
        distinct_mounts = sorted(
            {
                block.terminal_id
                for item in compatible
                for block in matching_blocks[item.node.node_id]
            }
        )
        if terminal_mount is None and len(distinct_mounts) > 1:
            raise SessionResolutionError(
                f"link rule {rule.id!r} terminal selector matches multiple terminal mounts "
                f"{distinct_mounts}; select one exact mount"
            )
        matched_media = sorted(
            {block.medium for item in compatible for block in matching_blocks[item.node.node_id]}
        )
        if len(matched_media) != 1:
            raise SessionResolutionError(
                f"link rule {rule.id!r} terminal selector matches multiple terminal media "
                f"{matched_media}; select one compatible medium"
            )
        terminal_medium = matched_media[0]
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
                terminal_role=terminal_role,
                terminal_medium=terminal_medium,
                terminal_id=terminal_mount,
                min_elevation_deg=endpoint.min_elevation_deg,
                node_ids=tuple(item.node.node_id for item in compatible),
            )
        )
        endpoint_nodes.append(tuple(item.node for item in compatible))
    if endpoints[0].terminal_medium != endpoints[1].terminal_medium:
        raise SessionResolutionError(
            f"link rule {rule.id!r} selects incompatible terminal media: "
            f"endpoint 0={endpoints[0].terminal_medium!r}, "
            f"endpoint 1={endpoints[1].terminal_medium!r}"
        )
    kind = _derive_link_label(rule.id, endpoints, endpoint_nodes)
    for endpoint_index, (authored, nodes) in enumerate(
        zip(rule.endpoints, endpoint_nodes, strict=True)
    ):
        if authored.min_elevation_deg is None:
            continue
        if kind != "access":
            raise SessionResolutionError(
                f"link rule {rule.id!r} endpoint {endpoint_index} declares min_elevation_deg, "
                "but the field is valid only on the ground endpoint of an access rule"
            )
        non_ground = sorted(node.node_id for node in nodes if node.kind != "ground_station")
        if non_ground:
            raise SessionResolutionError(
                f"link rule {rule.id!r} endpoint {endpoint_index} declares min_elevation_deg "
                f"for non-ground node(s): {non_ground}; the field is valid only on the ground "
                "endpoint of an access rule"
            )
    return ResolvedLinkRule(
        rule_id=rule.id,
        kind=kind,
        enabled=rule.enabled,
        endpoints=(endpoints[0], endpoints[1]),
        topology=rule.topology,
        constraints=rule.constraints,
        tags=tuple(rule.tags or ()),
    )


def _check_workload_adapter_support(
    runtime_nodes: tuple[_RuntimeNode, ...],
    support: RuntimeSupport,
) -> None:
    for item in runtime_nodes:
        adapter = item.profile_adapter
        if adapter is None:
            continue
        if feature := support.check_workload_adapter(adapter):
            raise UnsupportedFeatureError([feature])


def _resolve_routing_domains(
    cfg: SegmentSessionConfig,
    runtime_nodes: tuple[_RuntimeNode, ...],
) -> list[ResolvedRoutingDomain]:
    # A routing domain is a declaration about routers. A node is a router
    # exactly when its profile's adapter renders routing configuration;
    # membership derives from that router population.
    if cfg.routing is None:
        routers = tuple(
            sorted(
                item.node.node_id
                for item in runtime_nodes
                if adapter_renders(item.profile_adapter, "isis")
            )
        )
        if not routers:
            raise SessionResolutionError(
                "session declares no routing and resolves zero routers rendering IS-IS"
            )
        return [
            ResolvedRoutingDomain(
                domain_id="default_domain",
                protocol="isis",
                timers=_effective_routing_timers("isis", None),
                node_ids=routers,
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
        # Membership is the routers among the selected nodes whose adapter
        # renders this domain's protocol and declared capabilities.
        selected_ids = {
            item.node.node_id
            for item in runtime_nodes
            if item.node.node_id in selected_ids
            and adapter_renders(item.profile_adapter, domain.protocol, tuple(capabilities))
        }
        if not selected_ids:
            raise SessionResolutionError(f"routing domain {domain.id!r} contains zero routers")
        selected_nodes = tuple(
            item.node for item in runtime_nodes if item.node.node_id in selected_ids
        )
        _validate_area_assignment(domain, selected_nodes)
        domains.append(
            ResolvedRoutingDomain(
                domain_id=domain.id,
                protocol=domain.protocol,
                timers=_effective_routing_timers(domain.protocol, domain.timers),
                node_ids=tuple(sorted(selected_ids)),
                capabilities=tuple(capabilities),
                area_assignment=domain.area_assignment,
            )
        )
    _validate_routing_domain_partition(domains, runtime_nodes)
    return domains


def _validate_area_assignment(
    domain: RoutingDomain,
    selected_nodes: tuple[ResolvedNode, ...],
) -> None:
    assignment = domain.area_assignment
    if assignment is None or assignment.strategy == "flat":
        return

    satellites = tuple(node for node in selected_nodes if node.kind == "satellite")
    if assignment.strategy in {"per_plane", "stripe"} and not satellites:
        raise SessionResolutionError(
            f"{assignment.strategy} area assignment in domain {domain.id!r} requires "
            "at least one selected satellite"
        )
    unaddressable_satellites = sorted(node.node_id for node in satellites if node.plane is None)
    if unaddressable_satellites:
        raise SessionResolutionError(
            f"{assignment.strategy} area assignment in domain {domain.id!r} cannot target "
            "satellite(s) "
            f"without a resolved plane: {unaddressable_satellites}"
        )
    selected_planes = {node.plane for node in satellites if node.plane is not None}

    if assignment.strategy in {"per_plane", "stripe"}:
        if assignment.strategy == "per_plane":
            highest_area_index = max(selected_planes) + 1
        else:
            assert assignment.planes_per_stripe is not None
            highest_area_index = max(selected_planes) // assignment.planes_per_stripe + 1
        maximum = 255 if domain.protocol == "ospf" else 9999
        if highest_area_index > maximum:
            raise SessionResolutionError(
                f"{assignment.strategy} area assignment in domain {domain.id!r} derives "
                f"area index {highest_area_index}, exceeding the {domain.protocol} "
                f"derived-area limit {maximum}"
            )
        return

    if assignment.strategy != "explicit":
        raise SessionResolutionError(
            f"unsupported area assignment strategy {assignment.strategy!r}"
        )

    ground_by_local_id = {
        node.local_node_id: node for node in selected_nodes if node.kind == "ground_station"
    }
    plane_owner: dict[int, str] = {}
    ground_owner: dict[str, str] = {}
    for mapping in assignment.assignments or ():
        for plane in mapping.planes or ():
            if plane not in selected_planes:
                raise SessionResolutionError(
                    f"explicit area assignment in domain {domain.id!r} targets plane {plane}, "
                    "but that plane is not selected into the domain"
                )
            if existing := plane_owner.get(plane):
                raise SessionResolutionError(
                    f"explicit area assignment in domain {domain.id!r} maps plane {plane} "
                    f"more than once ({existing!r}, {mapping.area_id!r})"
                )
            plane_owner[plane] = mapping.area_id

        ground_targets = mapping.ground_stations
        if ground_targets is None:
            continue
        if ground_targets == "all":
            target_ids = tuple(ground_by_local_id)
            if not target_ids:
                raise SessionResolutionError(
                    f"explicit area assignment in domain {domain.id!r} targets all ground "
                    "stations, but the domain contains none"
                )
        else:
            unknown = sorted(set(ground_targets) - set(ground_by_local_id))
            if unknown:
                raise SessionResolutionError(
                    f"explicit area assignment in domain {domain.id!r} targets unknown ground "
                    f"station local_node_id value(s): {unknown}"
                )
            target_ids = ground_targets
        for local_node_id in target_ids:
            if existing := ground_owner.get(local_node_id):
                raise SessionResolutionError(
                    f"explicit area assignment in domain {domain.id!r} maps ground station "
                    f"{local_node_id!r} more than once ({existing!r}, {mapping.area_id!r})"
                )
            ground_owner[local_node_id] = mapping.area_id

    missing_planes = sorted(selected_planes - set(plane_owner))
    if missing_planes:
        raise SessionResolutionError(
            f"explicit area assignment in domain {domain.id!r} has no mapping for "
            f"selected plane(s): {missing_planes}"
        )


def _effective_routing_timers(
    protocol: str,
    timers: RoutingTimers | None,
) -> RoutingTimers:
    effective = timers or RoutingTimers()
    if protocol != "isis":
        return effective
    spf = effective.spf
    return effective.model_copy(
        update={
            "spf": spf.model_copy(
                update={
                    "holddown_ms": 2000 if spf.holddown_ms is None else spf.holddown_ms,
                    "time_to_learn_ms": (
                        500 if spf.time_to_learn_ms is None else spf.time_to_learn_ms
                    ),
                }
            )
        }
    )


def _derive_host_attachments(
    runtime_nodes: tuple[_RuntimeNode, ...],
) -> tuple[_RuntimeNode, ...]:
    """Derive substrate attachment facts for host-forwarding nodes.

    A host node attaches to the segment its allocated Ethernet address
    names; its gateway is the routed node holding an address on that same
    subnet. With more than one routed node on the segment the lowest node
    id is the gateway, deterministically. Host attachment is substrate
    configuration derived from allocation; nothing here is a protocol
    decision.
    """
    routed_segment_ports: list[tuple[str, Any]] = []
    for item in runtime_nodes:
        node = item.node
        if node.forwarding != "routed" or node.interfaces is None:
            continue
        for address in node.interfaces.ethernet.values():
            if address.ipv4:
                routed_segment_ports.append(
                    (node.node_id, ipaddress.ip_interface(address.ipv4))
                )
    routed_segment_ports.sort(key=lambda entry: entry[0])

    # A session with zero routed nodes refuses at routing-domain
    # resolution with the documented message; deriving attachments first
    # would bury that fundamental refusal under a gateway complaint.
    if not any(item.node.forwarding == "routed" for item in runtime_nodes):
        return runtime_nodes

    next_nodes: list[_RuntimeNode] = []
    for item in runtime_nodes:
        node = item.node
        if node.forwarding != "host":
            next_nodes.append(item)
            continue
        ethernet = dict(node.interfaces.ethernet) if node.interfaces is not None else {}
        attach_entry = next(
            (
                (name, address)
                for name, address in sorted(ethernet.items())
                if address.ipv4
            ),
            None,
        )
        if attach_entry is None:
            if node.kind == "ground_station":
                raise SessionResolutionError(
                    f"host node {node.node_id!r} requires segment addressing from placement"
                )
            # A host-forwarding space node is valid structural grammar with
            # no derivable segment attachment; it resolves without one, and
            # any consumer that needs attachment facts refuses it there.
            next_nodes.append(item)
            continue
        interface_name, host_address = attach_entry
        host_port = ipaddress.ip_interface(host_address.ipv4)
        gateway = next(
            (
                (gateway_id, gateway_port)
                for gateway_id, gateway_port in routed_segment_ports
                if gateway_port.network == host_port.network
            ),
            None,
        )
        if gateway is None:
            raise SessionResolutionError(
                f"host node {node.node_id!r} has no routed gateway on its "
                f"segment {host_port.network}"
            )
        gateway_id, gateway_port = gateway
        attached = node.model_copy(
            update={
                "host_attachment": ResolvedHostAttachment(
                    interface=interface_name,
                    ipv4=str(host_port),
                    gateway_ipv4=str(gateway_port.ip),
                    gateway_node_id=gateway_id,
                )
            }
        )
        next_nodes.append(replace(item, node=attached))
    return tuple(next_nodes)


def _validate_routing_domain_partition(
    domains: list[ResolvedRoutingDomain],
    runtime_nodes: tuple[_RuntimeNode, ...],
) -> None:
    # Coverage is owed to the routers: the nodes whose profile's adapter
    # renders routing configuration.
    domain_ids_by_node: dict[str, list[str]] = {
        item.node.node_id: []
        for item in runtime_nodes
        if adapter_renders_routing(item.profile_adapter)
    }
    for domain in domains:
        for node_id in domain.node_ids:
            if node_id in domain_ids_by_node:
                domain_ids_by_node[node_id].append(domain.domain_id)
    missing = [node_id for node_id, domain_ids in domain_ids_by_node.items() if not domain_ids]
    if missing:
        raise SessionResolutionError(
            f"routing domains must cover every router; "
            f"{len(missing)} node{'s are' if len(missing) != 1 else ' is'} in no domain "
            f"(e.g. {', '.join(sorted(missing)[:3])})"
        )
    overlaps = {
        node_id: domain_ids
        for node_id, domain_ids in domain_ids_by_node.items()
        if len(domain_ids) > 1
    }
    if overlaps:
        # Summarize, and address the wall: name the overlapping domains and a
        # few example nodes — enumerating every member of a large session was
        # a wall of prose no one could act on. The subject is the last
        # declared overlapping domain: the one most recently added is the one
        # whose membership to fix.
        domain_names = sorted({d for ids in overlaps.values() for d in ids})
        examples = ", ".join(sorted(overlaps)[:3])
        declared_order = [d.domain_id for d in domains]
        subject = next(
            (d for d in reversed(declared_order) if d in domain_names),
            domain_names[-1],
        )
        raise SessionResolutionError(
            f"routing domains must be disjoint: {', '.join(domain_names)} share "
            f"{len(overlaps)} node{'s' if len(overlaps) != 1 else ''} "
            f"(e.g. {examples})",
            subject_kind="routing_domain",
            subject_id=subject,
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
    selector is the binding declaration, and one ground terminal serving two
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


def _eligible_fixed_interfaces(
    node: ResolvedNode,
    rule: ResolvedLinkRule,
    rule_selectors: tuple,
    node_id: str,
) -> list[str]:
    """WAN interfaces on ``node`` whose owning terminal block matches the
    rule's terminal selector on the side(s) that include the node.

    For fixed (non-access) rules only isl-manifest interfaces are eligible
    — the same constraint ``_validate_fixed_interface_capacity`` enforces.
    Without it, a selector that also matches an access mount would let the
    allocator claim (and the facts display advertise as free) an interface
    the capacity validator then vetoes."""
    side_selectors = [
        selector
        for endpoint, selector in zip(rule.endpoints, rule_selectors, strict=True)
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
    if rule.kind == "access":
        return eligible
    return [name for name in eligible if name.startswith("isl")]


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
        # terminal block matches this rule's terminal selector — a candidate
        # must never claim an interface the manifest assigned to a different
        # mount (an rf link on the optical mount's interface is wire fiction).
        node = node_by_id[node_id]
        rule = rules_by_id[rule_id]
        eligible = _eligible_fixed_interfaces(node, rule, selectors[rule_id], node_id)
        used = used_ifaces.setdefault(node_id, set())
        for name in eligible:
            if name not in used:
                used.add(name)
                return name
        raise SessionResolutionError(
            f"link rule {rule_id!r} needs another fixed interface on {node_id!r}, but "
            f"every matching terminal interface is allocated ({sorted(eligible)})",
            subject_kind="link_rule",
            subject_id=rule_id,
            segment_id=node.segment_id,
            node_id=node_id,
        )

    for candidate in declared:
        node_a, node_b = candidate.pair
        left = node_by_id[node_a]
        right = node_by_id[node_b]
        if candidate.kind == "access":
            _validate_access_candidate_endpoints(left, right)
            iface_a = iface_b = None
        else:
            iface_a = _fixed_iface(node_a, candidate.rule_id)
            iface_b = _fixed_iface(node_b, candidate.rule_id)
        role_a, role_b = _candidate_side_roles(
            candidate, node_a, node_b, rules_by_id[candidate.rule_id]
        )
        terminal_id_a, terminal_id_b = _candidate_side_terminal_ids(
            candidate, node_a, node_b, rules_by_id[candidate.rule_id]
        )
        bandwidth = _candidate_bandwidth_mbps(
            left,
            right,
            role_left=role_a,
            role_right=role_b,
            medium=candidate.terminal_medium,
            terminal_id_left=terminal_id_a,
            terminal_id_right=terminal_id_b,
        )
        candidates.append(
            ResolvedLinkCandidate(
                rule_id=candidate.rule_id,
                kind=candidate.kind,
                terminal_roles=(role_a, role_b),
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
    """Bound the candidate graph before materializing it.

    Multi-segment sessions must declare candidate_limits — an all-by-all rule
    over composed segments is exactly the case the budget exists for, and an
    absent budget must not mean an absent bound. Static mode-aware per-rule and
    aggregate upper bounds are checked against the declared budget before any
    pair, interface, or rank is built.
    """
    limits = cfg.simulation.candidate_limits if cfg.simulation is not None else None
    if limits is None:
        if len(cfg.segments) > 1 and resolved.link_rules:
            raise SessionResolutionError(
                "multi-segment sessions with link rules must declare simulation.candidate_limits"
            )
        return
    aggregate_bound = 0
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
            continue
        aggregate_bound += bound
        if bound > limits.max_pairs_per_rule:
            raise SessionResolutionError(
                f"link rule {rule.rule_id!r} declares a static candidate upper bound of "
                f"{bound} pairs ({mode}), exceeding "
                f"simulation.candidate_limits.max_pairs_per_rule={limits.max_pairs_per_rule} "
                "before materialization"
            )
    if aggregate_bound > limits.max_pairs_per_tick:
        raise SessionResolutionError(
            "enabled link rules declare a static aggregate candidate upper bound of "
            f"{aggregate_bound} pairs, exceeding "
            "simulation.candidate_limits.max_pairs_per_tick="
            f"{limits.max_pairs_per_tick} before materialization"
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


def _validate_access_candidate_endpoints(left: ResolvedNode, right: ResolvedNode) -> None:
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


def _candidate_side_roles(
    candidate: Any,
    node_a: str,
    node_b: str,
    rule: ResolvedLinkRule,
) -> tuple[str, str]:
    """Map endpoint-ordered terminal roles onto the candidate's pair order."""

    def _role_for(node_id: str) -> str:
        for endpoint, role in zip(rule.endpoints, candidate.terminal_roles, strict=True):
            if node_id in endpoint.node_ids:
                return role
        raise SessionResolutionError(
            f"candidate node {node_id!r} belongs to neither endpoint of rule {rule.rule_id!r}"
        )

    return _role_for(node_a), _role_for(node_b)


def _candidate_side_terminal_ids(
    candidate: Any,
    node_a: str,
    node_b: str,
    rule: ResolvedLinkRule,
) -> tuple[str | None, str | None]:
    """Map endpoint-ordered exact mount IDs onto candidate pair order."""

    def _terminal_id_for(node_id: str) -> str | None:
        for endpoint, terminal_id in zip(
            rule.endpoints, candidate.endpoint_terminal_ids, strict=True
        ):
            if node_id in endpoint.node_ids:
                return terminal_id
        raise SessionResolutionError(
            f"candidate node {node_id!r} belongs to neither endpoint of rule {rule.rule_id!r}"
        )

    return _terminal_id_for(node_a), _terminal_id_for(node_b)


def _candidate_bandwidth_mbps(
    left: ResolvedNode,
    right: ResolvedNode,
    *,
    role_left: str,
    role_right: str,
    medium: str | None,
    terminal_id_left: str | None,
    terminal_id_right: str | None,
) -> float:
    left_bandwidth = _matching_terminal_bandwidth_mbps(
        left,
        role=role_left,
        medium=medium,
        terminal_id=terminal_id_left,
    )
    right_bandwidth = _matching_terminal_bandwidth_mbps(
        right,
        role=role_right,
        medium=medium,
        terminal_id=terminal_id_right,
    )
    return min(left_bandwidth, right_bandwidth)


def _matching_terminal_bandwidth_mbps(
    node: ResolvedNode,
    *,
    role: str,
    medium: str | None,
    terminal_id: str | None,
) -> float:
    matches = [
        block
        for block in node.terminal_inventory
        if block.endpoint_role == role
        and (medium is None or block.medium == medium)
        and (terminal_id is None or block.terminal_id == terminal_id)
    ]
    if not matches:
        raise SessionResolutionError(
            f"node {node.node_id!r} has no terminal block for role={role!r} "
            f"medium={medium!r} mount={terminal_id!r}"
        )
    if any(block.bandwidth_mbps is None for block in matches):
        raise SessionResolutionError(
            f"node {node.node_id!r} matching terminal mount is missing bandwidth"
        )
    bandwidths = {
        float(block.bandwidth_mbps) for block in matches if block.bandwidth_mbps is not None
    }
    if len(bandwidths) != 1:
        mounts = [block.terminal_id for block in matches]
        raise SessionResolutionError(
            f"node {node.node_id!r} terminal selector matches mounts {mounts} with "
            f"heterogeneous bandwidths {sorted(bandwidths)}; select one exact mount"
        )
    return next(iter(bandwidths))


def _validate_fixed_interface_capacity(
    candidates: list[ResolvedLinkCandidate],
    node_by_id: dict[str, ResolvedNode],
) -> None:
    used: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.kind == "access":
            continue
        interface_a, interface_b = candidate.fixed_interfaces
        used.setdefault(candidate.node_a, set()).add(interface_a)
        used.setdefault(candidate.node_b, set()).add(interface_b)
    for node_id, interfaces in used.items():
        available = {
            iface.name
            for iface in node_by_id[node_id].wan_interfaces
            if iface.name.startswith("isl")
        }
        extra = sorted(interfaces - available)
        if extra:
            raise SessionResolutionError(
                f"fixed link candidate(s) landed on interface(s) {extra} of node "
                f"{node_id!r}, which are not fixed-link capable "
                f"(fixed-capable: {sorted(available)})",
                subject_kind="node",
                subject_id=node_id,
                segment_id=node_by_id[node_id].segment_id,
                node_id=node_id,
            )


def _pair_rank_map(resolved: ResolvedSession) -> dict[tuple[str, str], float]:
    node_by_id = {node.node_id: node for node in resolved.nodes}
    radius_by_body = {facts.body_id: facts.mean_radius_km for facts in resolved.bodies}
    tle_positions = _tle_rank_positions(resolved)
    positions = {
        node.node_id: _static_rank_position(node, radius_by_body, tle_positions)
        for node in resolved.nodes
    }
    ranks: dict[tuple[str, str], float] = {}
    for rule in resolved.link_rules:
        for a in rule.endpoints[0].node_ids:
            for b in rule.endpoints[1].node_ids:
                if a == b:
                    continue
                pair = (a, b) if a < b else (b, a)
                ranks[pair] = _pair_static_rank(
                    node_by_id[a],
                    node_by_id[b],
                    positions,
                )
    return ranks


def _pair_static_rank(
    left: ResolvedNode,
    right: ResolvedNode,
    positions: dict[str, tuple[float, float, float] | None],
) -> float:
    left_pos = positions[left.node_id]
    right_pos = positions[right.node_id]
    if left_pos is None or right_pos is None:
        raise SessionResolutionError(
            f"cannot rank pair {left.node_id}<->{right.node_id} by distance: a node "
            "has neither resolved orbit facts nor a surface position"
        )
    return math.dist(left_pos, right_pos)


def _static_rank_position(
    node: ResolvedNode,
    radius_by_body: dict[str, float],
    tle_positions: dict[str, tuple[float, float, float]],
) -> tuple[float, float, float] | None:
    """Epoch position for nearest-N ranking: orbital state for space nodes,
    body-fixed surface position for placed ground nodes (resolved body radius,
    never a hardcoded constant). A "nearest" rank derived from node-id
    character sums would be geometry theater."""
    orbital = tle_positions.get(node.node_id) or _orbit_rank_position(node)
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


def _tle_rank_positions(resolved: ResolvedSession) -> dict[str, tuple[float, float, float]]:
    tle_nodes = [
        node
        for node in resolved.nodes
        if node.orbit is not None and node.orbit.propagator == "sgp4_tle"
    ]
    if not tle_nodes:
        return {}
    if resolved.time is None:
        raise SessionResolutionError("SGP4/TLE nearest-node ranking requires session time")
    epoch_unix = session_epoch_unix(resolved.time)
    body_facts = {facts.body_id: facts for facts in resolved.bodies}
    positions: dict[str, tuple[float, float, float]] = {}
    for node in tle_nodes:
        orbit = node.orbit
        if orbit is None or orbit.tle_line_1 is None or orbit.tle_line_2 is None:
            raise SessionResolutionError(
                f"space node {node.node_id!r} is missing resolved TLE records"
            )
        facts = body_facts.get(orbit.central_body)
        if facts is None:
            raise SessionResolutionError(
                f"space node {node.node_id!r} is missing body facts for {orbit.central_body!r}"
            )
        support = body_runtime_support_for(facts.body_id)
        frame = BodyFrame(
            name=facts.body_id,
            mean_radius_km=facts.mean_radius_km,
            equatorial_radius_km=facts.equatorial_radius_km,
            polar_radius_km=facts.polar_radius_km,
            rotation_rate_rad_s=support.rotation_rate_rad_s,
            gravitational_parameter_km3_s2=facts.gravitational_parameter_km3_s2,
            j2=support.j2,
        )
        position, _velocity, _geodetic = propagate_sgp4_tle(
            orbit.tle_line_1,
            orbit.tle_line_2,
            epoch_unix,
            0.0,
            body_frame=frame,
        )
        positions[node.node_id] = (position.x, position.y, position.z)
    return positions


def _orbit_rank_position(node: ResolvedNode) -> tuple[float, float, float] | None:
    if node.orbit is None:
        return None
    orbit = node.orbit
    if orbit.propagator == "sgp4_tle":
        return None
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


def _endpoint_terminal_mount(selector: TerminalSelector) -> str | None:
    """Return the mount required by every positive selector branch, if any."""
    if selector.mount is not None:
        return selector.mount
    if selector.not_ is not None or selector.role is not None or selector.medium is not None:
        return None
    if selector.all is not None:
        required = {
            mount
            for child in selector.all
            if (mount := _endpoint_terminal_mount(child)) is not None
        }
        if len(required) > 1:
            raise SessionResolutionError(
                f"link endpoint terminal selector requires conflicting mounts: {sorted(required)}"
            )
        return next(iter(required)) if required else None
    if selector.any is not None:
        required = tuple(_endpoint_terminal_mount(child) for child in selector.any)
        if (
            required
            and required[0] is not None
            and all(mount == required[0] for mount in required[1:])
        ):
            return required[0]
        return None
    raise AssertionError("unreachable terminal selector")


def _link_class_body(node: ResolvedNode) -> str:
    body = node.central_body or node.reference_body
    if body is None:
        raise SessionResolutionError(
            f"cannot derive link class for node {node.node_id!r}: no resolved body"
        )
    return body


def _derive_link_label(
    rule_id: str,
    endpoints: list[ResolvedEndpoint],
    endpoint_nodes: list[tuple[ResolvedNode, ...]],
) -> str:
    left, right = endpoints
    # An access-role endpoint makes the rule an access rule regardless of
    # segment arrangement — labeling it "isl" sends it down the wrong
    # interface/candidate path with a misleading failure.
    if left.terminal_role == "access" or right.terminal_role == "access":
        return "access"

    left_bodies = {_link_class_body(node) for node in endpoint_nodes[0]}
    right_bodies = {_link_class_body(node) for node in endpoint_nodes[1]}
    body_relations = {
        left_body == right_body for left_body in left_bodies for right_body in right_bodies
    }
    if body_relations == {True}:
        return "isl"
    if body_relations == {False}:
        return "inter_body"
    raise SessionResolutionError(
        f"link rule {rule_id!r} mixes same-body and cross-body endpoint pairs; "
        "split it into body-specific rules so one derived link class applies"
    )


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
    Unsupported dynamic constraints are refused by runtime-support preflight.
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
        if constraints.max_links_per_node is None:
            continue
        if not isinstance(constraints.max_links_per_node, int):
            selected_node_ids = {
                node_id for endpoint in rule.endpoints for node_id in endpoint.node_ids
            }
            for node_id in sorted(selected_node_ids):
                _constraint_limit_for_node(
                    constraints.max_links_per_node,
                    nodes[node_id],
                )
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
        endpoint_domains = tuple(
            {domain_of_node[node_id] for node_id in endpoint.node_ids}
            for endpoint in rule.endpoints
        )
        for endpoint_index, resolved_domains in enumerate(endpoint_domains):
            if len(resolved_domains) != 1:
                raise SessionResolutionError(
                    f"routing boundary over {boundary.over!r} endpoint {endpoint_index} "
                    f"spans routing domains {sorted(resolved_domains)}; each boundary "
                    "endpoint must resolve wholly to one domain"
                )
        left_domain = next(iter(endpoint_domains[0]))
        right_domain = next(iter(endpoint_domains[1]))
        if left_domain == right_domain:
            raise SessionResolutionError(
                f"routing boundary over {boundary.over!r} has both endpoints in routing "
                f"domain {left_domain!r}; boundary endpoints must be in opposite domains"
            )
        boundary_rule_ids.add(rule.rule_id)
        rule_domains = {left_domain, right_domain}
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


def _load_ref_or_object(value: str, roots: CatalogRoots) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, str):
        raise SessionResolutionError(f"expected catalog reference, got {type(value)!r}")
    ref = value if isinstance(value, CatalogRef) else CatalogRef(value)
    path = resolve_catalog_reference(ref, roots)
    data = load_configuration_yaml(path.read_text(encoding="utf-8")) or {}
    try:
        wrapper, model = validate_referenced_configuration_document(ref, data)
    except Exception as exc:
        raise SessionResolutionError(f"invalid catalog object: {exc}") from exc
    if wrapper is None:
        raise SessionResolutionError(f"expected wrapped catalog object, got session {ref!r}")
    return wrapper, model.model_dump(mode="python", by_alias=True, exclude_none=True)


def resolve_env_value(
    value_from: EnvValueFrom,
    nodes: tuple[ResolvedNode, ...],
) -> str:
    """The address, by interface name and family, of the single tagged node."""

    matches = [node for node in nodes if value_from.tag in node.tags]
    if not matches:
        raise SessionResolutionError(f"env value_from tag {value_from.tag!r} matches no node")
    if len(matches) > 1:
        examples = ", ".join(sorted(node.node_id for node in matches)[:3])
        raise SessionResolutionError(
            f"env value_from tag {value_from.tag!r} matches {len(matches)} nodes "
            f"(e.g. {examples}); exactly one is required"
        )
    node = matches[0]
    interfaces: dict[str, Any] = {}
    if node.interfaces is not None:
        interfaces["lo0"] = node.interfaces.lo0
        interfaces.update(node.interfaces.ethernet)
    interface = interfaces.get(value_from.interface)
    if interface is None:
        raise SessionResolutionError(
            f"env value_from tag {value_from.tag!r} matched node {node.node_id!r}, "
            f"which has no interface {value_from.interface!r}"
        )
    address = getattr(interface, value_from.family, None)
    if address is None:
        raise SessionResolutionError(
            f"env value_from: interface {value_from.interface!r} on node "
            f"{node.node_id!r} has no {value_from.family} address"
        )
    return str(ipaddress.ip_interface(address).ip)


def _check_profile_env(
    workload_profiles: Mapping[str, Profile],
    resolved_nodes: tuple[ResolvedNode, ...],
) -> None:
    for reference, profile in sorted(workload_profiles.items()):
        entries = (
            *profile.env,
            *(entry for sidecar in profile.sidecars for entry in sidecar.env),
        )
        for entry in entries:
            if not isinstance(entry, ResolvedEnvEntry):
                continue
            try:
                resolve_env_value(entry.value_from, resolved_nodes)
            except SessionResolutionError as error:
                raise SessionResolutionError(
                    f"profile {reference} env {entry.name!r}: {error}"
                ) from error


def _effective_profile(
    *,
    described: str,
    placed: Any,
    segment_profile: Any,
    definition: Any,
    roots: CatalogRoots,
) -> tuple[str, str, str | None]:
    """The most specific authored profile statement wins; absence is a refusal."""

    if placed is not None:
        reference, level = str(placed), "node"
    elif segment_profile is not None:
        reference, level = str(segment_profile), "segment"
    elif definition is not None:
        reference, level = str(definition), "node_definition"
    else:
        raise SessionResolutionError(
            f"{described} has no workload profile at any level: none on the placed "
            "entry, none on the segment, and none on the node definition. There is "
            "no default workload; state what the node runs."
        )
    profile_body = _load_expected(reference, roots, "profile")
    return reference, level, profile_body.get("adapter")


def _load_expected(ref: str, roots: CatalogRoots, expected_wrapper: str) -> dict[str, Any]:
    wrapper, body = _load_ref_or_object(ref, roots)
    if wrapper != expected_wrapper:
        raise SessionResolutionError(
            f"expected catalog object {expected_wrapper!r}, got {wrapper!r}"
        )
    return body
