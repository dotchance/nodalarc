# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session generator for the catalog configuration language.

The wizard is an authoring helper. It emits the same catalog session grammar
that upload/deploy accepts; it does not revive the retired session grammar or
project catalog primitives through old constellation/ground-station models.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from nodalarc.catalog_paths import (
    CatalogRoots,
    resolve_catalog_reference,
    resolve_site_set_reference,
    validate_catalog_name,
)
from nodalarc.models.catalog import validate_catalog_document
from nodalarc.models.resolved_session import SourceContext
from nodalarc.models.segment_session import RoutingTimers
from nodalarc.resolve_session import resolve_session
from nodalarc.stack_resolver import normalize_extensions, resolve_stack


class ConstellationPreset(BaseModel):
    """Wizard card for one shipped constellation primitive."""

    name: str
    description: str
    satellite_count: int
    constellation: str
    ground_stations: str
    mode: str


def _default_catalog_roots() -> CatalogRoots:
    return CatalogRoots.from_catalog_root(Path("catalog/nodalarc"))


def _catalog_ref_for_path(path: Path, roots: CatalogRoots) -> str:
    rel = path.resolve(strict=True).relative_to(roots.root.resolve(strict=True))
    return "nodalarc:" + rel.as_posix()


def _load_catalog_document(ref: str, roots: CatalogRoots) -> tuple[str, dict[str, Any]]:
    path = resolve_catalog_reference(ref, roots)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    wrapper, model = validate_catalog_document(raw)
    return wrapper, model.model_dump(mode="python", by_alias=True, exclude_none=True)


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
) -> dict[str, ConstellationPreset]:
    """Scan catalog constellation primitives and return wizard cards."""
    roots = catalog_roots or _default_catalog_roots()
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
            mode=wrapper,
        )
        results[preset.name] = preset
    return results


def constellation_source_mode(
    source: str | Path | dict,
    catalog_roots: CatalogRoots | None = None,
) -> str | None:
    """Return the catalog wrapper for a constellation-like source."""
    if isinstance(source, dict):
        try:
            wrapper, _model = validate_catalog_document(source)
        except Exception:
            return None
        return wrapper
    roots = catalog_roots or _default_catalog_roots()
    try:
        wrapper, _value = _load_catalog_document(str(source), roots)
    except Exception:
        return None
    return wrapper


def _resolve_constellation_source(
    constellation: str,
    preset: ConstellationPreset | None,
    roots: CatalogRoots,
    custom_constellation: dict | None,
) -> str | dict[str, Any]:
    if custom_constellation is not None:
        wrapper, _model = validate_catalog_document(custom_constellation)
        if wrapper not in {"constellation", "space_node_set"}:
            raise ValueError(
                "custom_constellation must be a catalog constellation or space_node_set object"
            )
        return custom_constellation
    if preset is not None:
        return preset.constellation
    if isinstance(constellation, str) and constellation.startswith("nodalarc:"):
        wrapper, _value = _load_catalog_document(constellation, roots)
        if wrapper not in {"constellation", "space_node_set"}:
            raise ValueError(f"constellation source must resolve to constellation, got {wrapper!r}")
        return constellation
    raise ValueError(f"Unknown constellation preset: {constellation}")


def _resolve_ground_source(
    ground_stations: str | list[str] | dict | None,
    preset: ConstellationPreset | None,
    custom_ground_stations: list[dict] | None,
    roots: CatalogRoots,
) -> str | dict[str, Any]:
    if custom_ground_stations is not None:
        raise ValueError(
            "custom_ground_stations list is retired; provide a catalog site_set object instead"
        )
    if isinstance(ground_stations, dict):
        wrapper, _model = validate_catalog_document(ground_stations)
        if wrapper != "site_set":
            raise ValueError("custom ground source must be a catalog site_set object")
        return ground_stations
    if isinstance(ground_stations, list):
        raise ValueError("ground station name lists are retired; provide a catalog site_set")
    if isinstance(ground_stations, str) and ground_stations:
        resolve_site_set_reference(ground_stations, roots)
        return ground_stations
    if preset is not None:
        resolve_site_set_reference(preset.ground_stations, roots)
        return preset.ground_stations
    raise ValueError("No ground station source: provide a catalog site_set")


def _routing_capabilities(extensions: tuple[str, ...]) -> dict[str, Any] | None:
    capabilities: dict[str, Any] = {}
    if "mpls" in extensions:
        capabilities["mpls"] = {}
    if "sr" in extensions:
        capabilities["segment_routing"] = {"data_plane": "mpls"}
    if "te" in extensions:
        capabilities["traffic_engineering"] = {}
    return capabilities or None


def _area_assignment(area_strategy: str) -> dict[str, Any]:
    strategy = validate_catalog_name(area_strategy, label="area_strategy")
    if strategy == "flat":
        return {"strategy": "flat", "gs_area_id": "area0"}
    if strategy == "per_plane":
        return {"strategy": "per_plane", "gs_area_id": "area0"}
    if strategy == "stripe":
        return {"strategy": "stripe", "gs_area_id": "area0", "planes_per_stripe": 2}
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
        "step_seconds": 10,
        "compression": 1,
    }


def generate_session_yaml(
    constellation: str,
    protocol: str,
    extensions: list[str],
    *,
    orbit_propagator: str,
    area_strategy: str = "flat",
    ground_stations: str | list[str] | dict | None = None,
    satellite_type: str | None = None,
    custom_constellation: dict | None = None,
    custom_ground_stations: list[dict] | None = None,
    routing_config: dict | None = None,
    timers: dict | None = None,
    ground_policy: str = "highest_elevation",
    ground_selection_lookahead_horizon_ticks: int = 0,
    catalog_roots: CatalogRoots | None = None,
) -> tuple[str, list[str]]:
    """Generate catalog session YAML from wizard selections."""
    warnings: list[str] = []
    roots = catalog_roots or _default_catalog_roots()
    if satellite_type is not None:
        validate_catalog_name(satellite_type, label="satellite_type")
        raise ValueError(
            "satellite_type overrides are retired; choose or author a constellation primitive"
        )

    protocol = validate_catalog_name(protocol, label="protocol")
    normalized_extensions = normalize_extensions(tuple(extensions))
    resolve_stack(protocol, list(normalized_extensions))

    supported_propagators = {"two_body", "j2_mean_elements", "sgp4_tle"}
    if orbit_propagator not in supported_propagators:
        raise ValueError(f"Unsupported orbit_propagator: {orbit_propagator!r}")
    if orbit_propagator == "sgp4_tle":
        raise ValueError(
            "orbit_propagator='sgp4_tle' is structurally valid future grammar, "
            "but the current runtime does not materialize TLE inputs"
        )

    presets = load_constellation_presets(roots)
    preset = presets.get(constellation)
    constellation_value = _resolve_constellation_source(
        constellation,
        preset,
        roots,
        custom_constellation,
    )
    ground_value = _resolve_ground_source(
        ground_stations,
        preset,
        custom_ground_stations,
        roots,
    )

    ext_suffix = "-".join(normalized_extensions) if normalized_extensions else "plain"
    session_name = validate_catalog_name(f"{constellation}-{protocol}-{ext_suffix}".lower())
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

    session_dict: dict[str, Any] = {
        "session": {"name": session_name},
        "segments": [
            {"id": "space", "source": constellation_value},
            {
                "id": "ground",
                "placement": {"from_site_set": ground_value},
                "apply": {"scheduling": ground_scheduling},
            },
        ],
        "link_rules": [
            {
                "id": "ground_access",
                "topology": {"mode": "visible_candidates"},
                "endpoints": [
                    {
                        "select": {"segment": "ground"},
                        "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                        "min_elevation_deg": 10,
                    },
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                    },
                ],
            },
            {
                "id": "space_isl",
                "topology": {"mode": "nearest_n", "n": 1},
                "endpoints": [
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
                    },
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
                    },
                ],
            },
        ],
        "addressing": {
            "loopbacks": [
                {
                    "id": "space_loopbacks_v4",
                    "applies_to": {"segment": "space"},
                    "ipv4_pool": "10.0.0.0/16",
                    "prefix_length": 32,
                    "allocation": "by_plane_slot",
                },
                {
                    "id": "space_loopbacks_v6",
                    "applies_to": {"segment": "space"},
                    "ipv6_pool": "fd00::/64",
                    "prefix_length": 128,
                    "allocation": "by_plane_slot",
                },
            ]
        },
        "routing": {
            "domains": [
                {
                    "id": "default",
                    "protocol": protocol,
                    "selectors": [{"any": [{"segment": "space"}, {"segment": "ground"}]}],
                    "area_assignment": _area_assignment(area_strategy),
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
        "orbit": {"default_propagator": orbit_propagator},
        "time": _default_time(),
        "dispatch": {"latency_authority": "ome", "max_latency_age_ticks": 3},
    }
    if routing_config:
        raise ValueError(
            "routing_config overrides are retired; use routing.domains[].timers "
            "(the 'timers' request field) for IGP timer tuning"
        )

    resolve_session(
        session_dict,
        catalog_roots=roots,
        source_context=SourceContext(origin="session_generator"),
    )
    return yaml.safe_dump(session_dict, default_flow_style=False, sort_keys=False), warnings


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


def merge_constellation_with_satellite_type(
    constellation_path: str,
    satellite_type: str,
    catalog_roots: CatalogRoots | None = None,
) -> dict:
    """Reject the retired satellite-type override path."""
    validate_catalog_name(satellite_type, label="satellite_type")
    raise ValueError(
        "satellite_type overrides are retired; choose or author a constellation primitive"
    )
