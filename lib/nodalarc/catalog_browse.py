# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog browsing — list and read primitive files for authoring surfaces.

Sits beside ``catalog_paths`` (path containment) and ``models.catalog`` (the
grammar): every listed entry is validated through the same catalog document
validator the resolver uses, so the library never advertises a primitive the
resolver would reject. A file that fails validation is listed with its error —
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


def _entry_summary(wrapper: str, model: Any) -> str | None:
    """The entry's hardware line — what this block is, at a glance."""
    try:
        if wrapper == "constellation":
            planes = model.planes.count
            slots = model.slots_per_plane
            node = model.node if isinstance(model.node, str) else "inline node"
            node_name = node.split("/")[-1].removesuffix(".yaml") if isinstance(node, str) else node
            return f"{planes}×{slots} = {planes * slots} sat · {node_name}"
        if wrapper == "site_set":
            return f"{len(model.sites)} sites"
        if wrapper == "node":
            mounts = " · ".join(f"{m.role} ×{m.count}" for m in model.terminals)
            lan = f" · lan ×{len(model.ethernet)}" if model.ethernet else ""
            return f"{model.forwarding}{' · ' + mounts if mounts else ''}{lan}"
        if wrapper == "terminal":
            # Planning-critical capabilities: who can talk to whom depends on
            # range, rate, slew, and simultaneous-track capacity.
            signal = (
                f"rf {model.signal.band} {model.signal.frequency_hz / 1e9:g} GHz"
                if model.medium == "rf"
                else f"optical {model.signal.wavelength_nm:.0f} nm"
            )
            bw = model.bandwidth_mbps
            rate = (
                f"{bw.transmit:g}/{bw.receive:g} Mbps"
                if bw.transmit != bw.receive
                else f"{bw.transmit:g} Mbps"
            )
            slew = model.limits.max_tracking_rate_deg_s
            return (
                f"{signal} · {model.max_range_km:.0f} km · {rate}"
                f" · slew {slew:g}°/s · tracks {model.tracking_capacity}"
            )
        if wrapper == "orbit":
            shape = getattr(model, "shape", None)
            if shape is not None and hasattr(shape, "altitude_km"):
                alt = f"{shape.altitude_km:.0f} km circular"
            elif shape is not None:
                alt = f"{shape.perigee_altitude_km:.0f}×{shape.apogee_altitude_km:.0f} km"
            else:
                elements = model.elements
                alt = f"a={elements.semi_major_axis_km:.0f} km e={elements.eccentricity}"
            return f"{alt} · {model.orientation.inclination_deg:.1f}°"
        if wrapper == "space_node_set":
            return f"{len(model.nodes)} placed nodes"
        if wrapper == "body":
            return f"R={model.mean_radius_km:.0f} km"
    except Exception:
        return None
    return None


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
                    summary=_entry_summary(wrapper, model),
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
    """List one family's primitives across the user and shipped roots.

    The user's entries come first: what someone just made is what they are
    working with, and burying it under the shipped vocabulary made their own
    library read as missing. Shipped ``nodalarc:`` entries follow. Both
    tiers are first-class; order is prominence, not privilege.
    """
    wrapper = CATALOG_FAMILIES.get(family)
    if wrapper is None:
        raise ValueError(f"unknown catalog family {family!r}")
    entries: list[BuilderCatalogEntry] = []
    if roots.user_root is not None and roots.user_root.is_dir():
        entries.extend(_browse_root(family, wrapper, "user", roots.user_root))
    entries.extend(_browse_root(family, wrapper, "nodalarc", roots.root))
    return entries


def save_user_object(
    family: str,
    document: dict[str, Any],
    *,
    roots: CatalogRoots,
    overwrite: bool = False,
) -> BuilderCatalogEntry:
    """Write one validated document into the user catalog.

    The file content is the canonical serialization of the validated model —
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
        # Depth counts reference hops only (a chain longer than the limit is
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


def rehydrate_user_references(document: Any, *, roots: CatalogRoots) -> Any:
    """Replace inline objects that match current user-library content with
    their ``user:`` references — the read-side inverse of
    ``flatten_user_references``.

    Saved sessions are hermetic (user references are inlined at save time);
    the builder edits in the AUTHORING form (library objects by reference).
    Substitution happens only on deep equality with an entry's flattened
    document, so the rehydrated session means exactly what the file means
    (loader contract: inline and reference are the same grammar). Inline
    objects that match nothing — drifted or deleted library entries,
    hand-authored blocks — stay inline, verbatim.
    """
    index = _user_reference_index(roots)
    if not index:
        return document
    return _rehydrate(document, index)


def _user_reference_index(roots: CatalogRoots) -> list[tuple[dict[str, Any], str]]:
    """Every readable user-library entry as (flattened document, ref),
    ref-sorted so duplicate contents substitute deterministically."""
    if roots.user_root is None or not roots.user_root.is_dir():
        return []
    index: list[tuple[dict[str, Any], str]] = []
    for family, wrapper in CATALOG_FAMILIES.items():
        for entry in _browse_root(family, wrapper, "user", roots.user_root):
            if entry.error is not None:
                continue
            try:
                _wrapper, wrapped = read_catalog_object(entry.ref, roots=roots)
                index.append((flatten_user_references(wrapped, roots=roots), entry.ref))
            except ValueError, OSError, yaml.YAMLError:
                # The file is re-read here (a concurrent library save can
                # tear it): a broken entry stays inline in the session,
                # never fails the whole load.
                continue
    index.sort(key=lambda item: item[1])
    return index


def _rehydrate(document: Any, index: list[tuple[dict[str, Any], str]]) -> Any:
    if isinstance(document, dict):
        for flattened, ref in index:
            if document == flattened:
                return ref
        return {key: _rehydrate(value, index) for key, value in document.items()}
    if isinstance(document, list):
        return [_rehydrate(value, index) for value in document]
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
