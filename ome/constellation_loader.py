"""Constellation loader — expands config to satellite orbital elements.

Handles parametric (Walker-delta/star), explicit, and TLE modes.
YAML loading happens here (component responsibility, not shared lib).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import TypeAdapter

from ome.propagator import OrbitalElements, elements_from_params
from nodalarc.models.constellation import (
    ConstellationConfig,
    ExplicitConstellation,
    ParametricConstellation,
    TLEConstellation,
)
from nodalarc.models.ground_station import GroundStationFile

adapter = TypeAdapter(ConstellationConfig)


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


def load_constellation(path: str | Path) -> ConstellationConfig:
    """Load and validate constellation YAML."""
    data = yaml.safe_load(Path(path).read_text())
    return adapter.validate_python(data)


def load_ground_stations(path: str | Path) -> GroundStationFile:
    """Load and validate ground station YAML."""
    data = yaml.safe_load(Path(path).read_text())
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

    default_isl_count = sum(t.count for t in config.default_terminals.isl)
    default_gnd_count = sum(t.count for t in config.default_terminals.ground)

    # Build plane override lookup
    plane_terminal_overrides: dict[int, tuple[int, int]] = {}
    if config.plane_overrides:
        for ovr in config.plane_overrides:
            isl_count = sum(t.count for t in ovr.terminals.isl)
            gnd_count = sum(t.count for t in ovr.terminals.ground)
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
            satellites.append(SatelliteNode(
                plane=p, slot=s,
                elements=elements,
                isl_terminal_count=isl_count,
                ground_terminal_count=gnd_count,
            ))

    return satellites


def expand_explicit(config: ExplicitConstellation) -> list[SatelliteNode]:
    """Expand explicit constellation — each satellite has its own orbital elements."""
    satellites: list[SatelliteNode] = []

    default_isl_count = sum(t.count for t in config.default_terminals.isl)
    default_gnd_count = sum(t.count for t in config.default_terminals.ground)

    for sat_cfg in config.satellites:
        if sat_cfg.terminals:
            isl_count = sum(t.count for t in sat_cfg.terminals.isl)
            gnd_count = sum(t.count for t in sat_cfg.terminals.ground)
        else:
            isl_count = default_isl_count
            gnd_count = default_gnd_count

        elements = elements_from_params(
            altitude_km=sat_cfg.orbit.altitude_km,
            inclination_deg=sat_cfg.orbit.inclination_deg,
            raan_deg=sat_cfg.orbit.raan_deg,
            true_anomaly_deg=sat_cfg.orbit.true_anomaly_deg,
        )
        satellites.append(SatelliteNode(
            plane=sat_cfg.plane, slot=sat_cfg.slot,
            elements=elements,
            isl_terminal_count=isl_count,
            ground_terminal_count=gnd_count,
        ))

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
