# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Ephemeris manifest grammar.

Earth-only LEO/MEO/GEO sessions do not require ``ephemeris``; Earth-Luna sessions
do. ``skyfield_bsp`` is the first runtime-supported provider; the others are
structurally valid but runtime-future. Runtime network download of ephemeris
files is forbidden — kernels are local, checksum-verified, and must cover the
session time window. See ``specs/plans/multi-segment-yaml-grammar.md``.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from nodalarc.body_frames import FrameBodyName
from nodalarc.models.segments import Identifier

EphemerisProvider = Literal["skyfield_bsp", "spice_kernel_stack", "operator_supplied_spk"]
EphemerisQualityTier = Literal["jpl_de_bsp", "spice_kernel_stack", "operator_supplied_spk"]


class EphemerisKernel(BaseModel):
    """One local ephemeris kernel and its provenance/coverage.

    Coverage timestamps must be timezone-aware: the session feeds a single master
    clock to an ephemeris that expects an explicit time scale, so a naive
    (timezone-less) coverage window is ambiguous and rejected. Use UTC (``...Z``).
    """

    model_config = ConfigDict(extra="forbid")

    id: Identifier
    path: str
    checksum: str
    targets: list[FrameBodyName] = Field(min_length=1)
    frame: Identifier
    coverage_start: AwareDatetime
    coverage_end: AwareDatetime

    @model_validator(mode="after")
    def _validate_window(self) -> EphemerisKernel:
        if self.coverage_end <= self.coverage_start:
            raise ValueError(
                f"ephemeris kernel {self.id!r} coverage_end must be after coverage_start"
            )
        return self


class EphemerisConfig(BaseModel):
    """Session ephemeris manifest. Local kernels only; no runtime fetch."""

    model_config = ConfigDict(extra="forbid")

    provider: EphemerisProvider
    quality_tier: EphemerisQualityTier
    kernels: list[EphemerisKernel] = Field(min_length=1)
