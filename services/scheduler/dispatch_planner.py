# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Pure Scheduler dispatch planning helpers."""

from __future__ import annotations

from collections.abc import Callable, Container, Mapping
from dataclasses import dataclass

from scheduler.desired_state import ActiveLinkInfo


@dataclass(frozen=True)
class ReconcileDiff:
    """Set-level difference between actual and desired link state."""

    to_remove: set[tuple[str, str]]
    to_add: set[tuple[str, str]]
    to_update_latency: set[tuple[str, str]]
    # Links OME keeps between the same two nodes on other terminals. Each is in ``to_remove``
    # with the terminals it holds and in ``to_add`` with the terminals OME assigned.
    reassigned: set[tuple[str, str]]

    @property
    def has_changes(self) -> bool:
        return bool(self.to_remove or self.to_add or self.to_update_latency)


def choosing_nodes(
    pair: tuple[str, str],
    *,
    fixed_pairs: Container[tuple[str, str]],
    stating_nodes: Container[str],
) -> tuple[str, ...]:
    """The nodes that choose a link.

    A link whose interfaces the resolver fixed has none: its topology mode fixed the pair,
    and nothing is chosen for it. Otherwise they are the pair's nodes that state scheduling.
    When neither states it, NodalArc chooses the link for both nodes, and both are its
    choosing nodes.
    """
    if pair in fixed_pairs:
        return ()
    stating = tuple(node_id for node_id in pair if node_id in stating_nodes)
    return stating or pair


@dataclass(frozen=True)
class StagedChanges:
    """One reconcile pass's downs and ups, with the order each choosing node needs.

    ``making`` holds the choosing nodes that bring their new links up before their old links
    go down. ``breaking`` holds the choosing nodes whose old links must go down first. A
    choosing node with no new link in the pass is in neither: it has no say in the order.
    """

    # Every changed pair's choosing nodes; none for a link nothing is chosen for.
    choosers: Mapping[tuple[str, str], tuple[str, ...]]
    # Each choosing node's new links in this pass.
    ups_of: Mapping[str, frozenset[tuple[str, str]]]
    making: frozenset[str]
    breaking: frozenset[str]

    def down_goes_first(self, pair: tuple[str, str]) -> bool:
        """Whether a down is sent before the pass's ups: a choosing node of it must break
        first, or none of them is making."""
        nodes = self.choosers[pair]
        return any(node in self.breaking for node in nodes) or not any(
            node in self.making for node in nodes
        )

    def up_follows_downs(self, pair: tuple[str, str]) -> bool:
        """Whether an up belongs to a choosing node that breaks first, so it needs the
        terminals that node's downs free."""
        return any(node in self.breaking for node in self.choosers[pair])

    def down_may_follow(self, pair: tuple[str, str], added: Container[tuple[str, str]]) -> bool:
        """Whether a down that waited for the ups may go: each of its choosing nodes that is
        making has a new link up. Otherwise the old link stays for a later pass."""
        return all(
            any(up in added for up in self.ups_of[node])
            for node in self.choosers[pair]
            if node in self.making
        )


def diff_link_state(
    actual: Mapping[tuple[str, str], ActiveLinkInfo],
    desired: Mapping[tuple[str, str], ActiveLinkInfo],
    *,
    latency_tolerance_ms: float = 1e-9,
) -> ReconcileDiff:
    """Compute add/remove/latency-update sets for a reconcile pass.

    A link is a node pair on two terminals. OME may keep a pair and assign it other
    terminals: it assigns every link anew at a seek. Such a link goes down on the terminals
    it holds and up on the terminals OME assigned. Rejected alternative: comparing pairs
    alone, which left the kernel on the old terminals under a record naming the new ones.
    """
    current_pairs = set(actual.keys())
    desired_pairs = set(desired.keys())
    common_pairs = current_pairs & desired_pairs
    reassigned = {
        pair
        for pair in common_pairs
        if (actual[pair].interface_a, actual[pair].interface_b)
        != (desired[pair].interface_a, desired[pair].interface_b)
    }
    return ReconcileDiff(
        to_remove=(current_pairs - desired_pairs) | reassigned,
        to_add=(desired_pairs - current_pairs) | reassigned,
        reassigned=reassigned,
        to_update_latency={
            pair
            for pair in common_pairs - reassigned
            if (
                abs(actual[pair].latency_a_to_b_ms - desired[pair].latency_a_to_b_ms)
                > latency_tolerance_ms
                or abs(actual[pair].latency_b_to_a_ms - desired[pair].latency_b_to_a_ms)
                > latency_tolerance_ms
                or actual[pair].range_km != desired[pair].range_km
            )
        },
    )


def interface_colliding_downs(
    *,
    to_remove: set[tuple[str, str]],
    to_add: set[tuple[str, str]],
    actual: Mapping[tuple[str, str], ActiveLinkInfo],
    desired: Mapping[tuple[str, str], ActiveLinkInfo],
) -> set[tuple[str, str]]:
    """Release pairs whose kernel interface is reused by a same-pass acquire.

    The OME allocator may hand a terminal freed in one tick straight to the
    successor link in the same tick. Such a handover cannot be
    make-before-break: one kernel interface cannot carry the old and the new
    link at once, and dispatching the acquire first means the deferred
    release tears down the interface state the acquire just created. Every
    release returned here must be dispatched before the acquires.
    """
    acquire_endpoints: set[tuple[str, str]] = set()
    for pair in to_add:
        info = desired[pair]
        acquire_endpoints.add((pair[0], info.interface_a))
        acquire_endpoints.add((pair[1], info.interface_b))

    colliding: set[tuple[str, str]] = set()
    for pair in to_remove:
        info = actual[pair]
        if (pair[0], info.interface_a) in acquire_endpoints or (
            pair[1],
            info.interface_b,
        ) in acquire_endpoints:
            colliding.add(pair)
    return colliding


def stage_changes(
    *,
    to_remove: set[tuple[str, str]],
    to_add: set[tuple[str, str]],
    choosers: Callable[[tuple[str, str]], tuple[str, ...]],
    terminal_capacities: Mapping[str, int],
    active_count: Mapping[str, int],
    forced_break_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> StagedChanges:
    """Decide, per choosing node, whether one reconcile pass makes before it breaks.

    A choosing node breaks first when one of its changed pairs is forced to (its stated
    mode, a terminal an up of the pass needs, an operator or scenario command), or when it
    has no spare terminal for each of its new links, or a new link's other node has none.
    It makes first when it has new links and none of that holds. ``terminal_capacities`` and
    ``active_count`` are per node: the terminals OME may assign there, and the links that
    hold one now.
    """
    pair_choosers = {pair: choosers(pair) for pair in to_remove | to_add}
    downs_of: dict[str, set[tuple[str, str]]] = {}
    ups_of: dict[str, set[tuple[str, str]]] = {}
    for pair in to_remove:
        for node_id in pair_choosers[pair]:
            downs_of.setdefault(node_id, set()).add(pair)
    for pair in to_add:
        for node_id in pair_choosers[pair]:
            ups_of.setdefault(node_id, set()).add(pair)

    def spare(node_id: str) -> int:
        return terminal_capacities[node_id] - active_count.get(node_id, 0)

    making: set[str] = set()
    breaking: set[str] = set()
    for node_id in set(downs_of) | set(ups_of):
        ups = ups_of.get(node_id, set())
        if (downs_of.get(node_id, set()) | ups) & forced_break_pairs:
            breaking.add(node_id)
            continue
        if not ups:
            continue
        others_have_room = all(
            spare(pair[1] if pair[0] == node_id else pair[0]) > 0 for pair in ups
        )
        if spare(node_id) >= len(ups) and others_have_room:
            making.add(node_id)
        else:
            breaking.add(node_id)

    return StagedChanges(
        choosers=pair_choosers,
        ups_of={node_id: frozenset(pairs) for node_id, pairs in ups_of.items()},
        making=frozenset(making),
        breaking=frozenset(breaking),
    )
