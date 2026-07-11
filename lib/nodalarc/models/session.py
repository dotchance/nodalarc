# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime helper models derived from canonical session resolution.

This module is not a persisted configuration root. Persisted session YAML is
validated by :class:`nodalarc.models.segment_session.SegmentSessionConfig` and
then materialized as a resolved session before runtime consumers use these
helpers.
"""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodalarc.ephemeris_runtime import session_epoch_unix
from nodalarc.model_validation import NonEmptyReference
from nodalarc.models.ground_policy import (
    CrossTenantDisplacementPolicy,
    HandoverPolicySpec,
    HysteresisParameters,
    MbbPreemptionPolicy,
    RankingComponent,
    SelectionPolicySpec,
    SuccessorAbortPolicy,
)
from nodalarc.models.segment_session import TimeConfig


class GroundSchedulingConfig(BaseModel):
    """Ground handover and allocation behavior.

    Ground scheduling keeps mechanism and policy separate. This model is only the
    operator-configured policy surface; the OME allocator consumes the resolved
    specs and dispatches to registered pure policy hooks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    selection_policy: SelectionPolicySpec = Field(default_factory=SelectionPolicySpec)
    handover_policy: HandoverPolicySpec = Field(
        default_factory=lambda: HandoverPolicySpec(
            name="hysteresis",
            params=HysteresisParameters().model_dump(),
        )
    )
    ranking_order: tuple[RankingComponent, ...] = Field(
        default_factory=lambda: (
            "service_priority",
            "selection_score",
            "satellite_ground_terminal_capacity",
            "lex_pair",
        )
    )

    handover_mode: Literal["bbm", "mbb"] = "bbm"
    mbb_overlap_ticks: int = 3
    mbb_reserve: int = 0
    mbb_preemption: MbbPreemptionPolicy = "off"
    successor_abort_policy: SuccessorAbortPolicy = "hard_release"
    cross_tenant_displacement: CrossTenantDisplacementPolicy = "off"
    bbm_acquire_timeout_ticks: int = 1

    @field_validator("mbb_overlap_ticks")
    @classmethod
    def _positive_overlap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("scheduling.ground.mbb_overlap_ticks must be >= 0")
        return value

    @field_validator("mbb_reserve")
    @classmethod
    def _bounded_single_overlap_reserve(cls, value: int) -> int:
        if value < 0:
            raise ValueError("scheduling.ground.mbb_reserve must be >= 0")
        # `mbb_reserve > 1` reads like "this ground station may run two or more
        # simultaneous make-before-break overlaps." The current allocator does
        # not implement that. It serializes active MBB overlap per GS through
        # `mbb_overlap_locked`, so accepting reserve=2 would reserve capacity the
        # engine cannot use and would make the model look stronger than reality.
        if value > 1:
            raise ValueError(
                "scheduling.ground.mbb_reserve > 1 requires future MBB-002 "
                "multi-overlap allocator support; current implementation supports "
                "at most one concurrent MBB overlap per ground station"
            )
        return value

    @field_validator("bbm_acquire_timeout_ticks")
    @classmethod
    def _strict_bbm_acquire_timeout(cls, value: int) -> int:
        if value != 1:
            raise ValueError(
                "scheduling.ground.bbm_acquire_timeout_ticks values other than 1 are "
                "reserved extension points; the current implementation has no specified multi-tick BBMGap "
                "wait-state algorithm"
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def _normalize_handover_params(cls, data):
        # Fill hysteresis defaults before the frozen HandoverPolicySpec is built,
        # so the spec is never mutated after construction. SelectionPolicySpec
        # self-normalizes its own params.
        if not isinstance(data, dict):
            return data
        handover = data.get("handover_policy")
        if isinstance(handover, HandoverPolicySpec):
            handover = handover.model_dump()
        if isinstance(handover, Mapping) and handover.get("name") == "hysteresis":
            normalized = HysteresisParameters(**dict(handover.get("params") or {})).model_dump()
            data = {**data, "handover_policy": {**handover, "params": normalized}}
        return data

    @model_validator(mode="after")
    def _resolve_policy_surface(self):
        # selection_policy and handover_policy validate/normalize themselves; this
        # enforces only the cross-field ground-scheduling rules.
        if not self.ranking_order:
            raise ValueError("scheduling.ground.ranking_order must not be empty")
        if self.ranking_order[-1] != "lex_pair":
            raise ValueError("scheduling.ground.ranking_order must end with 'lex_pair'")
        if len(self.ranking_order) == 1:
            raise ValueError(
                "scheduling.ground.ranking_order must include at least one decision "
                "component before 'lex_pair'"
            )
        if len(set(self.ranking_order)) != len(self.ranking_order):
            raise ValueError("scheduling.ground.ranking_order must not contain duplicates")

        if self.handover_mode == "mbb":
            if self.mbb_overlap_ticks <= 0:
                raise ValueError("MBB handover requires mbb_overlap_ticks > 0")
            if self.mbb_reserve <= 0:
                raise ValueError("MBB handover requires mbb_reserve > 0")
        return self


def resolve_session_epoch(time_config: TimeConfig | None) -> float:
    """Return the canonical session epoch used by OME runtime consumers."""

    return session_epoch_unix(time_config)


class TrafficFlowConfig(BaseModel):
    """Traffic-flow input for the separate scenario runner."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    flow_id: NonEmptyReference
    src: NonEmptyReference
    dst: NonEmptyReference
    protocol: Literal["udp", "tcp"]
    bandwidth_kbps: float = Field(gt=0)
    probe_type: Literal["continuous", "burst"]

    @model_validator(mode="after")
    def _distinct_endpoints(self):
        if self.src == self.dst:
            raise ValueError("traffic flow src and dst must differ")
        return self
