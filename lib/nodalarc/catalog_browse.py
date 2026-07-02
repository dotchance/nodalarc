# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog browsing — list and read primitive files for authoring surfaces.

Sits beside ``catalog_paths`` (path containment) and ``models.catalog`` (the
grammar): every listed entry is validated through the same catalog document
validator the resolver uses, so the library never advertises a primitive the
resolver would reject. A file that fails validation is listed WITH its error —
a broken catalog entry is a fact to show, not to hide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from nodalarc.catalog_paths import (
    CatalogRoots,
    catalog_reference_scheme,
    resolve_catalog_reference,
    validate_catalog_name,
)
from nodalarc.models.builder_world import BuilderCatalogEntry
from nodalarc.models.catalog import validate_catalog_document

# Family directory -> expected document wrapper. Closed set: the API never
# walks a caller-supplied path.
CATALOG_FAMILIES: dict[str, str] = {
    "bodies": "body",
    "terminals": "terminal",
    "orbits": "orbit",
    "payloads": "payload",
    "nodes": "node",
    "sites": "site",
    "site-sets": "site_set",
    "constellations": "constellation",
    "space-node-sets": "space_node_set",
}


def _browse_root(family: str, wrapper: str, scheme: str, root: Path) -> list[BuilderCatalogEntry]:
    family_dir = root / family
    entries: list[BuilderCatalogEntry] = []
    if not family_dir.is_dir():
        return entries
    resolved_root = root.resolve(strict=True)
    for path in sorted(family_dir.rglob("*.yaml")):
        ref = f"{scheme}:{path.relative_to(root).as_posix()}"
        try:
            # Containment first: a symlink under the root must not read
            # outside it, listed or not.
            path.resolve(strict=True).relative_to(resolved_root)
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            seen_wrapper, model = validate_catalog_document(data)
            if seen_wrapper != wrapper:
                raise ValueError(f"expected {wrapper!r} document, found {seen_wrapper!r}")
            entries.append(
                BuilderCatalogEntry(
                    ref=ref,
                    family=family,
                    id=getattr(model, "id", None),
                    display_name=getattr(model, "display_name", None),
                    notes=getattr(model, "notes", None),
                    error=None,
                )
            )
        except Exception as exc:
            entries.append(
                BuilderCatalogEntry(
                    ref=ref,
                    family=family,
                    id=None,
                    display_name=None,
                    notes=None,
                    error=str(exc),
                )
            )
    return entries


def browse_catalog(family: str, *, roots: CatalogRoots) -> list[BuilderCatalogEntry]:
    """List one family's primitives across the shipped and user roots.

    Shipped entries come first (they are the stable vocabulary); user entries
    follow with ``user:`` references. Both tiers are first-class.
    """
    wrapper = CATALOG_FAMILIES.get(family)
    if wrapper is None:
        raise ValueError(f"unknown catalog family {family!r}")
    entries = _browse_root(family, wrapper, "nodalarc", roots.root)
    if roots.user_root is not None and roots.user_root.is_dir():
        entries.extend(_browse_root(family, wrapper, "user", roots.user_root))
    return entries


def save_user_object(
    family: str,
    document: dict[str, Any],
    *,
    roots: CatalogRoots,
    overwrite: bool = False,
) -> BuilderCatalogEntry:
    """Write one validated document into the user catalog.

    The file content is the canonical serialization of the VALIDATED model —
    the library never stores what the grammar would reject. The object's own
    ``id`` names the file; overwriting an existing user entry requires the
    caller to say so. The shipped catalog is never writable here.
    """
    wrapper = CATALOG_FAMILIES.get(family)
    if wrapper is None:
        raise ValueError(f"unknown catalog family {family!r}")
    if roots.user_root is None:
        raise ValueError("user catalog is not available here")
    seen_wrapper, model = validate_catalog_document(document)
    if seen_wrapper != wrapper:
        raise ValueError(f"expected {wrapper!r} document, found {seen_wrapper!r}")
    object_id = validate_catalog_name(getattr(model, "id", None), label=f"{wrapper} id")
    family_dir = roots.user_root / family
    family_dir.mkdir(parents=True, exist_ok=True)
    path = family_dir / f"{object_id}.yaml"
    if path.exists() and not overwrite:
        raise FileExistsError(f"user catalog entry {family}/{object_id} already exists")
    canonical = {wrapper: model.model_dump(mode="json", by_alias=True, exclude_none=True)}
    path.write_text(
        yaml.dump(canonical, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return BuilderCatalogEntry(
        ref=f"user:{family}/{object_id}.yaml",
        family=family,
        id=object_id,
        display_name=getattr(model, "display_name", None),
        notes=getattr(model, "notes", None),
        error=None,
    )


def delete_user_object(ref: str, *, roots: CatalogRoots) -> None:
    """Delete one user catalog entry. Shipped entries are immutable."""
    if catalog_reference_scheme(ref) != "user":
        raise ValueError("only user catalog entries can be deleted")
    path = resolve_catalog_reference(ref, roots, label="user catalog entry")
    path.unlink()


_FLATTEN_DEPTH_LIMIT = 32


def flatten_user_references(document: Any, *, roots: CatalogRoots, _depth: int = 0) -> Any:
    """Replace every ``user:`` reference with its loaded (wrapped) document.

    Saved and deployed sessions must be HERMETIC: runtime services resolve
    against the shipped catalog alone, and a flattened session means exactly
    what the referencing session meant at the moment of flattening — later
    library edits never ripple into it. Inline objects are the same grammar
    (loader contract G002), so flattening changes no semantics. Shipped
    ``nodalarc:`` references stay references — that content is immutable per
    image tag.
    """
    if isinstance(document, str) and catalog_reference_scheme(document) == "user":
        # Depth counts REFERENCE hops only (a chain longer than the limit is
        # a cycle); structural nesting recurses at the same depth.
        if _depth >= _FLATTEN_DEPTH_LIMIT:
            raise ValueError("user catalog reference cycle detected while flattening")
        _wrapper, wrapped = read_catalog_object(document, roots=roots)
        return flatten_user_references(wrapped, roots=roots, _depth=_depth + 1)
    if isinstance(document, dict):
        return {
            key: flatten_user_references(value, roots=roots, _depth=_depth)
            for key, value in document.items()
        }
    if isinstance(document, list):
        return [flatten_user_references(value, roots=roots, _depth=_depth) for value in document]
    return document


def read_catalog_object(ref: str, *, roots: CatalogRoots) -> tuple[str, dict[str, Any]]:
    """Load one catalog document by reference; returns (wrapper, document).

    The document is the validated grammar object in its authoring-wrapper
    form — the same shape a session may inline. The grammar is the schema.
    """
    path = resolve_catalog_reference(ref, roots, label="catalog object")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    wrapper, model = validate_catalog_document(data)
    return wrapper, {wrapper: model.model_dump(mode="json", by_alias=True, exclude_none=True)}
