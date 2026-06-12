# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME lifecycle operational event contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nodalarc.models.link_decisions import GroundAllocationEventCategory


class OmeOpsCode(StrEnum):
    """OME-produced OpsEvent codes used by operator-facing lifecycle surfaces."""

    MBB_TEARDOWN_TERMINAL = "MBB_TEARDOWN_TERMINAL"
    # Periodic per-segment pacing attribution (PacingWindowStats payload).
    PACING_TELEMETRY = "PACING_TELEMETRY"
    # The Pacemaker cannot sustain the commanded rate (entering) / can again
    # (leaving). Details carry requested vs achieved and the segment split.
    RATE_DEGRADED = "RATE_DEGRADED"
    RATE_RECOVERED = "RATE_RECOVERED"


class MbbPairAuthority(BaseModel):
    """Authority facts for one ground pair at a lifecycle boundary."""

    model_config = ConfigDict(frozen=True)

    pair: list[str]
    scheduled: bool
    pending_teardown: bool
    visible: bool | None = None
    terminal_indices: list[int] | None = None


class MbbTeardownLifecycleDetails(BaseModel):
    """Typed details stored inside an OME MBB lifecycle OpsEvent.

    OpsEvent.details is a dict for wire compatibility. OME producers construct
    that dict from this model so terminal teardown outcomes are not ad hoc JSON.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    epoch_id: int
    snapshot_seq: int | None
    allocator_step: int
    master_sim_time: datetime
    gs_id: str
    teardown_id: str
    old_pair: list[str]
    successor_pair: list[str]
    terminal_outcome: GroundAllocationEventCategory
    source_allocation_event_category: GroundAllocationEventCategory | None = None
    message: str
    authority_before: dict[str, MbbPairAuthority]
    authority_after: dict[str, MbbPairAuthority] | None = None
    seek_target_sim_time: datetime | None = None
    ground_policy_audit_ref: dict[str, int] | None = None
    terminal_indices: dict[str, list[int]] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)
