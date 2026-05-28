# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Terminal physics models shared by config and link-decision schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

GroundBoresightMode = Literal["local_vertical", "configured_inertial", "steerable_envelope"]
SatGroundBoresightMode = Literal["nadir"]


class TerminalBoresight(BaseModel):
    """Body-aware field-of-regard reference for a ground terminal."""

    model_config = ConfigDict(extra="forbid")

    mode: GroundBoresightMode
    half_angle_deg: float
    configured_az_deg: float | None = None
    configured_el_deg: float | None = None
    min_az_deg: float | None = None
    max_az_deg: float | None = None
    min_el_deg: float | None = None
    max_el_deg: float | None = None

    @field_validator("half_angle_deg")
    @classmethod
    def _half_angle_range(cls, value: float) -> float:
        if not 0.0 <= value <= 180.0:
            raise ValueError("half_angle_deg must be in [0, 180]")
        return value

    @field_validator("configured_az_deg", "min_az_deg", "max_az_deg")
    @classmethod
    def _az_range(cls, value: float | None) -> float | None:
        if value is not None and not -360.0 <= value <= 360.0:
            raise ValueError("azimuth values must be in [-360, 360]")
        return value

    @field_validator("configured_el_deg", "min_el_deg", "max_el_deg")
    @classmethod
    def _el_range(cls, value: float | None) -> float | None:
        if value is not None and not -90.0 <= value <= 90.0:
            raise ValueError("elevation values must be in [-90, 90]")
        return value

    @model_validator(mode="after")
    def _mode_fields_are_complete(self) -> TerminalBoresight:
        if self.mode == "configured_inertial" and (
            self.configured_az_deg is None or self.configured_el_deg is None
        ):
            raise ValueError(
                "configured_inertial boresight requires configured_az_deg and configured_el_deg"
            )
        if self.mode == "steerable_envelope":
            required = (
                self.min_az_deg,
                self.max_az_deg,
                self.min_el_deg,
                self.max_el_deg,
            )
            if any(v is None for v in required):
                raise ValueError(
                    "steerable_envelope boresight requires min/max azimuth and elevation bounds"
                )
            if (
                self.min_az_deg is not None
                and self.max_az_deg is not None
                and self.min_az_deg > self.max_az_deg
            ):
                raise ValueError("min_az_deg must be <= max_az_deg")
            if (
                self.min_el_deg is not None
                and self.max_el_deg is not None
                and self.min_el_deg > self.max_el_deg
            ):
                raise ValueError("min_el_deg must be <= max_el_deg")
        return self


class SatGroundTerminalBoresight(BaseModel):
    """Body-aware field-of-regard reference for a satellite ground terminal."""

    model_config = ConfigDict(extra="forbid")

    target_body: str
    mode: SatGroundBoresightMode
    half_angle_deg: float

    @field_validator("target_body")
    @classmethod
    def _target_body_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("target_body must be non-empty")
        return value

    @field_validator("half_angle_deg")
    @classmethod
    def _half_angle_range(cls, value: float) -> float:
        if not 0.0 <= value <= 180.0:
            raise ValueError("half_angle_deg must be in [0, 180]")
        return value
