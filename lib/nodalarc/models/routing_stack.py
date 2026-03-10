"""Routing stack configuration models (stack.yaml schema)."""

from typing import Any

from pydantic import BaseModel


class ConfigTemplate(BaseModel):
    """Jinja2 template file to render into a pod."""

    src: str  # Template file in stack directory
    dst: str  # Destination path inside the pod


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
