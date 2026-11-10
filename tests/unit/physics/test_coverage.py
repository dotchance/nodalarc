# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Coverage of the ephemeris data against what a tick queries, at the shipped data's real edges:
Earth's orientation data ends 2126-11-03T00:00Z, the planetary positions on 2150-01-21."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.frames import GeoPosition
from nodalarc.models.session import session_epoch_unix
from nodalarc.ome_inputs import build_ome_inputs_from_resolved, coverage_requirement
from nodalarc.physics import PhysicsContext, PhysicsDataError
from nodalarc.physics.coverage import (
    TickCoverageGap,
    describe_tick_coverage_gap,
    light_time_bound_s,
    tick_coverage_gap,
)
from nodalarc.physics.ephemeris_table import body_separation
from nodalarc.physics.geodesy import body_fixed_from_geodetic
from nodalarc.physics.lighttime import (
    Endpoint,
    SiteEndpoint,
    signals_received_at,
    signals_sent_at,
)
from nodalarc.physics.samples import body_sample_window
from nodalarc.resolve_session import resolve_session
from ome.propagation_engine import satellite_endpoints

from tests.catalog_session_fixtures import shipped_read_view
from tests.physics_fixtures import EARTH_TEST_BODY_FRAME, LUNA_TEST_BODY_FRAME

EARTH_DATA_START = datetime(2000, 1, 1, tzinfo=UTC).timestamp()
EARTH_DATA_END = datetime(2126, 11, 3, tzinfo=UTC).timestamp()
SHAPES = {"earth": EARTH_TEST_BODY_FRAME, "luna": LUNA_TEST_BODY_FRAME}
GOLDSTONE = SiteEndpoint("earth", (-2353.6, -4641.3, 3677.1))
MOON_CENTRE = SiteEndpoint("luna", (0.0, 0.0, 0.0))
# An Earth-Moon session: geostationary relays and a low lunar orbit.
BOUND_S = light_time_bound_s({"earth": 42_164.0, "luna": 1_837.4})


def _gap(physics: PhysicsContext, t: float, lookahead_s: float) -> TickCoverageGap | None:
    return tick_coverage_gap(physics, t, light_time_bound_s=BOUND_S, lookahead_s=lookahead_s)


def test_a_tick_inside_the_data_has_no_gap(physics_context: PhysicsContext) -> None:
    assert _gap(physics_context, datetime(2026, 11, 3, 12, tzinfo=UTC).timestamp(), 7_520.0) is None


@pytest.mark.parametrize("lookahead_s", [0.0, 7_520.0])
def test_every_query_of_the_last_usable_tick_lies_inside_the_data(
    physics_context: PhysicsContext, lookahead_s: float
) -> None:
    """The last tick the check allows makes all its queries: its body-sample window, and a signal
    sent at the end of its lookahead. A second later the check refuses, naming Earth, whose
    orientation data ends first, and the same usable interval."""
    gap = _gap(physics_context, EARTH_DATA_END, lookahead_s)
    assert gap is not None
    assert gap.body_id == "earth"
    last = math.floor(gap.usable_end_unix)

    assert _gap(physics_context, last, lookahead_s) is None
    body_sample_window(physics_context, epoch_id=0, produced_at_unix=last)
    signals_sent_at(physics_context, SHAPES, [GOLDSTONE], [MOON_CENTRE], last + lookahead_s)

    assert _gap(physics_context, last + 1.0, lookahead_s) == gap
    with pytest.raises(PhysicsDataError):
        body_sample_window(physics_context, epoch_id=0, produced_at_unix=EARTH_DATA_END)


def test_every_query_of_the_first_usable_tick_lies_inside_the_data(
    physics_context: PhysicsContext,
) -> None:
    """The first tick the check allows makes its queries reaching back: its body-sample window,
    and a signal received at it. A second earlier the check refuses with the same interval."""
    gap = _gap(physics_context, EARTH_DATA_START, 0.0)
    assert gap is not None
    first = math.ceil(gap.usable_start_unix)

    assert _gap(physics_context, first, 0.0) is None
    body_sample_window(physics_context, epoch_id=0, produced_at_unix=first)
    signals_received_at(physics_context, SHAPES, [MOON_CENTRE], [GOLDSTONE], first)

    assert _gap(physics_context, first - 1.0, 0.0) == gap


def test_data_too_short_for_any_tick_says_so() -> None:
    gap = TickCoverageGap("mars", usable_start_unix=10_000.0, usable_end_unix=9_000.0)
    assert describe_tick_coverage_gap(gap) == (
        "the mars data is too short to support this session at any time"
    )


def test_the_light_time_bound_is_the_farthest_two_endpoints_can_be() -> None:
    earth_reach, luna_reach = 42_164.0, 7_000.0
    bound = light_time_bound_s({"earth": earth_reach, "luna": luna_reach})
    separation = body_separation("earth", "luna").farthest_km
    assert bound == pytest.approx((separation + earth_reach + luna_reach) / SPEED_OF_LIGHT_KM_S)
    assert light_time_bound_s({"earth": earth_reach}) == pytest.approx(
        2 * earth_reach / SPEED_OF_LIGHT_KM_S
    )


def test_a_body_pair_with_no_recorded_separation_has_no_bound() -> None:
    with pytest.raises(PhysicsDataError, match="no separation is recorded"):
        light_time_bound_s({"earth": 7_000.0, "sun": 700_000.0})


@pytest.mark.parametrize(
    "session", ["earth-leo-heo-geo-luna-reachability", "earth-luna-dtn", "earth-geo-tdrs"]
)
def test_every_light_time_a_session_has_is_within_its_bound(session: str) -> None:
    """At the session epoch, every pair of the session's endpoints (satellites and stations,
    each direction) has a light time within the session's bound."""
    raw = load_configuration_yaml(Path(f"catalog/nodalarc/sessions/{session}.yaml").read_bytes())
    resolved = resolve_session(raw, catalog=shipped_read_view())
    inputs = build_ome_inputs_from_resolved(resolved)
    bound = coverage_requirement(resolved).light_time_bound_s
    endpoints: list[Endpoint] = list(
        satellite_endpoints(
            satellites=inputs.satellites,
            addressing=inputs.addressing,
            propagator_id=inputs.propagator_id,
            body_frames=inputs.body_frames,
        ).values()
    )
    for station in inputs.gs_file.stations if inputs.gs_file else ():
        fixed = body_fixed_from_geodetic(
            GeoPosition(station.lat_deg, station.lon_deg, station.alt_m / 1000.0),
            inputs.body_frames[station.reference_body],
        )
        endpoints.append(SiteEndpoint(station.reference_body, (fixed[0], fixed[1], fixed[2])))
    senders = [a for a in endpoints for _b in endpoints]
    receivers = [b for _a in endpoints for b in endpoints]
    signals = signals_sent_at(
        inputs.physics, inputs.body_frames, senders, receivers, session_epoch_unix(resolved.time)
    )
    assert float(np.max(signals.light_time_s)) <= bound
