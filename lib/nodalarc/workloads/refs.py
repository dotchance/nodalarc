# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed references for workload documents.

The ``profiles`` and ``bindings`` families are deliberately absent from
``CatalogFamily`` and the configuration registry: they are not session-grammar
vocabulary, must not surface as authoring components, and resolve against a
workload package source, never the session catalog roots.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from nodalarc.catalog_refs import CatalogRef
from nodalarc.content_identity import Sha256Digest


class ProfileRef(CatalogRef):
    allowed_families = frozenset({"profiles"})


class ImplementationBindingRef(CatalogRef):
    allowed_families = frozenset({"bindings"})


class SelectionRef(BaseModel):
    """The CR's explicit workload selection: one binding, one package digest.

    External runtime selection metadata — never part of session YAML, and
    unable to redefine the resolved world. Present as a pair or not at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    binding_ref: ImplementationBindingRef
    package_digest: Sha256Digest

    def identity(self) -> str:
        """The one-line selection identity stamped on workload pods."""
        return f"{self.binding_ref}@{self.package_digest}"


BUILTIN_FRR_SELECTION_IDENTITY = "builtin-frr-default"


class SelectionPairError(ValueError):
    """The CR's selection pair is malformed. Deterministic, never transient."""


def selection_ref_from_spec(spec: Mapping) -> SelectionRef | None:
    """Parse the CR pair, both-or-neither, through the typed references.

    This is the ONE validation authority for the pair: the CR loader, the
    reconciler, and the platform hash all consume this function.
    """
    binding_ref = spec.get("implementationBindingRef") or ""
    package_digest = spec.get("implementationPackageDigest") or ""
    if not binding_ref and not package_digest:
        return None
    if not binding_ref or not package_digest:
        raise SelectionPairError(
            "implementationBindingRef and implementationPackageDigest must be "
            "present together or absent together"
        )
    try:
        return SelectionRef(
            binding_ref=ImplementationBindingRef(binding_ref),
            package_digest=package_digest,
        )
    except ValueError as error:
        raise SelectionPairError(f"implementation selection pair is invalid: {error}") from error
