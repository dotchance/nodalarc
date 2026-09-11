# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog path containment helpers for API-facing config references."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nodalarc.catalog_refs import (
    CatalogRef,
    CatalogReferenceError,
    parse_catalog_reference,
)
from nodalarc.catalog_refs import (
    validate_catalog_name as validate_reference_name,
)


class CatalogPathError(ValueError):
    """Raised when a config path escapes an approved catalog root."""


@dataclass(frozen=True)
class CatalogRoots:
    """Approved catalog roots.

    ``root`` is the shipped ``nodalarc:`` catalog — immutable content baked
    into every service image. ``user_root`` is the selected ``user:`` catalog,
    which may be a writable authoring view or a read-only deployment upload.
    Callers must supply both roots required by the document they resolve; this
    type never flattens references or falls back between namespaces.
    """

    root: Path
    user_root: Path | None = None

    @classmethod
    def from_catalog_root(
        cls,
        catalog_root: str | Path = "catalog/nodalarc",
        *,
        user_root: str | Path | None = None,
    ) -> CatalogRoots:
        return cls(
            root=Path(catalog_root),
            user_root=Path(user_root) if user_root is not None else None,
        )


def validate_catalog_name(name: str, *, label: str = "name") -> str:
    """Return a catalog object name after rejecting path syntax."""
    try:
        return validate_reference_name(name, label=label)
    except CatalogReferenceError as exc:
        raise CatalogPathError(str(exc)) from exc


def resolve_catalog_reference(
    source: str | Path,
    roots: CatalogRoots,
    *,
    label: str = "catalog reference",
) -> Path:
    """Resolve a ``nodalarc:<path>`` or ``user:<path>`` token under its root.

    Both schemes get identical path validation and containment; ``user:``
    additionally requires a configured user root. Callers without one reject
    user references instead of guessing or falling back.
    """
    try:
        parsed = parse_catalog_reference(CatalogRef(str(source)), label=label)
    except CatalogReferenceError as exc:
        raise CatalogPathError(str(exc)) from exc

    if parsed.namespace == "user":
        if roots.user_root is None:
            raise CatalogPathError(f"{label} uses the user catalog, which is not available here")
        root = roots.user_root
    else:
        root = roots.root
    reference = parsed.relative_path
    root_resolved = root.resolve(strict=True)
    resolved = (root_resolved / reference).resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise CatalogPathError(f"{label} escapes approved catalog root: {root}") from exc
    return resolved
