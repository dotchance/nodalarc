# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Shared session resolver for the segment YAML grammar.

This is the single authority that turns user-facing segment YAML into runtime
truth. Production services may consume the internal assets returned here, but
must not parse the old top-level session shape themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from nodalarc.catalog_paths import (
    CatalogRoots,
    config_value_for,
    resolve_constellation_reference,
    resolve_ground_station_reference,
)
from nodalarc.constellation_loader import (
    SatelliteNode,
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.ground_handover import resolve_station_ground_scheduling
from nodalarc.ground_terminals import ground_terminal_type, station_ground_terminal_type
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.identity import IdentityMode
from nodalarc.models.link_rules import LinkRule, NodeSelector, VisibleCandidatesTopology
from nodalarc.models.resolved_session import (
    ResolvedEndpoint,
    ResolvedLinkRule,
    ResolvedNode,
    ResolvedSession,
    ResolvedTerminalBlock,
    SidBlock,
    SourceContext,
)
from nodalarc.models.segment_session import SegmentSessionConfig
from nodalarc.models.segments import ConstellationSegment, GroundSegment
from nodalarc.models.session import (
    AddressingConfig,
    GroundSchedulingConfig,
    SessionConfig,
)
from nodalarc.runtime_naming import (
    gs_bridge_port_name,
    isl_host_name,
    satellite_ground_host_name,
    validate_runtime_node_id,
)
from nodalarc.runtime_support import RuntimeSupport, UnsupportedFeature, UnsupportedFeatureError

_NORMALIZE_RE = re.compile(r"[^a-z0-9-]+")


class SessionResolutionError(ValueError):
    """Raised when a session is structurally valid YAML but invalid runtime intent."""


@dataclass(frozen=True)
class ResolvedConstellationAssets:
    segment: ConstellationSegment
    source: str | dict[str, Any]
    config: ConstellationConfig
    satellites: tuple[SatelliteNode, ...]


@dataclass(frozen=True)
class ResolvedGroundAssets:
    segment: GroundSegment
    source: str | dict[str, Any]
    config: GroundStationFile


@dataclass(frozen=True)
class SessionResolution:
    """Resolver output plus loaded assets for existing runtime engines.

    ``resolved`` is the product contract. The remaining fields are loaded from the
    same resolver pass so OME/Scheduler/Operator can keep using mature internals
    without re-parsing or deriving a divergent view.
    """

    resolved: ResolvedSession
    runtime_session: SessionConfig
    constellations: tuple[ResolvedConstellationAssets, ...]
    ground_sets: tuple[ResolvedGroundAssets, ...]
    addressing: AddressingScheme

    @property
    def primary_constellation(self) -> ResolvedConstellationAssets:
        if len(self.constellations) != 1:
            raise SessionResolutionError(
                "current runtime requires exactly one constellation segment"
            )
        return self.constellations[0]

    @property
    def primary_ground_set(self) -> ResolvedGroundAssets:
        if len(self.ground_sets) != 1:
            raise SessionResolutionError("current runtime requires exactly one ground_set segment")
        return self.ground_sets[0]


def default_catalog_roots() -> CatalogRoots:
    return CatalogRoots.from_config_root(Path("configs"))


def resolve_session(
    raw_session: dict[str, Any],
    *,
    catalog_roots: CatalogRoots | None = None,
    runtime_support: RuntimeSupport | None = None,
    source_context: SourceContext | None = None,
) -> ResolvedSession:
    """Resolve segment YAML into the authoritative runtime model."""

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
    """Resolve segment YAML and return the authoritative model plus runtime assets."""

    if not isinstance(raw_session, dict):
        raise SessionResolutionError("session YAML must parse to a mapping")
    if "segments" not in raw_session:
        if "constellation" in raw_session or "ground_stations" in raw_session:
            raise SessionResolutionError(
                "old session grammar is not supported; use top-level segments and link_rules"
            )
        raise SessionResolutionError("session YAML requires top-level segments")
    if "constellation" in raw_session or "ground_stations" in raw_session:
        raise SessionResolutionError(
            "session YAML must not mix old constellation/ground_stations keys with segments"
        )
    if "satellite_type" in raw_session:
        raise SessionResolutionError(
            "top-level satellite_type is not supported; scope it to a segment"
        )

    roots = catalog_roots or default_catalog_roots()
    support = runtime_support or RuntimeSupport.mvp_m1()
    context = source_context or SourceContext(origin="resolve_session")

    try:
        cfg = SegmentSessionConfig.model_validate(raw_session)
    except ValidationError:
        raise
    except Exception as exc:
        raise SessionResolutionError(f"invalid segment session: {exc}") from exc

    _check_runtime_support(cfg, support)

    const_assets: list[ResolvedConstellationAssets] = []
    ground_assets: list[ResolvedGroundAssets] = []
    for segment in cfg.segments:
        if isinstance(segment, ConstellationSegment):
            const_assets.append(_load_constellation_segment(segment, roots))
        elif isinstance(segment, GroundSegment):
            ground_assets.append(_load_ground_segment(segment, roots))
        else:
            # RuntimeSupport should catch this before we get here. Keep the guard
            # fail-loud so a future matrix bug does not silently skip a segment.
            raise SessionResolutionError(f"segment kind {segment.kind!r} is not runtime-supported")

    if len(const_assets) != 1 or len(ground_assets) != 1:
        raise SessionResolutionError(
            "M1 runtime supports exactly one constellation segment and one ground_set segment; "
            f"got {len(const_assets)} constellation segment(s), {len(ground_assets)} ground_set segment(s)"
        )

    constellation = const_assets[0]
    ground_set = ground_assets[0]
    if not constellation.satellites:
        raise SessionResolutionError(
            f"constellation segment {constellation.segment.id!r} expands to 0 satellites"
        )
    if not ground_set.config.stations:
        raise SessionResolutionError(
            f"ground_set segment {ground_set.segment.id!r} expands to 0 stations"
        )

    effective_ground = _effective_ground_scheduling(cfg, ground_set.segment, ground_set.config)
    runtime_session = _build_runtime_session_projection(
        cfg, constellation, ground_set, effective_ground
    )
    addressing = AddressingScheme(
        runtime_session.addressing, list(constellation.satellites), ground_set.config
    )

    nodes, node_meta = _materialize_nodes(
        constellation, ground_set, runtime_session, addressing, effective_ground
    )
    _validate_runtime_identity_and_interface_names(nodes)
    _validate_m1_link_rule_runtime_shape(cfg, constellation.segment.id, ground_set.segment.id)
    _validate_m1_ground_terminal_compatibility(constellation, ground_set, addressing)
    link_rules = _resolve_link_rules(cfg.link_rules, nodes, node_meta)
    _validate_candidate_budgets(cfg, link_rules)
    sid_blocks = _allocate_sid_blocks(nodes)

    resolved = ResolvedSession(
        identity_mode=IdentityMode.SEGMENT_NAMESPACED,
        session=cfg.session,
        nodes=tuple(nodes),
        link_rules=tuple(link_rules),
        sid_blocks=tuple(sid_blocks),
        simulation=cfg.simulation,
        orbit=cfg.orbit,
        routing=cfg.routing,
        dispatch=cfg.dispatch,
        scheduling=runtime_session.scheduling,
        addressing=runtime_session.addressing,
        observability=cfg.observability,
        time=cfg.time,
        placement=cfg.placement,
        mi=cfg.mi,
        traffic_flows=tuple(cfg.traffic_flows or ()),
        terrestrial_links=tuple(cfg.terrestrial_links or ()),
        source_context=context,
    )
    return SessionResolution(
        resolved=resolved,
        runtime_session=runtime_session,
        constellations=tuple(const_assets),
        ground_sets=tuple(ground_assets),
        addressing=addressing,
    )


def load_session_resolution_from_file(
    session_path: str | Path,
    *,
    catalog_roots: CatalogRoots | None = None,
    runtime_support: RuntimeSupport | None = None,
    origin: str = "file",
) -> SessionResolution:
    raw = yaml.safe_load(Path(session_path).read_text())
    return resolve_session_with_assets(
        raw,
        catalog_roots=catalog_roots,
        runtime_support=runtime_support,
        source_context=SourceContext(origin=origin, session_path=str(session_path)),
    )


def _check_runtime_support(cfg: SegmentSessionConfig, support: RuntimeSupport) -> None:
    unsupported: list[UnsupportedFeature] = []
    for segment in cfg.segments:
        if feature := support.check_segment_kind(segment.kind):
            unsupported.append(feature)
        central_body = getattr(segment, "central_body", None)
        if central_body and (feature := support.check_central_body(str(central_body))):
            unsupported.append(feature)
        reference_body = getattr(segment, "reference_body", None)
        if reference_body and (feature := support.check_reference_body(str(reference_body))):
            unsupported.append(feature)
        frame = getattr(segment, "frame", None)
        if frame is not None:
            for body in (frame.primary_body, frame.secondary_body):
                if feature := support.check_frame_body(str(body)):
                    unsupported.append(feature)
    for rule in cfg.link_rules:
        if rule.protocol_boundary is not None and (
            feature := support.check_protocol_adapter(rule.protocol_boundary.adapter)
        ):
            unsupported.append(feature)
    if cfg.ephemeris is not None and (
        feature := support.check_ephemeris_provider(cfg.ephemeris.provider)
    ):
        unsupported.append(feature)
    if unsupported:
        raise UnsupportedFeatureError(unsupported)


def _load_constellation_segment(
    segment: ConstellationSegment,
    roots: CatalogRoots,
) -> ResolvedConstellationAssets:
    source: str | dict[str, Any]
    if isinstance(segment.source, str):
        source_path = resolve_constellation_reference(segment.source, roots)
        source = config_value_for(source_path)
        raw = yaml.safe_load(source_path.read_text())
        if segment.satellite_type is not None:
            if not isinstance(raw, dict):
                raise SessionResolutionError(
                    f"constellation segment {segment.id!r} source is not a mapping"
                )
            raw = dict(raw)
            raw["satellite_type"] = segment.satellite_type
            raw.pop("default_terminals", None)
            config = load_constellation(raw)
            source = raw
        else:
            config = load_constellation(source_path)
    else:
        raw = segment.source.model_dump(mode="python")
        if segment.satellite_type is not None:
            raw = dict(raw)
            raw["satellite_type"] = segment.satellite_type
            raw.pop("default_terminals", None)
        config = load_constellation(raw)
        source = raw
    satellites = tuple(expand_constellation(config))
    return ResolvedConstellationAssets(
        segment=segment, source=source, config=config, satellites=satellites
    )


def _load_ground_segment(segment: GroundSegment, roots: CatalogRoots) -> ResolvedGroundAssets:
    if isinstance(segment.source, str):
        source_path = resolve_ground_station_reference(segment.source, roots)
        source: str | dict[str, Any] = config_value_for(source_path)
        config = load_ground_stations(source_path)
    else:
        source = segment.source
        config = load_ground_stations(segment.source)
    return ResolvedGroundAssets(segment=segment, source=source, config=config)


def _build_runtime_session_projection(
    cfg: SegmentSessionConfig,
    constellation: ResolvedConstellationAssets,
    ground_set: ResolvedGroundAssets,
    effective_ground: GroundSchedulingConfig,
) -> SessionConfig:
    sat_namespace = constellation.segment.namespace
    gs_namespace = ground_set.segment.namespace
    addressing = AddressingConfig(
        sat_id_template=f"{sat_namespace}-sat-p{{plane:02d}}s{{slot:02d}}",
        gs_id_template=f"{gs_namespace}-gs-{{name}}",
        ipv4_sat_template=cfg.addressing.ipv4_sat_template,
        ipv4_gs_template=cfg.addressing.ipv4_gs_template,
        ipv6_sat_template=cfg.addressing.ipv6_sat_template,
        ipv6_gs_template=cfg.addressing.ipv6_gs_template,
    )
    raw = {
        "session": cfg.session.model_dump(mode="python"),
        "constellation": constellation.source,
        "ground_stations": ground_set.source,
        "simulation": cfg.simulation.model_dump(mode="python"),
        "orbit": cfg.orbit.model_dump(mode="python"),
        "scheduling": {"ground": effective_ground.model_dump(mode="python")},
        "dispatch": cfg.dispatch.model_dump(mode="python"),
        "observability": cfg.observability.model_dump(mode="python"),
        "addressing": addressing.model_dump(mode="python"),
        "routing": cfg.routing.model_dump(mode="python"),
        "time": cfg.time.model_dump(mode="python"),
        "placement": cfg.placement.model_dump(mode="python"),
        "mi": cfg.mi.model_dump(mode="python"),
    }
    if cfg.traffic_flows is not None:
        raw["traffic_flows"] = [f.model_dump(mode="python") for f in cfg.traffic_flows]
    if cfg.terrestrial_links is not None:
        raw["terrestrial_links"] = [t.model_dump(mode="python") for t in cfg.terrestrial_links]
    return SessionConfig.model_validate(raw)


def _effective_ground_scheduling(
    cfg: SegmentSessionConfig,
    segment: GroundSegment,
    gs_file: GroundStationFile,
) -> GroundSchedulingConfig:
    if cfg.scheduling is None and segment.scheduling is None:
        raise SessionResolutionError(
            f"ground segment {segment.id!r} must declare scheduling, or the session "
            "must declare explicit scheduling defaults"
        )
    data = (
        cfg.scheduling.ground.model_dump(mode="python")
        if cfg.scheduling is not None
        else GroundSchedulingConfig().model_dump(mode="python")
    )
    if gs_file.default_selection_policy is not None:
        data["selection_policy"] = gs_file.default_selection_policy.model_dump(mode="python")
    if gs_file.default_handover_policy is not None:
        data["handover_policy"] = gs_file.default_handover_policy.model_dump(mode="python")
    if gs_file.default_handover_mode is not None:
        data["handover_mode"] = gs_file.default_handover_mode
    if gs_file.default_mbb_overlap_ticks is not None:
        data["mbb_overlap_ticks"] = gs_file.default_mbb_overlap_ticks
    if gs_file.default_mbb_reserve is not None:
        data["mbb_reserve"] = gs_file.default_mbb_reserve
    if segment.scheduling is not None:
        data.update(segment.scheduling.model_dump(mode="python", exclude_none=True))
    return GroundSchedulingConfig.model_validate(data)


def _materialize_nodes(
    constellation: ResolvedConstellationAssets,
    ground_set: ResolvedGroundAssets,
    session: SessionConfig,
    addressing: AddressingScheme,
    effective_ground: GroundSchedulingConfig,
) -> tuple[list[ResolvedNode], dict[str, dict[str, Any]]]:
    nodes: list[ResolvedNode] = []
    meta: dict[str, dict[str, Any]] = {}
    const_segment = constellation.segment
    ground_segment = ground_set.segment

    for sat in constellation.satellites:
        local_id = f"sat-P{sat.plane:02d}S{sat.slot:02d}"
        node_id = addressing.sat_id(sat.plane, sat.slot)
        tags = tuple(const_segment.tags or ())
        node = ResolvedNode(
            node_id=node_id,
            local_node_id=local_id,
            segment_id=const_segment.id,
            namespace=const_segment.namespace,
            kind="satellite",
            frame_id=str(const_segment.central_body),
            central_body=const_segment.central_body,
            tags=tags,
            satellite_type=const_segment.satellite_type
            or getattr(constellation.config, "satellite_type", None),
            tenant_id=getattr(constellation.config, "tenant_id", "default"),
            terminal_inventory=tuple(_satellite_terminal_blocks(node_id, sat, const_segment.id)),
            clock=const_segment.clock or cfg_clock_default(),
        )
        nodes.append(node)
        meta[node_id] = {
            "segment_id": const_segment.id,
            "local_node_id": local_id,
            "tags": set(tags),
            "kind": "satellite",
            "plane": sat.plane,
            "slot": sat.slot,
            "name": None,
        }

    for station in ground_set.config.stations:
        local_id = f"gs-{station.name}"
        node_id = addressing.gs_id(station.name)
        tags = tuple(ground_segment.tags or ())
        station_policy = _station_ground_scheduling(effective_ground, ground_set.config, station)
        node = ResolvedNode(
            node_id=node_id,
            local_node_id=local_id,
            segment_id=ground_segment.id,
            namespace=ground_segment.namespace,
            kind="ground_station",
            frame_id=str(ground_segment.reference_body),
            reference_body=ground_segment.reference_body,
            tags=tags,
            tenant_id=station.tenant_id,
            terminal_inventory=tuple(_ground_terminal_blocks(node_id, station, ground_set.config)),
            ground_scheduling=station_policy,
        )
        nodes.append(node)
        meta[node_id] = {
            "segment_id": ground_segment.id,
            "local_node_id": local_id,
            "tags": set(tags),
            "kind": "ground_station",
            "plane": None,
            "slot": None,
            "name": station.name,
        }
    return nodes, meta


def cfg_clock_default():
    from nodalarc.models.segments import SegmentClock

    return SegmentClock()


def _station_ground_scheduling(
    base: GroundSchedulingConfig, gs_file: GroundStationFile, station: Any
) -> GroundSchedulingConfig:
    return resolve_station_ground_scheduling(base, gs_file, station).scheduling


def _satellite_terminal_blocks(node_id: str, sat: SatelliteNode, segment_id: str):
    for index, terminal in enumerate(sat.isl_terminals):
        yield ResolvedTerminalBlock(
            terminal_id=f"{node_id}#isl[{index}]",
            owner_node_id=node_id,
            endpoint_role="isl",
            medium=terminal.type,
            count=terminal.count,
            max_range_km=terminal.max_range_km,
            field_of_regard_deg=getattr(terminal, "field_of_regard_deg", None),
            tracking_rate_deg_s=getattr(terminal, "max_tracking_rate_deg_s", None),
            bandwidth_mbps=terminal.bandwidth_mbps,
            source_ref=f"segment:{segment_id}#satellite.isl[{index}]",
        )
    for index, terminal in enumerate(sat.ground_terminals):
        yield ResolvedTerminalBlock(
            terminal_id=f"{node_id}#ground[{index}]",
            owner_node_id=node_id,
            endpoint_role="ground",
            medium=terminal.type,
            count=terminal.count,
            max_range_km=getattr(terminal, "max_range_km", None),
            field_of_regard_deg=getattr(terminal, "field_of_regard_deg", None),
            tracking_rate_deg_s=getattr(terminal, "max_tracking_rate_deg_s", None),
            bandwidth_mbps=terminal.bandwidth_mbps,
            source_ref=f"segment:{segment_id}#satellite.ground[{index}]",
        )


def _ground_terminal_blocks(node_id: str, station: Any, gs_file: GroundStationFile):
    terminals = station.terminals or gs_file.default_terminals
    if not terminals:
        raise SessionResolutionError(f"ground station {station.name!r} has no terminal definitions")
    for index, terminal in enumerate(terminals):
        yield ResolvedTerminalBlock(
            terminal_id=f"{node_id}#ground[{index}]",
            owner_node_id=node_id,
            endpoint_role="ground",
            medium=terminal.type,
            count=terminal.count,
            tracking_capacity=terminal.tracking_capacity,
            max_range_km=terminal.max_range_km,
            field_of_regard_deg=terminal.field_of_regard_deg,
            tracking_rate_deg_s=terminal.max_tracking_rate_deg_s,
            bandwidth_mbps=terminal.bandwidth_mbps,
            source_ref=f"station:{station.name}#ground[{index}]",
        )


def _terminal_index(block: ResolvedTerminalBlock) -> int:
    start = block.terminal_id.rfind("[")
    end = block.terminal_id.rfind("]")
    if start == -1 or end == -1 or end <= start + 1:
        raise SessionResolutionError(
            f"terminal_id {block.terminal_id!r} does not carry a bracketed terminal index"
        )
    try:
        return int(block.terminal_id[start + 1 : end])
    except ValueError as exc:
        raise SessionResolutionError(
            f"terminal_id {block.terminal_id!r} has a non-integer terminal index"
        ) from exc


def _validate_runtime_identity_and_interface_names(nodes: list[ResolvedNode]) -> None:
    """Validate IDs and host interfaces before they reach Kubernetes/Linux."""
    iface_owner: dict[str, str] = {}

    def remember(ifname: str, owner: str) -> None:
        previous = iface_owner.get(ifname)
        if previous is not None and previous != owner:
            raise SessionResolutionError(
                f"host interface name collision: {ifname!r} for {owner} and {previous}"
            )
        iface_owner[ifname] = owner

    for node in nodes:
        try:
            validate_runtime_node_id(node.node_id)
        except ValueError as exc:
            raise SessionResolutionError(str(exc)) from exc

        for block in node.terminal_inventory:
            index = _terminal_index(block)
            try:
                if node.kind == "ground_station" and block.endpoint_role == "ground":
                    remember(
                        gs_bridge_port_name(node.node_id, index),
                        f"{node.node_id}:{block.terminal_id}",
                    )
                elif node.kind == "satellite" and block.endpoint_role == "ground":
                    remember(
                        satellite_ground_host_name(node.node_id, index),
                        f"{node.node_id}:{block.terminal_id}",
                    )
                elif node.kind == "satellite" and block.endpoint_role == "isl":
                    remember(
                        isl_host_name(node.node_id, index), f"{node.node_id}:{block.terminal_id}"
                    )
            except ValueError as exc:
                raise SessionResolutionError(str(exc)) from exc


def _validate_m1_link_rule_runtime_shape(
    cfg: SegmentSessionConfig,
    constellation_segment_id: str,
    ground_segment_id: str,
) -> None:
    """Reject link-rule semantics the M1 runtime cannot yet honor.

    M1 still feeds the mature Earth access runtime through a one-constellation +
    one-ground-set projection. That runtime can honestly implement exactly one
    rule: all ground stations to all satellites using visible-candidate access.
    More expressive rules are public grammar, but accepting them before OME
    candidate generation consumes them would lie about the permission graph.
    """
    if len(cfg.link_rules) != 1:
        raise SessionResolutionError(
            "M1 runtime supports exactly one link_rule: all ground_set nodes to all "
            "constellation nodes via topology.mode='visible_candidates'"
        )
    rule = cfg.link_rules[0]
    if not rule.enabled:
        raise SessionResolutionError("M1 runtime cannot execute a disabled link_rule")
    if rule.kind != "access":
        raise SessionResolutionError("M1 runtime supports only access link_rules")
    if rule.constraints is not None:
        raise SessionResolutionError("M1 runtime does not support link_rule constraints")
    if rule.protocol_boundary is not None:
        raise SessionResolutionError("M1 runtime does not support link_rule protocol_boundary")
    if not isinstance(rule.topology, VisibleCandidatesTopology):
        raise SessionResolutionError("M1 runtime supports only topology.mode='visible_candidates'")
    segments = {rule.endpoints[0].selector.segment, rule.endpoints[1].selector.segment}
    if segments != {ground_segment_id, constellation_segment_id}:
        raise SessionResolutionError(
            "M1 runtime link_rule must connect the single ground_set segment to the "
            "single constellation segment"
        )
    for endpoint in rule.endpoints:
        if endpoint.terminal_role != "ground":
            raise SessionResolutionError(
                "M1 access runtime supports only terminal_role='ground' endpoints"
            )
        if endpoint.terminal_medium is not None:
            raise SessionResolutionError(
                "M1 access runtime does not support terminal_medium filtering"
            )
        selector = endpoint.selector
        narrowed = any(
            value is not None
            for value in (
                selector.node_ids,
                selector.node_tags,
                selector.planes,
                selector.slots,
                selector.names,
            )
        )
        if narrowed:
            raise SessionResolutionError(
                "M1 runtime does not support narrowed link_rule selectors; "
                "link-rule-driven candidate generation lands in M2"
            )


def _validate_m1_ground_terminal_compatibility(
    constellation: ResolvedConstellationAssets,
    ground_set: ResolvedGroundAssets,
    addressing: AddressingScheme,
) -> None:
    """Reject M1 access sessions whose selected terminals cannot talk.

    M1 emits one all-ground-to-all-satellite access rule through the mature
    ground-link runtime. That runtime does not yet carry terminal-block identity
    per candidate, so every candidate pair must collapse to one compatible
    ground-terminal medium. Mixed or mismatched media require M2's
    terminal-aware candidate generation; accepting them now would make the
    session appear valid while later services fail or silently produce no links.
    """
    sat_ground_types: dict[str, str] = {}
    for sat in constellation.satellites:
        sat_id = addressing.sat_id(sat.plane, sat.slot)
        try:
            sat_ground_types[sat_id] = ground_terminal_type(sat.ground_terminals)
        except ValueError as exc:
            raise SessionResolutionError(
                f"satellite {sat_id!r} has unsupported ground-terminal media for M1: {exc}"
            ) from exc

    for station in ground_set.config.stations:
        gs_id = addressing.gs_id(station.name)
        try:
            gs_type = station_ground_terminal_type(ground_set.config, station)
        except ValueError as exc:
            raise SessionResolutionError(
                f"ground station {gs_id!r} has unsupported terminal media for M1: {exc}"
            ) from exc
        for sat_id, sat_type in sat_ground_types.items():
            if gs_type != sat_type:
                raise SessionResolutionError(
                    f"M1 access terminal media mismatch for {gs_id}<->{sat_id}: "
                    f"ground station uses {gs_type!r}, satellite uses {sat_type!r}. "
                    "Mixed media require terminal-aware link_rule candidate generation."
                )


def _validate_endpoint_terminal_compatibility(
    rule: LinkRule,
    endpoint,
    selected: list[ResolvedNode],
) -> None:
    """Every selected node must own a terminal matching the endpoint intent."""
    for node in selected:
        matches = [
            block
            for block in node.terminal_inventory
            if block.endpoint_role == endpoint.terminal_role
            and (endpoint.terminal_medium is None or block.medium == endpoint.terminal_medium)
        ]
        if not matches:
            medium = f" medium={endpoint.terminal_medium!r}" if endpoint.terminal_medium else ""
            raise SessionResolutionError(
                f"link_rule {rule.id!r} endpoint segment {endpoint.selector.segment!r} "
                f"selects node {node.node_id!r}, but that node has no "
                f"terminal_role={endpoint.terminal_role!r}{medium} terminal"
            )


def _resolve_link_rules(
    rules: list[LinkRule],
    nodes: list[ResolvedNode],
    node_meta: dict[str, dict[str, Any]],
) -> list[ResolvedLinkRule]:
    if not rules:
        raise SessionResolutionError("segment sessions must declare at least one link_rule")
    node_by_segment: dict[str, list[ResolvedNode]] = {}
    for node in nodes:
        node_by_segment.setdefault(node.segment_id, []).append(node)

    resolved: list[ResolvedLinkRule] = []
    for rule in rules:
        endpoints_list: list[ResolvedEndpoint] = []
        for endpoint in rule.endpoints:
            selected = _select_nodes(endpoint.selector, node_by_segment, node_meta)
            _validate_endpoint_terminal_compatibility(rule, endpoint, selected)
            endpoints_list.append(
                ResolvedEndpoint(
                    segment_id=endpoint.selector.segment,
                    terminal_role=endpoint.terminal_role,
                    terminal_medium=endpoint.terminal_medium,
                    node_ids=tuple(node.node_id for node in selected),
                )
            )
        endpoints = tuple(endpoints_list)
        resolved.append(
            ResolvedLinkRule(
                rule_id=rule.id,
                kind=rule.kind,
                enabled=rule.enabled,
                endpoints=endpoints,  # type: ignore[arg-type]
                topology=rule.topology,
                constraints=rule.constraints,
                protocol_boundary=rule.protocol_boundary,
                tags=tuple(rule.tags or ()),
            )
        )
    return resolved


def _select_nodes(
    selector: NodeSelector,
    node_by_segment: dict[str, list[ResolvedNode]],
    node_meta: dict[str, dict[str, Any]],
) -> list[ResolvedNode]:
    candidates = list(node_by_segment.get(selector.segment, ()))
    if not candidates:
        raise SessionResolutionError(
            f"selector references unknown or empty segment {selector.segment!r}"
        )

    def keep(node: ResolvedNode) -> bool:
        meta = node_meta[node.node_id]
        if selector.node_ids is not None and node.local_node_id not in selector.node_ids:
            return False
        if selector.node_tags is not None and not set(selector.node_tags).issubset(meta["tags"]):
            return False
        if selector.planes is not None and meta["plane"] not in selector.planes:
            return False
        if selector.slots is not None and meta["slot"] not in selector.slots:
            return False
        return not (selector.names is not None and meta["name"] not in selector.names)

    selected = [node for node in candidates if keep(node)]
    if not selected:
        raise SessionResolutionError(
            f"selector for segment {selector.segment!r} matched zero nodes"
        )
    return selected


def _validate_candidate_budgets(cfg: SegmentSessionConfig, rules: list[ResolvedLinkRule]) -> None:
    limits = cfg.simulation.candidate_limits
    if limits is None:
        raise SessionResolutionError("simulation.candidate_limits is required for segment sessions")
    total = 0
    for rule in rules:
        count = len(rule.endpoints[0].node_ids) * len(rule.endpoints[1].node_ids)
        if count > limits.max_pairs_per_rule:
            raise SessionResolutionError(
                f"link_rule {rule.rule_id!r} static candidate upper bound {count} exceeds "
                f"simulation.candidate_limits.max_pairs_per_rule={limits.max_pairs_per_rule}"
            )
        total += count
    if limits.max_pairs_per_tick is not None and total > limits.max_pairs_per_tick:
        raise SessionResolutionError(
            f"static candidate upper bound {total} exceeds "
            f"simulation.candidate_limits.max_pairs_per_tick={limits.max_pairs_per_tick}"
        )


def _allocate_sid_blocks(nodes: list[ResolvedNode]) -> list[SidBlock]:
    by_segment: dict[str, int] = {}
    for node in nodes:
        by_segment[node.segment_id] = by_segment.get(node.segment_id, 0) + 1
    blocks: list[SidBlock] = []
    cursor = 1
    for segment_id in sorted(by_segment):
        count = by_segment[segment_id]
        blocks.append(SidBlock(segment_id=segment_id, sid_start=cursor, sid_end=cursor + count - 1))
        cursor += count
    return blocks
