# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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


_PRESETS_DIR = Path("configs/presets/constellations")


def load_constellation_presets() -> dict[str, ConstellationPreset]:
    """Scan preset directory and return name -> preset map."""
    presets: dict[str, ConstellationPreset] = {}
    if not _PRESETS_DIR.is_dir():
        return presets
    for yaml_path in sorted(_PRESETS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text())
        preset = ConstellationPreset.model_validate(raw)
        presets[preset.name] = preset
    return presets


def merge_constellation_with_satellite_type(
    constellation_path: str,
    satellite_type: str,
) -> dict:
    """Load a constellation file and replace its satellite_type field.

    Returns the merged constellation as a plain dict suitable for
    embedding inline in a session YAML.
    """
    data = yaml.safe_load(Path(constellation_path).read_text())
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
    area_strategy: str = "flat",
    ground_stations: str | list[str] | None = None,
    satellite_type: str | None = None,
    custom_constellation: dict | None = None,
    custom_ground_stations: list[dict] | None = None,
    routing_config: dict | None = None,
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

    Returns:
        (yaml_str, warnings).
    Raises ValueError for invalid combinations.
    """
    warnings: list[str] = []

    # Load preset for defaults — optional when custom_constellation is provided
    presets = load_constellation_presets()
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
        built_in_type = _read_constellation_satellite_type(preset.constellation)
        if built_in_type and built_in_type != satellite_type:
            constellation_value = merge_constellation_with_satellite_type(
                preset.constellation, satellite_type
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

    # --- Resolve ground stations ---
    if custom_ground_stations is not None:
        gs_value: str | list[str] | dict = {
            "default_terminals": [
                {"type": "optical", "count": 1, "bandwidth_mbps": 1000, "tracking_capacity": 1}
            ],
            "stations": custom_ground_stations,
        }
    elif ground_stations:
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

    # Build session dict
    session_dict: dict[str, Any] = {
        "session": {"name": session_name},
        "constellation": constellation_value,
        "ground_stations": gs_value,
        "routing": {
            "protocol": protocol,
            "extensions": extensions,
        },
    }

    if satellite_type:
        session_dict["satellite_type"] = satellite_type

    if area_assignment:
        session_dict["routing"]["area_assignment"] = area_assignment
    if routing_config:
        session_dict["routing"].update(routing_config)

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


def _read_constellation_satellite_type(constellation_path: str) -> str | None:
    """Read the satellite_type field from a constellation YAML without full parsing."""
    try:
        data = yaml.safe_load(Path(constellation_path).read_text())
        if isinstance(data, dict):
            return data.get("satellite_type")
    except Exception:
        pass
    return None
