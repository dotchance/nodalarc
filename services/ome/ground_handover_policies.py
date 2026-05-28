# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground-link incumbent displacement policies for the OME allocator.

Handover policies are pure functions over a challenger, an incumbent, and
resolved policy params. They do not allocate terminals or publish events.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from nodalarc.models.ground_policy import HandoverPolicySpec
from nodalarc.models.ground_station import HysteresisParameters
from nodalarc.models.link_decisions import GroundUnscheduledReason

from ome.visibility import GroundVisibility


@dataclass(frozen=True)
class HandoverContext:
    """Context available to pure handover policies."""

    step: int
    gs_id: str
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
    if challenger_score > incumbent_score:
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


def _hysteresis(
    incumbent_score: float,
    challenger_score: float,
    context: HandoverContext,
    params: Mapping[str, Any],
) -> HandoverDecision:
    hysteresis = HysteresisParameters(**dict(params))
    discount = compute_effective_hysteresis_discount(
        elevation_deg=context.incumbent_visibility.elevation_deg,
        min_elevation_deg=context.min_elevation_deg,
        params=hysteresis,
    )
    hold_score = incumbent_score * discount
    if challenger_score > hold_score:
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
        raise ValueError(f"Unknown ground handover policy: {policy.name!r}")
    return fn(incumbent_score, challenger_score, context, policy.params)
