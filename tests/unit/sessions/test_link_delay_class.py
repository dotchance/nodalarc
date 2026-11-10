# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Local and long-delay link classes: the delay range each candidate can have, and the class the
resolver gives it against the platform's long-delay bound."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.models.session import session_epoch_unix
from nodalarc.physics import PhysicsDataError
from nodalarc.physics.ephemeris_table import body_separation
from nodalarc.physics.link_delay import LinkEnd, link_delay_range_s, link_distance_range_km
from nodalarc.physics.session import node_link_end, session_node_positions, session_physics
from nodalarc.platform_config import get_platform_config, init_platform_config
from nodalarc.resolve_session import resolve_session
from nodalarc.runtime_support import UnsupportedFeatureError

from tests.catalog_session_fixtures import SHIPPED_ROOT, shipped_read_view

SESSION = SHIPPED_ROOT / "sessions" / "earth-luna-dtn.yaml"
GEO = LinkEnd("earth", 42_164.0)
LUNAR_ELFO = LinkEnd("luna", 1_737.4 + 7_332.0)


def _resolve(path: Path = SESSION) -> ResolvedSession:
    return resolve_session(load_configuration_yaml(path.read_bytes()), catalog=shipped_read_view())


@pytest.fixture
def bound_s() -> Iterator[Callable[[float], None]]:
    """Set the platform's long-delay bound for one test; the shipped value comes back after."""
    shipped = get_platform_config()

    def set_bound(seconds: float) -> None:
        init_platform_config(shipped.model_copy(update={"long_delay_bound_s": seconds}))

    yield set_bound
    init_platform_config(shipped)


def _bodies(resolved: ResolvedSession, pair: tuple[str, str]) -> set[str]:
    nodes = {node.node_id: node for node in resolved.nodes}
    bodies = {facts.body_id: facts for facts in resolved.bodies}
    return {node_link_end(nodes[node_id], bodies).body_id for node_id in pair}


def test_endpoints_of_one_body_are_never_farther_apart_than_their_reaches() -> None:
    assert link_distance_range_km(GEO, LinkEnd("earth", 6_378.137)) == (0.0, 42_164.0 + 6_378.137)


def test_endpoints_of_two_bodies_are_bounded_by_the_bodies_separation() -> None:
    separation = body_separation("earth", "luna")
    reach = GEO.reach_km + LUNAR_ELFO.reach_km

    assert link_distance_range_km(GEO, LUNAR_ELFO) == (
        separation.nearest_km - reach,
        separation.farthest_km + reach,
    )


def test_terminal_range_caps_the_longest_delay_of_a_link_that_can_be_up() -> None:
    nearest_km, _ = link_distance_range_km(GEO, LUNAR_ELFO)
    limit_km = nearest_km + 10_000.0

    shortest, longest = link_delay_range_s(GEO, LUNAR_ELFO, range_limit_km=limit_km)

    assert shortest == nearest_km / SPEED_OF_LIGHT_KM_S
    assert longest == limit_km / SPEED_OF_LIGHT_KM_S


def test_endpoints_that_never_come_within_range_keep_their_geometric_range() -> None:
    nearest_km, farthest_km = link_distance_range_km(GEO, LUNAR_ELFO)

    assert link_delay_range_s(GEO, LUNAR_ELFO, range_limit_km=3_600.0) == (
        nearest_km / SPEED_OF_LIGHT_KM_S,
        farthest_km / SPEED_OF_LIGHT_KM_S,
    )


def test_bodies_with_no_recorded_separation_have_no_range() -> None:
    with pytest.raises(PhysicsDataError, match="no separation is recorded"):
        link_distance_range_km(GEO, LinkEnd("sun", 695_700.0))


@pytest.mark.parametrize("hours", [0.0, 7.3, 101.0, 24.0 * 400.0])
def test_every_candidate_lies_inside_its_distance_range(hours: float) -> None:
    """At several instants, the distance between every candidate's endpoints, from the shared
    physics positions, lies inside the range the classification uses."""
    resolved = _resolve()
    nodes = {node.node_id: node for node in resolved.nodes}
    bodies = {facts.body_id: facts for facts in resolved.bodies}
    positions = session_node_positions(
        resolved, session_physics(resolved), session_epoch_unix(resolved.time) + hours * 3600.0
    )
    for candidate in resolved.link_candidates:
        a, b = (positions[node_id] for node_id in candidate.pair)
        distance = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
        nearest, farthest = link_distance_range_km(
            node_link_end(nodes[candidate.node_a], bodies),
            node_link_end(nodes[candidate.node_b], bodies),
        )
        assert nearest <= distance <= farthest, candidate.pair


def test_at_the_shipped_bound_every_shipped_earth_luna_link_is_local() -> None:
    resolved = _resolve()

    assert resolved.long_delay_bound_s == 10.0
    assert {candidate.delay_class for candidate in resolved.link_candidates} == {"local"}
    assert any(len(_bodies(resolved, c.pair)) == 2 for c in resolved.link_candidates)


def test_at_the_shipped_bound_the_earth_mars_trunk_is_the_one_long_delay_link() -> None:
    """Every Earth-Mars delay is over 185 s and every delay within Mars or within Earth's
    orbit under a second, so the trunk is long-delay and every other candidate is local."""
    resolved = _resolve(SHIPPED_ROOT / "sessions" / "earth-mars-hdtn-relay.yaml")

    classes = {candidate.rule_id: set() for candidate in resolved.link_candidates}
    for candidate in resolved.link_candidates:
        classes[candidate.rule_id].add(candidate.delay_class)
    assert classes == {
        "mars_uhf": {"local"},
        "mars_relay_crosslink": {"local"},
        "earth_mars_trunk": {"long_delay"},
        "earth_optical": {"local"},
    }


def test_a_lower_bound_makes_the_links_between_earth_and_the_moon_long_delay(bound_s) -> None:
    """The bound is a platform setting: changing it reclassifies the same session with no other
    change. Below the shortest Earth-Moon delay every link between the two bodies is long-delay
    and every link within one body stays local."""
    bound_s(0.5)
    resolved = _resolve()

    assert resolved.long_delay_bound_s == 0.5
    classes = {
        candidate.pair: (len(_bodies(resolved, candidate.pair)), candidate.delay_class)
        for candidate in resolved.link_candidates
    }
    assert set(classes.values()) == {(1, "local"), (2, "long_delay")}


def test_a_bound_inside_a_links_delay_range_is_refused(bound_s) -> None:
    bound_s(1.2)

    with pytest.raises(UnsupportedFeatureError) as refusal:
        _resolve()

    (feature, *_) = refusal.value.features
    assert (feature.category, feature.value) == ("link_topology", "delay_spans_long_delay_bound")
    assert "a link's delays must stay on one side of the 1.2 s long-delay bound" in feature.message
    assert "link rule '" in feature.message


def test_a_resolution_under_another_bound_has_another_semantic_digest(bound_s) -> None:
    """Deployment checks compare the resolved semantic digest to prove two resolutions describe
    one runtime. Under a 0.5 s bound the links between Earth and the Moon are long-delay (static
    neighbours, admission hooks, drained contacts); under 10 s they are local. The two
    resolutions differ only in the bound and the classes it gives, and their digests differ."""
    from nodalarc.semantic_projection import (
        resolved_session_semantic_digest,
        resolved_session_semantic_projection,
    )

    local = _resolve()
    bound_s(0.5)
    long_delay = _resolve()

    assert resolved_session_semantic_digest(local) != resolved_session_semantic_digest(long_delay)
    projected = resolved_session_semantic_projection(long_delay)["links"]
    assert projected["long_delay_bound_s"] == 0.5
    assert {candidate["delay_class"] for candidate in projected["candidates"]} == {
        "local",
        "long_delay",
    }
