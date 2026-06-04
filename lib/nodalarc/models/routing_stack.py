# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Routing stack configuration models (stack.yaml schema)."""

from typing import Any

from pydantic import BaseModel

from nodalarc.model_validation import NonEmptyReference


class ConfigTemplate(BaseModel):
    """Jinja2 template file to render into a pod."""

    src: NonEmptyReference  # Template file in stack directory
    dst: NonEmptyReference  # Destination path inside the pod


class SecurityContext(BaseModel):
    """Security context for a sidecar container."""

    capabilities: list[NonEmptyReference] = []


class EnvVar(BaseModel):
    """Environment variable for a sidecar container."""

    name: NonEmptyReference
    value: str


class RoutingStackConfig(BaseModel):
    """Routing stack definition from stack.yaml."""

    name: NonEmptyReference
    image: NonEmptyReference  # Container image reference
    daemons: list[NonEmptyReference] | None = None  # FRR-specific daemon list
    config_templates: list[ConfigTemplate]
    template_variables: dict[str, Any] = {}
    mi_adapter: NonEmptyReference | None = None  # MI adapter module name (null for non-FRR stacks)
    max_compression: int = 10
    reconfigure_command: NonEmptyReference | None = (
        None  # Reconfigure command (null for non-FRR stacks)
    )
    security_context: SecurityContext | None = None
    env: list[EnvVar] = []
    host_setup: dict[str, Any] = {}
    segment_routing: bool = False
    ttl_propagation: NonEmptyReference | None = None  # "uniform" | "pipe" | None
    transport: NonEmptyReference | None = None
