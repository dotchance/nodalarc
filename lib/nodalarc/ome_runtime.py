"""Internal OME runtime facts materialized from a resolved session."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol

PropagatorId = Literal["two-body", "keplerian-circular", "j2-mean-elements", "sgp4-tle"]
SessionPropagatorId = PropagatorId | Literal["mixed"]

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.body_frames import BodyFrame, SupportedSurfaceBody
from nodalarc.models.ground_policy import HandoverPolicySpec, SelectionPolicySpec
from nodalarc.models.terminal_physics import (
    SatGroundTerminalBoresight,
    TerminalBoresight,
)
from nodalarc.orbital import OrbitalElements
from nodalarc.propagator import advance_mean_elements


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
        "elements_epoch_unix",
        "authored_elements",
        "authored_epoch_unix",
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
        authored_elements: OrbitalElements | None = None,
        authored_epoch_unix: float | None = None,
        elements_epoch_unix: float | None = None,
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
        # The working photograph's validity instant. Propagation derives each
        # satellite's elapsed time from this owned anchor, so a caller-
        # supplied epoch can never silently re-interpret the elements at a
        # different instant.
        self.elements_epoch_unix = elements_epoch_unix
        # The authored photograph: elements exactly as declared, valid at the
        # orbit's own epoch. ``elements`` above is the working photograph at
        # the current pacing anchor; re-anchoring derives it from these two
        # fields and never accumulates.
        self.authored_elements = authored_elements
        self.authored_epoch_unix = authored_epoch_unix
        self.isl_terminal_count = isl_terminal_count
        self.ground_terminal_count = ground_terminal_count
        self.isl_terminals = tuple(isl_terminals or ())
        self.ground_terminals = tuple(ground_terminals or ())
        self.tle_line_1 = tle_line_1
        self.tle_line_2 = tle_line_2
        self.norad_id = norad_id
        self.propagator_id = propagator_id


def satellite_propagator_id(
    sat: SatelliteNode, session_propagator_id: SessionPropagatorId
) -> PropagatorId:
    """The propagator this satellite integrates under, session default aware."""
    sat_propagator_id = getattr(sat, "propagator_id", None)
    if sat_propagator_id is not None:
        if sat_propagator_id not in (
            "two-body",
            "keplerian-circular",
            "j2-mean-elements",
            "sgp4-tle",
        ):
            raise ValueError(f"Unsupported satellite propagator: {sat_propagator_id!r}")
        return sat_propagator_id
    if session_propagator_id == "mixed":
        raise ValueError("OME mixed propagation requires every satellite to carry propagator_id")
    return session_propagator_id


def retarget_satellites(
    satellites: list[SatelliteNode],
    *,
    session_propagator_id: SessionPropagatorId,
    anchor_epoch_unix: float,
    body_frames: Mapping[str, BodyFrame],
) -> None:
    """Re-anchor every satellite's working elements to ``anchor_epoch_unix``.

    The dt propagation model reads ``sat.elements`` as a photograph taken at
    the anchoring epoch. This derives that photograph from the authored one
    (``authored_elements`` at ``authored_epoch_unix``) wherever an epoch is
    established: input construction, session start, seek, and checkpoint
    recovery. Derivation always starts from the authored photograph, never
    from the previous working one, so repeated re-anchoring cannot
    accumulate error. SGP4 satellites are untouched; a TLE carries its own
    epoch.
    """
    for sat in satellites:
        sat_propagator_id = satellite_propagator_id(sat, session_propagator_id)
        if sat_propagator_id == "sgp4-tle":
            continue
        if sat.authored_elements is None or sat.authored_epoch_unix is None:
            raise ValueError(
                f"satellite {sat.node_id!r} has no authored element photograph; "
                "orbit-placed satellites must carry authored_elements and "
                "authored_epoch_unix to be re-anchored"
            )
        body_frame = body_frames.get(sat.central_body)
        if body_frame is None:
            raise ValueError(
                f"retarget missing body frame for satellite {sat.node_id!r} "
                f"central_body={sat.central_body!r}"
            )
        sat.elements = advance_mean_elements(
            sat.authored_elements,
            anchor_epoch_unix - sat.authored_epoch_unix,
            body_frame=body_frame,
            propagator_id=sat_propagator_id,
        )
        sat.elements_epoch_unix = anchor_epoch_unix


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
