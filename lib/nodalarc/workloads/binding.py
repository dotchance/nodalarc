# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Implementation-binding document model (v1).

A binding maps resolved nodes to node-workload profiles through a small closed
selector vocabulary. It is external to session YAML and frozen with a
deployment. This module owns document structure only; selector evaluation
against a resolved world lives in ``nodalarc.workloads.resolution``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodalarc.workloads.profile import (
    DESCRIPTION_MAX_BYTES,
    CatalogName,
    DnsLabel,
    NonEmptyStr,
    RequiredTrue,
)
from nodalarc.workloads.refs import ProfileRef

BINDING_MAX_ENTRIES = 64
SELECTOR_MAX_EXPLICIT_NODES = 2048


class BindingSelector(BaseModel):
    """Exactly one selector form per entry; no expression language."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[NonEmptyStr, ...] | None = Field(
        default=None, max_length=SELECTOR_MAX_EXPLICIT_NODES
    )
    segment: NonEmptyStr | None = None
    node_kind: Literal["satellite", "ground_station", "relay"] | None = None
    forwarding: Literal["routed", "host", "bridge", "control_only"] | None = None
    domain: NonEmptyStr | None = None
    tag: NonEmptyStr | None = None
    remainder: RequiredTrue | None = None

    @model_validator(mode="after")
    def _exactly_one_member(self) -> BindingSelector:
        members = [
            self.nodes,
            self.segment,
            self.node_kind,
            self.forwarding,
            self.domain,
            self.tag,
            self.remainder,
        ]
        set_count = sum(1 for member in members if member is not None)
        if set_count != 1:
            raise ValueError("selector must set exactly one member")
        if self.nodes is not None:
            if not self.nodes:
                raise ValueError("nodes selector must be nonempty")
            if len(set(self.nodes)) != len(self.nodes):
                raise ValueError("nodes selector entries must be unique")
        return self


class BindingEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: DnsLabel
    selector: BindingSelector
    profile: ProfileRef


class ImplementationBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"]
    id: CatalogName
    description: NonEmptyStr
    entries: tuple[BindingEntry, ...] = Field(
        json_schema_extra={"minItems": 1, "maxItems": BINDING_MAX_ENTRIES}
    )

    @field_validator("entries", mode="before")
    @classmethod
    def _entry_count(cls, entries: object) -> object:
        if isinstance(entries, list | tuple) and not 1 <= len(entries) <= BINDING_MAX_ENTRIES:
            raise ValueError(f"entries must contain 1 through {BINDING_MAX_ENTRIES} items")
        return entries

    @field_validator("description")
    @classmethod
    def _description_rules(cls, description: str) -> str:
        if len(description.encode()) > DESCRIPTION_MAX_BYTES:
            raise ValueError(f"description exceeds {DESCRIPTION_MAX_BYTES} bytes")
        return description

    @model_validator(mode="after")
    def _document_rules(self) -> ImplementationBinding:
        entry_ids = [entry.id for entry in self.entries]
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("binding entry ids must be unique")
        remainders = [entry.id for entry in self.entries if entry.selector.remainder is True]
        if len(remainders) > 1:
            raise ValueError("at most one remainder entry is permitted")
        return self


class ImplementationBindingDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    implementation_binding: ImplementationBinding
