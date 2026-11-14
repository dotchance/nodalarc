# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Held-link displacement rules for the OME link allocator.

One base rule decides every displacement: a new pair takes a held link's place only on a
strictly higher score (``strictly_higher``). The stated handover policies are that rule on the
choosing node's selection scores: ``none`` compares the scores as they are, and ``hysteresis``
raises the held link's score first. The allocator applies the same rule to the cases no node
states, with a score of its own for each.

Handover policies are pure functions over a challenger, an incumbent, and resolved policy
params. They do not allocate terminals or publish events.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from nodalarc.models.ground_policy import HandoverPolicySpec, HysteresisParameters
from nodalarc.models.link_decisions import GroundUnscheduledReason

from ome.visibility import GroundVisibility


@dataclass(frozen=True)
class HandoverContext:
    """Context available to pure handover policies. ``node_id`` is the choosing node."""

    step: int
    node_id: str
    incumbent_pair: tuple[str, str]
    challenger_pair: tuple[str, str]
    incumbent_visibility: GroundVisibility
    challenger_visibility: GroundVisibility
    min_elevation_deg: float


@dataclass(frozen=True)
class HandoverDecision:
    """Result of evaluating one challenger against one incumbent."""

    action: Literal["hold", "displace"]
    unscheduled_reason: GroundUnscheduledReason | None
    incumbent_hold_score: float | None


HandoverFunction = Callable[[float, float, HandoverContext, Mapping[str, Any]], HandoverDecision]


def strictly_higher(challenger_score: float, incumbent_score: float) -> bool:
    """The base displacement rule: a new pair takes a held link's place only on a strictly
    higher score. An equal score holds the link, so nothing moves on a tie."""
    return challenger_score > incumbent_score


def compute_effective_hysteresis_discount(
    *,
    elevation_deg: float,
    min_elevation_deg: float,
    params: HysteresisParameters,
) -> float:
    """Compute incumbent stickiness from physical elevation and hysteresis params."""

    fade_bottom = min_elevation_deg
    fade_top = min_elevation_deg + params.mask_fade_range_deg
    if elevation_deg <= fade_bottom:
        return 1.0
    if elevation_deg >= fade_top:
        return params.discount_factor
    t = (elevation_deg - fade_bottom) / params.mask_fade_range_deg
    return 1.0 + (params.discount_factor - 1.0) * t


def _none(
    incumbent_score: float,
    challenger_score: float,
    _context: HandoverContext,
    params: Mapping[str, Any],
) -> HandoverDecision:
    if params:
        raise ValueError("handover_policy.name='none' requires empty params")
    if strictly_higher(challenger_score, incumbent_score):
        return HandoverDecision(
            action="displace",
            unscheduled_reason=None,
            incumbent_hold_score=None,
        )
    return HandoverDecision(
        action="hold",
        unscheduled_reason="incumbent_held",
        incumbent_hold_score=None,
    )


def _hysteresis_hold_score(
    incumbent_score: float,
    incumbent_visibility: GroundVisibility,
    min_elevation_deg: float,
    params: Mapping[str, Any],
) -> float:
    hysteresis = HysteresisParameters(**dict(params))
    discount = compute_effective_hysteresis_discount(
        elevation_deg=incumbent_visibility.elevation_deg,
        min_elevation_deg=min_elevation_deg,
        params=hysteresis,
    )
    return incumbent_score * discount


def incumbent_hold_score(
    *,
    policy: HandoverPolicySpec,
    incumbent_score: float,
    incumbent_visibility: GroundVisibility,
    min_elevation_deg: float,
) -> float:
    """The score a held link is compared at under a stated handover policy: its own score
    under ``none``, and its score raised by the hysteresis discount under ``hysteresis``."""
    if policy.name == "none":
        if policy.params:
            raise ValueError("handover_policy.name='none' requires empty params")
        return incumbent_score
    if policy.name == "hysteresis":
        return _hysteresis_hold_score(
            incumbent_score, incumbent_visibility, min_elevation_deg, policy.params
        )
    raise ValueError(f"Unknown handover policy: {policy.name!r}")


def _hysteresis(
    incumbent_score: float,
    challenger_score: float,
    context: HandoverContext,
    params: Mapping[str, Any],
) -> HandoverDecision:
    hold_score = _hysteresis_hold_score(
        incumbent_score, context.incumbent_visibility, context.min_elevation_deg, params
    )
    if strictly_higher(challenger_score, hold_score):
        return HandoverDecision(
            action="displace",
            unscheduled_reason=None,
            incumbent_hold_score=hold_score,
        )
    return HandoverDecision(
        action="hold",
        unscheduled_reason="hysteresis_hold",
        incumbent_hold_score=hold_score,
    )


HOLD_SCORE_FUNCTIONS: dict[str, HandoverFunction] = {
    "none": _none,
    "hysteresis": _hysteresis,
}


def evaluate_handover(
    *,
    policy: HandoverPolicySpec,
    incumbent_score: float,
    challenger_score: float,
    context: HandoverContext,
) -> HandoverDecision:
    """Evaluate whether a challenger may displace an incumbent."""

    fn = HOLD_SCORE_FUNCTIONS.get(policy.name)
    if fn is None:
        raise ValueError(f"Unknown handover policy: {policy.name!r}")
    return fn(incumbent_score, challenger_score, context, policy.params)
