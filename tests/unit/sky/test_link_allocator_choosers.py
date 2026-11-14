# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""The link allocator's choosers, checked against reference choosers.

A pair whose nodes state no scheduling is chosen by NodalArc: nearest first, a held link gives
way only to a strictly nearer pair, and a node makes before it breaks when it has a terminal
beyond its cap. Pairs with a stated chooser are placed first. The reference choosers below state
those rules in a few lines each; the allocator must select the same links.

The nodes here are plain ids with terminal pools. None of them has a sky view, so every test of
NodalArc's own choosing is also a test of a choosing node that is not a ground station.
"""

from __future__ import annotations

import itertools
import random

import pytest
from nodalarc.models.ground_policy import (
    HandoverPolicySpec,
    HysteresisParameters,
    SelectionPolicySpec,
)
from ome.link_allocator import LinkAllocationResult, UsablePair, allocate_links
from ome.types import MbbTeardown
from ome.visibility import GroundVisibility

from tests.ome_runtime_fixtures import UniformPairDelays

Pair = tuple[str, str]
RULE = "rule"
POOL = "links"


# ----- Reference choosers ---------------------------------------------------------------------


def default_tick(usable: dict[Pair, float], cap: dict[str, int], held: set[Pair]) -> set[Pair]:
    """One tick of NodalArc's own chooser.

    1. Drop held pairs that are no longer usable.
    2. Take every usable unheld pair once, in ascending (range, pair) order:
       - if both nodes have free cap, link it;
       - otherwise, for each full node, find its held pair with the greatest (range, pair);
         the new pair may take that place only when its range is strictly smaller. It links
         only if every full node can give up such a pair; those pairs are released.
    """
    held = {p for p in held if p in usable}
    load: dict[str, int] = {}
    for a, b in held:
        load[a] = load.get(a, 0) + 1
        load[b] = load.get(b, 0) + 1
    for pair in sorted((p for p in usable if p not in held), key=lambda p: (usable[p], p)):
        rng = usable[pair]
        releases: list[Pair] = []
        ok = True
        for end in pair:
            if load.get(end, 0) < cap[end]:
                continue
            mine = [p for p in held if end in p]
            if not mine:
                ok = False
                break
            worst = max(mine, key=lambda p: (usable[p], p))
            if usable[worst] > rng and worst not in releases:
                releases.append(worst)
            else:
                ok = False
                break
        if not ok:
            continue
        for released in releases:
            held.discard(released)
            for end in released:
                load[end] -= 1
        held.add(pair)
        for end in pair:
            load[end] = load.get(end, 0) + 1
    return held


def stated_then_default(
    usable: dict[Pair, float], cap: dict[str, int], stated_score: dict[Pair, float]
) -> set[Pair]:
    """Stated beats derived, from an empty state: pairs with a stated chooser are placed first,
    in their rank order (score, then pair), within caps at both nodes; NodalArc's own chooser
    then fills what is left, nearest first."""
    held: set[Pair] = set()
    load: dict[str, int] = {}

    def room(node: str) -> bool:
        return load.get(node, 0) < cap[node]

    for pair in sorted(
        (p for p in usable if p in stated_score), key=lambda p: (-stated_score[p], p)
    ):
        if room(pair[0]) and room(pair[1]):
            held.add(pair)
            for node in pair:
                load[node] = load.get(node, 0) + 1
    for pair in sorted((p for p in usable if p not in stated_score), key=lambda p: (usable[p], p)):
        if room(pair[0]) and room(pair[1]):
            held.add(pair)
            for node in pair:
                load[node] = load.get(node, 0) + 1
    return held


# ----- Running the allocator on plain nodes ---------------------------------------------------


def _view(peer: str, score: float) -> GroundVisibility:
    """A stating node's view of one pair. The tests state the score as an elevation."""
    return GroundVisibility(
        sat_id=peer,
        visible=True,
        elevation_deg=score,
        range_km=1000.0,
        remaining_visible_s=None,
        reject_reason="ok",
    )


def _usable(
    ranges: dict[Pair, float],
    *,
    stating: frozenset[str] = frozenset(),
    scores: dict[Pair, float] | None = None,
    rules: dict[Pair, str] | None = None,
    pool_names: dict[tuple[Pair, str], str] | None = None,
) -> list[UsablePair]:
    items = []
    for pair, range_km in ranges.items():
        a, b = pair
        score = (scores or {}).get(pair)
        items.append(
            UsablePair(
                pair=pair,
                rule_id=(rules or {}).get(pair, RULE),
                pool_a=(pool_names or {}).get((pair, a), POOL),
                pool_b=(pool_names or {}).get((pair, b), POOL),
                range_km=range_km,
                view_a=_view(b, score) if a in stating and score is not None else None,
                view_b=_view(a, score) if b in stating and score is not None else None,
            )
        )
    return items


def _allocate(
    usable: list[UsablePair],
    *,
    pools: dict[str, dict[str, tuple[int, ...]]],
    stating: frozenset[str] = frozenset(),
    rule_n: dict[str, int] | None = None,
    previous: LinkAllocationResult | None = None,
    step: int = 0,
    handover_modes: dict[str, str] | None = None,
    mbb_reserve: dict[str, int] | None = None,
    mbb_overlap_ticks: dict[str, int] | None = None,
    tenants: dict[str, str] | None = None,
    priorities: dict[str, int] | None = None,
    successor_abort_policy: str = "hard_release",
    handover_policies: dict[str, HandoverPolicySpec] | None = None,
) -> LinkAllocationResult:
    modes = handover_modes or dict.fromkeys(stating, "bbm")
    return allocate_links(
        step=step,
        usable=usable,
        current_associations=dict(previous.associations) if previous else {},
        pending_teardowns=dict(previous.pending_teardowns) if previous else {},
        terminal_pools=pools,
        stating_nodes=stating,
        rule_n=rule_n or {},
        selection_policies={
            node: SelectionPolicySpec(name="highest-elevation", params={}) for node in stating
        },
        min_elevations=dict.fromkeys(stating, 25.0),
        handover_policies=handover_policies
        or {node: HandoverPolicySpec(name="none", params={}) for node in stating},
        handover_modes=modes,
        mbb_overlap_ticks=mbb_overlap_ticks or dict.fromkeys(stating, 0),
        mbb_reserve=mbb_reserve or dict.fromkeys(stating, 0),
        service_priorities=priorities or dict.fromkeys(stating, 10),
        tenant_ids=tenants or dict.fromkeys(pools, "default"),
        reference_bodies=dict.fromkeys(pools, "earth"),
        ranking_order=("service_priority", "selection_score", "lex_pair"),
        mbb_preemption="off",
        successor_abort_policy=successor_abort_policy,
        cross_tenant_displacement="off",
        bbm_acquire_timeout_ticks=1,
        ignored_capacity_fields=(),
        sim_time_unix=1000.0 + step,
        pair_delays_ms=UniformPairDelays(3.0),
        receive_windows=dict(previous.receive_windows or {}) if previous else {},
    )


def _pools_for_caps(cap: dict[str, int]) -> dict[str, dict[str, tuple[int, ...]]]:
    """One pool per node with exactly its cap in terminals: no node has a terminal to spare."""
    return {node: {POOL: tuple(range(count))} for node, count in cap.items()}


def _unscheduled(result: LinkAllocationResult) -> dict[Pair, object]:
    return {item.pair: item for item in result.unscheduled_pairs}


def _random_case(rnd: random.Random) -> tuple[list[str], dict[str, int], dict[Pair, float]]:
    nodes = [f"n{i}" for i in range(rnd.randint(2, 8))]
    cap = {node: rnd.randint(0, 3) for node in nodes}
    usable = {
        (a, b): rnd.choice([1.0, 2.0, 2.0, 3.0, round(rnd.uniform(0.5, 4.0), 3)])
        for a, b in itertools.combinations(nodes, 2)
        if rnd.random() < 0.7
    }
    return nodes, cap, usable


# ----- NodalArc's own chooser against the reference -------------------------------------------


def test_default_chooser_selects_the_reference_links_over_time():
    """With no terminal to spare, the allocator's links equal the reference chooser's at every
    tick: on moving geometry with equal ranges, and until it settles on still geometry."""
    rnd = random.Random(20261114)
    for _ in range(400):
        nodes, cap, usable = _random_case(rnd)
        pools = _pools_for_caps(cap)
        held: set[Pair] = set()
        previous: LinkAllocationResult | None = None
        moves = 6
        for step in range(moves + len(nodes) * 4):
            result = _allocate(_usable(usable), pools=pools, previous=previous, step=step)
            reference = default_tick(usable, cap, held)
            assert set(result.associations) == reference, (usable, cap, sorted(held))
            assert result.pending_teardowns == {}
            for item in result.unscheduled_pairs:
                assert item.unscheduled_reason in {
                    "not_strictly_nearer",
                    "cap_full",
                    "replaced_by_successor",
                }, item
            settled = step >= moves and reference == held
            held, previous = reference, result
            if settled:
                break
            if step < moves:
                usable = {
                    pair: max(0.1, rng + rnd.choice([-0.3, 0.0, 0.0, 0.3]))
                    for pair, rng in usable.items()
                    if rnd.random() < 0.95
                }
        else:
            pytest.fail(f"no fixed point on still geometry: {usable} {cap}")


def test_stated_pairs_are_placed_before_the_pairs_nodalarc_chooses():
    """From an empty state, the allocator's links equal the reference: every pair with a
    stated chooser first, in rank order, then NodalArc's own choices in what is left."""
    rnd = random.Random(20261115)
    for _ in range(600):
        nodes, cap, usable = _random_case(rnd)
        stating = frozenset(node for node in nodes if rnd.random() < 0.4)
        # A node that states scheduling has at least one terminal, and a pair between two
        # such nodes is a different case, which these scenarios leave out.
        cap = {node: max(1, count) if node in stating else count for node, count in cap.items()}
        usable = {
            pair: rng
            for pair, rng in usable.items()
            if not (pair[0] in stating and pair[1] in stating)
        }
        stated_score = {
            pair: round(rnd.uniform(26.0, 88.0), 3)
            for pair in usable
            if pair[0] in stating or pair[1] in stating
        }
        result = _allocate(
            _usable(usable, stating=stating, scores=stated_score),
            pools=_pools_for_caps(cap),
            stating=stating,
            priorities=dict.fromkeys(stating, 10),
        )
        assert set(result.associations) == stated_then_default(usable, cap, stated_score), (
            usable,
            cap,
            sorted(stating),
            stated_score,
        )


# ----- What each reason names -----------------------------------------------------------------


def test_not_strictly_nearer_names_the_held_link_and_both_ranges():
    pools = _pools_for_caps({"a": 1, "b": 1, "c": 1})
    first = _allocate(_usable({("a", "b"): 5.0}), pools=pools)
    assert set(first.associations) == {("a", "b")}

    second = _allocate(
        _usable({("a", "b"): 5.0, ("a", "c"): 6.0}), pools=pools, previous=first, step=1
    )

    assert set(second.associations) == {("a", "b")}
    refused = _unscheduled(second)[("a", "c")]
    assert refused.unscheduled_reason == "not_strictly_nearer"
    assert refused.incumbent_pair == ("a", "b")
    assert (refused.range_km, refused.incumbent_range_km) == (6.0, 5.0)


def test_a_strictly_nearer_pair_takes_the_farthest_link_which_is_final_for_the_tick():
    pools = _pools_for_caps({"a": 1, "b": 1, "c": 1})
    first = _allocate(_usable({("a", "b"): 5.0}), pools=pools)

    second = _allocate(
        _usable({("a", "b"): 5.0, ("a", "c"): 4.0}), pools=pools, previous=first, step=1
    )

    assert set(second.associations) == {("a", "c")}
    released = _unscheduled(second)[("a", "b")]
    assert released.unscheduled_reason == "replaced_by_successor"
    assert released.incumbent_pair == ("a", "c")
    # The node has one terminal, so it breaks before it makes and turns that terminal to
    # the new peer while the old peer's last signals are still arriving.
    assert [event.category for event in second.allocation_events] == [
        "bbm_gap",
        "retargeted_before_last_arrival",
    ]


def test_cap_full_names_the_node_its_cap_and_the_links_it_holds():
    """A node with no terminal for the pair holds its cap of 0."""
    pools = {"a": {POOL: (0,)}, "b": {POOL: ()}}

    result = _allocate(_usable({("a", "b"): 5.0}), pools=pools)

    assert result.associations == {}
    refused = _unscheduled(result)[("a", "b")]
    assert refused.unscheduled_reason == "cap_full"
    assert (refused.node_id, refused.cap, refused.held_pairs) == ("b", 0, ())
    assert refused.capacity_constraint == f"b.{POOL}"


def test_a_default_pair_never_takes_a_stated_link():
    stating = frozenset({"s"})
    pools = _pools_for_caps({"s": 1, "x": 1, "y": 1})
    first = _allocate(
        _usable({("s", "x"): 9.0}, stating=stating, scores={("s", "x"): 40.0}),
        pools=pools,
        stating=stating,
    )
    assert set(first.associations) == {("s", "x")}

    second = _allocate(
        _usable({("s", "x"): 9.0, ("x", "y"): 1.0}, stating=stating, scores={("s", "x"): 40.0}),
        pools=pools,
        stating=stating,
        previous=first,
        step=1,
    )

    assert set(second.associations) == {("s", "x")}
    refused = _unscheduled(second)[("x", "y")]
    assert refused.unscheduled_reason == "taken_by_stated_choice"
    assert (refused.node_id, refused.incumbent_pair) == ("x", ("s", "x"))
    assert refused.capacity_constraint == f"x.{POOL}"


def test_a_stated_pair_takes_the_room_of_a_link_nodalarc_chose():
    stating = frozenset({"s"})
    pools = _pools_for_caps({"s": 1, "x": 1, "y": 1})
    first = _allocate(_usable({("x", "y"): 1.0}), pools=pools, stating=stating)
    assert set(first.associations) == {("x", "y")}

    second = _allocate(
        _usable({("s", "x"): 9.0, ("x", "y"): 1.0}, stating=stating, scores={("s", "x"): 40.0}),
        pools=pools,
        stating=stating,
        previous=first,
        step=1,
    )

    assert set(second.associations) == {("s", "x")}
    released = _unscheduled(second)[("x", "y")]
    assert released.unscheduled_reason == "taken_by_stated_choice"
    assert (released.node_id, released.incumbent_pair) == ("x", ("s", "x"))


def test_a_stated_pair_that_links_on_a_second_pass_still_comes_before_nodalarc_choices():
    """Stations A and B state scheduling. B's pair to s is refused in the first pass, because
    A, of another tenant, holds s. A then moves to t, and B's pair links in the second pass.
    The pair (s, y) that NodalArc chooses is considered only after that, so it finds s taken
    and the held link (y, z) stays."""
    stating = frozenset({"gs-A", "gs-B"})
    pools = _pools_for_caps({"gs-A": 1, "gs-B": 1, "s": 1, "t": 1, "y": 1, "z": 1})
    tenants = dict.fromkeys(pools, "tenant-a") | {"gs-B": "tenant-b"}
    priorities = {"gs-A": 10, "gs-B": 1}
    first = _allocate(
        _usable(
            {("gs-A", "s"): 9.0, ("y", "z"): 5.0},
            stating=stating,
            scores={("gs-A", "s"): 40.0},
        ),
        pools=pools,
        stating=stating,
        tenants=tenants,
        priorities=priorities,
    )
    assert set(first.associations) == {("gs-A", "s"), ("y", "z")}

    second = _allocate(
        _usable(
            {
                ("gs-A", "s"): 9.0,
                ("gs-A", "t"): 9.0,
                ("gs-B", "s"): 9.0,
                ("s", "y"): 1.0,
                ("y", "z"): 5.0,
            },
            stating=stating,
            scores={("gs-A", "s"): 40.0, ("gs-A", "t"): 80.0, ("gs-B", "s"): 60.0},
        ),
        pools=pools,
        stating=stating,
        tenants=tenants,
        priorities=priorities,
        previous=first,
        step=1,
    )

    assert set(second.associations) == {("gs-A", "t"), ("gs-B", "s"), ("y", "z")}
    assert _unscheduled(second)[("s", "y")].unscheduled_reason == "taken_by_stated_choice"


# ----- A rule's n, and a node with a terminal to spare ----------------------------------------


def test_a_node_cap_is_counted_per_rule_on_a_pool_two_rules_share():
    """A relay node with four terminals is on two rules, each stating n: 1. It holds one link
    of each rule: the nearest pair of the first rule and the one pair of the second."""
    pools = {
        "geo": {POOL: (0, 1, 2, 3)},
        "leo-1": {POOL: (0,)},
        "leo-2": {POOL: (0,)},
        "relay": {POOL: (0, 1)},
    }
    rules = {
        ("geo", "leo-1"): "leo_to_geo",
        ("geo", "leo-2"): "leo_to_geo",
        ("geo", "relay"): "geo_to_luna",
    }

    result = _allocate(
        _usable(
            {("geo", "leo-1"): 30.0, ("geo", "leo-2"): 20.0, ("geo", "relay"): 380.0},
            rules=rules,
        ),
        pools=pools,
        rule_n={"leo_to_geo": 1, "geo_to_luna": 1},
    )

    assert set(result.associations) == {("geo", "leo-2"), ("geo", "relay")}
    refused = _unscheduled(result)[("geo", "leo-1")]
    assert refused.unscheduled_reason == "not_strictly_nearer"
    assert refused.incumbent_pair == ("geo", "leo-2")


def _relay_pools() -> dict[str, dict[str, tuple[int, ...]]]:
    """A relay with two terminals on a rule that states n: 1, and three peers with one each."""
    return {"g1": {POOL: (0,)}, "g2": {POOL: (0,)}, "g3": {POOL: (0,)}, "relay": {POOL: (0, 1)}}


def test_a_node_with_a_spare_terminal_brings_the_new_link_up_before_the_old_one_down():
    pools = _relay_pools()
    rule_n = {RULE: 1}
    first = _allocate(_usable({("g1", "relay"): 10.0}), pools=pools, rule_n=rule_n)
    assert first.associations == {("g1", "relay"): (0, 0)}

    # g2 is strictly nearer. The relay has a terminal beyond its cap of 1, so it schedules
    # the new link and keeps the old one. It takes no other link meanwhile: g3, nearer still
    # but considered first, started the handover, and g2 waits.
    second = _allocate(
        _usable({("g1", "relay"): 10.0, ("g2", "relay"): 5.0, ("g3", "relay"): 1.0}),
        pools=pools,
        rule_n=rule_n,
        previous=first,
        step=1,
    )
    assert set(second.associations) == {("g1", "relay"), ("g3", "relay")}
    assert second.pending_teardowns == {
        ("g1", "relay"): MbbTeardown(start_step=1, successor_pair=("g3", "relay"))
    }
    assert [event.category for event in second.allocation_events] == ["mbb_overlap_started"]
    waiting = _unscheduled(second)[("g2", "relay")]
    assert waiting.unscheduled_reason == "mbb_overlap_locked"
    assert waiting.incumbent_pair == ("g1", "relay")

    # One tick later the old link is released.
    third = _allocate(
        _usable({("g1", "relay"): 10.0, ("g2", "relay"): 5.0, ("g3", "relay"): 1.0}),
        pools=pools,
        rule_n=rule_n,
        previous=second,
        step=2,
    )
    assert set(third.associations) == {("g3", "relay")}
    assert third.pending_teardowns == {}
    assert _unscheduled(third)[("g1", "relay")].unscheduled_reason == "replaced_by_successor"
    assert [event.category for event in third.lifecycle_events] == ["teardown_completed"]


def test_a_link_in_a_handover_cannot_be_taken_at_its_other_end():
    """While the relay hands over to g3, g3 holds its cap with a link that cannot be taken:
    a later pair at g3 is refused for capacity, naming g3 and that link."""
    pools = _relay_pools() | {"x": {POOL: (0,)}}
    rule_n = {RULE: 1}
    first = _allocate(_usable({("g1", "relay"): 10.0}), pools=pools, rule_n=rule_n)

    second = _allocate(
        _usable({("g1", "relay"): 10.0, ("g3", "relay"): 1.0, ("g3", "x"): 0.5}),
        pools=pools,
        rule_n=rule_n,
        previous=first,
        step=1,
    )
    # (g3, x) is nearer and is considered first, so it links, and the relay's pair to g3 is
    # then not strictly nearer than g3's link.
    assert set(second.associations) == {("g1", "relay"), ("g3", "x")}

    third = _allocate(
        _usable({("g1", "relay"): 10.0, ("g3", "relay"): 1.0, ("g3", "x"): 2.0}),
        pools=pools,
        rule_n=rule_n,
        previous=first,
        step=1,
    )
    # Here the relay's pair to g3 is nearest: it starts the handover, and (g3, x) then meets
    # g3 full with a link in a handover.
    assert set(third.associations) == {("g1", "relay"), ("g3", "relay")}
    refused = _unscheduled(third)[("g3", "x")]
    assert refused.unscheduled_reason == "cap_full"
    assert (refused.node_id, refused.cap, refused.held_pairs) == ("g3", 1, (("g3", "relay"),))


def test_a_new_link_lost_during_the_overlap_releases_both_links_for_one_tick():
    pools = _relay_pools()
    rule_n = {RULE: 1}
    first = _allocate(_usable({("g1", "relay"): 10.0}), pools=pools, rule_n=rule_n)
    second = _allocate(
        _usable({("g1", "relay"): 10.0, ("g2", "relay"): 5.0}),
        pools=pools,
        rule_n=rule_n,
        previous=first,
        step=1,
    )
    assert set(second.pending_teardowns) == {("g1", "relay")}

    # The new link stops being usable during the overlap. Hard release drops both.
    third = _allocate(
        _usable({("g1", "relay"): 10.0}), pools=pools, rule_n=rule_n, previous=second, step=2
    )
    assert third.associations == {}
    assert third.pending_teardowns == {}
    assert _unscheduled(third)[("g1", "relay")].unscheduled_reason == "successor_aborted"
    assert "successor_aborted" in [event.category for event in third.allocation_events]

    # The old link comes back on the next tick.
    fourth = _allocate(
        _usable({("g1", "relay"): 10.0}), pools=pools, rule_n=rule_n, previous=third, step=3
    )
    assert set(fourth.associations) == {("g1", "relay")}


def test_n_plus_a_stated_reserve_above_the_pool_is_refused():
    stating = frozenset({"s"})
    pools = {"s": {POOL: (0, 1)}, "x": {POOL: (0,)}}

    with pytest.raises(ValueError, match="together they exceed"):
        _allocate(
            _usable({("s", "x"): 9.0}, stating=stating, scores={("s", "x"): 40.0}),
            pools=pools,
            stating=stating,
            rule_n={RULE: 2},
            handover_modes={"s": "mbb"},
            mbb_reserve={"s": 1},
            mbb_overlap_ticks={"s": 3},
        )


# ----- Pairs both of whose nodes state scheduling ---------------------------------------------


def mutual_choice(
    usable: dict[Pair, float], cap: dict[str, int], score: dict[str, dict[Pair, float]]
) -> set[Pair]:
    """Both nodes state scheduling: each node takes its top-cap usable candidates by its own
    score, independently, with ties broken by node pair; a pair links only when both took it."""
    picks: dict[str, set[Pair]] = {}
    for node in {node for pair in usable for node in pair}:
        mine = sorted((p for p in usable if node in p), key=lambda p: (-score[node][p], p))
        picks[node] = set(mine[: cap[node]])
    return {p for p in usable if p in picks[p[0]] and p in picks[p[1]]}


def _first_to_decline(
    pair: Pair, usable: dict[Pair, float], cap: dict[str, int], score: dict[str, dict[Pair, float]]
) -> str:
    """The first node of a pair, in node-pair order, that did not take it."""
    for node in pair:
        mine = sorted((p for p in usable if node in p), key=lambda p: (-score[node][p], p))
        if pair not in mine[: cap[node]]:
            return node
    raise AssertionError(f"{pair} was taken by both of its nodes")


def _usable_by_both(
    usable: dict[Pair, float], score: dict[str, dict[Pair, float]]
) -> list[UsablePair]:
    """Usable pairs whose two nodes both state scheduling: each node has its own view."""
    return [
        UsablePair(
            pair=pair,
            rule_id=RULE,
            pool_a=POOL,
            pool_b=POOL,
            range_km=range_km,
            view_a=_view(pair[1], score[pair[0]][pair]),
            view_b=_view(pair[0], score[pair[1]][pair]),
        )
        for pair, range_km in usable.items()
    ]


def test_a_pair_two_nodes_choose_links_only_when_both_take_it():
    """Every node states scheduling. With no terminal to spare and no hysteresis, the links
    equal the reference at every tick of moving scores, ties included."""
    rnd = random.Random(20261116)
    for _ in range(400):
        nodes, cap, usable = _random_case(rnd)
        cap = {node: max(1, count) for node, count in cap.items()}
        stating = frozenset(nodes)
        pools = _pools_for_caps(cap)
        previous: LinkAllocationResult | None = None
        for step in range(6):
            score = {
                node: {pair: round(rnd.uniform(26.0, 88.0), 0) for pair in usable if node in pair}
                for node in nodes
            }
            result = _allocate(
                _usable_by_both(usable, score),
                pools=pools,
                stating=stating,
                previous=previous,
                step=step,
            )
            assert set(result.associations) == mutual_choice(usable, cap, score), (
                usable,
                cap,
                score,
            )
            for item in result.unscheduled_pairs:
                assert item.unscheduled_reason in {"not_mutual", "replaced_by_successor"}, item
                if item.unscheduled_reason == "not_mutual":
                    assert item.node_id == _first_to_decline(item.pair, usable, cap, score)
            previous = result
            usable = {pair: rng for pair, rng in usable.items() if rnd.random() < 0.95}


def _hysteresis() -> HandoverPolicySpec:
    return HandoverPolicySpec(
        name="hysteresis",
        params=HysteresisParameters(discount_factor=1.15, mask_fade_range_deg=5.0).model_dump(),
    )


@pytest.mark.parametrize(
    ("policy_of_a", "expected_links", "declined_pair"),
    [
        ("hysteresis", {("a", "b")}, ("a", "c")),
        ("none", {("a", "c")}, None),
    ],
)
def test_a_held_link_ranks_by_its_nodes_handover_policy(policy_of_a, expected_links, declined_pair):
    """Node a holds its link to b, which it scores 40. A new pair to c scores 44 at a. Under
    hysteresis a compares the held link at 40 x 1.15 = 46 and keeps it; under none it compares
    it at 40 and takes c."""
    stating = frozenset({"a", "b", "c"})
    pools = _pools_for_caps({"a": 1, "b": 1, "c": 1})
    policies = {node: HandoverPolicySpec(name="none", params={}) for node in stating}
    if policy_of_a == "hysteresis":
        policies["a"] = _hysteresis()
    score = {
        "a": {("a", "b"): 40.0, ("a", "c"): 44.0},
        "b": {("a", "b"): 40.0},
        "c": {("a", "c"): 50.0},
    }
    first = _allocate(
        _usable_by_both({("a", "b"): 5.0}, score),
        pools=pools,
        stating=stating,
        handover_policies=policies,
    )
    assert set(first.associations) == {("a", "b")}

    second = _allocate(
        _usable_by_both({("a", "b"): 5.0, ("a", "c"): 5.0}, score),
        pools=pools,
        stating=stating,
        handover_policies=policies,
        previous=first,
        step=1,
    )

    assert set(second.associations) == expected_links
    if declined_pair is not None:
        declined = _unscheduled(second)[declined_pair]
        assert declined.unscheduled_reason == "not_mutual"
        assert declined.node_id == "a"


def test_one_cap_shared_by_pairs_chosen_alone_and_pairs_chosen_together_is_refused():
    """Node s chooses its pair to x alone, because x states nothing, and its pair to t together
    with t. Both draw on one pool under one rule, and nothing states how they would share s's
    cap."""
    stating = frozenset({"s", "t"})
    pools = _pools_for_caps({"s": 2, "t": 1, "x": 1})
    usable = [
        UsablePair(
            pair=("s", "t"),
            rule_id=RULE,
            pool_a=POOL,
            pool_b=POOL,
            range_km=5.0,
            view_a=_view("t", 40.0),
            view_b=_view("s", 40.0),
        ),
        UsablePair(
            pair=("s", "x"),
            rule_id=RULE,
            pool_a=POOL,
            pool_b=POOL,
            range_km=5.0,
            view_a=_view("x", 40.0),
            view_b=None,
        ),
    ]

    with pytest.raises(ValueError, match="both alone and together"):
        _allocate(usable, pools=pools, stating=stating)


# ----- Cases an independent review asked for ---------------------------------------------------


def test_nodalarc_chooses_only_after_every_stated_pair_is_placed():
    """The pair (s, y) is nearer than the held link (y, z) and s is free when the tick starts.
    The stated pair to s is placed first, so (s, y) finds s taken and (y, z) stays. Choosing
    (s, y) before the stated pair would have released (y, z) for nothing."""
    stating = frozenset({"gs-B"})
    pools = _pools_for_caps({"gs-B": 1, "s": 1, "y": 1, "z": 1})
    first = _allocate(_usable({("y", "z"): 5.0}), pools=pools, stating=stating)
    assert set(first.associations) == {("y", "z")}

    second = _allocate(
        _usable(
            {("gs-B", "s"): 9.0, ("s", "y"): 1.0, ("y", "z"): 5.0},
            stating=stating,
            scores={("gs-B", "s"): 60.0},
        ),
        pools=pools,
        stating=stating,
        previous=first,
        step=1,
    )

    assert set(second.associations) == {("gs-B", "s"), ("y", "z")}
    refused = _unscheduled(second)[("s", "y")]
    assert (refused.unscheduled_reason, refused.node_id) == ("taken_by_stated_choice", "s")


def test_a_stated_pair_takes_a_link_nodalarc_chose_whatever_its_tenant():
    stating = frozenset({"gs"})
    pools = _pools_for_caps({"gs": 1, "peer": 1, "sat": 1})
    tenants = {"gs": "tenant-a", "peer": "tenant-b", "sat": "tenant-b"}
    first = _allocate(
        _usable({("peer", "sat"): 1.0}), pools=pools, stating=stating, tenants=tenants
    )
    assert set(first.associations) == {("peer", "sat")}

    second = _allocate(
        _usable(
            {("gs", "sat"): 9.0, ("peer", "sat"): 1.0},
            stating=stating,
            scores={("gs", "sat"): 40.0},
        ),
        pools=pools,
        stating=stating,
        tenants=tenants,
        previous=first,
        step=1,
    )

    assert set(second.associations) == {("gs", "sat")}
    assert _unscheduled(second)[("peer", "sat")].unscheduled_reason == "taken_by_stated_choice"


def test_a_node_whose_spare_terminal_is_in_use_takes_the_old_link_down_first():
    """The hub has two terminals. Its rule A states n: 1, and a link of rule B uses the second
    terminal, so the hub has none to spare. A strictly nearer pair of rule A still replaces
    the held one, at once. The event about the released link carries that link's own tenant."""
    pools = {
        "a-far": {POOL: (0,)},
        "a-near": {POOL: (0,)},
        "b-peer": {POOL: (0,)},
        "hub": {POOL: (0, 1)},
    }
    tenants = dict.fromkeys(pools, "default") | {"a-far": "tenant-far", "a-near": "tenant-near"}
    rules = {("a-far", "hub"): "A", ("a-near", "hub"): "A", ("b-peer", "hub"): "B"}
    first = _allocate(
        _usable({("a-far", "hub"): 500.0, ("b-peer", "hub"): 50.0}, rules=rules),
        pools=pools,
        rule_n={"A": 1},
        tenants=tenants,
    )
    assert set(first.associations) == {("a-far", "hub"), ("b-peer", "hub")}

    second = _allocate(
        _usable(
            {("a-far", "hub"): 500.0, ("a-near", "hub"): 100.0, ("b-peer", "hub"): 50.0},
            rules=rules,
        ),
        pools=pools,
        rule_n={"A": 1},
        tenants=tenants,
        previous=first,
        step=1,
    )

    assert set(second.associations) == {("a-near", "hub"), ("b-peer", "hub")}
    assert second.pending_teardowns == {}
    assert _unscheduled(second)[("a-far", "hub")].unscheduled_reason == "replaced_by_successor"
    gap = next(event for event in second.allocation_events if event.category == "bbm_gap")
    assert (gap.pair, gap.tenant_id) == (("a-far", "hub"), "tenant-far")


def test_a_handover_no_node_states_follows_the_sessions_successor_abort_value():
    """With the session's value soft_retain, a relay keeps its old link when the new one is
    lost during the overlap."""
    pools = _relay_pools()
    rule_n = {RULE: 1}
    first = _allocate(_usable({("g1", "relay"): 10.0}), pools=pools, rule_n=rule_n)
    second = _allocate(
        _usable({("g1", "relay"): 10.0, ("g2", "relay"): 5.0}),
        pools=pools,
        rule_n=rule_n,
        previous=first,
        step=1,
        successor_abort_policy="soft_retain",
    )
    assert set(second.pending_teardowns) == {("g1", "relay")}

    third = _allocate(
        _usable({("g1", "relay"): 10.0}),
        pools=pools,
        rule_n=rule_n,
        previous=second,
        step=2,
        successor_abort_policy="soft_retain",
    )

    assert set(third.associations) == {("g1", "relay")}
    assert third.pending_teardowns == {}


def test_cap_full_names_the_pool_size_when_the_pool_binds_before_the_rule_cap():
    """The rule states n: 2 and node b has no terminal: the limit b holds is 0."""
    pools = {"a": {POOL: (0, 1)}, "b": {POOL: ()}}

    result = _allocate(_usable({("a", "b"): 5.0}), pools=pools, rule_n={RULE: 2})

    refused = _unscheduled(result)[("a", "b")]
    assert (refused.unscheduled_reason, refused.node_id, refused.cap) == ("cap_full", "b", 0)


def test_an_association_orders_its_two_indices_by_the_lead_node():
    """The lead is the one node that states scheduling, and the first node in node-pair order
    when neither or both do."""
    pools = {"a": {POOL: (5,)}, "b": {POOL: (7,)}}
    neither = _allocate(_usable({("a", "b"): 5.0}), pools=pools)
    assert neither.associations == {("a", "b"): (5, 7)}

    second_states = _allocate(
        _usable({("a", "b"): 5.0}, stating=frozenset({"b"}), scores={("a", "b"): 40.0}),
        pools=pools,
        stating=frozenset({"b"}),
    )
    assert second_states.associations == {("a", "b"): (7, 5)}

    both = frozenset({"a", "b"})
    score = {"a": {("a", "b"): 40.0}, "b": {("a", "b"): 40.0}}
    both_state = _allocate(_usable_by_both({("a", "b"): 5.0}, score), pools=pools, stating=both)
    assert both_state.associations == {("a", "b"): (5, 7)}


def test_a_link_its_two_nodes_no_longer_both_take_is_released_and_says_why():
    """Node a now ranks c first, and c ranks d first: (a, b) is no longer taken by a, and
    (a, c) is not taken by c. The held link is released, its terminals keep receiving, and the
    release cause says the link was no longer chosen by both nodes."""
    stating = frozenset({"a", "b", "c", "d"})
    pools = _pools_for_caps({"a": 1, "b": 1, "c": 1, "d": 1})
    before = {"a": {("a", "b"): 40.0}, "b": {("a", "b"): 40.0}}
    first = _allocate(_usable_by_both({("a", "b"): 5.0}, before), pools=pools, stating=stating)
    assert set(first.associations) == {("a", "b")}

    after = {
        "a": {("a", "b"): 30.0, ("a", "c"): 80.0},
        "b": {("a", "b"): 40.0},
        "c": {("a", "c"): 30.0, ("c", "d"): 80.0},
        "d": {("c", "d"): 40.0},
    }
    second = _allocate(
        _usable_by_both({("a", "b"): 5.0, ("a", "c"): 5.0, ("c", "d"): 5.0}, after),
        pools=pools,
        stating=stating,
        previous=first,
        step=1,
    )

    assert set(second.associations) == {("c", "d")}
    refused = _unscheduled(second)
    assert (refused[("a", "b")].unscheduled_reason, refused[("a", "b")].node_id) == (
        "not_mutual",
        "a",
    )
    assert (refused[("a", "c")].unscheduled_reason, refused[("a", "c")].node_id) == (
        "not_mutual",
        "c",
    )
    assert second.receive_windows[("a", 0)].released_by == "not_mutual"
    assert second.receive_windows[("b", 0)].released_by == "not_mutual"


def test_a_stating_node_hands_a_pair_two_nodes_choose_over_by_its_own_mode():
    """Node a states make before break with one reserved terminal. When it takes c in place of
    b, it keeps its link to b for its stated overlap of one tick."""
    stating = frozenset({"a", "b", "c"})
    pools = {"a": {POOL: (0, 1)}, "b": {POOL: (0,)}, "c": {POOL: (0,)}}
    modes = {"a": "mbb", "b": "bbm", "c": "bbm"}
    reserve = {"a": 1, "b": 0, "c": 0}
    overlap = {"a": 1, "b": 0, "c": 0}
    before = {"a": {("a", "b"): 40.0}, "b": {("a", "b"): 40.0}}
    first = _allocate(
        _usable_by_both({("a", "b"): 5.0}, before),
        pools=pools,
        stating=stating,
        handover_modes=modes,
        mbb_reserve=reserve,
        mbb_overlap_ticks=overlap,
    )
    assert set(first.associations) == {("a", "b")}

    after = {
        "a": {("a", "b"): 30.0, ("a", "c"): 80.0},
        "b": {("a", "b"): 40.0},
        "c": {("a", "c"): 50.0},
    }
    usable = {("a", "b"): 5.0, ("a", "c"): 5.0}
    second = _allocate(
        _usable_by_both(usable, after),
        pools=pools,
        stating=stating,
        handover_modes=modes,
        mbb_reserve=reserve,
        mbb_overlap_ticks=overlap,
        previous=first,
        step=1,
    )
    assert set(second.associations) == {("a", "b"), ("a", "c")}
    assert second.pending_teardowns == {
        ("a", "b"): MbbTeardown(start_step=1, successor_pair=("a", "c"))
    }

    third = _allocate(
        _usable_by_both(usable, after),
        pools=pools,
        stating=stating,
        handover_modes=modes,
        mbb_reserve=reserve,
        mbb_overlap_ticks=overlap,
        previous=second,
        step=2,
    )
    assert set(third.associations) == {("a", "c")}
    assert third.pending_teardowns == {}


def test_a_rule_that_names_two_pools_at_one_node_is_refused():
    usable = [
        UsablePair(("a", "hub"), RULE, POOL, "east", 5.0, None, None),
        UsablePair(("b", "hub"), RULE, POOL, "west", 5.0, None, None),
    ]
    pools = {"a": {POOL: (0,)}, "b": {POOL: (0,)}, "hub": {"east": (0,), "west": (1,)}}

    with pytest.raises(ValueError, match="names one pool at each node"):
        _allocate(usable, pools=pools)


def test_n_below_one_is_refused():
    with pytest.raises(ValueError, match="n must be at least 1"):
        _allocate(
            _usable({("a", "b"): 5.0}), pools=_pools_for_caps({"a": 1, "b": 1}), rule_n={RULE: 0}
        )


def test_n_plus_a_stated_reserve_that_fills_the_pool_exactly_is_accepted():
    stating = frozenset({"s"})
    pools = {"s": {POOL: (0, 1)}, "x": {POOL: (0,)}}

    result = _allocate(
        _usable({("s", "x"): 9.0}, stating=stating, scores={("s", "x"): 40.0}),
        pools=pools,
        stating=stating,
        rule_n={RULE: 1},
        handover_modes={"s": "mbb"},
        mbb_reserve={"s": 1},
        mbb_overlap_ticks={"s": 3},
    )

    assert set(result.associations) == {("s", "x")}
