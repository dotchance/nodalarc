# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog path containment helpers for API-facing config references."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from nodalarc.catalog_refs import (
    CatalogRef,
    CatalogReferenceError,
    catalog_reference_namespace,
    is_catalog_name,
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
    sessions: Path
    user_root: Path | None = None

    @classmethod
    def from_catalog_root(
        cls,
        catalog_root: str | Path = "catalog/nodalarc",
        *,
        user_root: str | Path | None = None,
    ) -> CatalogRoots:
        root = Path(catalog_root)
        return cls(
            root=root,
            sessions=root / "sessions",
            user_root=Path(user_root) if user_root is not None else None,
        )


def safe_display_stem(name: str) -> str:
    """Return the exact display-name stem used for generated files."""
    return re.sub(r"[^a-z0-9_-]+", "_", name.strip().lower()).strip("_")[:48] or "session"


def reject_path_name(name: str, *, label: str = "name") -> None:
    """Reject display/name values that contain path separators or traversal."""
    if "/" in name or "\\" in name:
        raise CatalogPathError(f"{label} must not contain path separators")
    if name == ".." or ".." in Path(name).parts:
        raise CatalogPathError(f"{label} must not contain path traversal")


def validate_catalog_name(name: str, *, label: str = "name") -> str:
    """Return a catalog object name after rejecting path syntax."""
    try:
        return validate_reference_name(name, label=label)
    except CatalogReferenceError as exc:
        raise CatalogPathError(str(exc)) from exc


def generated_file_stem(display_name: str, write_id: str | None = None) -> str:
    """Return a collision-resistant generated-file stem for one API write."""
    reject_path_name(display_name, label="session.name")
    ident = write_id or uuid.uuid4().hex
    if not is_catalog_name(ident):
        raise CatalogPathError("write identifier must contain only [A-Za-z0-9_-]")
    return f"{safe_display_stem(display_name)}-{ident}"


def config_value_for(path: Path) -> str:
    """Return a stable config string, preferring repo-relative paths."""
    resolved = path if path.is_absolute() else Path.cwd() / path
    try:
        return str(resolved.relative_to(Path.cwd()))
    except ValueError:
        return str(resolved)


def catalog_reference_scheme(source: str | Path) -> str | None:
    """Return the catalog scheme of a reference token, if it has one."""
    return catalog_reference_namespace(source)


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


def resolve_constellation_reference(source: str | Path, roots: CatalogRoots) -> Path:
    """Resolve a constellation catalog token."""
    return resolve_catalog_reference(source, roots, label="constellation")


def resolve_site_set_reference(source: str | Path, roots: CatalogRoots) -> Path:
    """Resolve a site-set catalog token."""
    return resolve_catalog_reference(source, roots, label="ground placement")


def validate_station_names(names: list[str]) -> None:
    """Validate individual ground-station names before loader path expansion."""
    for name in names:
        validate_catalog_name(name, label="ground station name")


def generated_file_path(root: Path, filename: str) -> Path:
    """Resolve a new generated file path under an approved root."""
    reject_path_name(filename, label="generated filename")
    if not filename.endswith((".yaml", ".yml")):
        raise CatalogPathError("generated filename must be YAML")
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve(strict=True)
    resolved = (root_resolved / filename).resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise CatalogPathError(f"generated filename escapes approved root: {root}") from exc
    return resolved


def write_text_exclusive(path: Path, text: str) -> None:
    """Write text without overwriting an existing generated file."""
    with path.open("x", encoding="utf-8") as fh:
        fh.write(text)


def write_text_atomic(path: Path, text: str) -> None:
    """Write text atomically, replacing any existing generated file.

    For writes where the filename is the identity (one artifact per name):
    a temp file in the same directory then os.replace, so readers never see
    a partial file and a re-save never leaves siblings behind.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
