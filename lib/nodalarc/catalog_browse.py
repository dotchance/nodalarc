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

from typing import Any

import yaml

from nodalarc.catalog_paths import CatalogRoots, resolve_catalog_reference
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


def browse_catalog(family: str, *, roots: CatalogRoots) -> list[BuilderCatalogEntry]:
    """List one family's primitives as validated summary entries."""
    wrapper = CATALOG_FAMILIES.get(family)
    if wrapper is None:
        raise ValueError(f"unknown catalog family {family!r}")
    root = roots.root.resolve(strict=True)
    family_dir = root / family
    entries: list[BuilderCatalogEntry] = []
    if not family_dir.is_dir():
        return entries
    for path in sorted(family_dir.rglob("*.yaml")):
        ref = f"nodalarc:{path.relative_to(root).as_posix()}"
        try:
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


def read_catalog_object(ref: str, *, roots: CatalogRoots) -> tuple[str, dict[str, Any]]:
    """Load one catalog document by reference; returns (wrapper, document).

    The document is the validated grammar object in its authoring-wrapper
    form — the same shape a session may inline. The grammar is the schema.
    """
    path = resolve_catalog_reference(ref, roots, label="catalog object")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    wrapper, model = validate_catalog_document(data)
    return wrapper, {wrapper: model.model_dump(mode="json", by_alias=True, exclude_none=True)}
