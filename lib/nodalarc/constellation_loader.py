# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Constellation loader — expands config to satellite orbital elements.

Handles parametric (Walker-delta/star), explicit, and TLE modes.
YAML loading happens here (component responsibility, not shared lib).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from nodalarc.models.constellation import (
    ConstellationConfig,
    ExplicitConstellation,
    GroundTerminal,
    IslTerminal,
    ParametricConstellation,
    TerminalConfig,
    TLEConstellation,
)
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundStationSetConfig,
    GroundTerminalDef,
    TerrestrialPrefixTemplate,
)
from nodalarc.models.satellite_type import SatelliteTypeConfig
from nodalarc.orbital import OrbitalElements, elements_from_params

adapter = TypeAdapter(ConstellationConfig)

# Default search paths for config directories
_SAT_TYPE_DIR: Path | None = None
_GS_STATIONS_DIR: Path | None = None
_GS_SETS_DIR: Path | None = None


def set_satellite_type_dir(path: str | Path) -> None:
    """Set the directory to search for satellite type YAML files."""
    global _SAT_TYPE_DIR
    _SAT_TYPE_DIR = Path(path)


def set_ground_station_dirs(
    stations_dir: str | Path | None = None,
    sets_dir: str | Path | None = None,
) -> None:
    """Set directories for ground station files."""
    global _GS_STATIONS_DIR, _GS_SETS_DIR
    if stations_dir is not None:
        _GS_STATIONS_DIR = Path(stations_dir)
    if sets_dir is not None:
        _GS_SETS_DIR = Path(sets_dir)


def _find_repo_root() -> Path:
    """Walk up from this file to find the repo root (contains configs/)."""
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "configs").is_dir():
            return p
        p = p.parent
    raise FileNotFoundError("Cannot find repo root (directory containing configs/)")


def _resolve_sat_type_dir() -> Path:
    """Resolve the satellite type directory, defaulting to configs/satellite-types/."""
    if _SAT_TYPE_DIR is not None:
        return _SAT_TYPE_DIR
    candidate = _find_repo_root() / "configs" / "satellite-types"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(
        "Cannot find configs/satellite-types/ directory. "
        "Call set_satellite_type_dir() to configure explicitly."
    )


def _resolve_gs_stations_dir() -> Path:
    if _GS_STATIONS_DIR is not None:
        return _GS_STATIONS_DIR
    candidate = _find_repo_root() / "configs" / "ground-stations" / "stations"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError("Cannot find configs/ground-stations/stations/ directory.")


def _resolve_gs_sets_dir() -> Path:
    if _GS_SETS_DIR is not None:
        return _GS_SETS_DIR
    candidate = _find_repo_root() / "configs" / "ground-stations" / "sets"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError("Cannot find configs/ground-stations/sets/ directory.")


@lru_cache(maxsize=32)
def load_satellite_type(name: str) -> SatelliteTypeConfig:
    """Load and validate a satellite type YAML file by name.

    The name resolves to configs/satellite-types/{name}.yaml.
    Results are cached since the same type may be referenced multiple times.
    """
    sat_type_dir = _resolve_sat_type_dir()
    path = sat_type_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Satellite type file not found: {path}")
    data = yaml.safe_load(path.read_text())
    # Handle top-level 'satellite_type' key
    if isinstance(data, dict) and "satellite_type" in data:
        data = data["satellite_type"]
    return SatelliteTypeConfig.model_validate(data)


def _sat_type_to_terminal_config(sat_type: SatelliteTypeConfig) -> TerminalConfig:
    """Convert a SatelliteTypeConfig to the legacy TerminalConfig format.

    Bridges the new satellite type model to the inline terminal format
    that downstream code (template_vars, addressing, ome/main) expects.
    """
    isl_terminals = []
    for td in sat_type.isl_terminals:
        isl_terminals.append(
            IslTerminal(
                type=td.type,
                count=td.count,
                max_range_km=td.max_range_km,
                bandwidth_mbps=td.bandwidth_mbps,
                max_tracking_rate_deg_s=td.max_tracking_rate_deg_s,
                field_of_regard_deg=td.field_of_regard_deg,
            )
        )
    ground_terminals = []
    for td in sat_type.ground_terminals:
        ground_terminals.append(
            GroundTerminal(
                type=td.type,
                count=td.count,
                bandwidth_mbps=td.bandwidth_mbps,
            )
        )
    return TerminalConfig(isl=isl_terminals, ground=ground_terminals)


def resolve_constellation_terminals(config: ConstellationConfig) -> ConstellationConfig:
    """Resolve satellite_type references and populate default_terminals.

    If the constellation uses satellite_type instead of inline
    default_terminals, loads the satellite type and fills in
    default_terminals so downstream code works unchanged.

    Returns the config (possibly mutated) for convenience.
    """
    if not isinstance(config, ParametricConstellation | ExplicitConstellation | TLEConstellation):
        return config
    if config.default_terminals is not None:
        return config
    if config.satellite_type is None:
        raise ValueError("Constellation must specify satellite_type or default_terminals")
    sat_type = load_satellite_type(config.satellite_type)
    config.default_terminals = _sat_type_to_terminal_config(sat_type)
    return config


def _terminal_counts_from_sat_type(sat_type: SatelliteTypeConfig) -> tuple[int, int]:
    """Extract (isl_terminal_count, ground_terminal_count) from a satellite type."""
    isl_count = sum(t.count for t in sat_type.isl_terminals)
    gnd_count = sum(t.count for t in sat_type.ground_terminals)
    return isl_count, gnd_count


def _terminal_counts_from_inline(terminals) -> tuple[int, int]:
    """Extract terminal counts from inline TerminalConfig."""
    isl_count = sum(t.count for t in terminals.isl)
    gnd_count = sum(t.count for t in terminals.ground)
    return isl_count, gnd_count


class SatelliteNode:
    """Expanded satellite with computed orbital elements and identity."""

    __slots__ = ("plane", "slot", "elements", "isl_terminal_count", "ground_terminal_count")

    def __init__(
        self,
        plane: int,
        slot: int,
        elements: OrbitalElements,
        isl_terminal_count: int,
        ground_terminal_count: int,
    ) -> None:
        self.plane = plane
        self.slot = slot
        self.elements = elements
        self.isl_terminal_count = isl_terminal_count
        self.ground_terminal_count = ground_terminal_count


def load_constellation(source: str | Path | dict) -> ConstellationConfig:
    """Load and validate a constellation definition.

    Accepts either a file path (str/Path) or an inline dict.
    If the constellation references a satellite_type, resolves it and
    populates default_terminals so downstream code works unchanged.
    """
    if isinstance(source, dict):
        config = adapter.validate_python(source)
    else:
        data = yaml.safe_load(Path(source).read_text())
        config = adapter.validate_python(data)
    resolve_constellation_terminals(config)
    return config


def load_ground_station_individual(name: str) -> GroundStationConfig:
    """Load an individual ground station YAML file by name."""
    stations_dir = _resolve_gs_stations_dir()
    path = stations_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Ground station file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if isinstance(data, dict) and "ground_station" in data:
        return GroundStationConfig.model_validate(data["ground_station"])
    return GroundStationConfig.model_validate(data)


def load_ground_station_set(name: str) -> GroundStationSetConfig:
    """Load a ground station set YAML file by name."""
    sets_dir = _resolve_gs_sets_dir()
    path = sets_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Ground station set file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if isinstance(data, dict) and "ground_station_set" in data:
        return GroundStationSetConfig.model_validate(data["ground_station_set"])
    return GroundStationSetConfig.model_validate(data)


# Default terminal/elevation/policy values for individual station files
# (stations in sets use the set's defaults; individual stations need sensible fallbacks)
_DEFAULT_GS_TERMINALS = [
    GroundTerminalDef(type="optical", count=1, bandwidth_mbps=1000, tracking_capacity=1)
]
_DEFAULT_MIN_ELEVATION_DEG = 25.0
_DEFAULT_SCHEDULING_POLICY = "highest-elevation"


def _build_gs_file_from_stations(
    stations: list[GroundStationConfig],
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = None,
    default_min_elevation_deg: float | None = None,
    default_scheduling_policy: str | None = None,
) -> GroundStationFile:
    """Build a GroundStationFile from a list of individual station configs.

    Bridges the new individual/set formats to the monolithic format that
    the rest of the system expects. Set-level defaults override the code
    defaults; per-station values override set-level defaults.
    """
    return GroundStationFile(
        default_terminals=_DEFAULT_GS_TERMINALS,
        default_min_elevation_deg=default_min_elevation_deg or _DEFAULT_MIN_ELEVATION_DEG,
        default_scheduling_policy=default_scheduling_policy or _DEFAULT_SCHEDULING_POLICY,
        default_terrestrial_prefixes=default_terrestrial_prefixes,
        stations=stations,
    )


def load_ground_stations_from_set(
    set_name: str,
) -> GroundStationFile:
    """Load a ground station set and resolve all station references.

    Returns a GroundStationFile for backward compatibility with downstream code.
    """
    gs_set = load_ground_station_set(set_name)
    stations: list[GroundStationConfig] = []
    for station_name in gs_set.stations:
        station = load_ground_station_individual(station_name)
        stations.append(station)
    return _build_gs_file_from_stations(
        stations,
        gs_set.default_terrestrial_prefixes,
        gs_set.default_min_elevation_deg,
        gs_set.default_scheduling_policy,
    )


def load_ground_stations_from_list(
    station_names: list[str],
    default_terrestrial_prefixes: TerrestrialPrefixTemplate | None = None,
) -> GroundStationFile:
    """Load a list of individual station files by name.

    Returns a GroundStationFile for backward compatibility with downstream code.
    """
    stations: list[GroundStationConfig] = []
    for name in station_names:
        station = load_ground_station_individual(name)
        stations.append(station)
    return _build_gs_file_from_stations(stations, default_terrestrial_prefixes)


def load_ground_stations(source: str | Path | list[str] | dict) -> GroundStationFile:
    """Load and validate ground stations.

    Accepts:
    - str/Path: YAML file path (set, individual, or legacy format)
    - list[str]: list of individual station names to load directly
    - dict: inline GroundStationFile definition (for self-contained session YAML)
    """
    if isinstance(source, list):
        return load_ground_stations_from_list(source)

    if isinstance(source, dict):
        # Inline definition — validate directly as GroundStationFile
        return GroundStationFile.model_validate(source)

    data = yaml.safe_load(Path(source).read_text())

    if isinstance(data, dict):
        if "ground_station" in data:
            station = GroundStationConfig.model_validate(data["ground_station"])
            return _build_gs_file_from_stations([station])
        if "ground_station_set" in data:
            gs_set = GroundStationSetConfig.model_validate(data["ground_station_set"])
            stations = [load_ground_station_individual(n) for n in gs_set.stations]
            return _build_gs_file_from_stations(
                stations,
                gs_set.default_terrestrial_prefixes,
                gs_set.default_min_elevation_deg,
                gs_set.default_scheduling_policy,
            )

    # Legacy monolithic format
    return GroundStationFile.model_validate(data)


def _resolve_default_terminals(config) -> tuple[int, int]:
    """Resolve default terminal counts from satellite_type or inline default_terminals."""
    if config.satellite_type is not None:
        sat_type = load_satellite_type(config.satellite_type)
        return _terminal_counts_from_sat_type(sat_type)
    if config.default_terminals is not None:
        return _terminal_counts_from_inline(config.default_terminals)
    raise ValueError("Constellation must specify satellite_type or default_terminals")


def _resolve_plane_override(ovr) -> tuple[int, int]:
    """Resolve terminal counts from a plane override."""
    if ovr.satellite_type is not None:
        sat_type = load_satellite_type(ovr.satellite_type)
        return _terminal_counts_from_sat_type(sat_type)
    if ovr.terminals is not None:
        return _terminal_counts_from_inline(ovr.terminals)
    raise ValueError("PlaneOverride must specify satellite_type or terminals")


def expand_parametric(config: ParametricConstellation) -> list[SatelliteNode]:
    """Expand parametric constellation to individual satellite nodes.

    Walker-delta and Walker-star use the same orbital element formulas:
    - raan = plane_index * raan_spacing_deg
    - true_anomaly = slot_index * (360 / sats_per_plane) + plane_index * phase_offset_deg

    The difference between Walker-star and Walker-delta is handled by:
    1. visibility.py (polar seam tracking dynamics)
    2. assign_isl_neighbors() (cross-plane wrap behavior)
    """
    satellites: list[SatelliteNode] = []

    plane_count = config.planes.count
    sats_per_plane = config.planes.sats_per_plane
    raan_spacing = config.planes.raan_spacing_deg
    phase_offset = config.planes.phase_offset_deg
    anomaly_spacing = 360.0 / sats_per_plane

    default_isl_count, default_gnd_count = _resolve_default_terminals(config)

    # Build plane override lookup
    plane_terminal_overrides: dict[int, tuple[int, int]] = {}
    if config.plane_overrides:
        for ovr in config.plane_overrides:
            isl_count, gnd_count = _resolve_plane_override(ovr)
            for p in ovr.planes:
                plane_terminal_overrides[p] = (isl_count, gnd_count)

    for p in range(plane_count):
        raan = p * raan_spacing
        isl_count, gnd_count = plane_terminal_overrides.get(
            p, (default_isl_count, default_gnd_count)
        )

        for s in range(sats_per_plane):
            true_anomaly = s * anomaly_spacing + p * phase_offset
            elements = elements_from_params(
                altitude_km=config.orbit.altitude_km,
                inclination_deg=config.orbit.inclination_deg,
                raan_deg=raan,
                true_anomaly_deg=true_anomaly,
            )
            satellites.append(
                SatelliteNode(
                    plane=p,
                    slot=s,
                    elements=elements,
                    isl_terminal_count=isl_count,
                    ground_terminal_count=gnd_count,
                )
            )

    return satellites


def expand_explicit(config: ExplicitConstellation) -> list[SatelliteNode]:
    """Expand explicit constellation — each satellite has its own orbital elements."""
    satellites: list[SatelliteNode] = []

    default_isl_count, default_gnd_count = _resolve_default_terminals(config)

    for sat_cfg in config.satellites:
        # Priority: per-node satellite_type > per-node inline terminals > constellation default
        if sat_cfg.satellite_type is not None:
            sat_type = load_satellite_type(sat_cfg.satellite_type)
            isl_count, gnd_count = _terminal_counts_from_sat_type(sat_type)
        elif sat_cfg.terminals:
            isl_count, gnd_count = _terminal_counts_from_inline(sat_cfg.terminals)
        else:
            isl_count = default_isl_count
            gnd_count = default_gnd_count

        elements = elements_from_params(
            altitude_km=sat_cfg.orbit.altitude_km,
            inclination_deg=sat_cfg.orbit.inclination_deg,
            raan_deg=sat_cfg.orbit.raan_deg,
            true_anomaly_deg=sat_cfg.orbit.true_anomaly_deg,
        )
        satellites.append(
            SatelliteNode(
                plane=sat_cfg.plane,
                slot=sat_cfg.slot,
                elements=elements,
                isl_terminal_count=isl_count,
                ground_terminal_count=gnd_count,
            )
        )

    return satellites


def expand_tle(config: TLEConstellation) -> list[SatelliteNode]:
    """Expand TLE constellation — stub for Phase 1."""
    raise NotImplementedError("TLE constellation expansion deferred to Phase 1B+")


def expand_constellation(config: ConstellationConfig) -> list[SatelliteNode]:
    """Dispatch to the correct expansion function based on mode."""
    if isinstance(config, ParametricConstellation):
        return expand_parametric(config)
    if isinstance(config, ExplicitConstellation):
        return expand_explicit(config)
    if isinstance(config, TLEConstellation):
        return expand_tle(config)
    raise ValueError(f"Unknown constellation type: {type(config)}")
