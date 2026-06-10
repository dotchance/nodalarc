# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground-link scheduling policy schema.

These models describe operator-configured policy hooks. They are shared
configuration boundary types, not allocator implementation. The allocator reads
the resolved canonical shapes and dispatches to registered policy functions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.frozen import FrozenDict, ImmutableStrDict

SelectionPolicyName = Literal[
    "highest-elevation",
    "lowest-elevation",
    "longest-remaining-pass",
]
HandoverPolicyName = Literal["hysteresis", "none"]
RankingComponent = Literal[
    "service_priority",
    "selection_score",
    "per_gs_rank",
    "satellite_ground_terminal_capacity",
    "lex_pair",
]
MbbPreemptionPolicy = Literal["off"]
SuccessorAbortPolicy = Literal["hard_release", "soft_retain"]
CrossTenantDisplacementPolicy = Literal["off"]

VALID_SELECTION_POLICY_NAMES: frozenset[str] = frozenset(get_args(SelectionPolicyName))
VALID_HANDOVER_POLICY_NAMES: frozenset[str] = frozenset(get_args(HandoverPolicyName))


SELECTION_POLICY_SCORE_SCALES: dict[str, str] = {
    "highest-elevation": "normalized-elevation-degrees",
    "lowest-elevation": "normalized-elevation-degrees",
    "longest-remaining-pass": "remaining-visible-seconds",
}


def validate_selection_score_scale_compatibility(
    *,
    policy_names: Mapping[str, str],
    ranking_order: tuple[str, ...],
) -> None:
    """Fail if global ranking compares incompatible raw selection scores.

    ``selection_score`` ranks ground stations by their policies' raw outputs;
    mixing score scales (elevation degrees vs remaining seconds) makes that
    comparison meaningless. Enforced at the pre-deploy readiness gate and
    re-checked by OME at startup.
    """
    if "selection_score" not in ranking_order:
        return
    scales: dict[str, list[str]] = {}
    for gs_id, policy_name in sorted(policy_names.items()):
        scales.setdefault(selection_policy_score_scale(policy_name), []).append(gs_id)
    if len(scales) <= 1:
        return
    details = "; ".join(f"{scale}: {', '.join(gs_ids)}" for scale, gs_ids in sorted(scales.items()))
    raise ValueError(
        "scheduling.ground.ranking_order includes 'selection_score', but resolved "
        "ground selection policies use incompatible score scales. Use 'per_gs_rank' "
        "for cross-policy arbitration or configure compatible selection policies. "
        f"Resolved scales: {details}"
    )


def selection_policy_score_scale(policy_name: str) -> str:
    """Return the score scale used when comparing policy output across GSes."""

    try:
        return SELECTION_POLICY_SCORE_SCALES[policy_name]
    except KeyError as exc:
        raise ValueError(f"Unknown ground selection policy: {policy_name!r}") from exc


class SelectionPolicySpec(BaseModel):
    """Operator-selected pure candidate scoring policy. Frozen runtime truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: SelectionPolicyName = "highest-elevation"
    params: ImmutableStrDict = Field(default_factory=FrozenDict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_params(cls, data):
        # Normalize before construction so the frozen model is never mutated.
        if isinstance(data, dict) and data.get("name") == "longest-remaining-pass":
            params = data.get("params")
            if isinstance(params, Mapping):
                horizon = params.get("lookahead_horizon_ticks")
                if horizon is not None:
                    data = {
                        **data,
                        "params": {**params, "lookahead_horizon_ticks": int(horizon)},
                    }
        return data

    @model_validator(mode="after")
    def _validate_policy_params(self):
        if self.name in ("highest-elevation", "lowest-elevation"):
            if self.params:
                raise ValueError(f"selection_policy.name={self.name!r} requires empty params")
        elif self.name == "longest-remaining-pass":
            extra = sorted(set(self.params) - {"lookahead_horizon_ticks"})
            if extra:
                raise ValueError(
                    "selection_policy.name='longest-remaining-pass' received unsupported "
                    f"params: {', '.join(extra)}"
                )
            horizon = self.params.get("lookahead_horizon_ticks")
            if horizon is None or int(horizon) <= 0:
                raise ValueError(
                    "selection_policy.name='longest-remaining-pass' requires "
                    "params.lookahead_horizon_ticks > 0"
                )
        return self


class HandoverPolicySpec(BaseModel):
    """Operator-selected incumbent displacement policy. Frozen runtime truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: HandoverPolicyName = "hysteresis"
    params: ImmutableStrDict = Field(default_factory=FrozenDict)

    @model_validator(mode="after")
    def _validate_none_params(self):
        if self.name == "none" and self.params:
            raise ValueError("handover_policy.name='none' requires empty params")
        return self
