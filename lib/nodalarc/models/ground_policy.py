# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ground-link scheduling policy schema.

These models describe operator-configured policy hooks. They are shared
configuration boundary types, not allocator implementation. The allocator reads
the resolved canonical shapes and dispatches to registered policy functions.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SelectionPolicyName = Literal[
    "highest-elevation",
    "lowest-elevation",
    "longest-remaining-pass",
]
HandoverPolicyName = Literal["hysteresis", "none"]
RankingComponent = Literal["service_priority", "selection_score", "per_gs_rank", "lex_pair"]
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


def selection_policy_score_scale(policy_name: str) -> str:
    """Return the score scale used when comparing policy output across GSes."""

    try:
        return SELECTION_POLICY_SCORE_SCALES[policy_name]
    except KeyError as exc:
        raise ValueError(f"Unknown ground selection policy: {policy_name!r}") from exc


class SelectionPolicySpec(BaseModel):
    """Operator-selected pure candidate scoring policy."""

    model_config = ConfigDict(extra="forbid")

    name: SelectionPolicyName = "highest-elevation"
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _string_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if not isinstance(key, str):
                raise ValueError("selection_policy.params keys must be strings")
        return value

    @model_validator(mode="after")
    def _validate_policy_params(self):
        params = dict(self.params)
        if self.name in ("highest-elevation", "lowest-elevation"):
            if params:
                raise ValueError(f"selection_policy.name={self.name!r} requires empty params")
        elif self.name == "longest-remaining-pass":
            extra = sorted(set(params) - {"lookahead_horizon_ticks"})
            if extra:
                raise ValueError(
                    "selection_policy.name='longest-remaining-pass' received unsupported "
                    f"params: {', '.join(extra)}"
                )
            horizon = params.get("lookahead_horizon_ticks")
            if horizon is None or int(horizon) <= 0:
                raise ValueError(
                    "selection_policy.name='longest-remaining-pass' requires "
                    "params.lookahead_horizon_ticks > 0"
                )
            self.params["lookahead_horizon_ticks"] = int(horizon)
        return self


class HandoverPolicySpec(BaseModel):
    """Operator-selected incumbent displacement policy."""

    model_config = ConfigDict(extra="forbid")

    name: HandoverPolicyName = "hysteresis"
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _string_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if not isinstance(key, str):
                raise ValueError("handover_policy.params keys must be strings")
        return value

    @model_validator(mode="after")
    def _validate_none_params(self):
        if self.name == "none" and self.params:
            raise ValueError("handover_policy.name='none' requires empty params")
        return self
