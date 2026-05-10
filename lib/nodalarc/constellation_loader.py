# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Constellation loader — expands config to satellite orbital elements.

Handles parametric (Walker-delta/star), explicit, and TLE modes.
YAML loading happens here (component responsibility, not shared lib).
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

import yaml
from pydantic import TypeAdapter

from nodalarc.constants import EARTH_MU
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
from nodalarc.models.satellite_type import (
    GroundTerminalDef as SatelliteGroundTerminalDef,
)
from nodalarc.models.satellite_type import (
    IslTerminalDef,
    SatelliteTypeConfig,
)
from nodalarc.orbital import OrbitalElements, elements_from_params
from nodalarc.tle import tle_norad_id, validate_tle_pair

adapter = TypeAdapter(ConstellationConfig)

IslTerminalLike = IslTerminal | IslTerminalDef
GroundTerminalLike = GroundTerminal | SatelliteGroundTerminalDef

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
                role=td.role,
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

    __slots__ = (
        "plane",
        "slot",
        "elements",
        "isl_terminal_count",
        "ground_terminal_count",
        "isl_terminals",
        "ground_terminals",
        "tle_line_1",
        "tle_line_2",
        "norad_id",
    )

    def __init__(
        self,
        plane: int,
        slot: int,
        elements: OrbitalElements,
        isl_terminal_count: int,
        ground_terminal_count: int,
        isl_terminals: list | tuple | None = None,
        ground_terminals: list | tuple | None = None,
        tle_line_1: str | None = None,
        tle_line_2: str | None = None,
        norad_id: int | None = None,
    ) -> None:
        self.plane = plane
        self.slot = slot
        self.elements = elements
        self.isl_terminal_count = isl_terminal_count
        self.ground_terminal_count = ground_terminal_count
        self.isl_terminals = tuple(isl_terminals or ())
        self.ground_terminals = tuple(ground_terminals or ())
        self.tle_line_1 = tle_line_1
        self.tle_line_2 = tle_line_2
        self.norad_id = norad_id


def load_constellation(source: str | Path | dict) -> ConstellationConfig:
    """Load and validate a constellation definition.

    Accepts either a file path (str/Path) or an inline dict.
    If the constellation references a satellite_type, resolves it and
    populates default_terminals so downstream code works unchanged.
    """
    if isinstance(source, dict):
        config = adapter.validate_python(source)
    else:
        source_path = Path(source)
        data = yaml.safe_load(source_path.read_text())
        config = adapter.validate_python(data)
        if isinstance(config, TLEConstellation):
            tle_path = Path(config.tle_file)
            if not tle_path.is_absolute():
                config.tle_file = str(source_path.parent / tle_path)
    resolve_constellation_terminals(config)
    return config


def load_ground_station_individual(name: str) -> GroundStationConfig:
    """Load an individual ground station YAML file by name.

    Validates that the station model is self-contained — it must define
    its own terminals. Station models are the single source of truth for
    hardware capabilities. Set files and session files must not override
    terminal definitions.
    """
    stations_dir = _resolve_gs_stations_dir()
    path = stations_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Ground station file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if isinstance(data, dict) and "ground_station" in data:
        station = GroundStationConfig.model_validate(data["ground_station"])
    else:
        station = GroundStationConfig.model_validate(data)
    if not station.terminals:
        log.warning(
            "Ground station '%s' has no terminals defined. A containing station set "
            "must provide explicit default_terminals or the session will fail validation.",
            name,
        )
    return station


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


# Default elevation/policy values for individual station files
# (stations in sets use the set's defaults; individual stations need schema defaults)
_DEFAULT_MIN_ELEVATION_DEG = 25.0
_DEFAULT_SCHEDULING_POLICY = "highest-elevation"


def _build_gs_file_from_stations(
    stations: list[GroundStationConfig],
    *,
    default_terminals: list[GroundTerminalDef] | None = None,
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
        default_terminals=default_terminals or [],
        default_min_elevation_deg=(
            default_min_elevation_deg
            if default_min_elevation_deg is not None
            else _DEFAULT_MIN_ELEVATION_DEG
        ),
        default_scheduling_policy=(
            default_scheduling_policy
            if default_scheduling_policy is not None
            else _DEFAULT_SCHEDULING_POLICY
        ),
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
        default_terminals=gs_set.default_terminals,
        default_terrestrial_prefixes=gs_set.default_terrestrial_prefixes,
        default_min_elevation_deg=gs_set.default_min_elevation_deg,
        default_scheduling_policy=gs_set.default_scheduling_policy,
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
    return _build_gs_file_from_stations(
        stations,
        default_terrestrial_prefixes=default_terrestrial_prefixes,
    )


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
                default_terminals=gs_set.default_terminals,
                default_terrestrial_prefixes=gs_set.default_terrestrial_prefixes,
                default_min_elevation_deg=gs_set.default_min_elevation_deg,
                default_scheduling_policy=gs_set.default_scheduling_policy,
            )
        if "set" in data:
            gs_file = load_ground_stations(data["set"])
            if "default_terminals" in data:
                gs_file.default_terminals = [
                    GroundTerminalDef.model_validate(t) for t in data["default_terminals"]
                ]
            return gs_file

    # Legacy monolithic format
    return GroundStationFile.model_validate(data)


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

    for p in range(plane_count):
        raan = p * raan_spacing

        for s in range(sats_per_plane):
            isl_terminals, ground_terminals = _terminals_for_node(config, p, s)
            isl_count = sum(t.count for t in isl_terminals)
            gnd_count = sum(t.count for t in ground_terminals)
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
                    isl_terminals=isl_terminals,
                    ground_terminals=ground_terminals,
                )
            )

    return satellites


def expand_explicit(config: ExplicitConstellation) -> list[SatelliteNode]:
    """Expand explicit constellation — each satellite has its own orbital elements."""
    satellites: list[SatelliteNode] = []

    for sat_cfg in config.satellites:
        isl_terminals, ground_terminals = _terminals_for_node(config, sat_cfg.plane, sat_cfg.slot)
        isl_count = sum(t.count for t in isl_terminals)
        gnd_count = sum(t.count for t in ground_terminals)

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
                isl_terminals=isl_terminals,
                ground_terminals=ground_terminals,
            )
        )

    return satellites


def _elements_from_tle_lines(line_1: str, line_2: str) -> OrbitalElements:
    """Build approximate circular elements for non-authoritative metadata.

    SGP4 propagation uses the original TLE lines. These elements exist only for
    legacy code paths and ephemeris summaries that still expect circular
    `OrbitalElements` on `SatelliteNode`; they are not used as physics
    authority for `orbit.propagator: sgp4-tle`.
    """
    if not line_1.startswith("1 ") or not line_2.startswith("2 "):
        raise ValueError("TLE records must contain line 1 followed by line 2")

    inclination_deg = float(line_2[8:16])
    raan_deg = float(line_2[17:25])
    eccentricity = float(f"0.{line_2[26:33].strip()}")
    mean_anomaly_rad = math.radians(float(line_2[43:51]))
    mean_motion_rev_day = float(line_2[52:63])
    mean_motion_rad_s = mean_motion_rev_day * 2.0 * math.pi / 86400.0
    semi_major_axis_km = (EARTH_MU / (mean_motion_rad_s**2)) ** (1.0 / 3.0)

    # Convert mean anomaly to true anomaly for a closer metadata summary. This
    # does not make the circular element model authoritative for TLE sessions.
    eccentric_anomaly = mean_anomaly_rad
    for _ in range(8):
        eccentric_anomaly -= (
            eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly_rad
        ) / (1.0 - eccentricity * math.cos(eccentric_anomaly))
    true_anomaly_rad = math.atan2(
        math.sqrt(1.0 - eccentricity**2) * math.sin(eccentric_anomaly),
        math.cos(eccentric_anomaly) - eccentricity,
    )

    return OrbitalElements(
        semi_major_axis_km=semi_major_axis_km,
        inclination_rad=math.radians(inclination_deg),
        raan_rad=math.radians(raan_deg),
        true_anomaly_rad=true_anomaly_rad,
    )


def _read_tle_records(path: Path) -> list[tuple[str | None, str, str]]:
    """Read 2-line or 3-line TLE records from a file."""
    if not path.exists():
        raise FileNotFoundError(f"TLE file not found: {path}")

    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    records: list[tuple[str | None, str, str]] = []
    idx = 0
    while idx < len(lines):
        name: str | None = None
        if lines[idx].startswith("1 "):
            line_1 = lines[idx]
            idx += 1
        else:
            name = lines[idx]
            idx += 1
            if idx >= len(lines) or not lines[idx].startswith("1 "):
                raise ValueError(f"TLE name {name!r} is not followed by line 1")
            line_1 = lines[idx]
            idx += 1

        if idx >= len(lines) or not lines[idx].startswith("2 "):
            raise ValueError(f"TLE line 1 is not followed by line 2: {line_1!r}")
        line_2 = lines[idx]
        idx += 1
        validate_tle_pair(line_1, line_2)
        records.append((name, line_1, line_2))

    return records


def expand_tle(config: TLEConstellation) -> list[SatelliteNode]:
    """Expand a TLE constellation into SGP4-backed satellite nodes."""
    satellites: list[SatelliteNode] = []
    records = _read_tle_records(Path(config.tle_file))
    if config.filter and config.filter.norad_ids is not None:
        allowed = set(config.filter.norad_ids)
        records = [record for record in records if tle_norad_id(record[1]) in allowed]
    if config.filter and config.filter.max_count is not None:
        records = records[: config.filter.max_count]
    if not records:
        raise ValueError(f"TLE constellation {config.name!r} selected zero satellites")

    for slot, (_name, line_1, line_2) in enumerate(records):
        isl_terminals, ground_terminals = _terminals_for_node(config, 0, slot)
        isl_count = sum(t.count for t in isl_terminals)
        gnd_count = sum(t.count for t in ground_terminals)
        satellites.append(
            SatelliteNode(
                plane=0,
                slot=slot,
                elements=_elements_from_tle_lines(line_1, line_2),
                isl_terminal_count=isl_count,
                ground_terminal_count=gnd_count,
                isl_terminals=isl_terminals,
                ground_terminals=ground_terminals,
                tle_line_1=line_1,
                tle_line_2=line_2,
                norad_id=tle_norad_id(line_1),
            )
        )

    return satellites


def expand_constellation(config: ConstellationConfig) -> list[SatelliteNode]:
    """Dispatch to the correct expansion function based on mode."""
    if isinstance(config, ParametricConstellation):
        return expand_parametric(config)
    if isinstance(config, ExplicitConstellation):
        return expand_explicit(config)
    if isinstance(config, TLEConstellation):
        return expand_tle(config)
    raise ValueError(f"Unknown constellation type: {type(config)}")


# ---------------------------------------------------------------------------
# Terminal-bandwidth resolution (R-TO-003)
# ---------------------------------------------------------------------------
#
# A satellite's ISL interfaces are named `isl0`, `isl1`, ..., with the index
# running over consecutive blocks defined by the satellite's ISL terminal list.
# A terminal entry like `{type: optical, count: 2}` owns two consecutive
# interface indices starting at the block's base. Per-interface bandwidth
# comes from the owning block's `bandwidth_mbps` field.
#
# Ground-facing interfaces are all named `gnd0`. A satellite typically has
# one ground terminal type; if multiple are defined we use the minimum
# bandwidth as the conservative emulation value (the slowest terminal governs
# the emulated rate).
#
# For a link, the emulated bandwidth is `min(side_a_bw, side_b_bw)` — the
# slower endpoint is always the bottleneck in real-world RF / optical links.


def _terminals_for_node(
    config: ConstellationConfig,
    plane: int,
    slot: int,
) -> tuple[list[IslTerminalLike], list[GroundTerminalLike]]:
    """Return (isl_terminals, ground_terminals) for the satellite at (plane, slot).

    Resolves per-satellite overrides (ExplicitConstellation.satellites[*].satellite_type),
    per-plane overrides (ParametricConstellation.plane_overrides), and falls back to
    the constellation-level `default_terminals` (which `resolve_constellation_terminals`
    populates from `satellite_type` if inline terminals aren't specified).

    The returned terminal objects duck-type-match on `.count` and `.bandwidth_mbps`;
    they may be constellation.IslTerminal/GroundTerminal or satellite_type.IslTerminalDef/
    GroundTerminalDef depending on which code path populated them.
    """
    if isinstance(config, ExplicitConstellation):
        for sat_cfg in config.satellites:
            if sat_cfg.plane == plane and sat_cfg.slot == slot:
                if sat_cfg.satellite_type is not None:
                    sat_type = load_satellite_type(sat_cfg.satellite_type)
                    return list(sat_type.isl_terminals), list(sat_type.ground_terminals)
                if sat_cfg.terminals is not None:
                    return list(sat_cfg.terminals.isl), list(sat_cfg.terminals.ground)
                break  # Fall through to constellation default

    if isinstance(config, ParametricConstellation) and config.plane_overrides:
        for ovr in config.plane_overrides:
            if plane in ovr.planes:
                if ovr.satellite_type is not None:
                    sat_type = load_satellite_type(ovr.satellite_type)
                    return list(sat_type.isl_terminals), list(sat_type.ground_terminals)
                if ovr.terminals is not None:
                    return list(ovr.terminals.isl), list(ovr.terminals.ground)
                break  # Fall through to constellation default

    # Constellation-level fallback — prefer inline default_terminals (already
    # resolved from satellite_type by resolve_constellation_terminals).
    if config.default_terminals is not None:
        return list(config.default_terminals.isl), list(config.default_terminals.ground)
    if config.satellite_type is not None:
        sat_type = load_satellite_type(config.satellite_type)
        return list(sat_type.isl_terminals), list(sat_type.ground_terminals)

    raise ValueError(f"Satellite at plane={plane}, slot={slot} has no resolvable terminal config")


def isl_terminal_bandwidth_mbps(
    isl_terminals: list[IslTerminalLike] | tuple[IslTerminalLike, ...],
    interface_name: str,
) -> float:
    """Return the ISL terminal bandwidth for the given interface index.

    Interface naming maps to the flattened terminal index: `isl0` is the
    first terminal slot across the concatenated terminal blocks, `isl1` the
    second, etc. A terminal block of `count=N` owns N consecutive indices.

    Example:
      isl_terminals = [
          {type: optical, count: 2, bandwidth_mbps: 100000},
          {type: rf,      count: 2, bandwidth_mbps: 10000},
      ]
      isl0, isl1 -> 100000 (optical block)
      isl2, isl3 -> 10000  (rf block)
    """
    if not interface_name.startswith("isl"):
        raise ValueError(f"Expected 'islN' interface name, got {interface_name!r}")
    try:
        idx = int(interface_name[3:])
    except ValueError as exc:
        raise ValueError(f"Invalid ISL interface name {interface_name!r}") from exc

    cumulative = 0
    for block in isl_terminals:
        if idx < cumulative + block.count:
            return float(block.bandwidth_mbps)
        cumulative += block.count
    raise ValueError(
        f"ISL interface index {idx} (from {interface_name!r}) out of range — "
        f"satellite has only {cumulative} ISL terminals total"
    )


def isl_terminal_for_interface(
    isl_terminals: list[IslTerminalLike] | tuple[IslTerminalLike, ...],
    interface_name: str,
) -> IslTerminalLike:
    """Return the terminal block that owns an ISL interface.

    Interface names map to flattened terminal slots across ordered terminal
    blocks. This helper is the authoritative bridge from structural neighbor
    assignment (`isl2`) to terminal-role physics (`cross-plane`, max tracking
    rate, field of regard, range). Callers must use this instead of assuming
    `default_terminals.isl[0]` represents every ISL.
    """
    if not interface_name.startswith("isl"):
        raise ValueError(f"Expected 'islN' interface name, got {interface_name!r}")
    try:
        idx = int(interface_name[3:])
    except ValueError as exc:
        raise ValueError(f"Invalid ISL interface name {interface_name!r}") from exc

    cumulative = 0
    for block in isl_terminals:
        if idx < cumulative + block.count:
            return block
        cumulative += block.count
    raise ValueError(
        f"ISL interface index {idx} (from {interface_name!r}) out of range — "
        f"satellite has only {cumulative} ISL terminals total"
    )


def satellite_ground_bandwidth_mbps(
    ground_terminals: list[GroundTerminalLike] | tuple[GroundTerminalLike, ...],
) -> float:
    """Return the minimum bandwidth across a satellite's ground terminals.

    Satellites may declare multiple ground terminal types (optical + RF, for
    instance). The emulated link uses the slowest — in practice all ground
    connections share `gnd0` and the bottleneck governs.
    """
    if not ground_terminals:
        raise ValueError("Satellite has no ground terminals")
    return min(float(t.bandwidth_mbps) for t in ground_terminals)


def gs_terminal_bandwidth_mbps(gs_file: GroundStationFile, station_name: str) -> float:
    """Return the minimum bandwidth across a ground station's terminals.

    Per-station `terminals` override the file's `default_terminals` when set.
    """
    station = next((s for s in gs_file.stations if s.name == station_name), None)
    if station is None:
        raise ValueError(f"Ground station {station_name!r} not found in file")
    terminals = station.terminals or gs_file.default_terminals
    if not terminals:
        raise ValueError(f"Ground station {station_name!r} has no terminals")
    return min(float(t.bandwidth_mbps) for t in terminals)


def isl_link_bandwidth_mbps(
    config: ConstellationConfig,
    node_a_plane: int,
    node_a_slot: int,
    node_a_interface: str,
    node_b_plane: int,
    node_b_slot: int,
    node_b_interface: str,
) -> float:
    """Return the emulated bandwidth for an ISL link between two satellites.

    Min of both endpoints' terminal bandwidths at their respective interfaces.
    Slow side governs — matches real-world RF/optical link behavior.
    """
    isl_a, _ = _terminals_for_node(config, node_a_plane, node_a_slot)
    isl_b, _ = _terminals_for_node(config, node_b_plane, node_b_slot)
    bw_a = isl_terminal_bandwidth_mbps(isl_a, node_a_interface)
    bw_b = isl_terminal_bandwidth_mbps(isl_b, node_b_interface)
    return min(bw_a, bw_b)


def ground_link_bandwidth_mbps(
    config: ConstellationConfig,
    gs_file: GroundStationFile,
    sat_plane: int,
    sat_slot: int,
    station_name: str,
) -> float:
    """Return the emulated bandwidth for a ground link between a satellite and GS."""
    _, ground_terminals = _terminals_for_node(config, sat_plane, sat_slot)
    sat_bw = satellite_ground_bandwidth_mbps(ground_terminals)
    gs_bw = gs_terminal_bandwidth_mbps(gs_file, station_name)
    return min(sat_bw, gs_bw)
