# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Typed Node Agent wiring manifest contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

REQUIRED_WIRING_PHASES: tuple[str, ...] = (
    "phase0_cleanup",
    "sysctls",
    "isl_interfaces",
    "mpls",
    "ground_infrastructure",
    "terrestrial_interfaces",
    "pod_finalization",
)


def canonical_manifest_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derive_wiring_generation(data: dict[str, Any]) -> str:
    material = dict(data)
    material.pop("wiring_generation", None)
    return "sha256:" + hashlib.sha256(canonical_manifest_json(material).encode()).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InterfaceName(_StrictModel):
    name: str

    @field_validator("name")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("interface name must be non-empty")
        return value


class IslInterface(_StrictModel):
    name: str
    peer_node: str
    peer_iface: str

    @field_validator("name", "peer_node", "peer_iface")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("ISL interface fields must be non-empty")
        return value


class TerrestrialSpec(_StrictModel):
    addresses: list[str] = Field(default_factory=list)


class NodeSpec(_StrictModel):
    node_type: Literal["satellite", "ground_station"]
    sysctls: dict[str, str]
    isl_interfaces: list[IslInterface]
    gnd_interfaces: list[InterfaceName]
    mpls_enable: bool
    segment_routing: bool
    mtu: int
    remove_default_route: bool
    plane: int | None = None
    slot: int | None = None
    gs_name: str | None = None
    gs_index: int | None = None
    terrestrial: TerrestrialSpec | None = None

    @field_validator("sysctls")
    @classmethod
    def _sysctls_required(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("sysctls must be explicit")
        return value

    @field_validator("gnd_interfaces")
    @classmethod
    def _ground_interfaces_explicit(cls, value: list[InterfaceName]) -> list[InterfaceName]:
        if value is None:
            raise ValueError("gnd_interfaces must be explicit")
        return value


class WiringManifest(_StrictModel):
    session_id: str
    wiring_generation: str
    required_phases: list[str]
    nodes: dict[str, NodeSpec]
    ground_bridges: dict[str, dict[str, Any]]
    isl_link_count: int

    @field_validator("session_id", "wiring_generation")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("manifest identity fields must be non-empty")
        return value

    @field_validator("wiring_generation")
    @classmethod
    def _generation_format(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
            raise ValueError("wiring_generation must be sha256:<64 hex chars>")
        return value

    @field_validator("required_phases")
    @classmethod
    def _required_phases(cls, value: list[str]) -> list[str]:
        missing = set(REQUIRED_WIRING_PHASES) - set(value)
        if missing:
            raise ValueError(f"required_phases missing: {', '.join(sorted(missing))}")
        return value

    @field_validator("nodes")
    @classmethod
    def _nodes_required(cls, value: dict[str, NodeSpec]) -> dict[str, NodeSpec]:
        if not value:
            raise ValueError("manifest nodes must be non-empty")
        return value
