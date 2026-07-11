# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Backend preset assembly for the catalog configuration language.

Preset commands assemble the same catalog session document consumed by the
Builder compiler. They do not serialize a Wizard-specific YAML artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nodalarc.catalog_paths import (
    CatalogRoots,
    resolve_catalog_reference,
    resolve_site_set_reference,
    validate_catalog_name,
)
from nodalarc.catalog_refs import CatalogRef, SiteSetRef, SpaceSourceRef
from nodalarc.catalog_registry import validate_referenced_configuration_document
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.builder_api import (
    WizardConstellationCapability,
    WizardConstellationGeometry,
    WizardConstellationPreset,
    WizardConstellationPresetResponse,
    WizardOrbitModelMetadata,
)
from nodalarc.models.resolved_session import ResolvedNode, ResolvedSession, SourceContext
from nodalarc.models.segment_session import RoutingTimers
from nodalarc.resolve_session import resolve_session
from nodalarc.runtime_support import RuntimeSupport, UnsupportedFeatureError
from nodalarc.stack_resolver import normalize_extensions, resolve_stack

ConstellationPreset = WizardConstellationPreset

_WIZARD_GENERATED_ORBIT_PROPAGATORS = frozenset({"two_body", "j2_mean_elements"})
_WIZARD_SELECTABLE_PROPAGATORS = frozenset({*_WIZARD_GENERATED_ORBIT_PROPAGATORS, "sgp4_tle"})
WIZARD_CUSTOM_GEOMETRY_DEFAULT_NODE = "nodalarc:nodes/space/starlink-v2-mesh.yaml"

_WIZARD_ORBIT_MODELS = (
    WizardOrbitModelMetadata(
        id="j2_mean_elements",
        label="J2 Mean Elements",
        description=(
            "Includes Earth oblateness drift for parametric orbital geometry without "
            "requiring TLE data."
        ),
    ),
    WizardOrbitModelMetadata(
        id="two_body",
        label="Keplerian Two-Body",
        description="Propagates parametric orbital geometry with the two-body model.",
    ),
    WizardOrbitModelMetadata(
        id="sgp4_tle",
        label="SGP4 / TLE",
        description="Propagates explicit TLE-backed space-node placements with SGP4.",
    ),
)

_WIZARD_CUSTOM_GEOMETRY_SEED = WizardConstellationGeometry(
    display_name="Custom 4x11 shell",
    description="4 planes x 11 satellites, 550 km, 53 degree Walker delta",
    altitude_km=550,
    inclination_deg=53,
    pattern="walker_delta",
    planes=4,
    slots_per_plane=11,
    raan_spacing_deg=90,
    phase_offset_deg=360 / 44,
)


def _default_catalog_roots() -> CatalogRoots:
    return CatalogRoots.from_catalog_root(Path("catalog/nodalarc"))


def _catalog_ref_for_path(path: Path, roots: CatalogRoots) -> str:
    rel = path.resolve(strict=True).relative_to(roots.root.resolve(strict=True))
    return "nodalarc:" + rel.as_posix()


def _load_catalog_document(ref: str, roots: CatalogRoots) -> tuple[str, dict[str, Any]]:
    parsed = CatalogRef(ref)
    path = resolve_catalog_reference(parsed, roots)
    raw = load_configuration_yaml(path.read_text(encoding="utf-8")) or {}
    wrapper, model = validate_referenced_configuration_document(parsed, raw)
    if wrapper is None:
        raise ValueError(f"expected wrapped catalog object, got session {parsed!r}")
    return wrapper, model.model_dump(mode="python", by_alias=True, exclude_none=True)


def constellation_source_runtime_capability(
    source: str,
    roots: CatalogRoots,
    runtime_support: RuntimeSupport | None = None,
) -> WizardConstellationCapability:
    support = runtime_support or RuntimeSupport.earth_luna()
    source_ref = SpaceSourceRef(source)
    source_kind, source_value = _load_catalog_document(str(source_ref), roots)
    if source_kind not in {"constellation", "space_node_set"}:
        raise ValueError(
            "Wizard constellation source must resolve to a constellation or space_node_set, "
            f"got {source_kind!r}"
        )

    source_nodes = source_value["nodes"] if source_kind == "space_node_set" else ()
    orbit_refs = (
        (source_value["orbit"],)
        if source_kind == "constellation"
        else tuple(node["orbit"] for node in source_nodes if "orbit" in node)
    )
    tle_placements = tuple(node["sgp4_tle"] for node in source_nodes if "sgp4_tle" in node)
    if not orbit_refs and not tle_placements:
        return WizardConstellationCapability(
            source_kind=source_kind,
            runtime_supported_propagators=(),
            default_propagator=None,
            unavailable_reason=(
                "The Wizard cannot select a propagator for a space-node set that uses "
                "state-vector placement."
            ),
        )

    orbits: list[dict[str, Any]] = []
    bodies: list[dict[str, Any]] = []
    for orbit_ref in orbit_refs:
        orbit_kind, orbit = _load_catalog_document(orbit_ref, roots)
        if orbit_kind != "orbit":
            raise ValueError(f"space source orbit must resolve to orbit, got {orbit_kind!r}")
        body_kind, body = _load_catalog_document(orbit["central_body"], roots)
        if body_kind != "body":
            raise ValueError(f"orbit central body must resolve to body, got {body_kind!r}")
        orbits.append(orbit)
        bodies.append(body)

    for tle in tle_placements:
        body_kind, body = _load_catalog_document(tle["central_body"], roots)
        if body_kind != "body":
            raise ValueError(f"TLE central body must resolve to body, got {body_kind!r}")
        bodies.append(body)

    source_propagators = {
        *(str(orbit["propagator"]) for orbit in orbits),
        *("sgp4_tle" for _tle in tle_placements),
    }
    source_propagator = next(iter(source_propagators)) if len(source_propagators) == 1 else None
    if source_propagator is None:
        supported: tuple[str, ...] = ()
    elif source_kind == "space_node_set":
        supported = (
            (source_propagator,)
            if source_propagator in _WIZARD_SELECTABLE_PROPAGATORS
            and support.check_propagator(source_propagator) is None
            else ()
        )
    else:
        supported = tuple(
            propagator
            for propagator in support.compatible_supported_propagators(source_propagator)
            if propagator in _WIZARD_GENERATED_ORBIT_PROPAGATORS
        )
    blocker = support.check_segment_kind(source_kind)
    if blocker is None:
        blocker = next(
            (
                unsupported
                for body in bodies
                if (unsupported := support.check_central_body(str(body["id"]))) is not None
            ),
            None,
        )
    if blocker is not None:
        supported = ()
        unavailable_reason = blocker.message
    elif source_propagator is None:
        unavailable_reason = (
            "The Wizard cannot apply one propagator selection to a space source that uses "
            "multiple propagators."
        )
    elif not supported:
        unsupported = support.check_propagator(source_propagator)
        unavailable_reason = (
            unsupported.message
            if unsupported is not None
            else (
                f"The Wizard cannot author the source propagator {source_propagator!r} "
                "without changing its declared physical model."
            )
        )
    else:
        unavailable_reason = None

    return WizardConstellationCapability(
        source_kind=source_kind,
        runtime_supported_propagators=supported,
        default_propagator=(source_propagator if source_propagator in supported else None),
        unavailable_reason=unavailable_reason,
    )


def custom_geometry_runtime_capability(
    runtime_support: RuntimeSupport | None = None,
) -> WizardConstellationCapability:
    support = runtime_support or RuntimeSupport.earth_luna()
    supported = tuple(
        propagator
        for propagator in support.compatible_supported_propagators("j2_mean_elements")
        if propagator in _WIZARD_GENERATED_ORBIT_PROPAGATORS
    )
    if supported:
        unavailable_reason = None
        default = "j2_mean_elements" if "j2_mean_elements" in supported else supported[0]
    else:
        unavailable_reason = (
            "The current runtime does not support a propagator for Wizard-authored "
            "Keplerian constellation geometry."
        )
        default = None
    return WizardConstellationCapability(
        source_kind="custom_geometry",
        runtime_supported_propagators=supported,
        default_propagator=default,
        unavailable_reason=unavailable_reason,
    )


def _default_ground_sites_for_constellation(ref: str) -> str:
    if "/luna/" in ref:
        return "nodalarc:site-sets/luna/luna-surface-sites.yaml"
    if "/earth/geo/" in ref:
        return "nodalarc:site-sets/earth/geo/earth-geo-gateway-sites.yaml"
    if "/earth/heo/" in ref:
        return "nodalarc:site-sets/earth/heo/earth-heo-gateway-sites.yaml"
    if "/earth/meo/" in ref:
        return "nodalarc:site-sets/earth/meo/earth-meo-gateway-sites.yaml"
    if "polar" in ref:
        return "nodalarc:site-sets/earth/leo/earth-leo-polar-gateway-sites.yaml"
    return "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml"


def _satellite_count(wrapper: str, value: dict[str, Any]) -> int:
    if wrapper == "constellation":
        return int(value["planes"]["count"]) * int(value["slots_per_plane"])
    if wrapper == "space_node_set":
        return len(value["nodes"])
    return 0


def load_constellation_presets(
    catalog_roots: CatalogRoots | None = None,
    *,
    runtime_support: RuntimeSupport | None = None,
) -> dict[str, ConstellationPreset]:
    """Scan catalog constellation primitives and return wizard cards."""
    roots = catalog_roots or _default_catalog_roots()
    support = runtime_support or RuntimeSupport.earth_luna()
    results: dict[str, ConstellationPreset] = {}
    for yaml_path in sorted((roots.root / "constellations").rglob("*.yaml")):
        ref = _catalog_ref_for_path(yaml_path, roots)
        wrapper, value = _load_catalog_document(ref, roots)
        if wrapper != "constellation":
            continue
        preset = ConstellationPreset(
            name=value["id"],
            description=value.get("notes") or value.get("display_name") or value["id"],
            satellite_count=_satellite_count(wrapper, value),
            constellation=ref,
            ground_stations=_default_ground_sites_for_constellation(ref),
            default_node=constellation_default_node(ref, roots),
            capability=constellation_source_runtime_capability(ref, roots, support),
        )
        results[preset.name] = preset
    return results


def load_constellation_preset_response(
    catalog_roots: CatalogRoots | None = None,
    *,
    runtime_support: RuntimeSupport | None = None,
) -> WizardConstellationPresetResponse:
    """Return the closed Wizard preset catalog and backend capability facts."""

    roots = catalog_roots or _default_catalog_roots()
    support = runtime_support or RuntimeSupport.earth_luna()
    presets = load_constellation_presets(roots, runtime_support=support)
    return WizardConstellationPresetResponse(
        presets=tuple(presets.values()),
        custom_geometry=custom_geometry_runtime_capability(support),
        custom_geometry_seed=_WIZARD_CUSTOM_GEOMETRY_SEED,
        custom_geometry_default_node=WIZARD_CUSTOM_GEOMETRY_DEFAULT_NODE,
        orbit_models=_WIZARD_ORBIT_MODELS,
    )


def constellation_source_mode(
    source: str | Path,
    catalog_roots: CatalogRoots | None = None,
) -> str | None:
    """Return the catalog wrapper for a constellation-like source."""
    roots = catalog_roots or _default_catalog_roots()
    try:
        wrapper, _value = _load_catalog_document(str(SpaceSourceRef(str(source))), roots)
    except Exception:
        return None
    return wrapper


def _routing_capabilities(extensions: tuple[str, ...]) -> dict[str, Any] | None:
    capabilities: dict[str, Any] = {}
    if "mpls" in extensions:
        capabilities["mpls"] = {}
    if "sr" in extensions:
        capabilities["segment_routing"] = {"data_plane": "mpls"}
    if "te" in extensions:
        capabilities["traffic_engineering"] = {}
    return capabilities or None


def _space_node_isl_count(node_ref: str, roots: CatalogRoots) -> int:
    wrapper, node = _load_catalog_document(node_ref, roots)
    if wrapper != "node":
        raise ValueError(f"constellation node reference must resolve to node, got {wrapper!r}")
    return sum(
        int(mount.get("count", 1))
        for mount in node.get("terminals", ())
        if mount.get("role") == "isl"
    )


def _local_sat_id(plane: int, slot: int) -> str:
    return f"sat-p{plane:02d}s{slot:02d}"


def _walker_mesh_pairs(
    *,
    planes: int,
    slots_per_plane: int,
    raan_spacing_deg: float,
    isl_terminal_count: int,
) -> tuple[dict[str, str], ...]:
    """Generate the deterministic Walker ISL grid used by Starlink-style nodes."""
    if isl_terminal_count < 2:
        return ()

    pairs: set[tuple[str, str]] = set()

    def add_pair(a: str, b: str) -> None:
        if a == b:
            return
        pairs.add((a, b) if a < b else (b, a))

    for plane in range(planes):
        for slot in range(slots_per_plane):
            add_pair(_local_sat_id(plane, slot), _local_sat_id(plane, (slot + 1) % slots_per_plane))

    if isl_terminal_count >= 4 and planes > 1:
        wraps_cross_plane = raan_spacing_deg * planes >= 360.0
        last_cross_plane = planes if wraps_cross_plane else planes - 1
        for plane in range(last_cross_plane):
            right_plane = (plane + 1) % planes
            for slot in range(slots_per_plane):
                add_pair(_local_sat_id(plane, slot), _local_sat_id(right_plane, slot))

    return tuple({"a": a, "b": b} for a, b in sorted(pairs))


def generated_isl_topology(
    constellation_source: str,
    catalog_roots: CatalogRoots | None = None,
) -> dict[str, Any] | None:
    roots = catalog_roots or _default_catalog_roots()
    source_ref = SpaceSourceRef(constellation_source)
    wrapper, body = _load_catalog_document(str(source_ref), roots)
    if wrapper != "constellation":
        return None
    node_ref = body.get("node")
    if not isinstance(node_ref, str):
        raise ValueError("constellation node reference must be a catalog reference string")
    pairs = _walker_mesh_pairs(
        planes=int(body["planes"]["count"]),
        slots_per_plane=int(body["slots_per_plane"]),
        raan_spacing_deg=float(body["planes"]["raan_spacing_deg"]),
        isl_terminal_count=_space_node_isl_count(node_ref, roots),
    )
    if not pairs:
        return None
    return {"mode": "explicit_pairs", "pairs": list(pairs)}


def _area_assignment(area_strategy: str) -> dict[str, Any]:
    strategy = validate_catalog_name(area_strategy, label="area_strategy")
    if strategy == "flat":
        return {"strategy": "flat"}
    if strategy == "per_plane":
        return {"strategy": "per_plane"}
    if strategy == "stripe":
        return {"strategy": "stripe", "planes_per_stripe": 2}
    raise ValueError(f"Unsupported area_strategy: {area_strategy!r}")


def _selection_policy(ground_policy: str, lookahead_ticks: int) -> dict[str, Any]:
    policy = validate_catalog_name(ground_policy, label="ground_policy")
    if policy == "highest_elevation":
        if lookahead_ticks:
            raise ValueError(
                "ground_selection_lookahead_horizon_ticks is only valid with longest_remaining_pass"
            )
        return {"highest_elevation": {}}
    if policy == "lowest_elevation":
        if lookahead_ticks:
            raise ValueError(
                "ground_selection_lookahead_horizon_ticks is only valid with longest_remaining_pass"
            )
        return {"lowest_elevation": {}}
    if policy == "longest_remaining_pass":
        if lookahead_ticks <= 0:
            raise ValueError(
                "ground_selection_lookahead_horizon_ticks is required with longest_remaining_pass"
            )
        return {"longest_remaining_pass": {"lookahead_horizon_ticks": int(lookahead_ticks)}}
    raise ValueError(f"Unsupported ground_policy: {ground_policy!r}")


def _default_time() -> dict[str, Any]:
    return {
        "start_time": "2026-06-08T00:00:00Z",
        "step_seconds": 1,
        "compression": 1,
    }


# Orbit-regime classification thresholds — keep in lockstep with the frontend
# mirror (frontend/src/taxonomy/regime.ts). Regime is a property of the
# authored orbit (a Molniya bird at perigee is still HEO); anything outside
# the known classes stays unclassified rather than guessed.
_GEO_ALTITUDE_KM = 35_786.0
_GEO_BAND_KM = 1_500.0
_LEO_CEILING_KM = 2_000.0
_HEO_ECCENTRICITY = 0.25


def _orbit_regime(orbit: Any, radius_by_body: dict[str, float]) -> str | None:
    """Classify one resolved orbit; None when no known class applies."""
    if orbit.central_body == "luna":
        return "luna"
    if orbit.central_body != "earth":
        return None
    radius = radius_by_body.get("earth")
    if radius is None:
        return None
    if orbit.eccentricity >= _HEO_ECCENTRICITY:
        return "heo"
    altitude_km = orbit.semi_major_axis_km - radius
    if altitude_km < _LEO_CEILING_KM:
        return "leo"
    if altitude_km < _GEO_ALTITUDE_KM - _GEO_BAND_KM:
        return "meo"
    if altitude_km <= _GEO_ALTITUDE_KM + _GEO_BAND_KM:
        return "geo"
    return None


def _space_segment_id(resolved: Any) -> str:
    """Name the generated space segment after its orbit regime.

    Runtime node ids are {segment}-{local}, so this is what makes a wizard
    session produce leo-sat-p00s00 instead of space-sat-p00s00 — the same
    orbit-derived naming the shipped sessions use. A mixed or unclassifiable
    constellation keeps the neutral id.
    """
    radius_by_body = {body.body_id: body.mean_radius_km for body in resolved.bodies}
    regimes = {
        _orbit_regime(node.orbit, radius_by_body)
        for node in resolved.nodes
        if node.orbit is not None
    }
    regimes.discard(None)
    if len(regimes) == 1:
        return regimes.pop()
    return "space"


def _rf_access_mounts(node: ResolvedNode) -> set[str]:
    return {
        str(block.terminal_id)
        for block in node.terminal_inventory
        if block.endpoint_role == "access" and block.medium == "rf"
    }


def _common_access_mount(
    resolved: ResolvedSession,
    *,
    space_segment_id: str,
    ground_tag: str | None,
) -> str:
    space_nodes = [
        node
        for node in resolved.nodes
        if node.kind == "satellite" and node.segment_id == space_segment_id
    ]
    ground_nodes = [
        node
        for node in resolved.nodes
        if node.kind == "ground_station"
        and (node.segment_id == "ground" or "ground" in node.placement_groups)
        and (ground_tag is None or ground_tag in node.tags)
    ]
    if not space_nodes:
        raise ValueError(f"generated session segment {space_segment_id!r} contains no satellites")
    if not ground_nodes:
        qualifier = "" if ground_tag is None else f" tagged {ground_tag!r}"
        raise ValueError(f"generated session ground segment contains no nodes{qualifier}")

    mount_sets = [_rf_access_mounts(node) for node in (*space_nodes, *ground_nodes)]
    common = set.intersection(*mount_sets) if mount_sets else set()
    if len(common) != 1:
        inventory = {
            str(node.node_id): sorted(_rf_access_mounts(node))
            for node in (*space_nodes, *ground_nodes)
        }
        raise ValueError(
            "Wizard session requires one exact RF access mount shared by every selected "
            f"space and ground node; found {sorted(common)} across {inventory}"
        )
    return common.pop()


def assemble_session_document(
    constellation: str,
    protocol: str,
    extensions: list[str],
    *,
    orbit_propagator: str,
    area_strategy: str = "flat",
    ground_stations: str | None = None,
    timers: dict | None = None,
    ground_policy: str = "highest_elevation",
    ground_selection_lookahead_horizon_ticks: int = 0,
    session_name: str | None = None,
    catalog_roots: CatalogRoots | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Assemble one ref-composed session document from catalog choices."""
    warnings: list[str] = []
    roots = catalog_roots or _default_catalog_roots()
    protocol = validate_catalog_name(protocol, label="protocol")
    normalized_extensions = normalize_extensions(tuple(extensions))
    resolve_stack(protocol, list(normalized_extensions))

    runtime_support = RuntimeSupport.earth_luna()
    if unsupported := runtime_support.check_propagator(orbit_propagator):
        raise UnsupportedFeatureError([unsupported])

    constellation_ref = SpaceSourceRef(constellation)
    source_wrapper, _source = _load_catalog_document(str(constellation_ref), roots)
    expected_wrapper = (
        "constellation" if constellation_ref.family == "constellations" else "space_node_set"
    )
    if source_wrapper != expected_wrapper:
        raise ValueError(
            f"space source reference must resolve to {expected_wrapper}, got {source_wrapper!r}"
        )

    ground_ref = SiteSetRef(
        ground_stations or _default_ground_sites_for_constellation(str(constellation_ref))
    )
    resolve_site_set_reference(str(ground_ref), roots)
    constellation_value = str(constellation_ref)
    ground_value = str(ground_ref)
    isl_topology = generated_isl_topology(constellation_value, roots)

    ext_suffix = "-".join(normalized_extensions) if normalized_extensions else "plain"
    resolved_session_name = validate_catalog_name(
        session_name or f"{constellation_ref.relative_path.stem}-{protocol}-{ext_suffix}".lower()
    )
    capabilities = _routing_capabilities(normalized_extensions)

    ground_scheduling = {
        "selection_policy": _selection_policy(
            ground_policy,
            ground_selection_lookahead_horizon_ticks,
        ),
        "handover_policy": {
            "hysteresis": {
                "discount_factor": 1.15,
                "mask_fade_range_deg": 5.0,
            }
        },
        "handover_mode": "mbb",
        "mbb_overlap_ticks": 3,
        "mbb_reserve": 1,
        "handover_concurrency": "one_at_a_time",
        "ranking_order": [
            "service_priority",
            "selection_score",
            "satellite_ground_terminal_capacity",
            "lex_pair",
        ],
        "mbb_preemption": "off",
        "successor_abort_policy": "hard_release",
        "cross_tenant_displacement": "off",
        "bbm_acquire_timeout_ticks": 1,
    }

    def _session_dict(space_id: str, *, access_mount: str | None) -> dict[str, Any]:
        link_rules: list[dict[str, Any]] = []
        if access_mount is not None:
            ground_selector: dict[str, Any] = {"segment": "ground"}
            if space_id != "space":
                ground_selector = {
                    "all": [
                        {"segment": "ground"},
                        {"tag": space_id},
                    ]
                }
            link_rules.append(
                {
                    "id": f"{space_id}_access",
                    "topology": {"mode": "visible_candidates"},
                    "endpoints": [
                        {
                            "select": ground_selector,
                            "terminal": {
                                "all": [
                                    {"role": "access"},
                                    {"medium": "rf"},
                                    {"mount": access_mount},
                                ]
                            },
                            "min_elevation_deg": 10,
                        },
                        {
                            "select": {"segment": space_id},
                            "terminal": {
                                "all": [
                                    {"role": "access"},
                                    {"medium": "rf"},
                                    {"mount": access_mount},
                                ]
                            },
                        },
                    ],
                }
            )
        if isl_topology is not None:
            link_rules.append(
                {
                    "id": f"{space_id}_isl",
                    "topology": isl_topology,
                    "endpoints": [
                        {
                            "select": {"segment": space_id},
                            "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
                        },
                        {
                            "select": {"segment": space_id},
                            "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
                        },
                    ],
                }
            )
        return {
            "session": {"name": resolved_session_name},
            "segments": [
                {"id": space_id, "source": constellation_value},
                {
                    "id": "ground",
                    "placement": {"from_site_set": ground_value},
                    "apply": {"scheduling": ground_scheduling},
                },
            ],
            "link_rules": link_rules,
            "addressing": {
                "loopbacks": [
                    {
                        "id": f"{space_id}_loopbacks_v4",
                        "applies_to": {"segment": space_id},
                        "ipv4_pool": "10.0.0.0/16",
                        "prefix_length": 32,
                        "allocation": "by_node_order",
                    },
                    {
                        "id": f"{space_id}_loopbacks_v6",
                        "applies_to": {"segment": space_id},
                        "ipv6_pool": "fd00::/64",
                        "prefix_length": 128,
                        "allocation": "by_node_order",
                    },
                ]
            },
            "routing": {
                "domains": [
                    {
                        "id": "default",
                        "protocol": protocol,
                        "selectors": [{"any": [{"segment": space_id}, {"segment": "ground"}]}],
                        **(
                            {"area_assignment": _area_assignment(area_strategy)}
                            if protocol in {"isis", "ospf"}
                            else {}
                        ),
                        **({"capabilities": capabilities} if capabilities else {}),
                        **(_timers_block(timers)),
                    }
                ]
            },
            "simulation": {
                "candidate_limits": {
                    "max_pairs_per_rule": 100000,
                    "max_pairs_per_tick": 100000,
                }
            },
            "time": _default_time(),
            "dispatch": {"latency_authority": "ome", "max_latency_age_ticks": 3},
        }

    provisional_session = _session_dict("space", access_mount=None)
    provisional_resolved = resolve_session(
        provisional_session,
        catalog_roots=roots,
        source_context=SourceContext(origin="session_generator"),
    )
    # Name the space segment after its orbit regime (resolved orbit facts are
    # the one truth source), then re-resolve the renamed session so the YAML
    # we return is exactly what was validated.
    space_id = _space_segment_id(provisional_resolved)
    access_mount = _common_access_mount(
        provisional_resolved,
        space_segment_id="space",
        ground_tag=None if space_id == "space" else space_id,
    )
    session_dict = _session_dict(space_id, access_mount=access_mount)
    resolved = resolve_session(
        session_dict,
        catalog_roots=roots,
        source_context=SourceContext(origin="session_generator"),
    )
    # The requested propagator must be what the selected catalog content
    # actually uses — orbit primitives own their propagator, so a divergent
    # wizard choice is an authoring error, never a silent no-op.
    actual = sorted({node.orbit.propagator for node in resolved.nodes if node.orbit is not None})
    if actual and orbit_propagator not in actual:
        raise ValueError(
            f"requested orbit_propagator {orbit_propagator!r} does not match the selected "
            f"constellation's orbit propagator(s) {actual}"
        )
    json_document = json.loads(json.dumps(session_dict, allow_nan=False))
    return json_document, warnings


def _timers_block(timers: dict | None) -> dict:
    """Validated per-domain timer tuning for generated sessions.

    Defaults are engine-owned: only non-default values are written into the
    generated YAML, so an untouched wizard panel emits no timers block.
    """
    if not timers:
        return {}
    validated = RoutingTimers.model_validate(timers)
    dumped = validated.model_dump(mode="python", exclude_defaults=True)
    for key in ("spf", "bfd"):
        if key in dumped and not dumped[key]:
            del dumped[key]
    return {"timers": dumped} if dumped else {}


def constellation_default_node(
    source: str,
    catalog_roots: CatalogRoots | None = None,
) -> str | None:
    """The node primitive id a constellation flies when none is chosen."""
    roots = catalog_roots or _default_catalog_roots()
    try:
        source_ref = SpaceSourceRef(source)
        path = resolve_catalog_reference(source_ref, roots)
        raw = load_configuration_yaml(path.read_text(encoding="utf-8")) or {}
        wrapper, model = validate_referenced_configuration_document(source_ref, raw)
    except Exception:
        return None
    if wrapper != "constellation":
        return None
    body = model.model_dump(mode="python", by_alias=True, exclude_none=True)
    node_ref = body.get("node")
    if not isinstance(node_ref, str):
        return None
    return Path(node_ref).stem


def list_space_node_presets(catalog_roots: CatalogRoots | None = None) -> list[dict[str, Any]]:
    """Space node primitives available to fly a constellation's geometry.

    Sessions assemble from primitives: a constellation is geometry (orbit,
    planes, phasing) plus a default node; which satellite actually flies it
    is a separate primitive choice. This lists the candidates.
    """
    roots = catalog_roots or _default_catalog_roots()
    nodes_dir = roots.root / "nodes" / "space"
    results: list[dict[str, Any]] = []
    if not nodes_dir.is_dir():
        return results
    for path in sorted(nodes_dir.glob("*.yaml")):
        ref = CatalogRef(_catalog_ref_for_path(path, roots))
        raw = load_configuration_yaml(path.read_text(encoding="utf-8")) or {}
        wrapper, model = validate_referenced_configuration_document(ref, raw)
        if wrapper != "node":
            continue
        data = model.model_dump(mode="python", by_alias=True, exclude_none=True)
        results.append(
            {
                "name": data["id"],
                "display_name": data.get("display_name") or data["id"],
                "notes": data.get("notes") or "",
                "file": f"nodalarc:nodes/space/{path.name}",
                "terminals": [
                    {
                        "id": mount["id"],
                        "role": mount.get("role"),
                        "count": mount.get("count", 1),
                    }
                    for mount in data.get("terminals", [])
                ],
            }
        )
    return results
