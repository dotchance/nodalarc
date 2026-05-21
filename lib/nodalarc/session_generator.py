# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session generator — produces session YAML from wizard selections.

Takes independent wizard choices (constellation geometry, satellite type,
ground stations, protocol, extensions) and produces a valid session YAML.

Satellite type and constellation are fully orthogonal — the user can
combine any orbital geometry with any terminal hardware.  When the
satellite type differs from the constellation's built-in type, the
generator produces an inline constellation definition with the
satellite_type field replaced.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from nodalarc.catalog_paths import (
    CatalogPathError,
    CatalogRoots,
    config_value_for,
    resolve_constellation_reference,
    resolve_ground_station_reference,
    validate_station_names,
)
from nodalarc.models.session import SessionConfig
from nodalarc.stack_resolver import resolve_stack


class ConstellationPreset(BaseModel):
    """Schema for a constellation preset YAML file."""

    name: str
    description: str
    satellite_count: int
    constellation: str
    ground_stations: str
    time: dict[str, Any] = {}
    traffic_flows: list[dict[str, Any]] = []
    convergence: dict[str, Any] = {}


def _default_catalog_roots() -> CatalogRoots:
    return CatalogRoots.from_config_root(Path("configs"))


def load_constellation_presets(
    catalog_roots: CatalogRoots | None = None,
) -> dict[str, ConstellationPreset]:
    """Scan preset directory and return name -> preset map."""
    presets_dir = (catalog_roots or _default_catalog_roots()).constellation_presets
    presets: dict[str, ConstellationPreset] = {}
    if not presets_dir.is_dir():
        return presets
    for yaml_path in sorted(presets_dir.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text())
        preset = ConstellationPreset.model_validate(raw)
        presets[preset.name] = preset
    return presets


def constellation_source_mode(
    source: str | Path | dict,
    catalog_roots: CatalogRoots | None = None,
) -> str | None:
    """Return the configured constellation mode for a file or inline source."""
    if isinstance(source, dict):
        mode = source.get("mode")
        return mode if isinstance(mode, str) else None
    roots = catalog_roots or _default_catalog_roots()
    source_path = resolve_constellation_reference(source, roots)
    data = yaml.safe_load(source_path.read_text()) or {}
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    return mode if isinstance(mode, str) else None


def merge_constellation_with_satellite_type(
    constellation_path: str,
    satellite_type: str,
    catalog_roots: CatalogRoots | None = None,
) -> dict:
    """Load a constellation file and replace its satellite_type field.

    Returns the merged constellation as a plain dict suitable for
    embedding inline in a session YAML.
    """
    roots = catalog_roots or _default_catalog_roots()
    source_path = resolve_constellation_reference(constellation_path, roots)
    data = yaml.safe_load(source_path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Constellation file is not a dict: {constellation_path}")
    # Replace satellite_type with the wizard's selection
    data["satellite_type"] = satellite_type
    # Remove resolved default_terminals if present — force re-resolution
    # from the new satellite_type at load time
    data.pop("default_terminals", None)
    return data


def generate_session_yaml(
    constellation: str,
    protocol: str,
    extensions: list[str],
    *,
    orbit_propagator: str,
    area_strategy: str = "flat",
    ground_stations: str | list[str] | None = None,
    satellite_type: str | None = None,
    custom_constellation: dict | None = None,
    custom_ground_stations: list[dict] | None = None,
    routing_config: dict | None = None,
    ground_policy: str = "highest-elevation",
    ground_lookahead_horizon_ticks: int = 0,
    catalog_roots: CatalogRoots | None = None,
) -> tuple[str, list[str]]:
    """Generate a session YAML from wizard selections.

    Args:
        constellation: Preset name (used for orbital geometry and defaults).
        protocol: Routing protocol ("ospf", "isis", "nodalpath", etc.).
        extensions: Protocol extensions (["te", "mpls", "sr"]).
        area_strategy: Area assignment strategy.
        ground_stations: GS set file path, list of station names, or None
            (uses preset default).
        satellite_type: Independent satellite type selection. When set and
            different from the constellation's built-in type, produces an
            inline merged constellation definition.
        custom_constellation: Inline constellation dict (advanced mode).
            When provided, ``constellation`` preset is used only for name
            and defaults (time, traffic_flows).
        custom_ground_stations: List of inline station dicts (advanced mode).
        orbit_propagator: Required physical propagation model. This is the
            single user-facing fidelity choice; the fidelity label is derived
            from it.
        ground_policy: Ground handover scoring policy.
        ground_lookahead_horizon_ticks: Required when ground_policy is
            ``longest-remaining-pass``; measured in OME ticks.

    Returns:
        (yaml_str, warnings).
    Raises ValueError for invalid combinations.
    """
    warnings: list[str] = []
    roots = catalog_roots or _default_catalog_roots()

    # Load preset for defaults — optional when custom_constellation is provided
    presets = load_constellation_presets(roots)
    preset = presets.get(constellation)
    if preset is None and custom_constellation is None:
        raise ValueError(f"Unknown constellation preset: {constellation}")

    # Validate routing combo early
    resolve_stack(protocol, extensions)

    # Build session name
    ext_suffix = "-".join(extensions) if extensions else "plain"
    session_name = f"{constellation}-{protocol}-{ext_suffix}"

    # --- Resolve constellation definition ---
    if custom_constellation is not None:
        # Custom or real-world geometry preset: inline constellation dict
        constellation_value: str | dict = custom_constellation
        if satellite_type and isinstance(custom_constellation, dict):
            custom_constellation = dict(custom_constellation)
            custom_constellation["satellite_type"] = satellite_type
            custom_constellation.pop("default_terminals", None)
            constellation_value = custom_constellation
    elif preset is not None and satellite_type:
        # Library preset with satellite type override
        built_in_type = _read_constellation_satellite_type(preset.constellation, roots)
        if built_in_type and built_in_type != satellite_type:
            constellation_value = merge_constellation_with_satellite_type(
                preset.constellation, satellite_type, roots
            )
            warnings.append(
                f"Constellation '{constellation}' uses '{built_in_type}' terminals — "
                f"overriding with '{satellite_type}'"
            )
        else:
            constellation_value = preset.constellation
    elif preset is not None:
        constellation_value = preset.constellation
    else:
        raise ValueError("No constellation source: provide a preset name or custom_constellation")

    constellation_mode = constellation_source_mode(constellation_value, roots)
    if orbit_propagator == "sgp4-tle" and constellation_mode != "tle":
        raise ValueError("orbit_propagator='sgp4-tle' requires a TLE constellation source")
    if constellation_mode == "tle" and orbit_propagator != "sgp4-tle":
        raise ValueError("TLE constellation sources require orbit_propagator='sgp4-tle'")

    # --- Resolve ground stations ---
    if custom_ground_stations is not None:
        gs_value: str | list[str] | dict = {
            "default_terminals": [
                {"type": "optical", "count": 1, "bandwidth_mbps": 1000, "tracking_capacity": 1}
            ],
            "stations": custom_ground_stations,
        }
    elif ground_stations:
        if isinstance(ground_stations, str):
            try:
                gs_value = config_value_for(
                    resolve_ground_station_reference(ground_stations, roots)
                )
            except CatalogPathError as exc:
                raise ValueError(str(exc)) from exc
        elif isinstance(ground_stations, list):
            validate_station_names(ground_stations)
            gs_value = ground_stations
        else:
            gs_value = ground_stations
    elif preset is not None:
        gs_value = preset.ground_stations
    else:
        raise ValueError(
            "No ground station source: provide ground_stations or a preset with defaults"
        )

    # Build area assignment
    area_assignment: dict[str, Any] | None = None
    if protocol != "nodalpath":
        area_assignment = {"strategy": area_strategy}
        if area_strategy == "stripe":
            area_assignment["planes_per_stripe"] = 2
            warnings.append("Using default planes_per_stripe=2 for stripe strategy")
        if protocol == "isis":
            area_assignment["gs_area_id"] = "49.0001"
        elif protocol == "ospf":
            area_assignment["gs_area_id"] = "0.0.0.0"

    routing_overrides = dict(routing_config or {})
    mbb_requested = bool(routing_overrides.pop("mbb_dispatch", protocol == "nodalpath"))
    mbb_overlap_ticks = int(routing_overrides.pop("mbb_overlap_ticks", 3 if mbb_requested else 0))
    supported_propagators = {"keplerian-circular", "j2-mean-elements", "sgp4-tle"}
    if orbit_propagator not in supported_propagators:
        raise ValueError(f"Unsupported orbit_propagator: {orbit_propagator!r}")

    # Build session dict
    session_dict: dict[str, Any] = {
        "session": {"name": session_name},
        "constellation": constellation_value,
        "ground_stations": gs_value,
        "simulation": {
            "schema_version": 2,
        },
        "orbit": {
            "propagator": orbit_propagator,
        },
        "scheduling": {
            "ground": {
                "policy": ground_policy,
                "handover_mode": "mbb" if mbb_requested else "bbm",
                "mbb_overlap_ticks": mbb_overlap_ticks,
                "mbb_reserve": 1 if mbb_requested else 0,
                "lookahead_horizon_ticks": ground_lookahead_horizon_ticks,
            }
        },
        "dispatch": {
            "latency_authority": "ome",
            "max_latency_age_ticks": 1,
            "substrate_compensation": {
                "measurement_source": "node-agent-rtt",
                "rtt_to_one_way": "half-rtt",
            },
        },
        "routing": {
            "protocol": protocol,
            "extensions": extensions,
        },
    }
    if orbit_propagator == "sgp4-tle":
        session_dict["orbit"]["tle_max_age_days"] = 7.0

    if satellite_type:
        session_dict["satellite_type"] = satellite_type

    if area_assignment:
        session_dict["routing"]["area_assignment"] = area_assignment
    if routing_overrides:
        session_dict["routing"].update(routing_overrides)

    if preset and preset.time:
        session_dict["time"] = preset.time
    if preset and preset.traffic_flows:
        session_dict["traffic_flows"] = preset.traffic_flows
    if preset and preset.convergence:
        session_dict["convergence"] = preset.convergence

    # Validate through Pydantic
    SessionConfig.model_validate(session_dict)

    yaml_str = yaml.dump(session_dict, default_flow_style=False, sort_keys=False)
    return yaml_str, warnings


def _read_constellation_satellite_type(
    constellation_path: str,
    catalog_roots: CatalogRoots | None = None,
) -> str | None:
    """Read the satellite_type field from a constellation YAML without full parsing."""
    try:
        roots = catalog_roots or _default_catalog_roots()
        source_path = resolve_constellation_reference(constellation_path, roots)
        data = yaml.safe_load(source_path.read_text())
        if isinstance(data, dict):
            return data.get("satellite_type")
    except Exception:
        pass
    return None
