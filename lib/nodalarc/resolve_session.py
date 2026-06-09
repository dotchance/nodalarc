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
from nodalarc.models.segment_session import Dispatch, SegmentSessionConfig
from nodalarc.models.segments import GroundSegment, SpaceSegment
from nodalarc.runtime_naming import validate_runtime_node_id
from nodalarc.runtime_support import RuntimeSupport, UnsupportedFeatureError

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
    mounts: dict[str, dict[str, Any]] | None = None
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

    if runtime_support is not None:
        _check_runtime_support(cfg, runtime_support)

    runtime_nodes = _apply_addressing(cfg, _expand_segments(cfg, roots))
    resolved_nodes = tuple(item.node for item in runtime_nodes)
    body_facts = _collect_body_facts(runtime_nodes)
    ephemeris = _resolve_ephemeris(cfg, roots, resolved_nodes)
    link_rules = tuple(
        _resolve_link_rule(rule, cfg, runtime_nodes) for rule in cfg.link_rules or ()
    )
    routing_domains = tuple(_resolve_routing_domains(cfg, runtime_nodes))
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
    resolved = ResolvedSession(
        identity_mode=base_resolved.identity_mode,
        session=base_resolved.session,
        nodes=base_resolved.nodes,
        bodies=base_resolved.bodies,
        link_rules=base_resolved.link_rules,
        link_candidates=tuple(_resolve_link_candidates(base_resolved)),
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


def _check_runtime_support(cfg: SegmentSessionConfig, support: RuntimeSupport) -> None:
    unsupported = []
    for segment in cfg.segments:
        if isinstance(segment, GroundSegment):
            kind = "ground_set"
        elif isinstance(segment, SpaceSegment):
            kind = "constellation"
        else:
            kind = "lagrange_point"
        if feature := support.check_segment_kind(kind):
            unsupported.append(feature)
    if cfg.ephemeris is not None and (
        feature := support.check_ephemeris_provider(cfg.ephemeris.provider)
    ):
        unsupported.append(feature)
    for boundary in (
        cfg.routing.boundaries if cfg.routing is not None and cfg.routing.boundaries else ()
    ):
        if feature := support.check_protocol_adapter(boundary.adapter):
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

    return ResolvedEphemeris(
        provider=cfg.ephemeris.provider,
        quality_tier=cfg.ephemeris.quality_tier,
        kernels=tuple(kernels),
    )


def _ephemeris_target_body_id(target: Any, roots: CatalogRoots) -> str:
    body = _load_expected(target, roots, "body")
    return str(body["id"])


def _expand_segments(cfg: SegmentSessionConfig, roots: CatalogRoots) -> tuple[_RuntimeNode, ...]:
    nodes: list[_RuntimeNode] = []
    for segment in cfg.segments:
        if isinstance(segment, SpaceSegment):
            nodes.extend(_expand_space_segment(segment, roots))
        elif isinstance(segment, GroundSegment):
            nodes.extend(_expand_ground_segment(segment, roots))
        else:
            raise SessionResolutionError(
                f"segment {segment.id!r} uses runtime-unsupported lagrange placement"
            )
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
            phase_deg = (360.0 / slots) * slot + phase_offset
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
                        plane=plane,
                        slot=slot,
                    ),
                    plane=plane,
                    slot=slot,
                    mounts=_mounts_by_id(node),
                    body_facts=(_body_facts_from_catalog(body),),
                )
            )
            # The orbit phase is intentionally retained in the catalog object; the
            # runtime propagation seam will consume it when P5/P6 repoints engines.
            _ = phase_deg
    return expanded


def _space_node_from_entry(
    segment: SpaceSegment, entry: dict[str, Any], roots: CatalogRoots
) -> _RuntimeNode:
    node = _load_expected(entry["node"], roots, "node")
    orbit = _load_expected(entry["orbit"], roots, "orbit") if "orbit" in entry else None
    if orbit is None:
        raise SessionResolutionError(
            f"space node {entry['id']!r} uses state_vector placement, which is "
            "structurally valid but not runtime-supported by the catalog cutover yet"
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
            plane=None,
            slot=None,
        ),
        mounts=_mounts_by_id(node),
        body_facts=(_body_facts_from_catalog(body),),
    )


def _expand_ground_segment(segment: GroundSegment, roots: CatalogRoots) -> list[_RuntimeNode]:
    site_set = _load_expected(segment.placement.from_site_set, roots, "site_set")
    expanded: list[_RuntimeNode] = []
    for site_ref in site_set["sites"]:
        site = _load_expected(site_ref, roots, "site")
        body_ref = site["frame"]["body_fixed"]["body"]
        body = _load_expected(body_ref, roots, "body")
        for site_node in site["nodes"]:
            source_node = _load_expected(site_node["model"], roots, "node")
            local_id = f"{site['id']}-{site_node['id']}"
            runtime_id = _runtime_id(segment.id, local_id)
            tags = set(segment.tags or ())
            tags.update(site.get("tags") or ())
            tags.update(site_node.get("tags") or ())
            tags.update(
                segment.apply.tags if segment.apply is not None and segment.apply.tags else ()
            )
            scheduling = site_node.get("scheduling") or (
                segment.apply.scheduling.model_dump(mode="python")
                if segment.apply is not None and segment.apply.scheduling is not None
                else None
            )
            if site.get("location") is None:
                raise SessionResolutionError(
                    f"site {site['id']!r} is not body-fixed; runtime support requires "
                    "a fixed surface location for placed ground nodes"
                )
            originated = _merge_originated_prefixes(
                site_node.get("originated_prefixes"),
                segment.apply.originated_prefixes.model_dump(mode="python")
                if segment.apply is not None and segment.apply.originated_prefixes is not None
                else None,
            )
            expanded.append(
                _RuntimeNode(
                    node=ResolvedNode(
                        node_id=runtime_id,
                        local_node_id=local_id,
                        segment_id=segment.id,
                        namespace=segment.id,
                        kind="ground_station",
                        frame_id=body["id"],
                        reference_body=body["id"],
                        tags=tuple(sorted(tags)),
                        terminal_inventory=tuple(
                            _terminal_blocks_for_site_node(
                                runtime_id, source_node, site_node, roots
                            )
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
                    ),
                    mounts=_mounts_by_id(source_node),
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
        terminal_inventory=tuple(_terminal_blocks_for_node(runtime_id, source_node, None, roots)),
        wan_interfaces=tuple(_wan_interfaces_for_node(runtime_id, source_node)),
        orbit=orbit,
        forwarding=source_node["forwarding"],
        plane=plane,
        slot=slot,
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


def _terminal_blocks_for_node(
    runtime_id: str,
    source_node: dict[str, Any],
    installs: dict[str, Any] | None,
    roots: CatalogRoots,
) -> list[ResolvedTerminalBlock]:
    blocks: list[ResolvedTerminalBlock] = []
    for mount in source_node["terminals"]:
        terminal = _load_expected(mount["terminal"], roots, "terminal")
        installed = installs.get(mount["id"], {}) if installs is not None else {}
        capabilities = installed.get("capabilities") or {}
        limits = capabilities.get("limits") or terminal["limits"]
        bandwidth = capabilities.get("bandwidth_mbps") or terminal["bandwidth_mbps"]
        count = int(installed.get("installed_count", mount["count"]))
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
                bandwidth_mbps=float(max(bandwidth["transmit"], bandwidth["receive"])),
                source_ref=_catalog_source_ref(mount["terminal"], inline_id=terminal["id"]),
            )
        )
    return blocks


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
    for mount in source_node["terminals"]:
        for _ in range(int(mount["count"])):
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
    for mount in source_node["terminals"]:
        install = site_node["terminals"].get(mount["id"])
        count = int(install["installed_count"]) if install is not None else 0
        for _ in range(count):
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
            ordered_ids = [item.node.node_id for item in selected]
            ipv4 = (
                _allocate_pool_addresses(
                    assignment.ipv4_pool,
                    assignment.prefix_length,
                    count=len(ordered_ids),
                    assignment_id=assignment.id,
                )
                if assignment.ipv4_pool is not None
                else [None] * len(ordered_ids)
            )
            ipv6 = (
                _allocate_pool_addresses(
                    assignment.ipv6_pool,
                    assignment.prefix_length,
                    count=len(ordered_ids),
                    assignment_id=assignment.id,
                )
                if assignment.ipv6_pool is not None
                else [None] * len(ordered_ids)
            )
            allocated = {
                node_id: ResolvedInterfaceAddress(ipv4=v4, ipv6=v6)
                for node_id, v4, v6 in zip(ordered_ids, ipv4, ipv6, strict=True)
                if v4 is not None or v6 is not None
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
                            mounts=item.mounts,
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
                        mounts=item.mounts,
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
                mounts=item.mounts,
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
    if count > available:
        raise SessionResolutionError(
            f"address pool assignment {assignment_id!r} needs {count} address(es), "
            f"but pool {pool}/{prefix_length} has {available}"
        )
    addresses: list[str] = []
    for index in range(count):
        address = ipaddress.ip_address(start + index * subnet_size)
        if address not in network:
            raise SessionResolutionError(
                f"address pool assignment {assignment_id!r} allocated {address} outside {pool}"
            )
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


def _resolve_link_rule(
    rule: LinkRule, cfg: SegmentSessionConfig, runtime_nodes: tuple[_RuntimeNode, ...]
) -> ResolvedLinkRule:
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
        segment_ids = {item.node.segment_id for item in compatible}
        if len(segment_ids) != 1:
            raise SessionResolutionError(
                f"link rule {rule.id!r} endpoint selector spans multiple segments: {sorted(segment_ids)}"
            )
        endpoints.append(
            ResolvedEndpoint(
                segment_id=next(iter(segment_ids)),
                terminal_role=_first_terminal_role(endpoint.terminal),
                terminal_medium=_first_terminal_medium(endpoint.terminal),
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
        return [
            ResolvedRoutingDomain(
                domain_id="default_domain",
                protocol="isis",
                node_ids=tuple(sorted(item.node.node_id for item in runtime_nodes)),
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


def _resolve_link_candidates(resolved: ResolvedSession) -> list[ResolvedLinkCandidate]:
    pair_rank = _pair_rank_map(resolved)
    declared = generate_declared_link_candidates(resolved, pair_rank=pair_rank)
    candidates: list[ResolvedLinkCandidate] = []
    node_by_id = {node.node_id: node for node in resolved.nodes}
    fixed_iface_by_node: dict[str, int] = {}
    for candidate in declared:
        node_a, node_b = candidate.pair
        left = node_by_id[node_a]
        right = node_by_id[node_b]
        if candidate.kind == "access":
            iface_a, iface_b = _access_candidate_interfaces(left, right)
        else:
            iface_a = f"isl{fixed_iface_by_node.get(node_a, 0)}"
            fixed_iface_by_node[node_a] = fixed_iface_by_node.get(node_a, 0) + 1
            iface_b = f"isl{fixed_iface_by_node.get(node_b, 0)}"
            fixed_iface_by_node[node_b] = fixed_iface_by_node.get(node_b, 0) + 1
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
    ranks: dict[tuple[str, str], float] = {}
    for rule in resolved.link_rules:
        for a in rule.endpoints[0].node_ids:
            for b in rule.endpoints[1].node_ids:
                if a == b:
                    continue
                pair = (a, b) if a < b else (b, a)
                ranks[pair] = _pair_static_rank(node_by_id[a], node_by_id[b])
    return ranks


def _pair_static_rank(left: ResolvedNode, right: ResolvedNode) -> float:
    left_pos = _orbit_rank_position(left)
    right_pos = _orbit_rank_position(right)
    if left_pos is None or right_pos is None:
        return float(sum(ord(ch) for ch in f"{left.node_id}\0{right.node_id}"))
    return math.dist(left_pos, right_pos)


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
        return [item for item in universe if item.node.segment_id == selector.segment]
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


def _first_terminal_role(selector: TerminalSelector) -> str:
    if selector.role is not None:
        return selector.role
    children = selector.all or selector.any or ()
    for child in children:
        value = _first_terminal_role(child)
        if value:
            return value
    raise SessionResolutionError("link endpoint terminal selector must include role")


def _first_terminal_medium(selector: TerminalSelector) -> str | None:
    if selector.medium is not None:
        return selector.medium
    children = selector.all or selector.any or ()
    for child in children:
        value = _first_terminal_medium(child)
        if value:
            return value
    return None


def _derive_link_label(endpoints: list[ResolvedEndpoint]) -> str:
    left, right = endpoints
    if left.segment_id == right.segment_id:
        return "isl"
    if left.terminal_role == "access" or right.terminal_role == "access":
        return "access"
    return "inter_body"


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


def _mounts_by_id(node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {mount["id"]: mount for mount in node["terminals"]}


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
