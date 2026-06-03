# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Pure ground-link candidate selection policies for the OME allocator.

Selection policies score physically visible GS/satellite candidates. They do
not inspect incumbent state, terminal occupancy, or pending handovers. That
separation is the allocator contract: selection answers "which candidate is
best on its own merits?"; handover policy answers "may that candidate
displace the current incumbent?"
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nodalarc.models.ground_policy import SelectionPolicySpec, selection_policy_score_scale

from ome.visibility import GroundVisibility


@dataclass(frozen=True)
class SelectionContext:
    """Context available to pure selection policies."""

    step: int
    gs_id: str
    sat_id: str


ScoreFunction = Callable[[GroundVisibility, SelectionContext, Mapping[str, Any]], float]


def _reject_params(policy_name: str, params: Mapping[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(params) - allowed)
    if extra:
        raise ValueError(
            f"selection_policy.name={policy_name!r} received unsupported params: {', '.join(extra)}"
        )


def _highest_elevation(
    visibility: GroundVisibility,
    _context: SelectionContext,
    params: Mapping[str, Any],
) -> float:
    _reject_params("highest-elevation", params, set())
    return float(visibility.elevation_deg)


def _lowest_elevation(
    visibility: GroundVisibility,
    _context: SelectionContext,
    params: Mapping[str, Any],
) -> float:
    _reject_params("lowest-elevation", params, set())
    return 90.0 - float(visibility.elevation_deg)


def _longest_remaining_pass(
    visibility: GroundVisibility,
    _context: SelectionContext,
    params: Mapping[str, Any],
) -> float:
    _reject_params("longest-remaining-pass", params, {"lookahead_horizon_ticks"})
    horizon = params.get("lookahead_horizon_ticks")
    if horizon is None or int(horizon) <= 0:
        raise ValueError(
            "selection_policy.name='longest-remaining-pass' requires "
            "params.lookahead_horizon_ticks > 0"
        )
    if visibility.remaining_visible_s is None:
        raise ValueError(
            "Ground selection policy 'longest-remaining-pass' requires OME pass "
            "lookahead; missing remaining_visible_s"
        )
    return float(visibility.remaining_visible_s)


def validate_selection_score_scale_compatibility(
    *,
    policies: Mapping[str, SelectionPolicySpec],
    ranking_order: tuple[str, ...],
) -> None:
    """Fail if global ranking compares incompatible raw selection scores."""

    if "selection_score" not in ranking_order:
        return
    scales: dict[str, list[str]] = {}
    for gs_id, policy in sorted(policies.items()):
        scales.setdefault(selection_policy_score_scale(policy.name), []).append(gs_id)
    if len(scales) <= 1:
        return
    details = "; ".join(f"{scale}: {', '.join(gs_ids)}" for scale, gs_ids in sorted(scales.items()))
    raise ValueError(
        "scheduling.ground.ranking_order includes 'selection_score', but resolved "
        "ground selection policies use incompatible score scales. Use 'per_gs_rank' "
        "for cross-policy arbitration or configure compatible selection policies. "
        f"Resolved scales: {details}"
    )


SCORE_FUNCTIONS: dict[str, ScoreFunction] = {
    "highest-elevation": _highest_elevation,
    "lowest-elevation": _lowest_elevation,
    "longest-remaining-pass": _longest_remaining_pass,
}


def score_candidate(
    *,
    policy: SelectionPolicySpec,
    visibility: GroundVisibility,
    context: SelectionContext,
) -> float:
    """Return the raw selection score for a visible pair.

    Scores are raw policy outputs. Incumbent stickiness and terminal state are
    deliberately absent here. Unknown policies fail loudly at the policy
    dispatch boundary.
    """

    fn = SCORE_FUNCTIONS.get(policy.name)
    if fn is None:
        raise ValueError(f"Unknown ground selection policy: {policy.name!r}")
    score = fn(visibility, context, policy.params)
    if score < 0:
        raise ValueError(
            f"Ground selection policy {policy.name!r} returned negative score "
            f"{score} for {(context.gs_id, context.sat_id)!r}"
        )
    return score
