# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Tests for pure Scheduler dispatch planning helpers."""

from __future__ import annotations

import pytest
from scheduler.desired_state import ActiveLinkInfo
from scheduler.dispatch_planner import choosing_nodes, diff_link_state, stage_changes


def _info(
    latency: float = 3.0,
    range_km: float = 1000.0,
    *,
    b_to_a: float | None = None,
):
    """A link with ``latency`` in both directions, or ``b_to_a`` in the second direction."""
    return ActiveLinkInfo(
        "isl0",
        "isl1",
        latency,
        latency if b_to_a is None else b_to_a,
        assigned_terminals=False,
        range_km=range_km,
    )


def test_diff_link_state_classifies_add_remove_and_latency_update():
    actual = {
        ("sat-a", "sat-b"): _info(latency=3.0, range_km=1000.0),
        ("sat-c", "sat-d"): _info(latency=4.0, range_km=1200.0),
    }
    desired = {
        ("sat-a", "sat-b"): _info(latency=3.0 + 2e-9, range_km=1000.0),
        ("sat-e", "sat-f"): _info(latency=5.0, range_km=1500.0),
    }

    diff = diff_link_state(actual, desired)

    assert diff.to_remove == {("sat-c", "sat-d")}
    assert diff.to_add == {("sat-e", "sat-f")}
    assert diff.to_update_latency == {("sat-a", "sat-b")}
    assert diff.has_changes


def test_diff_link_state_ignores_sub_tolerance_latency_jitter():
    pair = ("sat-a", "sat-b")
    diff = diff_link_state(
        {pair: _info(latency=3.0, range_km=1000.0)},
        {pair: _info(latency=3.0 + 1e-10, range_km=1000.0)},
    )

    assert not diff.has_changes


def _moved(direction: str, step_ms: float) -> ActiveLinkInfo:
    """The (3.0, 4.0) ms link with one direction moved by ``step_ms``."""
    delays = {"a_to_b": 3.0, "b_to_a": 4.0}
    delays[direction] += step_ms
    return _info(latency=delays["a_to_b"], b_to_a=delays["b_to_a"])


@pytest.mark.parametrize("step_ms", [2e-9, -2e-9])
@pytest.mark.parametrize("direction", ["a_to_b", "b_to_a"])
def test_a_change_beyond_tolerance_in_one_direction_alone_is_a_latency_update(direction, step_ms):
    pair = ("sat-a", "sat-b")
    diff = diff_link_state(
        {pair: _info(latency=3.0, b_to_a=4.0)}, {pair: _moved(direction, step_ms)}
    )

    assert diff.to_update_latency == {pair}


@pytest.mark.parametrize("step_ms", [5e-10, -5e-10])
@pytest.mark.parametrize("direction", ["a_to_b", "b_to_a"])
def test_jitter_within_tolerance_in_one_direction_alone_is_no_change(direction, step_ms):
    pair = ("sat-a", "sat-b")
    diff = diff_link_state(
        {pair: _info(latency=3.0, b_to_a=4.0)}, {pair: _moved(direction, step_ms)}
    )

    assert not diff.has_changes


def _stage(to_remove, to_add, *, capacities, active, stating=("gs-a",), fixed=(), forced=()):
    """Stage one pass. ``stating`` are the nodes that state scheduling and ``fixed`` the links
    whose interfaces the resolver fixed."""
    return stage_changes(
        to_remove=set(to_remove),
        to_add=set(to_add),
        choosers=lambda pair: choosing_nodes(
            pair, fixed_pairs=frozenset(fixed), stating_nodes=frozenset(stating)
        ),
        terminal_capacities=capacities,
        active_count=active,
        forced_break_pairs=frozenset(forced),
    )


OLD = ("gs-a", "sat-old")
NEW = ("gs-a", "sat-new")


def test_a_choosing_node_with_a_spare_terminal_makes_before_it_breaks():
    plan = _stage(
        {OLD},
        {NEW},
        capacities={"gs-a": 2, "sat-old": 1, "sat-new": 1},
        active={"gs-a": 1, "sat-old": 1},
    )

    assert plan.making == {"gs-a"}
    assert plan.breaking == set()
    assert not plan.down_goes_first(OLD)
    assert not plan.up_follows_downs(NEW)
    # The old link goes once the new one is up, and stays while it is not.
    assert plan.down_may_follow(OLD, {NEW})
    assert not plan.down_may_follow(OLD, set())


def test_a_choosing_node_without_a_spare_terminal_breaks_first():
    plan = _stage(
        {OLD},
        {NEW},
        capacities={"gs-a": 1, "sat-old": 1, "sat-new": 1},
        active={"gs-a": 1, "sat-old": 1},
    )

    assert plan.making == set()
    assert plan.breaking == {"gs-a"}
    assert plan.down_goes_first(OLD)
    assert plan.up_follows_downs(NEW)


def test_a_new_link_whose_other_node_has_no_spare_terminal_breaks_first():
    plan = _stage(
        {OLD},
        {NEW},
        capacities={"gs-a": 4, "sat-old": 1, "sat-new": 1},
        active={"gs-a": 1, "sat-old": 1, "sat-new": 1},
    )

    assert plan.breaking == {"gs-a"}
    assert plan.down_goes_first(OLD)


def test_one_forced_pair_makes_its_choosing_node_break_first():
    plan = _stage(
        {OLD},
        {NEW},
        capacities={"gs-a": 4, "sat-old": 1, "sat-new": 1},
        active={"gs-a": 1, "sat-old": 1},
        forced={NEW},
    )

    assert plan.making == set()
    assert plan.breaking == {"gs-a"}
    assert plan.down_goes_first(OLD)
    assert plan.up_follows_downs(NEW)


def test_a_link_nothing_is_chosen_for_goes_down_first_and_up_with_the_ups():
    down = ("sat-a", "sat-b")
    up = ("sat-c", "sat-d")
    plan = _stage({down}, {up}, capacities={"gs-a": 1}, active={}, fixed={down, up})

    assert plan.choosers == {down: (), up: ()}
    assert plan.making == set() and plan.breaking == set()
    assert plan.down_goes_first(down)
    assert not plan.up_follows_downs(up)


def test_a_node_nodalarc_chooses_for_makes_before_it_breaks_when_it_has_a_spare_terminal():
    """No node states scheduling, so both nodes of each pair are its choosing nodes. The relay
    moves from one peer to the next. With a spare terminal it makes first: the old link waits
    for the new one, although the old peer has nothing new of its own. With no spare terminal
    it breaks first."""
    old = ("geo-a", "relay")
    new = ("geo-b", "relay")
    spare = _stage(
        {old},
        {new},
        stating=(),
        capacities={"relay": 2, "geo-a": 1, "geo-b": 1},
        active={"relay": 1, "geo-a": 1},
    )
    assert spare.choosers == {old: old, new: new}
    assert spare.making == {"relay", "geo-b"}
    assert spare.breaking == set()
    assert not spare.down_goes_first(old)
    assert not spare.up_follows_downs(new)
    assert spare.down_may_follow(old, {new})

    full = _stage(
        {old},
        {new},
        stating=(),
        capacities={"relay": 1, "geo-a": 1, "geo-b": 1},
        active={"relay": 1, "geo-a": 1},
    )
    assert "relay" in full.breaking
    assert full.down_goes_first(old)
    assert full.up_follows_downs(new)
