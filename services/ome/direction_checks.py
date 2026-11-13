# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Terminal and geometry constraints on each direction of a link, each end at its own epoch.

A direction is one signal: the transmitter at emission and the receiver at arrival (physics
``Signals``). Each end's constraints are checked from that end's position in its body's fixed
frame and the apparent direction it points along at its epoch. A link is open while both of its
directions are open.

The checks run on arrays over every direction of a tick. One check serves every link: each end
is held to its own terminal's limits, and its field of regard is specialised by its mount's frame
(a surface mount's local sky, a space mount's nadir cone or local-horizontal band). Precedence:
line of sight, range, elevation mask, field of regard (surface ends first), tracking rate, polar
seam.

Range is the direction's path length (c times its light time), against the smaller of the two
terminals' ranges, and the rejecting endpoint names the end that declared it. Every other limit
applies to its own end: each end slews at its own rate and can carry its own terminal.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

import numpy as np
from nodalarc.body_frames import BodyFrame
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.models.link_decisions import (
    GroundVisibilityRejectingEndpoint,
    GroundVisibilityRejectReason,
)
from nodalarc.models.terminal_physics import TerminalBoresight
from nodalarc.physics import PhysicsContext
from nodalarc.physics.lighttime import Endpoint, Signals, signals_sent_at


@dataclass(frozen=True)
class EndArrays:
    """One end of every direction at its own epoch: position in its body's fixed frame (K, 3),
    unit pointing in that frame (K, 3), line-of-sight angular rate (K,) in rad/s, and the unit
    direction toward the centre of the other end's body in that frame (K, 3)."""

    fixed_km: np.ndarray
    pointing_fixed: np.ndarray
    rate_rad_s: np.ndarray
    toward_other_body_fixed: np.ndarray

    def take(self, rows: np.ndarray) -> EndArrays:
        return EndArrays(
            self.fixed_km[rows],
            self.pointing_fixed[rows],
            self.rate_rad_s[rows],
            self.toward_other_body_fixed[rows],
        )


@dataclass(frozen=True)
class DirectionEnds:
    """Every direction's path length (K,), whether a body blocks it (K,), and both ends."""

    path_km: np.ndarray
    blocked: np.ndarray
    transmitter: EndArrays
    receiver: EndArrays

    @classmethod
    def from_signals(cls, signals: Signals) -> DirectionEnds:
        return cls(
            path_km=signals.light_time_s * SPEED_OF_LIGHT_KM_S,
            blocked=np.asarray(signals.occluded.any(axis=1), dtype=bool),
            transmitter=EndArrays(
                signals.transmitter.fixed_km,
                signals.transmitter.pointing_fixed,
                signals.transmitter.rate_rad_s,
                signals.transmitter.toward_other_body_fixed,
            ),
            receiver=EndArrays(
                signals.receiver.fixed_km,
                signals.receiver.pointing_fixed,
                signals.receiver.rate_rad_s,
                signals.receiver.toward_other_body_fixed,
            ),
        )


@dataclass(frozen=True)
class PairSignals:
    """Both directions of every given (a, b) pair, computed in one signals call: a tick's ISL
    and ground candidates share the call's fixed cost (measured 0.65 ms per call)."""

    signals: Signals
    rows: Mapping[tuple[str, str], int]
    count: int

    @classmethod
    def sent_at(
        cls,
        physics: PhysicsContext,
        shapes: Mapping[str, BodyFrame],
        endpoints: Mapping[str, Endpoint],
        pairs: Sequence[tuple[str, str]],
        sent_unix: float | np.ndarray,
    ) -> PairSignals:
        """Signals a sends to b and b sends to a, at ``sent_unix`` (one instant, or one per
        pair)."""
        missing = sorted({node for pair in pairs for node in pair} - set(endpoints))
        if missing:
            raise ValueError(
                f"no signal endpoint for {', '.join(missing)}; link geometry cannot be evaluated"
            )
        first = [endpoints[a] for a, _b in pairs]
        second = [endpoints[b] for _a, b in pairs]
        instants = np.broadcast_to(np.asarray(sent_unix, dtype=np.float64), (len(pairs),))
        return cls(
            signals=signals_sent_at(
                physics, shapes, first + second, second + first, np.concatenate([instants] * 2)
            ),
            rows={pair: row for row, pair in enumerate(pairs)},
            count=len(pairs),
        )

    def in_order(self) -> tuple[Signals, Signals]:
        """The a->b and b->a signals in the order the pairs were given (pairs may repeat when
        each row has its own instant)."""
        rows = np.arange(self.count)
        return self.signals.take(rows), self.signals.take(rows + self.count)

    def directions(self, pairs: Sequence[tuple[str, str]]) -> tuple[Signals, Signals]:
        """The a->b and b->a signals of each given pair, in the given order and orientation."""
        if len(self.rows) != self.count:
            raise ValueError(
                "signals were computed for repeated pairs (one instant per row); read them with "
                "in_order()"
            )
        try:
            rows = np.array([self.rows[pair] for pair in pairs], dtype=np.int64)
        except KeyError as exc:
            raise ValueError(f"pair {exc.args[0]} has no signals this tick") from exc
        return self.signals.take(rows), self.signals.take(rows + self.count)


def _off_horizontal_deg(end: EndArrays) -> np.ndarray:
    """Angle between the pointing and the end's local horizontal plane (radial from its body's
    center). An end at the center has no horizontal and is never limited."""
    radius = np.linalg.norm(end.fixed_km, axis=1)
    centred = radius < 1e-10
    radial = end.fixed_km / np.where(centred, 1.0, radius)[:, None]
    sine = np.clip(np.einsum("ni,ni->n", end.pointing_fixed, radial), -1.0, 1.0)
    return np.where(centred, 0.0, np.degrees(np.abs(np.arcsin(sine))))


@dataclass(frozen=True)
class SurfaceRegard:
    """A surface mount's sky: the end's geodetic position, its elevation mask, and its terminal's
    boresight and field of regard (None when no terminal limit is in effect, as in geometry-only
    links)."""

    lat_deg: float
    lon_deg: float
    min_elevation_deg: float
    boresight: TerminalBoresight | None = None
    field_of_regard_deg: float | None = None


@dataclass(frozen=True)
class SpaceRegard:
    """A space mount's field of regard: a cone about the direction to the centre of the other
    end's body (``nadir``), or a band about the end's local horizontal (``local_horizontal``).
    None, or 360 degrees about the horizontal, is no limit."""

    mode: Literal["nadir", "local_horizontal"]
    field_of_regard_deg: float | None = None


@dataclass(frozen=True)
class EndLimits:
    """One end's terminal limits. The field of regard is specialised by the mount's frame; every
    other limit is the same on every end. A limit that is None is not in effect and never binds.
    Each end is held to its own terminal's limits: the two ends of a link slew at different rates
    and can carry different terminals. Rejected alternative: the smaller of the two terminals'
    values on both ends, and a tracking limit on cross-plane links only, which let an intra-plane
    or inter-body link exceed its terminals' tracking rate."""

    regard: SurfaceRegard | SpaceRegard
    max_range_km: float | None = None
    max_tracking_rate_deg_s: float | None = None


@dataclass(frozen=True)
class PairLimits:
    """A candidate's two ends' limits, in pair order, and whether its polar-seam check applies."""

    a: EndLimits
    b: EndLimits
    polar_seam: bool = False


def _unbounded_if_none(value: float | None) -> float:
    """A terminal limit as a number: no limit in effect never binds."""
    return math.inf if value is None else value


def _enu(lat_deg: np.ndarray, lon_deg: np.ndarray, vector: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    sin_lat, cos_lat, sin_lon, cos_lon = np.sin(lat), np.cos(lat), np.sin(lon), np.cos(lon)
    x, y, z = vector[:, 0], vector[:, 1], vector[:, 2]
    east = -sin_lon * x + cos_lon * y
    north = -sin_lat * cos_lon * x - sin_lat * sin_lon * y + cos_lat * z
    up = cos_lat * cos_lon * x + cos_lat * sin_lon * y + sin_lat * z
    return np.stack([east, north, up], axis=1)


def _unit_from_az_el(az_deg: float, el_deg: float) -> tuple[float, float, float]:
    az, el = math.radians(az_deg), math.radians(el_deg)
    return (math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el))


def _surface_regard(regard: SurfaceRegard) -> tuple[int, tuple[float, ...]]:
    """Mode code and parameters of a surface field of regard: 0 none, 1 steerable envelope
    (min az, max az, min el, max el), 2 cone about a boresight (unit east, north, up, half
    angle)."""
    boresight = regard.boresight
    if boresight is None:
        return 0, ()
    if regard.field_of_regard_deg is None:
        raise ValueError("a surface boresight requires its field of regard")
    if boresight.mode == "steerable_envelope":
        bounds = (
            boresight.min_az_deg,
            boresight.max_az_deg,
            boresight.min_el_deg,
            boresight.max_el_deg,
        )
        if any(bound is None for bound in bounds):
            raise ValueError("steerable_envelope boresight is missing azimuth/elevation bounds")
        return 1, tuple(float(bound) for bound in bounds if bound is not None)
    if boresight.mode == "local_vertical":
        bore = (0.0, 0.0, 1.0)
    else:
        if boresight.configured_az_deg is None or boresight.configured_el_deg is None:
            raise ValueError(
                "configured_topocentric boresight is missing configured azimuth/elevation"
            )
        bore = _unit_from_az_el(boresight.configured_az_deg, boresight.configured_el_deg)
    return 2, (*bore, regard.field_of_regard_deg / 2.0)


def _space_regard(regard: SpaceRegard) -> tuple[int, float]:
    """Mode code (0 none, 1 nadir cone, 2 local-horizontal band) and half angle."""
    if regard.field_of_regard_deg is None:
        return 0, math.inf
    if regard.mode == "local_horizontal" and regard.field_of_regard_deg >= 360.0:
        return 0, math.inf
    return (1 if regard.mode == "nadir" else 2), regard.field_of_regard_deg / 2.0


@dataclass(frozen=True)
class EndTable:
    """Ends' limits as arrays, one row per candidate, validated once."""

    surface: np.ndarray
    lat_deg: np.ndarray
    lon_deg: np.ndarray
    min_elevation_deg: np.ndarray
    surface_mode: np.ndarray
    envelope: np.ndarray
    cone: np.ndarray
    space_mode: np.ndarray
    space_half_deg: np.ndarray
    max_range_km: np.ndarray
    max_rate_deg_s: np.ndarray

    @classmethod
    def from_limits(cls, limits: Sequence[EndLimits]) -> EndTable:
        count = len(limits)
        surface: np.ndarray = np.zeros(count, dtype=bool)
        lat, lon = np.zeros(count), np.zeros(count)
        min_elevation = np.full(count, -math.inf)
        surface_mode: np.ndarray = np.zeros(count, dtype=np.int8)
        envelope, cone = np.zeros((count, 4)), np.zeros((count, 4))
        space_mode: np.ndarray = np.zeros(count, dtype=np.int8)
        space_half = np.full(count, math.inf)
        for row, limit in enumerate(limits):
            regard = limit.regard
            if isinstance(regard, SurfaceRegard):
                surface[row] = True
                lat[row], lon[row] = regard.lat_deg, regard.lon_deg
                min_elevation[row] = regard.min_elevation_deg
                surface_mode[row], parameters = _surface_regard(regard)
                if surface_mode[row] == 1:
                    envelope[row] = parameters
                elif surface_mode[row] == 2:
                    cone[row] = parameters
            else:
                space_mode[row], space_half[row] = _space_regard(regard)
        return cls(
            surface=surface,
            lat_deg=lat,
            lon_deg=lon,
            min_elevation_deg=min_elevation,
            surface_mode=surface_mode,
            envelope=envelope,
            cone=cone,
            space_mode=space_mode,
            space_half_deg=space_half,
            max_range_km=np.array([_unbounded_if_none(limit.max_range_km) for limit in limits]),
            max_rate_deg_s=np.array(
                [_unbounded_if_none(limit.max_tracking_rate_deg_s) for limit in limits]
            ),
        )

    def take(self, rows: np.ndarray) -> EndTable:
        return EndTable(*(getattr(self, name)[rows] for name in self.__dataclass_fields__))


@dataclass(frozen=True)
class EndCheck:
    """One end of every direction against its own limits. Elevation and azimuth are the end's
    look angles in its local sky (NaN on a space end); the off-nadir angle is NaN unless the end
    has a nadir field of regard."""

    surface: np.ndarray
    elevation_deg: np.ndarray
    azimuth_deg: np.ndarray
    below_mask: np.ndarray
    outside_regard: np.ndarray
    off_nadir_deg: np.ndarray
    over_rate: np.ndarray


def end_check(end: EndArrays, table: EndTable) -> EndCheck:
    """One end against its own limits: a surface end in its local sky, a space end against its
    nadir cone or local-horizontal band, and every end against its own tracking rate."""
    local = _enu(table.lat_deg, table.lon_deg, end.pointing_fixed)
    elevation = np.degrees(np.arctan2(local[:, 2], np.hypot(local[:, 0], local[:, 1])))
    azimuth = np.degrees(np.arctan2(local[:, 0], local[:, 1])) % 360.0
    min_az, max_az, min_el, max_el = table.envelope.T
    outside_envelope = (table.surface_mode == 1) & ~(
        (min_az <= azimuth) & (azimuth <= max_az) & (min_el <= elevation) & (elevation <= max_el)
    )
    length = np.linalg.norm(local, axis=1, keepdims=True)
    target = np.divide(local, length, out=np.zeros_like(local), where=length > 0.0)
    cone_cosine = np.clip(np.einsum("ni,ni->n", table.cone[:, :3], target), -1.0, 1.0)
    outside_cone = (table.surface_mode == 2) & (
        np.degrees(np.arccos(cone_cosine)) > table.cone[:, 3]
    )
    # A nadir cone points at the centre of the other end's body: the end's own body for a
    # same-body pair, another body for a lunar orbiter serving an Earth station.
    nadir_cosine = np.clip(
        np.einsum("ni,ni->n", end.toward_other_body_fixed, end.pointing_fixed), -1.0, 1.0
    )
    off_nadir = np.where(table.space_mode == 1, np.degrees(np.arccos(nadir_cosine)), np.nan)
    outside_nadir = (table.space_mode == 1) & (off_nadir > table.space_half_deg)
    outside_band = (table.space_mode == 2) & (_off_horizontal_deg(end) > table.space_half_deg)
    surface = table.surface
    return EndCheck(
        surface=surface,
        elevation_deg=np.where(surface, elevation, np.nan),
        azimuth_deg=np.where(surface, azimuth, np.nan),
        below_mask=surface & (elevation < table.min_elevation_deg),
        outside_regard=outside_envelope | outside_cone | outside_nadir | outside_band,
        off_nadir_deg=off_nadir,
        over_rate=np.degrees(end.rate_rad_s) > table.max_rate_deg_s,
    )


@dataclass(frozen=True)
class DirectionVerdict:
    """Per direction: the first failing constraint ("ok" when none), the end that failed it
    ("transmitter", "receiver", "both", or "none" for line of sight, the elevation mask and the
    polar seam), and each end's check."""

    reason: np.ndarray
    rejecting_end: np.ndarray
    transmitter: EndCheck
    receiver: EndCheck


def direction_reasons(
    ends: DirectionEnds,
    transmitter: EndTable,
    receiver: EndTable,
    *,
    polar_seam: np.ndarray | None = None,
) -> DirectionVerdict:
    """Every constraint of each direction, in precedence: line of sight, range (the smaller of the
    two terminals'), elevation mask, field of regard (surface ends first), tracking rate (each end
    against its own), polar seam (the candidate's flag, evaluated by the caller)."""
    tx, rx = end_check(ends.transmitter, transmitter), end_check(ends.receiver, receiver)
    count = len(ends.path_km)
    range_limit = np.minimum(transmitter.max_range_km, receiver.max_range_km)
    range_end = np.where(
        transmitter.max_range_km < receiver.max_range_km,
        "transmitter",
        np.where(receiver.max_range_km < transmitter.max_range_km, "receiver", "both"),
    )
    tracking = tx.over_rate | rx.over_rate
    tracking_end = np.where(
        tx.over_rate & rx.over_rate, "both", np.where(tx.over_rate, "transmitter", "receiver")
    )
    seam = np.zeros(count, dtype=bool) if polar_seam is None else polar_seam
    conditions = [
        ends.blocked,
        ends.path_km > range_limit,
        tx.below_mask | rx.below_mask,
        tx.surface & tx.outside_regard,
        rx.surface & rx.outside_regard,
        ~tx.surface & tx.outside_regard,
        ~rx.surface & rx.outside_regard,
        tracking,
        seam,
    ]
    none: np.ndarray = np.full(count, "none", dtype=object)
    reason = np.select(
        conditions,
        [
            "los_blocked",
            "range_exceeded",
            "elevation_below_min",
            "field_of_regard",
            "field_of_regard",
            "field_of_regard",
            "field_of_regard",
            "tracking_exceeded",
            "polar_seam",
        ],
        default="ok",
    ).astype(object)
    rejecting = np.select(
        conditions,
        [
            none,
            range_end.astype(object),
            none,
            np.full(count, "transmitter", dtype=object),
            np.full(count, "receiver", dtype=object),
            np.full(count, "transmitter", dtype=object),
            np.full(count, "receiver", dtype=object),
            tracking_end.astype(object),
            none,
        ],
        default="none",
    ).astype(object)
    return DirectionVerdict(reason, rejecting, tx, rx)


def pair_tables(limits: Sequence[PairLimits]) -> tuple[EndTable, EndTable, np.ndarray]:
    """The a-end and b-end tables of candidates in pair order, and their polar-seam flags."""
    return (
        EndTable.from_limits([pair.a for pair in limits]),
        EndTable.from_limits([pair.b for pair in limits]),
        np.array([pair.polar_seam for pair in limits], dtype=bool),
    )


@dataclass(frozen=True)
class GroundDirections:
    """Per direction of a ground candidate: reason, rejecting endpoint named by role, the
    station's elevation and azimuth of its pointing, and the satellite's off-nadir angle of its
    pointing (NaN where the satellite has no boresight)."""

    reason: np.ndarray
    rejecting_endpoint: np.ndarray
    elevation_deg: np.ndarray
    azimuth_deg: np.ndarray
    sat_off_nadir_deg: np.ndarray


def ground_directions(verdict: DirectionVerdict, *, station_transmits: bool) -> GroundDirections:
    """A direction verdict in the published ground vocabulary: the station's end is "ground" and
    the other end "satellite"."""
    station, satellite = (
        (verdict.transmitter, verdict.receiver)
        if station_transmits
        else (verdict.receiver, verdict.transmitter)
    )
    station_end = "transmitter" if station_transmits else "receiver"
    role = np.where(
        verdict.rejecting_end == station_end,
        "ground",
        np.where(
            np.isin(verdict.rejecting_end, ["transmitter", "receiver"]),
            "satellite",
            verdict.rejecting_end,
        ),
    ).astype(object)
    return GroundDirections(
        verdict.reason, role, station.elevation_deg, station.azimuth_deg, satellite.off_nadir_deg
    )


# The typed vocabularies the per-direction arrays hold; an unknown value is a producer bug and
# fails the lookup.
_GROUND_REASONS: dict[str, GroundVisibilityRejectReason] = {
    reason: reason for reason in get_args(GroundVisibilityRejectReason.__value__)
}
_REJECTING_ENDPOINTS: dict[str, GroundVisibilityRejectingEndpoint] = {
    endpoint: endpoint for endpoint in get_args(GroundVisibilityRejectingEndpoint.__value__)
}


@dataclass(frozen=True)
class GroundPairVerdict:
    """One ground candidate's verdict: open while both directions are open."""

    visible: bool
    reason: GroundVisibilityRejectReason
    rejecting_endpoint: GroundVisibilityRejectingEndpoint
    elevation_deg: float
    azimuth_deg: float | None
    sat_off_nadir_deg: float | None


def ground_pair_verdicts(
    uplink: GroundDirections, downlink: GroundDirections
) -> list[GroundPairVerdict]:
    """Combine the station-transmits and satellite-transmits directions of each candidate. The
    first failing direction (uplink first) decides and supplies its angles; an open pair reports
    the station's look angles from the uplink and the satellite's off-nadir angle from the
    downlink, both at the tick. The off-nadir angle is reported for an open pair and for a
    satellite field-of-regard rejection; a blocked line of sight reports elevation -90 and no
    azimuth."""
    verdicts = []
    for row in range(len(uplink.reason)):
        if uplink.reason[row] != "ok":
            deciding, look, nadir = uplink, uplink, uplink
        elif downlink.reason[row] != "ok":
            deciding, look, nadir = downlink, downlink, downlink
        else:
            deciding, look, nadir = uplink, uplink, downlink
        reason = _GROUND_REASONS[str(deciding.reason[row])]
        reports_off_nadir = reason == "ok" or (
            reason == "field_of_regard" and deciding.rejecting_endpoint[row] == "satellite"
        )
        off_nadir = float(nadir.sat_off_nadir_deg[row]) if reports_off_nadir else math.nan
        if reason == "los_blocked":
            verdicts.append(GroundPairVerdict(False, "los_blocked", "none", -90.0, None, None))
            continue
        verdicts.append(
            GroundPairVerdict(
                visible=reason == "ok",
                reason=reason,
                rejecting_endpoint=_REJECTING_ENDPOINTS[str(deciding.rejecting_endpoint[row])],
                elevation_deg=float(look.elevation_deg[row]),
                azimuth_deg=float(look.azimuth_deg[row]),
                sat_off_nadir_deg=None if math.isnan(off_nadir) else off_nadir,
            )
        )
    return verdicts
