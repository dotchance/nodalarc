# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Shared OME structured types that cross engine boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import get_args

from nodalarc.body_frames import SUPPORTED_BODY_NAMES, SupportedSurfaceBody
from nodalarc.models.link_decisions import (
    GroundAllocationEventCategory,
    GroundVisibilityRejectingEndpoint,
    GroundVisibilityRejectReason,
    ObserverFrame,
)
from nodalarc.models.terminal_physics import GroundBoresightMode, SatGroundBoresightMode


@dataclass(frozen=True)
class MbbTeardown:
    """Pending make-before-break teardown for a superseded ground link."""

    start_step: int
    successor_pair: tuple[str, str]


MbbTeardownState = dict[tuple[str, str], MbbTeardown]


@dataclass(frozen=True)
class MbbTeardownLifecycleEvent:
    """Allocator/Pacemaker terminal MBB teardown fact before OpsEvent wrapping."""

    category: GroundAllocationEventCategory
    old_pair: tuple[str, str]
    successor_pair: tuple[str, str]
    gs_id: str
    message: str
    source_allocation_event_category: GroundAllocationEventCategory | None = None
    authority_before: dict[str, dict[str, object]] = field(default_factory=dict)
    terminal_indices: dict[str, tuple[int, int]] = field(default_factory=dict)
    seek_target_sim_time_unix: float | None = None

    @property
    def teardown_id(self) -> str:
        return f"{self.old_pair[0]}:{self.old_pair[1]}->{self.successor_pair[0]}:{self.successor_pair[1]}"


@dataclass(frozen=True, slots=True)
class GroundVisibilityDecision:
    """Hot-path internal form of a per-pair ground visibility decision.

    Slotted frozen dataclass — no validation overhead, named field
    access. The OME ground-visibility loop touches ``GS x sat`` pairs
    per tick; at 1k sats x 50 GSes that is 50,000 instantiations per
    tick. Pydantic instantiation in that loop is a deliberate cost we
    are not paying. The wire-boundary ``GroundVisibilityDecisionWire``
    in ``nodalarc.models.link_decisions`` is the same shape and is
    constructed only at NATS publish time.

    Every field is required. There are no permissive defaults. The
    ``applied_*`` fields use ``None`` to mean "this constraint was not
    in effect for the decision". Side-specific values identify the
    ground and satellite endpoint constraints separately; there are no
    duplicate combined applied-constraint fields on the ground decision
    surface.
    """

    pair: tuple[str, str]
    tenant_id: str
    reference_body: SupportedSurfaceBody
    visible: bool
    range_km: float
    elevation_deg: float
    azimuth_deg: float | None
    sat_off_nadir_deg: float | None
    observer_frame: ObserverFrame
    reject_reason: GroundVisibilityRejectReason
    rejecting_endpoint: GroundVisibilityRejectingEndpoint
    applied_min_elevation_deg: float
    applied_gs_max_range_km: float | None
    applied_sat_max_range_km: float | None
    applied_gs_field_of_regard_deg: float | None
    applied_sat_field_of_regard_deg: float | None
    applied_gs_max_tracking_rate_deg_s: float | None
    applied_sat_max_tracking_rate_deg_s: float | None
    applied_gs_boresight_mode: GroundBoresightMode | None
    applied_sat_boresight_mode: SatGroundBoresightMode | None
    applied_gs_terminal_profile: str | None
    applied_sat_terminal_profile: str | None

    def __post_init__(self) -> None:
        """Mirror of ``GroundVisibilityDecisionWire``'s validators."""
        if self.reference_body not in SUPPORTED_BODY_NAMES:
            raise ValueError(
                f"reference_body={self.reference_body!r} is not supported. "
                f"Supported bodies: {sorted(SUPPORTED_BODY_NAMES)!r}."
            )
        if self.reject_reason not in _GROUND_REJECT_REASONS:
            raise ValueError(
                f"reject_reason={self.reject_reason!r} is not a valid "
                "ground rejection reason. Allowed: "
                f"{sorted(_GROUND_REJECT_REASONS)!r}. ISL-only values "
                "(polar_seam, terminal_type_mismatch, terminal_role_mismatch) "
                "must never appear on a ground decision."
            )
        if self.rejecting_endpoint not in _GROUND_REJECTING_ENDPOINTS:
            raise ValueError(
                f"rejecting_endpoint={self.rejecting_endpoint!r} is not valid. "
                f"Allowed: {sorted(_GROUND_REJECTING_ENDPOINTS)!r}."
            )
        if self.visible and self.reject_reason != "ok":
            raise ValueError(
                f"visible=True requires reject_reason='ok', got "
                f"{self.reject_reason!r}. A visible pair cannot also carry "
                "a rejection reason — the two fields must be consistent."
            )
        if not self.visible and self.reject_reason == "ok":
            raise ValueError(
                "visible=False requires a non-'ok' reject_reason — an "
                "invisible pair must carry the reason it failed visibility."
            )
        if self.visible and self.rejecting_endpoint != "none":
            raise ValueError("visible=True requires rejecting_endpoint='none'")
        if (
            self.reject_reason in ("los_blocked", "elevation_below_min")
            and self.rejecting_endpoint != "none"
        ):
            raise ValueError(
                f"reject_reason={self.reject_reason!r} requires rejecting_endpoint='none'"
            )
        if self.reject_reason in (
            "range_exceeded",
            "field_of_regard",
            "tracking_exceeded",
        ):
            if self.rejecting_endpoint == "none":
                raise ValueError(
                    f"reject_reason={self.reject_reason!r} requires a terminal rejecting_endpoint"
                )
            if (
                self.applied_gs_terminal_profile is None
                and self.applied_sat_terminal_profile is None
            ):
                raise ValueError(
                    f"reject_reason={self.reject_reason!r} requires at least one of "
                    "applied_gs_terminal_profile / applied_sat_terminal_profile to be "
                    "set — the rejection must be attributable to a specific terminal "
                    "profile for audit."
                )
            if (
                self.rejecting_endpoint in ("ground", "both")
                and self.applied_gs_terminal_profile is None
            ):
                raise ValueError(
                    f"rejecting_endpoint={self.rejecting_endpoint!r} requires "
                    "applied_gs_terminal_profile for attributable audit"
                )
            if (
                self.rejecting_endpoint in ("satellite", "both")
                and self.applied_sat_terminal_profile is None
            ):
                raise ValueError(
                    f"rejecting_endpoint={self.rejecting_endpoint!r} requires "
                    "applied_sat_terminal_profile for attributable audit"
                )
            if (
                self.reject_reason == "field_of_regard"
                and self.rejecting_endpoint in ("satellite", "both")
                and self.sat_off_nadir_deg is None
            ):
                raise ValueError("satellite field_of_regard rejection requires sat_off_nadir_deg")
        if (
            self.visible
            and self.applied_sat_field_of_regard_deg is not None
            and self.sat_off_nadir_deg is None
        ):
            raise ValueError(
                "visible decision with satellite field_of_regard applied requires sat_off_nadir_deg"
            )


_GROUND_REJECT_REASONS: frozenset[str] = frozenset(get_args(GroundVisibilityRejectReason))
_GROUND_REJECTING_ENDPOINTS: frozenset[str] = frozenset(get_args(GroundVisibilityRejectingEndpoint))


GroundVisibilityDecisionMap = dict[tuple[str, str], GroundVisibilityDecision]
"""Per-pair decision map. Replaced the legacy positional tuple alias
``GroundVisibilityDetails`` — no positional unpacking, no
sentinel-value heuristics, every field named and typed."""
