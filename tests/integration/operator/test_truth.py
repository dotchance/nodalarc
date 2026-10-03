"""What NodalArc shows about the running session is what the emulated network does.

The same checks run at the end of every scenario that changes a session (test_sessions,
test_time). Here they run on the session as it is found.
"""

from __future__ import annotations

import pytest

from . import truths
from .harness.client import Operator
from .harness.network import watch

pytestmark = [pytest.mark.integration, pytest.mark.smoke, pytest.mark.timeout(600)]


def test_the_clock_runs_at_the_speed_shown(operator: Operator) -> None:
    assert not truths.clock_runs_at_the_reported_speed(operator)


def test_the_links_shown_are_the_routers_neighbors(operator: Operator) -> None:
    disagreements = truths.links_shown_are_the_routers_neighbors(operator, watch(operator))
    assert not disagreements, "\n".join(disagreements)


def test_the_latency_shown_is_the_delay_packets_get(operator: Operator) -> None:
    disagreements = truths.latency_shown_is_the_delay_packets_get(operator, watch(operator))
    assert not disagreements, "\n".join(disagreements)


def test_the_latency_shown_is_the_light_time_between_the_positions_shown(
    operator: Operator,
) -> None:
    disagreements = truths.latency_shown_is_the_light_time_between_the_positions_shown(
        watch(operator, 14.0)
    )
    assert not disagreements, "\n".join(disagreements)


def test_satellites_move_at_the_speed_their_orbits_require(operator: Operator) -> None:
    disagreements = truths.satellites_move_at_the_speed_their_orbits_require(watch(operator, 14.0))
    assert not disagreements, "\n".join(disagreements)


def test_far_sites_answer_no_sooner_than_light(operator: Operator) -> None:
    disagreements = truths.far_sites_answer_no_sooner_than_light(operator, watch(operator))
    assert not disagreements, "\n".join(disagreements)


def test_every_lan_address_shown_answers_its_router(operator: Operator) -> None:
    disagreements = truths.lan_addresses_shown_answer(operator, watch(operator))
    assert not disagreements, "\n".join(disagreements)


def test_every_node_shown_answers_its_own_terminal(operator: Operator) -> None:
    disagreements = truths.every_node_shown_answers_its_own_terminal(operator, watch(operator))
    assert not disagreements, "\n".join(disagreements)
