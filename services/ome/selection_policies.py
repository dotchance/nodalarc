# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Pure candidate selection policies for the OME link allocator.

A selection policy scores a usable candidate for the node that chooses it. It does
not inspect incumbent state, terminal occupancy, or pending handovers. That
separation is the allocator contract: selection answers "which candidate is
best on its own merits?"; handover policy answers "may that candidate
displace the current incumbent?"

The stated policies score the choosing node's view of the pair (its elevation, its
remaining pass), so they need a node with a sky. Their table holds exactly the names
the grammar offers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nodalarc.models.ground_policy import (
    SelectionPolicySpec,
)
from nodalarc.models.ground_policy import (
    validate_selection_score_scale_compatibility as _validate_scale_compatibility,
)

from ome.visibility import GroundVisibility


@dataclass(frozen=True)
class SelectionContext:
    """Context available to pure selection policies: the choosing node and its peer."""

    step: int
    node_id: str
    peer_id: str


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
            "Selection policy 'longest-remaining-pass' requires OME pass "
            "lookahead; missing remaining_visible_s"
        )
    return float(visibility.remaining_visible_s)


def validate_selection_score_scale_compatibility(
    *,
    policies: Mapping[str, SelectionPolicySpec],
    ranking_order: tuple[str, ...],
) -> None:
    """Runtime backstop for the pre-deploy scale-compatibility gate."""

    _validate_scale_compatibility(
        policy_names={node_id: policy.name for node_id, policy in policies.items()},
        ranking_order=ranking_order,
    )


SCORE_FUNCTIONS: dict[str, ScoreFunction] = {
    "highest-elevation": _highest_elevation,
    "lowest-elevation": _lowest_elevation,
    "longest-remaining-pass": _longest_remaining_pass,
}


def nearest_score(range_km: float) -> float:
    """NodalArc's own score, for a pair whose nodes state no scheduling: nearer is higher.

    It is the negated range, so comparing two scores is exactly comparing two ranges; a
    reciprocal or an offset would round. No user can state it: it is not a name in the
    grammar's policy vocabulary, and it is not in the table above, which holds exactly
    those names. The non-negative check in ``score_candidate`` applies to the stated
    policies only.
    """
    return -range_km


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
        raise ValueError(f"Unknown selection policy: {policy.name!r}")
    score = fn(visibility, context, policy.params)
    if score < 0:
        raise ValueError(
            f"Selection policy {policy.name!r} returned negative score "
            f"{score} for {(context.node_id, context.peer_id)!r}"
        )
    return score
