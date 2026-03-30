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


class CoveragePreviewResult(BaseModel):
    """Complete coverage preview result."""

    model_config = ConfigDict(frozen=True)

    orbital_period_s: float
    preview_step_s: int
    isl: IslPreview
    ground_stations: GsPreview
    warnings: list[str]
