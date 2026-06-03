# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Shared session resolver for the segment YAML grammar.

This is the single authority that turns user-facing segment YAML into runtime
truth. Production services may consume the internal assets returned here, but
must not parse the old top-level session shape themselves.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from nodalarc.body_frames import body_frame_for
from nodalarc.catalog_paths import (
    CatalogRoots,
    config_value_for,
    resolve_constellation_reference,
    resolve_ground_station_reference,
)
from nodalarc.constants import EARTH_RADIUS_KM
from nodalarc.constellation_loader import (
    SatelliteNode,
    clone_satellite_node,
    expand_constellation,
    load_constellation,
    load_ground_stations,
    satellite_local_node_id,
    satellite_local_plane_slot,
    satellite_node_id,
)
from nodalarc.ephemeris_runtime import (
    EphemerisValidationError,
    SkyfieldBspEphemeris,
    body_states_at,
    validate_ephemeris_manifest,
)
from nodalarc.frames import EcefVec3, GeoPosition, Vec3
from nodalarc.frozen import FrozenDict
from nodalarc.geo import geodetic_to_ecef
from nodalarc.ground_handover import resolve_station_ground_scheduling
from nodalarc.link_rule_candidates import (
    DeclaredLinkCandidate,
    generate_declared_link_candidates,
)
from nodalarc.models.addressing import AddressingScheme, NeighborAssignment, assign_isl_neighbors
from nodalarc.models.constellation import (
    ConstellationConfig,
    SatelliteConfig,
    TerminalConfig,
)
from nodalarc.models.constellation import (
    OrbitalElements as ConfigOrbitalElements,
)
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.identity import IdentityMode
from nodalarc.models.link_rules import LinkRule, NodeSelector
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
from nodalarc.models.segments import ConstellationSegment, GroundSegment, SpaceNodeSegment
from nodalarc.models.session import (
    AddressingConfig,
    GroundSchedulingConfig,
    SessionConfig,
    resolve_session_epoch,
)
from nodalarc.orbital import OrbitalElements
from nodalarc.propagator import (
    propagate_j2_mean_elements_for_body,
    propagate_keplerian_for_body,
    propagate_sgp4_tle,
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
    segment: ConstellationSegment | SpaceNodeSegment
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
    runtime_constellation: ConstellationConfig
    satellites: tuple[SatelliteNode, ...]
    constellations: tuple[ResolvedConstellationAssets, ...]
    ground_sets: tuple[ResolvedGroundAssets, ...]
    addressing: AddressingScheme
    declared_candidates: tuple[DeclaredLinkCandidate, ...]
    neighbors: frozenset[tuple[str, NeighborAssignment]]
    ground_candidate_satellites_by_gs: FrozenDict
    body_ephemeris: SkyfieldBspEphemeris | None
    active_bodies: frozenset[str]

    @property
    def primary_constellation(self) -> ResolvedConstellationAssets:
        if not self.constellations:
            raise SessionResolutionError("session resolved with no constellation segments")
        return self.constellations[0]

    @property
    def primary_ground_set(self) -> ResolvedGroundAssets:
        if len(self.ground_sets) != 1:
            raise SessionResolutionError("runtime requires exactly one ground_set segment")
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
    support = runtime_support or RuntimeSupport.mvp_m3()
    context = source_context or SourceContext(origin="resolve_session")

    try:
        cfg = SegmentSessionConfig.model_validate(raw_session)
    except ValidationError:
        raise
    except Exception as exc:
        raise SessionResolutionError(f"invalid segment session: {exc}") from exc

    _check_runtime_support(cfg, support)
    active_bodies = _active_bodies(cfg)
    required_ephemeris_bodies = active_bodies & support.ephemeris_required_bodies
    body_ephemeris = _build_body_ephemeris(
        cfg,
        required_bodies=required_ephemeris_bodies,
        epoch_unix=resolve_session_epoch(cfg.time),
    )

    const_assets: list[ResolvedConstellationAssets] = []
    ground_assets: list[ResolvedGroundAssets] = []
    space_assets: list[ResolvedConstellationAssets] = []
    for segment in cfg.segments:
        if isinstance(segment, ConstellationSegment):
            const_assets.append(_load_constellation_segment(segment, roots))
        elif isinstance(segment, GroundSegment):
            ground_assets.append(_load_ground_segment(segment, roots))
        elif isinstance(segment, SpaceNodeSegment):
            space_assets.append(_load_space_node_segment(segment))
        else:
            # RuntimeSupport should catch this before we get here. Keep the guard
            # fail-loud so a future matrix bug does not silently skip a segment.
            raise SessionResolutionError(f"segment kind {segment.kind!r} is not runtime-supported")

    if not const_assets or len(ground_assets) != 1:
        raise SessionResolutionError(
            "M3 runtime supports one or more satellite-producing segments and exactly one ground_set segment; "
            f"got {len(const_assets)} constellation segment(s), {len(ground_assets)} ground_set segment(s)"
        )

    ground_set = ground_assets[0]
    const_assets = list(_assign_constellation_runtime_identities([*const_assets, *space_assets]))
    for constellation in const_assets:
        if not constellation.satellites:
            raise SessionResolutionError(
                f"constellation segment {constellation.segment.id!r} expands to 0 satellites"
            )
    if not ground_set.config.stations:
        raise SessionResolutionError(
            f"ground_set segment {ground_set.segment.id!r} expands to 0 stations"
        )

    effective_ground = _effective_ground_scheduling(cfg, ground_set.segment, ground_set.config)
    all_satellites = tuple(sat for asset in const_assets for sat in asset.satellites)
    runtime_constellation_source = _build_runtime_constellation_source(cfg, all_satellites)
    runtime_constellation = load_constellation(runtime_constellation_source)
    runtime_session = _build_runtime_session_projection(
        cfg, runtime_constellation_source, ground_set, effective_ground
    )
    addressing = AddressingScheme(
        runtime_session.addressing, list(all_satellites), ground_set.config
    )

    nodes, node_meta = _materialize_nodes(
        tuple(const_assets), ground_set, runtime_session, addressing, effective_ground
    )
    _validate_runtime_identity_and_interface_names(nodes)
    link_rules = _resolve_link_rules(cfg.link_rules, nodes, node_meta)
    _validate_link_rule_runtime_shape(link_rules, nodes)
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
    pair_rank = _rank_pairs_by_epoch_range(
        cfg,
        satellites=all_satellites,
        addressing=addressing,
        gs_file=ground_set.config,
        body_ephemeris=body_ephemeris,
        active_bodies=active_bodies,
    )
    try:
        declared_candidates = generate_declared_link_candidates(resolved, pair_rank=pair_rank)
    except ValueError as exc:
        raise SessionResolutionError(str(exc)) from exc
    _validate_candidate_budgets(cfg, declared_candidates)
    _validate_declared_candidate_terminal_compatibility(resolved, declared_candidates)
    _validate_declared_candidate_constraints(resolved, declared_candidates)
    ground_candidate_satellites_by_gs = _ground_candidates_by_gs(resolved, declared_candidates)
    neighbors = _build_isl_neighbors_from_resolved_rules(
        resolved,
        constellations=tuple(const_assets),
        candidates=declared_candidates,
    )
    return SessionResolution(
        resolved=resolved,
        runtime_session=runtime_session,
        runtime_constellation=runtime_constellation,
        satellites=all_satellites,
        constellations=tuple(const_assets),
        ground_sets=tuple(ground_assets),
        addressing=addressing,
        declared_candidates=declared_candidates,
        neighbors=neighbors,
        ground_candidate_satellites_by_gs=ground_candidate_satellites_by_gs,
        body_ephemeris=body_ephemeris,
        active_bodies=frozenset(active_bodies),
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


def _active_bodies(cfg: SegmentSessionConfig) -> set[str]:
    bodies: set[str] = {"earth"}
    for segment in cfg.segments:
        central_body = getattr(segment, "central_body", None)
        if central_body is not None:
            bodies.add(str(central_body))
        reference_body = getattr(segment, "reference_body", None)
        if reference_body is not None:
            bodies.add(str(reference_body))
    return bodies


def _build_body_ephemeris(
    cfg: SegmentSessionConfig,
    *,
    required_bodies: set[str],
    epoch_unix: float,
) -> SkyfieldBspEphemeris | None:
    if not required_bodies:
        if cfg.ephemeris is not None:
            try:
                validate_ephemeris_manifest(
                    cfg.ephemeris,
                    required_bodies=set(),
                    epoch_unix=epoch_unix,
                )
            except EphemerisValidationError as exc:
                raise SessionResolutionError(str(exc)) from exc
        return None
    if cfg.ephemeris is None:
        raise SessionResolutionError(
            "session uses body/bodies requiring ephemeris but declares no ephemeris manifest: "
            + ", ".join(sorted(required_bodies))
        )
    try:
        return SkyfieldBspEphemeris.from_config(
            cfg.ephemeris,
            required_bodies=required_bodies,
            epoch_unix=epoch_unix,
        )
    except EphemerisValidationError as exc:
        raise SessionResolutionError(str(exc)) from exc


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


def _load_space_node_segment(segment: SpaceNodeSegment) -> ResolvedConstellationAssets:
    from nodalarc.models.segments import StateVector

    if isinstance(segment.node.state, StateVector):
        raise SessionResolutionError(
            f"space_node segment {segment.id!r} uses StateVector; M3 runtime supports "
            "orbital-elements space nodes only so propagation remains authoritative"
        )
    if segment.central_body is None:
        raise SessionResolutionError(
            f"space_node segment {segment.id!r} requires central_body for orbital-elements state"
        )
    raw = {
        "mode": "explicit",
        "name": segment.id,
        "satellite_type": segment.satellite_type,
        "satellites": [
            {
                "plane": 0,
                "slot": 0,
                "orbit": segment.node.state.model_dump(mode="python"),
            }
        ],
    }
    config = load_constellation(raw)
    expanded = tuple(expand_constellation(config))
    if len(expanded) != 1:
        raise SessionResolutionError(
            f"space_node segment {segment.id!r} expanded to {len(expanded)} nodes, expected 1"
        )
    sat = clone_satellite_node(
        expanded[0],
        local_node_id=segment.node.id,
        central_body=segment.central_body,
    )
    return ResolvedConstellationAssets(
        segment=segment,
        source=raw,
        config=config,
        satellites=(sat,),
    )


def _normalize_runtime_token(value: str) -> str:
    token = _NORMALIZE_RE.sub("-", value.strip().lower()).strip("-")
    if not token:
        raise SessionResolutionError(f"cannot normalize empty runtime token from {value!r}")
    return token


def _assign_constellation_runtime_identities(
    constellations: list[ResolvedConstellationAssets],
) -> tuple[ResolvedConstellationAssets, ...]:
    """Assign globally unique runtime IDs and runtime plane/slot coordinates.

    Source-local plane/slot stay on each ``SatelliteNode`` for selectors and
    explainability. Runtime plane/slot are a compatibility coordinate for mature
    templates/IP helpers that still need numeric indices.
    """
    assigned: list[ResolvedConstellationAssets] = []
    seen_ids: set[str] = set()
    plane_offset = 0
    for asset in constellations:
        sats: list[SatelliteNode] = []
        local_planes = sorted({satellite_local_plane_slot(sat)[0] for sat in asset.satellites})
        if not local_planes:
            raise SessionResolutionError(
                f"constellation segment {asset.segment.id!r} expands to 0 satellites"
            )
        local_plane_to_runtime = {
            local_plane: plane_offset + idx for idx, local_plane in enumerate(local_planes)
        }
        for sat in asset.satellites:
            local_id = satellite_local_node_id(sat)
            runtime_id = f"{asset.segment.namespace}-{_normalize_runtime_token(local_id)}"
            if runtime_id in seen_ids:
                raise SessionResolutionError(f"duplicate runtime satellite node_id {runtime_id!r}")
            seen_ids.add(runtime_id)
            local_plane, local_slot = satellite_local_plane_slot(sat)
            central_body = asset.segment.central_body or getattr(sat, "central_body", "earth")
            source_altitude_km = sat.elements.semi_major_axis_km - EARTH_RADIUS_KM
            body_frame = body_frame_for(central_body)
            body_elements = OrbitalElements(
                semi_major_axis_km=body_frame.equatorial_radius_km + source_altitude_km,
                inclination_rad=sat.elements.inclination_rad,
                raan_rad=sat.elements.raan_rad,
                true_anomaly_rad=sat.elements.true_anomaly_rad,
            )
            sats.append(
                clone_satellite_node(
                    sat,
                    plane=local_plane_to_runtime[local_plane],
                    slot=local_slot,
                    local_plane=local_plane,
                    local_slot=local_slot,
                    node_id=runtime_id,
                    local_node_id=local_id,
                    segment_id=asset.segment.id,
                    central_body=str(central_body),
                    elements=body_elements,
                )
            )
        plane_offset += len(local_planes)
        assigned.append(
            ResolvedConstellationAssets(
                segment=asset.segment,
                source=asset.source,
                config=asset.config,
                satellites=tuple(sats),
            )
        )
    return tuple(assigned)


def _dump_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    return value


def _terminal_config_from_satellite(sat: SatelliteNode) -> TerminalConfig:
    return TerminalConfig.model_validate(
        {
            "isl": [_dump_model(t) for t in sat.isl_terminals],
            "ground": [_dump_model(t) for t in sat.ground_terminals],
        }
    )


def _orbit_config_from_satellite(sat: SatelliteNode) -> ConfigOrbitalElements:
    body_frame = body_frame_for(getattr(sat, "central_body", "earth"))
    return ConfigOrbitalElements(
        altitude_km=sat.elements.semi_major_axis_km - body_frame.equatorial_radius_km,
        inclination_deg=math.degrees(sat.elements.inclination_rad),
        raan_deg=math.degrees(sat.elements.raan_rad),
        true_anomaly_deg=math.degrees(sat.elements.true_anomaly_rad),
    )


def _build_runtime_constellation_source(
    cfg: SegmentSessionConfig,
    satellites: tuple[SatelliteNode, ...],
) -> dict[str, Any]:
    if cfg.orbit.propagator == "sgp4-tle":
        raise SessionResolutionError(
            "multi-segment runtime projection does not support orbit.propagator='sgp4-tle' yet; "
            "use j2-mean-elements for M2 Earth multi-regime sessions"
        )
    return {
        "mode": "explicit",
        "name": f"{cfg.session.name}-runtime",
        "default_terminals": {"isl": [], "ground": []},
        "satellites": [
            SatelliteConfig(
                plane=sat.plane,
                slot=sat.slot,
                orbit=_orbit_config_from_satellite(sat),
                terminals=_terminal_config_from_satellite(sat),
            ).model_dump(mode="python")
            for sat in satellites
        ],
    }


def _build_runtime_session_projection(
    cfg: SegmentSessionConfig,
    runtime_constellation_source: dict[str, Any],
    ground_set: ResolvedGroundAssets,
    effective_ground: GroundSchedulingConfig,
) -> SessionConfig:
    gs_namespace = ground_set.segment.namespace
    addressing = AddressingConfig(
        sat_id_template="sat-p{plane:04d}s{slot:02d}",
        gs_id_template=f"{gs_namespace}-gs-{{name}}",
        ipv4_sat_template=cfg.addressing.ipv4_sat_template,
        ipv4_gs_template=cfg.addressing.ipv4_gs_template,
        ipv6_sat_template=cfg.addressing.ipv6_sat_template,
        ipv6_gs_template=cfg.addressing.ipv6_gs_template,
    )
    raw = {
        "session": cfg.session.model_dump(mode="python"),
        "constellation": runtime_constellation_source,
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
    constellations: tuple[ResolvedConstellationAssets, ...],
    ground_set: ResolvedGroundAssets,
    session: SessionConfig,
    addressing: AddressingScheme,
    effective_ground: GroundSchedulingConfig,
) -> tuple[list[ResolvedNode], dict[str, dict[str, Any]]]:
    nodes: list[ResolvedNode] = []
    meta: dict[str, dict[str, Any]] = {}
    ground_segment = ground_set.segment

    for constellation in constellations:
        const_segment = constellation.segment
        for sat in constellation.satellites:
            local_id = satellite_local_node_id(sat)
            node_id = satellite_node_id(sat, addressing)
            local_plane, local_slot = satellite_local_plane_slot(sat)
            node_tags = getattr(getattr(const_segment, "node", None), "tags", None) or ()
            tags = (*(const_segment.tags or ()), *node_tags)
            central_body = str(getattr(sat, "central_body", const_segment.central_body or "earth"))
            segment_clock = getattr(const_segment, "clock", None) or getattr(
                getattr(const_segment, "node", None),
                "clock",
                None,
            )
            node = ResolvedNode(
                node_id=node_id,
                local_node_id=local_id,
                segment_id=const_segment.id,
                namespace=const_segment.namespace,
                kind="satellite",
                frame_id=central_body,
                central_body=central_body,
                tags=tags,
                satellite_type=const_segment.satellite_type
                or getattr(constellation.config, "satellite_type", None),
                tenant_id=getattr(constellation.config, "tenant_id", "default"),
                terminal_inventory=tuple(
                    _satellite_terminal_blocks(node_id, sat, const_segment.id)
                ),
                clock=segment_clock or cfg_clock_default(),
            )
            nodes.append(node)
            meta[node_id] = {
                "segment_id": const_segment.id,
                "local_node_id": local_id,
                "tags": set(tags),
                "kind": "satellite",
                "plane": local_plane,
                "slot": local_slot,
                "runtime_plane": sat.plane,
                "runtime_slot": sat.slot,
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
    return resolve_station_ground_scheduling(
        base, gs_file, station, apply_ground_defaults=False
    ).scheduling


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


def _terminal_indices(block: ResolvedTerminalBlock) -> range:
    start = _terminal_index(block)
    return range(start, start + block.count)


def _concrete_terminal_indices(
    node: ResolvedNode,
    *,
    role: str | None = None,
    medium: str | None = None,
) -> tuple[tuple[ResolvedTerminalBlock, int], ...]:
    """Expand terminal blocks into concrete role-local interface indices.

    ``terminal_id`` records the source block ordinal. Concrete Linux interfaces
    are a flattened per-role resource: two ISL blocks with count=2 allocate
    isl0..isl3, not overlapping ranges derived from the block ordinal.
    """
    cursor_by_role: dict[str, int] = {}
    expanded: list[tuple[ResolvedTerminalBlock, int]] = []
    for block in node.terminal_inventory:
        start = cursor_by_role.get(block.endpoint_role, 0)
        cursor_by_role[block.endpoint_role] = start + block.count
        if role is not None and block.endpoint_role != role:
            continue
        if medium is not None and block.medium != medium:
            continue
        expanded.extend((block, index) for index in range(start, start + block.count))
    return tuple(expanded)


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

        for block, index in _concrete_terminal_indices(node):
            try:
                if node.kind == "ground_station" and block.endpoint_role == "ground":
                    remember(
                        gs_bridge_port_name(node.node_id, index),
                        f"{node.node_id}:{block.terminal_id}[{index}]",
                    )
                elif node.kind == "satellite" and block.endpoint_role == "ground":
                    remember(
                        satellite_ground_host_name(node.node_id, index),
                        f"{node.node_id}:{block.terminal_id}[{index}]",
                    )
                elif node.kind == "satellite" and block.endpoint_role == "isl":
                    remember(
                        isl_host_name(node.node_id, index),
                        f"{node.node_id}:{block.terminal_id}[{index}]",
                    )
            except ValueError as exc:
                raise SessionResolutionError(str(exc)) from exc


def _validate_link_rule_runtime_shape(
    rules: list[ResolvedLinkRule],
    nodes: list[ResolvedNode],
) -> None:
    """Validate runtime-supported link-rule kind/endpoint semantics."""
    node_kind = {node.node_id: node.kind for node in nodes}
    node_body = {
        node.node_id: node.central_body or node.reference_body or "earth" for node in nodes
    }
    for rule in rules:
        endpoint_node_kinds = [
            {node_kind[node_id] for node_id in endpoint.node_ids} for endpoint in rule.endpoints
        ]
        endpoint_bodies = [
            {str(node_body[node_id]) for node_id in endpoint.node_ids}
            for endpoint in rule.endpoints
        ]
        if rule.kind == "access":
            if rule.protocol_boundary is not None:
                raise SessionResolutionError(
                    f"access link_rule {rule.rule_id!r} must not declare protocol_boundary; "
                    "protocol boundaries are only runtime-supported for inter_body_relay"
                )
            if {frozenset(kinds) for kinds in endpoint_node_kinds} != {
                frozenset({"ground_station"}),
                frozenset({"satellite"}),
            }:
                raise SessionResolutionError(
                    f"access link_rule {rule.rule_id!r} must connect ground_station nodes "
                    "to satellite nodes"
                )
            if any(endpoint.terminal_role != "ground" for endpoint in rule.endpoints):
                raise SessionResolutionError(
                    f"access link_rule {rule.rule_id!r} requires terminal_role='ground' "
                    "on both endpoints"
                )
        elif rule.kind in {"inter_constellation", "relay"}:
            if rule.protocol_boundary is not None:
                raise SessionResolutionError(
                    f"{rule.kind} link_rule {rule.rule_id!r} must not declare "
                    "protocol_boundary; use kind='inter_body_relay' for static inter-body "
                    "routing boundaries"
                )
            if any(kinds != {"satellite"} for kinds in endpoint_node_kinds):
                raise SessionResolutionError(
                    f"{rule.kind} link_rule {rule.rule_id!r} must connect satellite nodes"
                )
            if any(endpoint.terminal_role != "isl" for endpoint in rule.endpoints):
                raise SessionResolutionError(
                    f"{rule.kind} link_rule {rule.rule_id!r} requires terminal_role='isl' "
                    "on both endpoints"
                )
            if endpoint_bodies[0] != endpoint_bodies[1]:
                raise SessionResolutionError(
                    f"{rule.kind} link_rule {rule.rule_id!r} crosses bodies "
                    f"{sorted(endpoint_bodies[0])}<->{sorted(endpoint_bodies[1])}; "
                    "cross-body satellite links require kind='inter_body_relay' with "
                    "protocol_boundary.adapter='static_ip'"
                )
        elif rule.kind == "inter_body_relay":
            if any(kinds != {"satellite"} for kinds in endpoint_node_kinds):
                raise SessionResolutionError(
                    f"inter_body_relay link_rule {rule.rule_id!r} must connect satellite-like "
                    "relay nodes"
                )
            if any(endpoint.terminal_role != "isl" for endpoint in rule.endpoints):
                raise SessionResolutionError(
                    f"inter_body_relay link_rule {rule.rule_id!r} requires terminal_role='isl' "
                    "on both endpoints for M3"
                )
            if len(endpoint_bodies[0]) != 1 or len(endpoint_bodies[1]) != 1:
                raise SessionResolutionError(
                    f"inter_body_relay link_rule {rule.rule_id!r} endpoints must each resolve "
                    "to one central body"
                )
            if endpoint_bodies[0] == endpoint_bodies[1]:
                raise SessionResolutionError(
                    f"inter_body_relay link_rule {rule.rule_id!r} does not cross bodies"
                )
            boundary = rule.protocol_boundary
            if boundary is None or not boundary.enabled or boundary.adapter != "static_ip":
                raise SessionResolutionError(
                    f"inter_body_relay link_rule {rule.rule_id!r} requires an enabled "
                    "protocol_boundary.adapter='static_ip'"
                )
            if boundary.routing_domain_a is None or boundary.routing_domain_b is None:
                raise SessionResolutionError(
                    f"inter_body_relay link_rule {rule.rule_id!r} requires routing_domain_a "
                    "and routing_domain_b for operator explanation and static boundary audit"
                )
        else:
            raise SessionResolutionError(
                f"unsupported link_rule kind {rule.kind!r} for rule {rule.rule_id!r}"
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


def _validate_candidate_budgets(
    cfg: SegmentSessionConfig,
    candidates: tuple[DeclaredLinkCandidate, ...],
) -> None:
    limits = cfg.simulation.candidate_limits
    if limits is None:
        raise SessionResolutionError("simulation.candidate_limits is required for segment sessions")
    total = 0
    by_rule: dict[str, int] = {}
    for candidate in candidates:
        by_rule[candidate.rule_id] = by_rule.get(candidate.rule_id, 0) + 1
    for rule_id, count in sorted(by_rule.items()):
        if count > limits.max_pairs_per_rule:
            raise SessionResolutionError(
                f"link_rule {rule_id!r} declared candidate count {count} exceeds "
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


def _rank_pairs_by_epoch_range(
    cfg: SegmentSessionConfig,
    *,
    satellites: tuple[SatelliteNode, ...],
    addressing: AddressingScheme,
    gs_file: GroundStationFile,
    body_ephemeris: SkyfieldBspEphemeris | None,
    active_bodies: set[str],
) -> FrozenDict:
    """Rank satellite pairs by physical range at the session epoch.

    ``nearest_n`` is a static M2 topology because ISL interfaces are wired at pod
    creation. The ranking source is still physical: range at the configured
    epoch using the selected OME propagator. Dynamic nearest-visible rewiring is
    a later runtime capability and remains fail-loud until implemented.
    """
    epoch_unix = resolve_session_epoch(cfg.time)
    body_states = body_states_at(body_ephemeris, set(active_bodies), epoch_unix)
    positions: dict[str, EcefVec3] = {}

    def _common_position(body_id: str, local_position: EcefVec3) -> EcefVec3:
        body_state = body_states.get(body_id)
        if body_state is None:
            raise SessionResolutionError(
                f"nearest_n ranking missing common-frame ephemeris state for body {body_id!r}"
            )
        return EcefVec3(
            Vec3(
                body_state.position_km.x + local_position.x,
                body_state.position_km.y + local_position.y,
                body_state.position_km.z + local_position.z,
            )
        )

    for sat in satellites:
        node_id = satellite_node_id(sat, addressing)
        central_body = str(getattr(sat, "central_body", "earth"))
        body_frame = body_frame_for(central_body)
        if cfg.orbit.propagator == "keplerian-circular":
            _pos_fixed, _vel_fixed, _geo, pos_inertial, _vel_inertial = (
                propagate_keplerian_for_body(
                    sat.elements,
                    epoch_unix,
                    0.0,
                    body_frame=body_frame,
                )
            )
        elif cfg.orbit.propagator == "j2-mean-elements":
            _pos_fixed, _vel_fixed, _geo, pos_inertial, _vel_inertial = (
                propagate_j2_mean_elements_for_body(
                    sat.elements,
                    epoch_unix,
                    0.0,
                    body_frame=body_frame,
                )
            )
        elif cfg.orbit.propagator == "sgp4-tle":
            if central_body != "earth":
                raise SessionResolutionError("nearest_n SGP4/TLE ranking is Earth-only")
            if sat.tle_line_1 is None or sat.tle_line_2 is None:
                raise SessionResolutionError(
                    f"Satellite {node_id!r} has no TLE lines for nearest_n ranking"
                )
            pos_inertial, _vel, _geo = propagate_sgp4_tle(
                sat.tle_line_1,
                sat.tle_line_2,
                epoch_unix,
                0.0,
            )
        else:
            raise SessionResolutionError(
                "nearest_n physical ranking does not support orbit.propagator="
                f"{cfg.orbit.propagator!r} yet"
            )
        positions[node_id] = _common_position(central_body, pos_inertial)
    for station in gs_file.stations:
        node_id = addressing.gs_id(station.name)
        geo = GeoPosition(
            station.lat_deg,
            station.lon_deg,
            (station.alt_m or 0.0) / 1000.0,
        )
        body_id = str(station.reference_body)
        positions[node_id] = _common_position(
            body_id, geodetic_to_ecef(geo, body_frame_for(body_id))
        )

    def _range(a: EcefVec3, b: EcefVec3) -> float:
        return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)

    ranks: dict[tuple[str, str], float] = {}
    ids = sorted(positions)
    for idx, node_a in enumerate(ids):
        for node_b in ids[idx + 1 :]:
            ranks[(node_a, node_b)] = _range(positions[node_a], positions[node_b])
    return FrozenDict(ranks)


def _blocks_for_role(
    node: ResolvedNode,
    role: str,
    medium: str | None,
) -> tuple[ResolvedTerminalBlock, ...]:
    return tuple(
        block
        for block in node.terminal_inventory
        if block.endpoint_role == role and (medium is None or block.medium == medium)
    )


def _compatible_medium(
    node_a: ResolvedNode,
    node_b: ResolvedNode,
    *,
    role: str,
    medium: str | None,
    rule_id: str,
) -> str:
    blocks_a = _blocks_for_role(node_a, role, medium)
    blocks_b = _blocks_for_role(node_b, role, medium)
    media_a = {block.medium for block in blocks_a}
    media_b = {block.medium for block in blocks_b}
    common = sorted(media_a & media_b)
    if not common:
        raise SessionResolutionError(
            f"link_rule {rule_id!r} candidate {node_a.node_id!r}<->{node_b.node_id!r} "
            f"has no compatible {role!r} terminal medium"
        )
    if medium is None and len(common) != 1:
        raise SessionResolutionError(
            f"link_rule {rule_id!r} candidate {node_a.node_id!r}<->{node_b.node_id!r} "
            f"has ambiguous {role!r} terminal media {common}; declare terminal_medium"
        )
    return medium or common[0]


def _validate_declared_candidate_terminal_compatibility(
    resolved: ResolvedSession,
    candidates: tuple[DeclaredLinkCandidate, ...],
) -> None:
    nodes = {node.node_id: node for node in resolved.nodes}
    for candidate in candidates:
        node_a = nodes[candidate.pair[0]]
        node_b = nodes[candidate.pair[1]]
        medium = _compatible_medium(
            node_a,
            node_b,
            role=candidate.terminal_role,
            medium=candidate.terminal_medium,
            rule_id=candidate.rule_id,
        )
        if candidate.terminal_medium is not None and medium != candidate.terminal_medium:
            raise SessionResolutionError(
                f"link_rule {candidate.rule_id!r} terminal_medium changed during validation"
            )


def _constraint_limit_for_node(
    limit: int | dict[str, int],
    node: ResolvedNode,
) -> int:
    if isinstance(limit, int):
        return limit
    segment_limit = limit.get(node.segment_id)
    if segment_limit is None:
        raise SessionResolutionError(
            f"link_rule max_links_per_node map has no entry for segment {node.segment_id!r}"
        )
    return int(segment_limit)


def _validate_declared_candidate_constraints(
    resolved: ResolvedSession,
    candidates: tuple[DeclaredLinkCandidate, ...],
) -> None:
    """Enforce the runtime-supported subset of link-rule constraints.

    ``max_links_per_node`` is a static graph constraint and is enforceable at
    resolve time. Range/mutual-visibility/scheduling constraints are dynamic OME
    semantics; accepting them before OME consumes them would be a lie, so they
    are rejected loudly for M3.
    """
    nodes = {node.node_id: node for node in resolved.nodes}
    degree_by_rule_node: dict[tuple[str, str], int] = {}
    for candidate in candidates:
        for node_id in candidate.pair:
            key = (candidate.rule_id, node_id)
            degree_by_rule_node[key] = degree_by_rule_node.get(key, 0) + 1

    for rule in resolved.link_rules:
        constraints = rule.constraints
        if constraints is None:
            continue
        unsupported = []
        if constraints.max_range_km is not None:
            unsupported.append("max_range_km")
        if constraints.require_mutual_visibility is not None:
            unsupported.append("require_mutual_visibility")
        if constraints.scheduling_policy is not None:
            unsupported.append("scheduling_policy")
        if unsupported:
            raise SessionResolutionError(
                f"link_rule {rule.rule_id!r} uses unsupported runtime constraint(s): "
                + ", ".join(unsupported)
            )
        if constraints.max_links_per_node is None:
            continue
        for (rule_id, node_id), degree in sorted(degree_by_rule_node.items()):
            if rule_id != rule.rule_id:
                continue
            limit = _constraint_limit_for_node(constraints.max_links_per_node, nodes[node_id])
            if degree > limit:
                raise SessionResolutionError(
                    f"link_rule {rule.rule_id!r} declares {degree} candidate links for "
                    f"{node_id!r}, exceeding max_links_per_node={limit}"
                )


def _ground_candidates_by_gs(
    resolved: ResolvedSession,
    candidates: tuple[DeclaredLinkCandidate, ...],
) -> FrozenDict:
    node_kind = {node.node_id: node.kind for node in resolved.nodes}
    result: dict[str, set[str]] = {}
    access_candidate_count = 0
    for candidate in candidates:
        if candidate.kind != "access":
            continue
        access_candidate_count += 1
        a, b = candidate.pair
        if node_kind[a] == "ground_station" and node_kind[b] == "satellite":
            gs_id, sat_id = a, b
        elif node_kind[b] == "ground_station" and node_kind[a] == "satellite":
            gs_id, sat_id = b, a
        else:
            raise SessionResolutionError(
                f"access link_rule {candidate.rule_id!r} produced non ground-satellite pair "
                f"{candidate.pair}"
            )
        result.setdefault(gs_id, set()).add(sat_id)

    ground_ids = sorted(node_id for node_id, kind in node_kind.items() if kind == "ground_station")
    if ground_ids and access_candidate_count == 0:
        raise SessionResolutionError(
            "session declares ground station(s) but no declared access candidates"
        )
    return FrozenDict({gs_id: tuple(sorted(result.get(gs_id, ()))) for gs_id in sorted(ground_ids)})


def _next_free_interface(
    used: dict[str, set[int]],
    node: ResolvedNode,
    *,
    role: str,
    medium: str | None,
    rule_id: str,
) -> str:
    blocks = _blocks_for_role(node, role, medium)
    taken = used.setdefault(node.node_id, set())
    allowed_indices = {
        index
        for block, index in _concrete_terminal_indices(node, role=role, medium=medium)
        if block in blocks
    }
    for index in sorted(allowed_indices):
        if index not in taken:
            taken.add(index)
            return f"isl{index}"
    raise SessionResolutionError(
        f"link_rule {rule_id!r} requires more {role!r} terminals on "
        f"{node.node_id!r} than the resolved terminal inventory provides"
    )


def _add_neighbor_pair(
    assignments: list[tuple[str, NeighborAssignment]],
    used_interfaces: dict[str, set[int]],
    node_a: ResolvedNode,
    node_b: ResolvedNode,
    *,
    link_type: str,
    priority: int,
    rule_id: str,
    medium: str | None = None,
) -> None:
    actual_medium = _compatible_medium(
        node_a,
        node_b,
        role="isl",
        medium=medium,
        rule_id=rule_id,
    )
    iface_a = _next_free_interface(
        used_interfaces,
        node_a,
        role="isl",
        medium=actual_medium,
        rule_id=rule_id,
    )
    iface_b = _next_free_interface(
        used_interfaces,
        node_b,
        role="isl",
        medium=actual_medium,
        rule_id=rule_id,
    )
    assignments.append(
        (
            node_a.node_id,
            NeighborAssignment(
                interface=iface_a,
                peer_node_id=node_b.node_id,
                link_type=link_type,
                priority=priority,
            ),
        )
    )
    assignments.append(
        (
            node_b.node_id,
            NeighborAssignment(
                interface=iface_b,
                peer_node_id=node_a.node_id,
                link_type=link_type,
                priority=priority,
            ),
        )
    )


def _build_isl_neighbors_from_resolved_rules(
    resolved: ResolvedSession,
    *,
    constellations: tuple[ResolvedConstellationAssets, ...],
    candidates: tuple[DeclaredLinkCandidate, ...],
) -> frozenset[tuple[str, NeighborAssignment]]:
    """Build static ISL neighbor assignments from segment internals + link rules."""
    node_by_id = {node.node_id: node for node in resolved.nodes}
    assignments: list[tuple[str, NeighborAssignment]] = []
    used_interfaces: dict[str, set[int]] = {}
    seen_pairs: set[tuple[str, str]] = set()

    for asset in constellations:
        internal = getattr(asset.segment, "internal_links", None)
        if internal is not None and internal.isl is not None and not internal.isl.enabled:
            continue
        if internal is None and isinstance(asset.segment, SpaceNodeSegment):
            continue
        local_identity_sats = [
            clone_satellite_node(
                sat,
                plane=satellite_local_plane_slot(sat)[0],
                slot=satellite_local_plane_slot(sat)[1],
                node_id=satellite_node_id(sat, AddressingScheme(AddressingConfig())),
            )
            for sat in asset.satellites
        ]
        segment_addressing = AddressingScheme(
            AddressingConfig(),
            local_identity_sats,
            None,
        )
        for node_id, assignment in sorted(
            assign_isl_neighbors(asset.config, segment_addressing),
            key=lambda item: (item[0], item[1].priority, item[1].peer_node_id),
        ):
            pair = (min(node_id, assignment.peer_node_id), max(node_id, assignment.peer_node_id))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            node_a = node_by_id[pair[0]]
            node_b = node_by_id[pair[1]]
            _add_neighbor_pair(
                assignments,
                used_interfaces,
                node_a,
                node_b,
                link_type=assignment.link_type,
                priority=assignment.priority,
                rule_id=f"{asset.segment.id}.internal_isl",
            )

    base_priority = 1000
    for candidate in sorted(
        candidates,
        key=lambda c: (c.rule_id, c.priority, c.pair),
    ):
        if candidate.terminal_role != "isl":
            continue
        node_a = node_by_id[candidate.pair[0]]
        node_b = node_by_id[candidate.pair[1]]
        if candidate.pair in seen_pairs:
            raise SessionResolutionError(
                f"declared ISL candidate {candidate.pair} from link_rule "
                f"{candidate.rule_id!r} duplicates an existing internal ISL"
            )
        seen_pairs.add(candidate.pair)
        link_type = (
            f"static_ip:{candidate.rule_id}"
            if candidate.kind == "inter_body_relay"
            else f"link_rule:{candidate.rule_id}"
        )
        _add_neighbor_pair(
            assignments,
            used_interfaces,
            node_a,
            node_b,
            link_type=link_type,
            priority=base_priority + candidate.priority,
            rule_id=candidate.rule_id,
            medium=candidate.terminal_medium,
        )

    return frozenset(assignments)
