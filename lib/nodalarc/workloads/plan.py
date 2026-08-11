# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The workload plan: one node's effective profile, as a small envelope.

A plan names the node, its effective catalog profile reference, and carries
any per-node files an adapter rendered for it (exact bytes). It is
deliberately NOT a second copy of the profile schema — consumers read the
same admitted catalog profile everyone else reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from nodalarc.catalog_refs import CatalogReferenceError, ProfileRef

_RENDERED_NAME_FORBIDDEN = {".", ".."}


def validate_rendered_file_name(name: str) -> str:
    """One flat, contained file name for an adapter-rendered artifact."""

    if not name or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"rendered file name must be a flat, contained name: {name!r}")
    if name in _RENDERED_NAME_FORBIDDEN:
        raise ValueError(f"rendered file name must not be a dot segment: {name!r}")
    return name


@dataclass(frozen=True, slots=True)
class WorkloadPlan:
    """One node bound to its effective catalog profile."""

    node_id: str
    profile_ref: str
    # Adapter-rendered per-node files (for example a rendered native
    # configuration set), file name -> exact bytes. Projected into the
    # profile's config mount; never interpreted by the platform.
    rendered_files: Mapping[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("plan node_id must be non-empty")
        try:
            ProfileRef(self.profile_ref)
        except CatalogReferenceError as error:
            raise ValueError(f"plan profile_ref is not a profile reference: {error}") from error
        frozen = MappingProxyType(dict(self.rendered_files))
        object.__setattr__(self, "rendered_files", frozen)
        for name, content in frozen.items():
            validate_rendered_file_name(name)
            if not isinstance(content, bytes):
                raise TypeError(f"rendered file {name!r} must be bytes")
