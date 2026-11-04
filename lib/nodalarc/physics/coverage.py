# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""What part of the ephemeris data one simulation tick needs, and whether the data has it.

A tick at sim time t queries the data at other epochs too:

- light time: a signal sent at t reaches its receiver at t + tau, and one received at t left its
  transmitter at t - tau; tau is bounded by the largest possible distance between two endpoints;
- the ground-pass lookahead samples future ticks, up to its horizon plus one sampling chunk;
- body samples for the page span 1 h before to 6 h after the instant they are produced;
- the motion table reads grid points 600 s apart, so each end of the span needs the next grid
  point as well.

The data must cover the whole span for every session body and the Sun. When it does not, the
gap names the limiting body and the usable interval: the sim times at which a tick's whole span
lies inside the data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations_with_replacement

from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.physics.context import PhysicsContext
from nodalarc.physics.ephemeris_table import body_separation
from nodalarc.physics.motion import GRID_SPACING_S
from nodalarc.physics.samples import WINDOW_AFTER_S, WINDOW_BEFORE_S


def light_time_bound_s(reach_km: Mapping[str, float]) -> float:
    """The longest a signal between two of the session's endpoints can take. ``reach_km`` holds,
    per body, the farthest any endpoint on or around it sits from its center (a satellite's
    apoapsis, a site's radius)."""
    longest_km = 0.0
    for a, b in combinations_with_replacement(sorted(reach_km), 2):
        if a == b:
            distance_km = 2.0 * reach_km[a]
        else:
            distance_km = body_separation(a, b).farthest_km + reach_km[a] + reach_km[b]
        longest_km = max(longest_km, distance_km)
    return longest_km / SPEED_OF_LIGHT_KM_S


@dataclass(frozen=True)
class TickCoverageGap:
    """A tick the data cannot support: the body (or ``sun``) whose data falls short, and the
    usable interval, the span of master sim time (Unix seconds) in which the session's ticks can
    run on the data."""

    body_id: str
    usable_start_unix: float
    usable_end_unix: float


def tick_coverage_gap(
    physics: PhysicsContext,
    sim_time_unix: float,
    *,
    light_time_bound_s: float,
    lookahead_s: float,
) -> TickCoverageGap | None:
    """None when the data covers every query a tick at ``sim_time_unix`` makes."""
    before = max(WINDOW_BEFORE_S, light_time_bound_s) + GRID_SPACING_S
    after = max(WINDOW_AFTER_S, lookahead_s + light_time_bound_s) + GRID_SPACING_S
    gap = physics.coverage_gap(sim_time_unix - before, sim_time_unix + after)
    if gap is None:
        return None
    return TickCoverageGap(
        body_id=gap.body_id,
        usable_start_unix=gap.covered_start_unix + before,
        usable_end_unix=gap.covered_end_unix - after,
    )


def _utc(unix: float) -> str:
    return datetime.fromtimestamp(unix, UTC).isoformat(timespec="seconds")


def describe_tick_coverage_gap(gap: TickCoverageGap) -> str:
    """A refusal's text: the limiting data and the usable interval."""
    if gap.usable_end_unix < gap.usable_start_unix:
        return f"the {gap.body_id} data is too short to support this session at any time"
    return (
        f"the {gap.body_id} data supports this session from {_utc(gap.usable_start_unix)} to "
        f"{_utc(gap.usable_end_unix)}"
    )
