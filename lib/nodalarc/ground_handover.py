# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground-station handover policy resolution.

MBB/BBM is a per-ground-station runtime fact. A session-level
``GroundSchedulingConfig`` is only a default surface; the effective station
policy also depends on station overrides and on physical terminal capacity. A
single-terminal station cannot perform make-before-break, so the resolver and
OME both use this helper rather than carrying a global handover mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nodalarc.ground_terminals import station_ground_terminal_capacity
from nodalarc.models.ground_station import GroundStationConfig, GroundStationFile
from nodalarc.models.session import GroundSchedulingConfig

GroundHandoverMode = Literal["bbm", "mbb"]


@dataclass(frozen=True, slots=True)
class StationHandoverResolution:
    """Effective per-station handover policy and how it was derived."""

    scheduling: GroundSchedulingConfig
    terminal_capacity: int
    explicit_mbb_request: bool
    capability_forced_bbm: bool


def _station_touched_mbb_surface(station: GroundStationConfig) -> bool:
    return any(
        getattr(station, field) is not None
        for field in ("handover_mode", "mbb_overlap_ticks", "mbb_reserve")
    )


def resolve_station_ground_scheduling(
    base: GroundSchedulingConfig,
    gs_file: GroundStationFile,
    station: GroundStationConfig,
) -> StationHandoverResolution:
    """Resolve the effective scheduling policy for one ground station.

    Resolution order is station override > ground-set default policy > segment /
    session default. Capability is then applied as a truth constraint: a station
    whose terminal capacity cannot hold a steady link plus one reserved overlap
    terminal is not MBB-capable. If the station explicitly requested MBB anyway,
    resolution fails loudly. If MBB came only from the default surface, the
    station's effective mode is BBM and that fact is carried in the resolved
    policy/audit.
    """

    data = base.model_dump(mode="python")
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
    if station.selection_policy is not None:
        data["selection_policy"] = station.selection_policy.model_dump(mode="python")
    if station.handover_policy is not None:
        data["handover_policy"] = station.handover_policy.model_dump(mode="python")
    if station.handover_mode is not None:
        data["handover_mode"] = station.handover_mode
    if station.mbb_overlap_ticks is not None:
        data["mbb_overlap_ticks"] = station.mbb_overlap_ticks
    if station.mbb_reserve is not None:
        data["mbb_reserve"] = station.mbb_reserve

    explicit_mbb_request = station.handover_mode == "mbb" or (
        station.handover_mode is None
        and data.get("handover_mode") == "mbb"
        and _station_touched_mbb_surface(station)
    )

    capacity = station_ground_terminal_capacity(gs_file, station)
    mode = data.get("handover_mode", "bbm")

    if mode == "bbm":
        if station.handover_mode == "bbm" and station.mbb_reserve not in (None, 0):
            raise ValueError(
                f"ground station {station.name!r} requests BBM but also sets mbb_reserve"
            )
        data["mbb_overlap_ticks"] = 0
        data["mbb_reserve"] = 0
        return StationHandoverResolution(
            scheduling=GroundSchedulingConfig.model_validate(data),
            terminal_capacity=capacity,
            explicit_mbb_request=False,
            capability_forced_bbm=False,
        )

    reserve = int(data.get("mbb_reserve", 0))
    overlap = int(data.get("mbb_overlap_ticks", 0))
    if reserve > 1:
        raise ValueError(
            f"ground station {station.name!r} requests mbb_reserve={reserve}; "
            "MBB-002 multi-overlap support is not implemented"
        )
    if reserve <= 0 or overlap <= 0:
        raise ValueError(
            f"ground station {station.name!r} requests MBB but does not declare positive "
            "mbb_reserve and mbb_overlap_ticks"
        )
    if capacity <= reserve:
        if explicit_mbb_request:
            raise ValueError(
                f"ground station {station.name!r} explicitly requests MBB but has terminal "
                f"capacity {capacity}; MBB with mbb_reserve={reserve} requires capacity "
                f"> {reserve}"
            )
        data["handover_mode"] = "bbm"
        data["mbb_overlap_ticks"] = 0
        data["mbb_reserve"] = 0
        return StationHandoverResolution(
            scheduling=GroundSchedulingConfig.model_validate(data),
            terminal_capacity=capacity,
            explicit_mbb_request=False,
            capability_forced_bbm=True,
        )

    return StationHandoverResolution(
        scheduling=GroundSchedulingConfig.model_validate(data),
        terminal_capacity=capacity,
        explicit_mbb_request=explicit_mbb_request,
        capability_forced_bbm=False,
    )
