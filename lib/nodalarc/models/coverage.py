# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Coverage preview models — returned by the preview-coverage endpoint."""

from pydantic import BaseModel, ConfigDict


class IslFailureBreakdown(BaseModel):
    """Why ISLs fail to form — per-reason counts from visibility checks."""

    model_config = ConfigDict(frozen=True)

    range_exceeded: int = 0  # terminal max_range_km too short
    tracking_exceeded: int = 0  # angular velocity > max_tracking_rate_deg_s
    field_of_regard: int = 0  # peer outside terminal cone
    los_blocked: int = 0  # earth body occlusion
    polar_seam: int = 0  # hard latitude cutoff
    terminal_exhausted: int = 0  # all terminals allocated to higher-priority peers


class IslPreview(BaseModel):
    """ISL link feasibility statistics for one orbital period."""

    model_config = ConfigDict(frozen=True)

    total_possible: int
    formed_at_least_once: int
    never_formed: int
    feasibility_pct: float
    min_active: int
    max_active: int
    failure_reasons: IslFailureBreakdown | None = None


class GsStationPreview(BaseModel):
    """Per-ground-station coverage statistics."""

    model_config = ConfigDict(frozen=True)

    coverage_pct: float
    longest_gap_s: float
    reason: str | None = None  # why coverage is poor, if applicable


class GsPreview(BaseModel):
    """Ground station coverage statistics for one orbital period."""

    model_config = ConfigDict(frozen=True)

    per_station: dict[str, GsStationPreview]
    simultaneous_min: int
    simultaneous_max: int
    simultaneous_mean: float
    max_gap_s: float


class CoverageInsight(BaseModel):
    """A single insight about the constellation configuration.

    Severity levels:
    - "info": expected physics behavior, normal operation (e.g., Earth occlusion, range limits)
    - "note": topology characteristic worth knowing (e.g., full mesh, terminal allocation)
    - "warning": potential issue that may affect routing (e.g., tracking rate dropouts, coverage gaps)
    - "error": configuration problem that will prevent connectivity (e.g., no cross-plane links, station beyond visibility)
    """

    model_config = ConfigDict(frozen=True)

    severity: str  # "info", "note", "warning", "error"
    message: str


class CoveragePreviewResult(BaseModel):
    """Complete coverage preview result."""

    model_config = ConfigDict(frozen=True)

    orbital_period_s: float
    preview_step_s: int
    isl: IslPreview
    ground_stations: GsPreview
    warnings: list[CoverageInsight]
