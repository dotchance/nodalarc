# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Shared OME structured types that cross engine boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from nodalarc.models.link_decisions import GroundVisibilityRejectReason, ObserverFrame
from nodalarc.models.terminal_physics import GroundBoresightMode


@dataclass(frozen=True)
class MbbTeardown:
    """Pending make-before-break teardown for a superseded ground link."""

    start_step: int
    successor_pair: tuple[str, str]


MbbTeardownState = dict[tuple[str, str], MbbTeardown]


@dataclass(frozen=True, slots=True)
class GroundVisibilityDecision:
    """Hot-path internal form of a per-pair ground visibility decision.

    Slotted frozen dataclass — no validation overhead, named field
    access. The OME ground-visibility loop touches `(GS x sat)` pairs
    per tick; at 1k sats x 50 GSes that is 50,000 instantiations per
    tick. Pydantic instantiation in that loop is a deliberate cost we
    are not paying. The wire-boundary `GroundVisibilityDecisionWire`
    in `nodalarc.models.link_decisions` is the same shape and is
    constructed only at NATS publish time.

    Every field is required. There are no permissive defaults. The
    `applied_*` fields use `None` to mean "this constraint was not in
    effect for the decision" (e.g., a `geometry_only` session does
    not declare `max_range_km`, so the field is `None` and
    `range_exceeded` cannot appear in `reject_reason`). They never
    mean "we forgot to populate this."

    `applied_gs_terminal_profile` and `applied_sat_terminal_profile`
    identify which *terminal definition / constraint profile* the
    decision evaluated against (not the kernel interface name, not
    the instance index). See `GroundVisibilityDecisionWire` for the
    full contract.
    """

    pair: tuple[str, str]
    tenant_id: str
    reference_body: str
    visible: bool
    range_km: float
    elevation_deg: float
    azimuth_deg: float | None
    observer_frame: ObserverFrame
    reject_reason: GroundVisibilityRejectReason
    applied_min_elevation_deg: float
    applied_max_range_km: float | None
    applied_field_of_regard_deg: float | None
    applied_max_tracking_rate_deg_s: float | None
    applied_boresight_mode: GroundBoresightMode | None
    applied_gs_terminal_profile: str | None
    applied_sat_terminal_profile: str | None

    def __post_init__(self) -> None:
        """Mirror of `GroundVisibilityDecisionWire`'s validators.

        Hot-path producers fail loud at construction if visible /
        reject_reason are inconsistent, or if a terminal-bound
        rejection cannot be attributed to a terminal profile.

        Dataclasses do not enforce `Literal` at runtime, so we also
        check that `reject_reason` is a member of the ground-only
        rejection enum. An ISL-only value here would otherwise slip
        past static typing into the hot path.
        """
        if self.reject_reason not in _GROUND_REJECT_REASONS:
            raise ValueError(
                f"reject_reason={self.reject_reason!r} is not a valid "
                "ground rejection reason. Allowed: "
                f"{sorted(_GROUND_REJECT_REASONS)!r}. ISL-only values "
                "(polar_seam, terminal_type_mismatch, terminal_role_mismatch) "
                "must never appear on a ground decision."
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
        if self.reject_reason in (
            "range_exceeded",
            "field_of_regard",
            "tracking_exceeded",
        ) and (
            self.applied_gs_terminal_profile is None and self.applied_sat_terminal_profile is None
        ):
            raise ValueError(
                f"reject_reason={self.reject_reason!r} requires at least one of "
                "applied_gs_terminal_profile / applied_sat_terminal_profile to be "
                "set — the rejection must be attributable to a specific terminal "
                "profile for audit."
            )


_GROUND_REJECT_REASONS: frozenset[str] = frozenset(
    {
        "ok",
        "los_blocked",
        "elevation_below_min",
        "range_exceeded",
        "field_of_regard",
        "tracking_exceeded",
    }
)


GroundVisibilityDecisionMap = dict[tuple[str, str], GroundVisibilityDecision]
"""Per-pair decision map. Replaced the legacy positional tuple alias
`GroundVisibilityDetails` in Phase 1.2.b — no positional unpacking, no
sentinel-value heuristics, every field named and typed."""
