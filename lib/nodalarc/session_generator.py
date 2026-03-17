"""Session generator — produces session YAML from wizard selections.

Takes a constellation preset name, protocol, extensions, and area strategy,
and produces a valid session YAML string.
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


def generate_session_yaml(
    constellation: str,
    protocol: str,
    extensions: list[str],
    area_strategy: str = "flat",
    ground_stations: str | list[str] | None = None,
    satellite_type: str | None = None,
) -> tuple[str, list[str]]:
    """Generate a session YAML from wizard selections.

    Returns (yaml_str, warnings).
    Raises ValueError for invalid combinations.
    """
    warnings: list[str] = []
    if satellite_type:
        warnings.append(f"Satellite type '{satellite_type}' selected (informational, not yet wired into session config)")

    # Load preset
    presets = load_constellation_presets()
    if constellation not in presets:
        raise ValueError(f"Unknown constellation preset: {constellation}")
    preset = presets[constellation]

    # Validate combo early
    resolve_stack(protocol, extensions)

    # Build session name
    ext_suffix = "-".join(extensions) if extensions else "plain"
    session_name = f"{constellation}-{protocol}-{ext_suffix}"

    # Build area assignment
    area_assignment: dict[str, Any] | None = None
    if protocol not in ("nodalpath", "static"):
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
        "constellation": preset.constellation,
        "ground_stations": ground_stations or preset.ground_stations,
        "routing": {
            "protocol": protocol,
            "extensions": extensions,
        },
    }

    if area_assignment:
        session_dict["routing"]["area_assignment"] = area_assignment

    if preset.time:
        session_dict["time"] = preset.time
    if preset.traffic_flows:
        session_dict["traffic_flows"] = preset.traffic_flows
    if preset.convergence:
        session_dict["convergence"] = preset.convergence

    # Validate through Pydantic
    SessionConfig.model_validate(session_dict)

    yaml_str = yaml.dump(session_dict, default_flow_style=False, sort_keys=False)
    return yaml_str, warnings
