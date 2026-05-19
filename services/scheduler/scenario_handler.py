# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Scenario command protocol — pure parsing and validation.

Parses JSON scenario injection commands into typed Pydantic models.
No NATS, no threading, no event loop, no override state, no dispatch.

The Dispatcher owns the NATS subscription, override state mutation, and
dispatch intent enqueueing. This module is called by the Dispatcher for
command parsing only.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Tag, ValidationError


class InjectLinkDown(BaseModel):
    action: Literal["inject_link_down"]
    node_a: str
    node_b: str
    reason: str = "scenario_inject_down"


class InjectSatelliteLoss(BaseModel):
    action: Literal["inject_satellite_loss"]
    node: str


class ReleaseLinkOverride(BaseModel):
    action: Literal["inject_link_up"]
    node_a: str
    node_b: str


class RestoreSatellite(BaseModel):
    action: Literal["restore_satellite"]
    node: str


class ClearAllOverrides(BaseModel):
    action: Literal["clear_overrides"]


ScenarioCommand = Annotated[
    Annotated[InjectLinkDown, Tag("inject_link_down")]
    | Annotated[InjectSatelliteLoss, Tag("inject_satellite_loss")]
    | Annotated[ReleaseLinkOverride, Tag("inject_link_up")]
    | Annotated[RestoreSatellite, Tag("restore_satellite")]
    | Annotated[ClearAllOverrides, Tag("clear_overrides")],
    Discriminator("action"),
]


def parse_scenario_command(data: bytes) -> ScenarioCommand:
    """Parse a scenario injection command from raw bytes.

    Returns a typed ScenarioCommand model.
    Raises ValueError on malformed JSON or unknown/invalid action.
    """
    try:
        raw = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("expected JSON object")

    action = raw.get("action")
    if not action:
        raise ValueError("missing 'action' field")

    try:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(ScenarioCommand)
        return adapter.validate_python(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid command: {exc}") from exc
