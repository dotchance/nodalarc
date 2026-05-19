# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Routing stack configuration models (stack.yaml schema)."""

from typing import Any

from pydantic import BaseModel


class ConfigTemplate(BaseModel):
    """Jinja2 template file to render into a pod."""

    src: str  # Template file in stack directory
    dst: str  # Destination path inside the pod


class SecurityContext(BaseModel):
    """Security context for a sidecar container."""

    capabilities: list[str] = []


class EnvVar(BaseModel):
    """Environment variable for a sidecar container."""

    name: str
    value: str


class RoutingStackConfig(BaseModel):
    """Routing stack definition from stack.yaml."""

    name: str
    image: str  # Container image reference
    daemons: list[str] | None = None  # FRR-specific daemon list
    config_templates: list[ConfigTemplate]
    template_variables: dict[str, Any] = {}
    mi_adapter: str | None = None  # MI adapter module name (null for non-FRR stacks)
    max_compression: int = 10
    reconfigure_command: str | None = None  # Reconfigure command (null for non-FRR stacks)
    security_context: SecurityContext | None = None
    env: list[EnvVar] = []
    host_setup: dict[str, Any] = {}
    segment_routing: bool = False
    ttl_propagation: str | None = None  # "uniform" | "pipe" | None
    transport: str | None = None
