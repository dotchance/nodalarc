"""Internal OME runtime facts materialized from a resolved session."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.body_frames import SupportedSurfaceBody
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.terminal_physics import (
    SatGroundTerminalBoresight,
    TerminalBoresight,
)
from nodalarc.orbital import OrbitalElements


class OmeAddressing(Protocol):
    """Node identity operations used by OME algorithms."""

    @property
    def has_type_registry(self) -> bool: ...

    def node_type(self, node_id: str) -> str: ...

    def is_ground_segment(self, node_id: str) -> bool: ...

    def is_satellite(self, node_id: str) -> bool: ...

    def sat_id(self, plane: int, slot: int) -> str: ...

    def gs_id(self, name: str) -> str: ...


class IslTerminal(BaseModel):
    """Resolved ISL terminal block used by OME."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    type: str
    count: int = Field(gt=0)
    role: Literal["intra-plane", "cross-plane"] | None = None
    max_range_km: float = Field(gt=0)
    bandwidth_mbps: float = Field(gt=0)
    max_tracking_rate_deg_s: float = Field(gt=0)
    field_of_regard_deg: float = Field(default=360.0, ge=0, le=360)


class SatelliteGroundTerminal(BaseModel):
    """Resolved satellite access-terminal block used by OME."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    type: str
    count: int = Field(gt=0)
    interface_indices: tuple[int, ...] = Field(min_length=1)
    bandwidth_mbps: float = Field(gt=0)
    max_range_km: float | None = Field(default=None, gt=0)
    field_of_regard_deg: float | None = Field(default=None, gt=0, le=180)
    max_tracking_rate_deg_s: float | None = Field(default=None, gt=0)
    boresight: SatGroundTerminalBoresight | None = None

    @model_validator(mode="after")
    def _interface_count_matches(self):
        if len(self.interface_indices) != self.count:
            raise ValueError("satellite ground-terminal interface_indices must match count")
        if len(set(self.interface_indices)) != len(self.interface_indices):
            raise ValueError("satellite ground-terminal interface_indices must be unique")
        if any(index < 0 for index in self.interface_indices):
            raise ValueError("satellite ground-terminal interface_indices must be non-negative")
        return self


class GroundTerminal(BaseModel):
    """Resolved ground access-terminal block used by OME."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    id: str | None = None
    type: str
    count: int = Field(gt=0)
    interface_indices: tuple[int, ...] = Field(min_length=1)
    bandwidth_mbps: float = Field(gt=0)
    tracking_capacity: int = Field(gt=0)
    max_range_km: float | None = Field(default=None, gt=0)
    field_of_regard_deg: float | None = Field(default=None, gt=0, le=180)
    max_tracking_rate_deg_s: float | None = Field(default=None, gt=0)
    boresight: TerminalBoresight | None = None

    @model_validator(mode="after")
    def _interface_count_matches(self):
        if len(self.interface_indices) != self.count:
            raise ValueError("ground-terminal interface_indices must match count")
        if len(set(self.interface_indices)) != len(self.interface_indices):
            raise ValueError("ground-terminal interface_indices must be unique")
        if any(index < 0 for index in self.interface_indices):
            raise ValueError("ground-terminal interface_indices must be non-negative")
        return self


class GroundStation(BaseModel):
    """Resolved ground-node facts used by OME."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    name: str
    lat_deg: float = Field(ge=-90, le=90)
    lon_deg: float = Field(ge=-180, le=180)
    alt_m: float = 0.0
    min_elevation_deg: float | None = Field(default=None, ge=0, le=90)
    terminals: list[GroundTerminal] | None = None
    tenant_id: str = "default"
    reference_body: SupportedSurfaceBody
    service_priority: int = Field(default=10, gt=0)
    selection_policy: SelectionPolicySpec | None = None
    handover_policy: HandoverPolicySpec | None = None
    handover_mode: Literal["bbm", "mbb"] | None = None
    mbb_overlap_ticks: int | None = Field(default=None, ge=0)
    mbb_reserve: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _valid_handover_surface(self):
        if self.handover_mode == "mbb":
            if self.mbb_overlap_ticks is not None and self.mbb_overlap_ticks <= 0:
                raise ValueError("handover_mode='mbb' requires mbb_overlap_ticks > 0")
            if self.mbb_reserve is not None and self.mbb_reserve <= 0:
                raise ValueError("handover_mode='mbb' requires mbb_reserve > 0")
        if self.handover_mode == "bbm" and self.mbb_reserve not in (None, 0):
            raise ValueError("handover_mode='bbm' must not reserve MBB terminals")
        return self


class GroundStationFile(BaseModel):
    """Resolved collection of OME ground-node facts."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    default_handover_mode: Literal["bbm", "mbb"] | None = None
    default_mbb_overlap_ticks: int | None = Field(default=None, ge=0)
    default_mbb_reserve: int | None = Field(default=None, ge=0, le=1)
    default_terminals: list[GroundTerminal] = Field(default_factory=list)
    default_min_elevation_deg: float = Field(default=25.0, ge=0, le=90)
    default_selection_policy: SelectionPolicySpec | None = None
    default_handover_policy: HandoverPolicySpec | None = None
    stations: list[GroundStation] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_runtime_ground_facts(self):
        names = [station.name for station in self.stations]
        if len(names) != len(set(names)):
            raise ValueError("duplicate ground station names found")
        if self.default_handover_mode == "mbb":
            if self.default_mbb_overlap_ticks is not None and self.default_mbb_overlap_ticks <= 0:
                raise ValueError(
                    "default_handover_mode='mbb' requires default_mbb_overlap_ticks > 0"
                )
            if self.default_mbb_reserve is not None and self.default_mbb_reserve <= 0:
                raise ValueError("default_handover_mode='mbb' requires default_mbb_reserve > 0")
        if self.default_handover_mode == "bbm":
            if self.default_mbb_overlap_ticks not in (None, 0):
                raise ValueError(
                    "default_handover_mode='bbm' must not set default_mbb_overlap_ticks"
                )
            if self.default_mbb_reserve not in (None, 0):
                raise ValueError("default_handover_mode='bbm' must not reserve MBB terminals")
        return self


class SatelliteNode:
    """Resolved satellite with orbital and terminal runtime facts."""

    __slots__ = (
        "plane",
        "slot",
        "local_plane",
        "local_slot",
        "node_id",
        "local_node_id",
        "segment_id",
        "central_body",
        "elements",
        "isl_terminal_count",
        "ground_terminal_count",
        "isl_terminals",
        "ground_terminals",
        "tle_line_1",
        "tle_line_2",
        "norad_id",
        "propagator_id",
    )

    def __init__(
        self,
        plane: int,
        slot: int,
        elements: OrbitalElements,
        isl_terminal_count: int,
        ground_terminal_count: int,
        local_plane: int | None = None,
        local_slot: int | None = None,
        node_id: str | None = None,
        local_node_id: str | None = None,
        segment_id: str | None = None,
        central_body: str | None = None,
        isl_terminals: list[IslTerminal] | tuple[IslTerminal, ...] | None = None,
        ground_terminals: list[SatelliteGroundTerminal]
        | tuple[SatelliteGroundTerminal, ...]
        | None = None,
        tle_line_1: str | None = None,
        tle_line_2: str | None = None,
        norad_id: int | None = None,
        propagator_id: str | None = None,
    ) -> None:
        self.plane = plane
        self.slot = slot
        self.local_plane = plane if local_plane is None else local_plane
        self.local_slot = slot if local_slot is None else local_slot
        self.node_id = node_id
        self.local_node_id = local_node_id
        self.segment_id = segment_id
        if central_body is None:
            raise ValueError("SatelliteNode requires central_body from resolved orbit/body facts")
        self.central_body = central_body
        self.elements = elements
        self.isl_terminal_count = isl_terminal_count
        self.ground_terminal_count = ground_terminal_count
        self.isl_terminals = tuple(isl_terminals or ())
        self.ground_terminals = tuple(ground_terminals or ())
        self.tle_line_1 = tle_line_1
        self.tle_line_2 = tle_line_2
        self.norad_id = norad_id
        self.propagator_id = propagator_id


def satellite_node_id(satellite: SatelliteNode, _addressing: OmeAddressing) -> str:
    """Return the resolver-assigned runtime node ID for a satellite."""

    if satellite.node_id is None:
        raise ValueError(
            "SatelliteNode is missing resolver-assigned node_id; runtime identity "
            "must come from resolve_session(), not addressing plane/slot synthesis"
        )
    return satellite.node_id


def isl_terminal_for_interface(
    terminals: list[IslTerminal] | tuple[IslTerminal, ...],
    interface_name: str,
) -> IslTerminal:
    """Return the terminal block that owns an ISL interface."""

    if not interface_name.startswith("isl"):
        raise ValueError(f"Expected 'islN' interface name, got {interface_name!r}")
    try:
        index = int(interface_name[3:])
    except ValueError as exc:
        raise ValueError(f"Invalid ISL interface name {interface_name!r}") from exc

    cumulative = 0
    for terminal in terminals:
        if index < cumulative + terminal.count:
            return terminal
        cumulative += terminal.count
    raise ValueError(
        f"ISL interface index {index} (from {interface_name!r}) out of range - "
        f"satellite has only {cumulative} ISL terminals total"
    )
