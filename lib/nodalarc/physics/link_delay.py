# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The one-way delays a link between two endpoints can have over a whole session.

The range is bounded once, from facts that hold at every sim time a session can reach, seeks
included: each endpoint's body and the farthest the endpoint gets from that body's center, the
smallest and largest separation of the two bodies over the shipped data's whole coverage, and the
largest distance at which the link's terminals can hold the link. A session declares no end time,
so the range cannot come from the session's own interval.

Delay is distance over the speed of light. A signal's light time differs from that by at most the
endpoints' relative speed times the delay: about 5 us for Earth and the Moon (1.1 km/s for 1.4 s),
and under 0.3 s between Earth and Mars orbiters (under 65 km/s for 1,342 s). Neither moves a
delay across the 10 s local and long-delay bound.
"""

from __future__ import annotations

from dataclasses import dataclass

from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.physics.ephemeris_table import body_separation


@dataclass(frozen=True, slots=True)
class LinkEnd:
    """One endpoint of a link: its body and the farthest it gets from that body's center (a
    satellite's apoapsis radius, a site's equatorial radius plus its altitude)."""

    body_id: str
    reach_km: float


def link_distance_range_km(a: LinkEnd, b: LinkEnd) -> tuple[float, float]:
    """The smallest and largest distance between ``a`` and ``b`` the geometry allows."""
    if a.body_id == b.body_id:
        return 0.0, a.reach_km + b.reach_km
    separation = body_separation(a.body_id, b.body_id)
    reach = a.reach_km + b.reach_km
    return max(0.0, separation.nearest_km - reach), separation.farthest_km + reach


def link_delay_range_s(a: LinkEnd, b: LinkEnd, *, range_limit_km: float) -> tuple[float, float]:
    """The smallest and largest one-way delay of a link between ``a`` and ``b``.

    A link is up only within ``range_limit_km``, the largest distance its terminals can hold it,
    so that limit caps the largest delay. Endpoints whose nearest possible distance is beyond the
    limit never hold the link; their range is the geometric one, since no delay of theirs is ever
    carried and the limit gives no smaller one.
    """
    nearest_km, farthest_km = link_distance_range_km(a, b)
    if nearest_km <= range_limit_km:
        farthest_km = min(farthest_km, range_limit_km)
    return nearest_km / SPEED_OF_LIGHT_KM_S, farthest_km / SPEED_OF_LIGHT_KM_S
