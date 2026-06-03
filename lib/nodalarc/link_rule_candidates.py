# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Link-rule candidate generation.

This module turns resolved ``link_rules`` into the static candidate universe
that OME is allowed to evaluate. It does not decide physical visibility; it only
enforces the declaration/topology boundary:

    declaration -> physics -> topology -> allocation -> actuation

No declaration means no candidate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from nodalarc.models.link_rules import (
    ExplicitPairsTopology,
    NearestNTopology,
    NearestVisibleTopology,
    VisibleCandidatesTopology,
)
from nodalarc.models.resolved_session import ResolvedLinkRule, ResolvedNode, ResolvedSession


@dataclass(frozen=True)
class DeclaredLinkCandidate:
    """One declared node-pair candidate before physics evaluation."""

    pair: tuple[str, str]
    rule_id: str
    kind: str
    terminal_role: str
    terminal_medium: str | None
    topology_mode: str
    priority: int
    endpoint_segments: tuple[str, str]


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _node_map(resolved: ResolvedSession) -> dict[str, ResolvedNode]:
    return {node.node_id: node for node in resolved.nodes}


def _rank_key(node: ResolvedNode) -> tuple[str, str]:
    return (node.segment_id, node.local_node_id)


def _explicit_runtime_pairs(
    rule: ResolvedLinkRule,
    *,
    local_to_runtime: Mapping[tuple[str, str], str],
) -> tuple[tuple[str, str], ...]:
    topology = rule.topology
    if not isinstance(topology, ExplicitPairsTopology):
        raise TypeError("explicit runtime pairs requested for non-explicit topology")
    left_segment = rule.endpoints[0].segment_id
    right_segment = rule.endpoints[1].segment_id
    pairs: list[tuple[str, str]] = []
    for item in topology.pairs:
        a = local_to_runtime.get((left_segment, item.a))
        b = local_to_runtime.get((right_segment, item.b))
        if a is None or b is None:
            a = local_to_runtime.get((right_segment, item.a))
            b = local_to_runtime.get((left_segment, item.b))
        if a is None or b is None:
            raise ValueError(
                f"link_rule {rule.rule_id!r} explicit pair {item.a!r}<->{item.b!r} "
                "does not resolve inside the rule endpoint segments"
            )
        pairs.append(_pair(a, b))
    return tuple(pairs)


def _cross_product_pairs(rule: ResolvedLinkRule) -> tuple[tuple[str, str], ...]:
    left, right = rule.endpoints
    return tuple(_pair(a, b) for a in left.node_ids for b in right.node_ids)


def _apply_static_topology(
    rule: ResolvedLinkRule,
    pairs: Iterable[tuple[str, str]],
    *,
    node_by_id: Mapping[str, ResolvedNode],
    pair_rank: Mapping[tuple[str, str], float] | None = None,
) -> tuple[tuple[tuple[str, str], int], ...]:
    """Apply topology constraints that are independent of per-tick visibility."""
    unique = sorted(
        set(pairs), key=lambda p: (_rank_key(node_by_id[p[0]]), _rank_key(node_by_id[p[1]]))
    )
    topology = rule.topology
    if isinstance(topology, VisibleCandidatesTopology):
        return tuple((pair, idx) for idx, pair in enumerate(unique))
    if isinstance(topology, NearestVisibleTopology):
        raise ValueError(
            f"link_rule {rule.rule_id!r} uses topology.mode='nearest_visible', which is "
            "a dynamic per-tick topology. M2 only supports static declared graphs; use "
            "visible_candidates, nearest_n, or explicit_pairs."
        )
    if isinstance(topology, NearestNTopology):
        if pair_rank is None:
            raise ValueError(
                f"link_rule {rule.rule_id!r} uses topology.mode='nearest_n', but no "
                "physical pair ranking was provided"
            )
        for pair in unique:
            if pair not in pair_rank:
                raise ValueError(
                    f"link_rule {rule.rule_id!r} nearest_n pair {pair} has no physical rank"
                )
        degree: dict[str, int] = defaultdict(int)
        ordered: list[tuple[str, str]] = []
        for pair in sorted(
            unique,
            key=lambda p: (
                pair_rank[p],
                _rank_key(node_by_id[p[0]]),
                _rank_key(node_by_id[p[1]]),
                p,
            ),
        ):
            if degree[pair[0]] >= topology.n or degree[pair[1]] >= topology.n:
                continue
            ordered.append(pair)
            degree[pair[0]] += 1
            degree[pair[1]] += 1
        ordered.sort(key=lambda p: (_rank_key(node_by_id[p[0]]), _rank_key(node_by_id[p[1]]), p))
        return tuple((pair, idx) for idx, pair in enumerate(ordered))
    if isinstance(topology, ExplicitPairsTopology):
        return tuple((pair, idx) for idx, pair in enumerate(unique))
    raise TypeError(f"unsupported link topology {type(topology)!r}")


def generate_declared_link_candidates(
    resolved: ResolvedSession,
    *,
    pair_rank: Mapping[tuple[str, str], float] | None = None,
) -> tuple[DeclaredLinkCandidate, ...]:
    """Generate the declared static candidate universe from ``ResolvedSession``."""
    node_by_id = _node_map(resolved)
    local_to_runtime = {
        (node.segment_id, node.local_node_id): node.node_id for node in resolved.nodes
    }
    candidates: list[DeclaredLinkCandidate] = []
    seen_pairs_by_rule: set[tuple[str, tuple[str, str]]] = set()
    pair_owner: dict[tuple[str, str], str] = {}
    for rule in resolved.link_rules:
        if not rule.enabled:
            continue
        if rule.endpoints[0].terminal_role != rule.endpoints[1].terminal_role:
            raise ValueError(
                f"link_rule {rule.rule_id!r} has mixed terminal roles; "
                "candidate generation requires both endpoints to use the same role"
            )
        endpoint_media = {
            medium
            for medium in (
                rule.endpoints[0].terminal_medium,
                rule.endpoints[1].terminal_medium,
            )
            if medium is not None
        }
        if len(endpoint_media) > 1:
            raise ValueError(
                f"link_rule {rule.rule_id!r} has mixed terminal media; "
                "candidate generation requires compatible endpoint media"
            )
        candidate_medium = next(iter(endpoint_media), None)
        raw_pairs = (
            _explicit_runtime_pairs(rule, local_to_runtime=local_to_runtime)
            if isinstance(rule.topology, ExplicitPairsTopology)
            else _cross_product_pairs(rule)
        )
        for pair, priority in _apply_static_topology(
            rule,
            raw_pairs,
            node_by_id=node_by_id,
            pair_rank=pair_rank,
        ):
            key = (rule.rule_id, pair)
            if key in seen_pairs_by_rule:
                raise ValueError(f"link_rule {rule.rule_id!r} produced duplicate pair {pair}")
            seen_pairs_by_rule.add(key)
            previous_rule = pair_owner.get(pair)
            if previous_rule is not None:
                raise ValueError(
                    f"pair {pair} is declared by multiple link_rules "
                    f"({previous_rule!r}, {rule.rule_id!r}); rule ownership must be unique"
                )
            pair_owner[pair] = rule.rule_id
            candidates.append(
                DeclaredLinkCandidate(
                    pair=pair,
                    rule_id=rule.rule_id,
                    kind=rule.kind,
                    terminal_role=rule.endpoints[0].terminal_role,
                    terminal_medium=candidate_medium,
                    topology_mode=rule.topology.mode,
                    priority=priority,
                    endpoint_segments=(
                        rule.endpoints[0].segment_id,
                        rule.endpoints[1].segment_id,
                    ),
                )
            )
    return tuple(candidates)
