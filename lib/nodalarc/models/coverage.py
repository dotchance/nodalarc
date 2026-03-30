"""Coverage preview models — returned by the preview-coverage endpoint."""

from pydantic import BaseModel, ConfigDict


class IslPreview(BaseModel):
    """ISL link feasibility statistics for one orbital period."""

    model_config = ConfigDict(frozen=True)

    total_possible: int
    formed_at_least_once: int
    never_formed: int
    feasibility_pct: float
    min_active: int
    max_active: int


class GsStationPreview(BaseModel):
    """Per-ground-station coverage statistics."""

    model_config = ConfigDict(frozen=True)

    coverage_pct: float
    longest_gap_s: float


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
