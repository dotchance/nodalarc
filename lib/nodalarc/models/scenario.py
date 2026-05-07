# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Scenario configuration models."""

from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Tag

from nodalarc.models.session import TrafficFlowConfig


class WaitStep(BaseModel):
    action: Literal["wait"]
    duration_s: float


class InjectLinkDownStep(BaseModel):
    action: Literal["inject_link_down"]
    node_a: str
    node_b: str
    reason: str = "scenario_inject_down"


class InjectLinkUpStep(BaseModel):
    action: Literal["inject_link_up"]
    node_a: str
    node_b: str


class InjectSatelliteLossStep(BaseModel):
    action: Literal["inject_satellite_loss"]
    node: str


class RestoreSatelliteStep(BaseModel):
    action: Literal["restore_satellite"]
    node: str


class WaitConvergeStep(BaseModel):
    action: Literal["wait_converge"]
    timeout_s: float = 30.0


class MeasureStep(BaseModel):
    action: Literal["measure"]
    duration_s: float


class ReconfigStep(BaseModel):
    action: Literal["reconfig"]
    target: str  # e.g. "all", "plane:3", "node:sat-P03S07"
    set_values: dict[str, str] = {}


# Discriminated union on `action` field
ScenarioStep = Annotated[
    Annotated[WaitStep, Tag("wait")]
    | Annotated[InjectLinkDownStep, Tag("inject_link_down")]
    | Annotated[InjectLinkUpStep, Tag("inject_link_up")]
    | Annotated[InjectSatelliteLossStep, Tag("inject_satellite_loss")]
    | Annotated[RestoreSatelliteStep, Tag("restore_satellite")]
    | Annotated[WaitConvergeStep, Tag("wait_converge")]
    | Annotated[MeasureStep, Tag("measure")]
    | Annotated[ReconfigStep, Tag("reconfig")],
    Discriminator("action"),
]


class ScenarioConfig(BaseModel):
    """Top-level scenario configuration."""

    name: str
    description: str
    traffic_flows: list[TrafficFlowConfig] | None = None
    steps: list[ScenarioStep]
