# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Terminal and geometry constraints on each direction of a link, each end at its own epoch.

A direction is one signal: the transmitter at emission and the receiver at arrival (physics
``Signals``). Each end's constraints are checked from that end's position in its body's fixed
frame and the apparent direction it points along at its epoch. A link is open while both of its
directions are open.

The checks run on arrays over every direction of a tick. Precedence and limiting-endpoint rules
are the ones OME applied to both ends at one instant before light time existed:

- ISL: line of sight, range, field of regard (off local horizontal), tracking rate, polar seam.
- Ground: line of sight, range, elevation mask, ground field of regard, satellite off-nadir field
  of regard, tracking rate.

Range is the direction's path length (c times its light time). A limit on one quantity both ends
share takes the smaller of the two terminals' values, and the rejecting endpoint names the end
that declared it: range (one path length). A limit on what each end does on its own applies to
that end alone: a ground pair's tracking rate, since each end slews at its own rate (a station
turns with its body; an orbiter of another body holds the station with an inertial rate), and the
rejecting endpoint names the end whose rate exceeded its own limit. An ISL's two terminals are
one terminal type, so its single tracking limit is each end's own.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import get_args

import numpy as np
from nodalarc.body_frames import BodyFrame
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.models.link_decisions import (
    GroundVisibilityRejectingEndpoint,
    GroundVisibilityRejectReason,
)
from nodalarc.models.terminal_physics import SatGroundTerminalBoresight, TerminalBoresight
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
class IslLimits:
    """Applied limits of one ISL candidate (the smaller of the two terminals' values)."""

    max_range_km: float
    field_of_regard_deg: float
    max_tracking_rate_deg_s: float | None
    polar_seam: bool


def isl_reasons(
    ends: DirectionEnds,
    limits: Sequence[IslLimits],
    *,
    transmitter_latitude_deg: np.ndarray,
    receiver_latitude_deg: np.ndarray,
    latitude_threshold_deg: float,
) -> np.ndarray:
    """Per direction, "ok" or the first failing ISL constraint."""
    max_range = np.array([limit.max_range_km for limit in limits])
    field_of_regard = np.array([limit.field_of_regard_deg for limit in limits])
    max_rate = np.array(
        [
            math.inf if limit.max_tracking_rate_deg_s is None else limit.max_tracking_rate_deg_s
            for limit in limits
        ]
    )
    seam = np.array([limit.polar_seam for limit in limits], dtype=bool)
    half = field_of_regard / 2.0
    outside_regard = (field_of_regard < 360.0) & (
        (_off_horizontal_deg(ends.transmitter) > half) | (_off_horizontal_deg(ends.receiver) > half)
    )
    tracking = (np.degrees(ends.transmitter.rate_rad_s) > max_rate) | (
        np.degrees(ends.receiver.rate_rad_s) > max_rate
    )
    polar = seam & (
        (np.abs(transmitter_latitude_deg) > latitude_threshold_deg)
        | (np.abs(receiver_latitude_deg) > latitude_threshold_deg)
    )
    return np.select(
        [ends.blocked, ends.path_km > max_range, outside_regard, tracking, polar],
        ["los_blocked", "range_exceeded", "field_of_regard", "tracking_exceeded", "polar_seam"],
        default="ok",
    ).astype(object)


@dataclass(frozen=True)
class GroundLimits:
    """One ground candidate: the station's geodetic position, its elevation mask, and each end's
    terminal limits (None where a constraint is not in effect, as in geometry-only links)."""

    gs_lat_deg: float
    gs_lon_deg: float
    min_elevation_deg: float
    gs_max_range_km: float | None = None
    sat_max_range_km: float | None = None
    gs_boresight: TerminalBoresight | None = None
    gs_field_of_regard_deg: float | None = None
    sat_boresight: SatGroundTerminalBoresight | None = None
    sat_field_of_regard_deg: float | None = None
    gs_max_tracking_rate_deg_s: float | None = None
    sat_max_tracking_rate_deg_s: float | None = None


def limiting_endpoint(
    gs_value: float | None, sat_value: float | None
) -> GroundVisibilityRejectingEndpoint:
    """Which endpoint supplied the stricter terminal constraint."""
    if gs_value is None and sat_value is None:
        return "both"
    if gs_value is None:
        return "satellite"
    if sat_value is None:
        return "ground"
    if math.isclose(gs_value, sat_value, rel_tol=0.0, abs_tol=1e-12):
        return "both"
    return "ground" if gs_value < sat_value else "satellite"


def _unbounded_if_none(value: float | None) -> float:
    """A terminal limit as a number: no limit in effect (geometry-only links) never binds."""
    return math.inf if value is None else value


def _smaller(a: float | None, b: float | None) -> float:
    values = [v for v in (a, b) if v is not None]
    return min(values) if values else math.inf


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


def _ground_regard(limit: GroundLimits) -> tuple[int, tuple[float, ...]]:
    """Mode code and parameters of the ground field of regard: 0 none, 1 steerable envelope
    (min az, max az, min el, max el), 2 cone about a boresight (unit east, north, up, half
    angle)."""
    boresight = limit.gs_boresight
    if boresight is None:
        return 0, ()
    if limit.gs_field_of_regard_deg is None:
        raise ValueError("Ground boresight visibility requires gs_field_of_regard_deg")
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
    return 2, (*bore, limit.gs_field_of_regard_deg / 2.0)


def _satellite_regard(limit: GroundLimits) -> float:
    """Half apex angle of the satellite's nadir cone (infinite when it has no boresight)."""
    if limit.sat_boresight is None:
        return math.inf
    if limit.sat_field_of_regard_deg is None:
        raise ValueError("Satellite boresight visibility requires sat_field_of_regard_deg")
    if limit.sat_boresight.mode != "nadir":
        raise ValueError(
            f"Unsupported satellite ground-terminal boresight mode={limit.sat_boresight.mode!r}"
        )
    return limit.sat_field_of_regard_deg / 2.0


@dataclass(frozen=True)
class GroundLimitTable:
    """Ground candidates' limits as arrays (one row per candidate), validated once."""

    lat_deg: np.ndarray
    lon_deg: np.ndarray
    min_elevation_deg: np.ndarray
    max_range_km: np.ndarray
    gs_max_rate_deg_s: np.ndarray
    sat_max_rate_deg_s: np.ndarray
    regard_mode: np.ndarray
    envelope: np.ndarray
    cone: np.ndarray
    satellite_half_deg: np.ndarray
    range_rejector: np.ndarray

    @classmethod
    def from_limits(cls, limits: Sequence[GroundLimits]) -> GroundLimitTable:
        count = len(limits)
        mode: np.ndarray = np.zeros(count, dtype=np.int8)
        envelope = np.zeros((count, 4))
        cone = np.zeros((count, 4))
        for row, limit in enumerate(limits):
            mode[row], parameters = _ground_regard(limit)
            if mode[row] == 1:
                envelope[row] = parameters
            elif mode[row] == 2:
                cone[row] = parameters
        return cls(
            lat_deg=np.array([limit.gs_lat_deg for limit in limits], dtype=float),
            lon_deg=np.array([limit.gs_lon_deg for limit in limits], dtype=float),
            min_elevation_deg=np.array([limit.min_elevation_deg for limit in limits], dtype=float),
            max_range_km=np.array(
                [_smaller(limit.gs_max_range_km, limit.sat_max_range_km) for limit in limits],
                dtype=float,
            ),
            gs_max_rate_deg_s=np.array(
                [_unbounded_if_none(limit.gs_max_tracking_rate_deg_s) for limit in limits],
                dtype=float,
            ),
            sat_max_rate_deg_s=np.array(
                [_unbounded_if_none(limit.sat_max_tracking_rate_deg_s) for limit in limits],
                dtype=float,
            ),
            regard_mode=mode,
            envelope=envelope,
            cone=cone,
            satellite_half_deg=np.array(
                [_satellite_regard(limit) for limit in limits], dtype=float
            ),
            range_rejector=np.array(
                [
                    limiting_endpoint(limit.gs_max_range_km, limit.sat_max_range_km)
                    for limit in limits
                ],
                dtype=object,
            ),
        )

    def take(self, rows: np.ndarray) -> GroundLimitTable:
        return GroundLimitTable(*(getattr(self, name)[rows] for name in self.__dataclass_fields__))


@dataclass(frozen=True)
class GroundDirections:
    """Per direction: reason, rejecting endpoint, the station's elevation and azimuth of its
    pointing, and the satellite's off-nadir angle of its pointing (NaN where the satellite has
    no boresight)."""

    reason: np.ndarray
    rejecting_endpoint: np.ndarray
    elevation_deg: np.ndarray
    azimuth_deg: np.ndarray
    sat_off_nadir_deg: np.ndarray


def ground_directions(
    *,
    station: EndArrays,
    satellite: EndArrays,
    path_km: np.ndarray,
    blocked: np.ndarray,
    table: GroundLimitTable,
) -> GroundDirections:
    """Ground constraints for each direction, given its station end, satellite end and limits."""
    local = _enu(table.lat_deg, table.lon_deg, station.pointing_fixed)
    elevation = np.degrees(np.arctan2(local[:, 2], np.hypot(local[:, 0], local[:, 1])))
    azimuth = np.degrees(np.arctan2(local[:, 0], local[:, 1])) % 360.0

    min_az, max_az, min_el, max_el = table.envelope.T
    outside_envelope = (table.regard_mode == 1) & ~(
        (min_az <= azimuth) & (azimuth <= max_az) & (min_el <= elevation) & (elevation <= max_el)
    )
    length = np.linalg.norm(local, axis=1, keepdims=True)
    target = np.divide(local, length, out=np.zeros_like(local), where=length > 0.0)
    cone_cosine = np.clip(np.einsum("ni,ni->n", table.cone[:, :3], target), -1.0, 1.0)
    outside_cone = (table.regard_mode == 2) & (
        np.degrees(np.arccos(cone_cosine)) > table.cone[:, 3]
    )
    outside_ground = outside_envelope | outside_cone

    has_boresight = np.isfinite(table.satellite_half_deg)
    # The satellite terminal's nadir points at the centre of the body its station stands on:
    # the satellite's own body for a same-body pair, another body for a lunar orbiter serving an
    # Earth station.
    nadir = satellite.toward_other_body_fixed
    nadir_cosine = np.clip(np.einsum("ni,ni->n", nadir, satellite.pointing_fixed), -1.0, 1.0)
    off_nadir = np.where(has_boresight, np.degrees(np.arccos(nadir_cosine)), np.nan)
    outside_satellite = has_boresight & (off_nadir > table.satellite_half_deg)

    # Each end against its own terminal's limit: the two ends slew at different rates.
    station_over = np.degrees(station.rate_rad_s) > table.gs_max_rate_deg_s
    satellite_over = np.degrees(satellite.rate_rad_s) > table.sat_max_rate_deg_s
    tracking = station_over | satellite_over
    tracking_rejector = np.where(
        station_over & satellite_over, "both", np.where(station_over, "ground", "satellite")
    ).astype(object)
    out_of_range = path_km > table.max_range_km
    conditions = [
        blocked,
        out_of_range,
        elevation < table.min_elevation_deg,
        outside_ground,
        outside_satellite,
        tracking,
    ]
    reason = np.select(
        conditions,
        [
            "los_blocked",
            "range_exceeded",
            "elevation_below_min",
            "field_of_regard",
            "field_of_regard",
            "tracking_exceeded",
        ],
        default="ok",
    ).astype(object)
    rejecting = np.select(
        conditions,
        [
            np.full(len(reason), "none", dtype=object),
            table.range_rejector,
            np.full(len(reason), "none", dtype=object),
            np.full(len(reason), "ground", dtype=object),
            np.full(len(reason), "satellite", dtype=object),
            tracking_rejector,
        ],
        default="none",
    ).astype(object)
    return GroundDirections(reason, rejecting, elevation, azimuth, off_nadir)


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
