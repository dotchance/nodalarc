# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Catalog path containment helpers for API-facing config references."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path


class CatalogPathError(ValueError):
    """Raised when a config path escapes an approved catalog root."""


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_YAML_SUFFIXES = {".yaml", ".yml"}


@dataclass(frozen=True)
class CatalogRoots:
    """Approved root for the NodalArc catalog."""

    root: Path
    sessions: Path

    @classmethod
    def from_catalog_root(cls, catalog_root: str | Path = "catalog/nodalarc") -> CatalogRoots:
        root = Path(catalog_root)
        return cls(root=root, sessions=root / "sessions")


def safe_display_stem(name: str) -> str:
    """Return the exact display-name stem used for generated files."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:48] or "session"


def reject_path_name(name: str, *, label: str = "name") -> None:
    """Reject display/name values that contain path separators or traversal."""
    if "/" in name or "\\" in name:
        raise CatalogPathError(f"{label} must not contain path separators")
    if name == ".." or ".." in Path(name).parts:
        raise CatalogPathError(f"{label} must not contain path traversal")


def validate_catalog_name(name: str, *, label: str = "name") -> str:
    """Return a catalog object name after rejecting path syntax."""
    if not isinstance(name, str):
        raise CatalogPathError(f"{label} must be a string")
    reject_path_name(name, label=label)
    if not _SAFE_NAME.fullmatch(name):
        raise CatalogPathError(f"{label} must contain only [A-Za-z0-9_-]")
    return name


def generated_file_stem(display_name: str, write_id: str | None = None) -> str:
    """Return a collision-resistant generated-file stem for one API write."""
    reject_path_name(display_name, label="session.name")
    ident = write_id or uuid.uuid4().hex
    if not _SAFE_NAME.fullmatch(ident):
        raise CatalogPathError("write identifier must contain only [A-Za-z0-9_-]")
    return f"{safe_display_stem(display_name)}-{ident}"


def config_value_for(path: Path) -> str:
    """Return a stable config string, preferring repo-relative paths."""
    resolved = path if path.is_absolute() else Path.cwd() / path
    try:
        return str(resolved.relative_to(Path.cwd()))
    except ValueError:
        return str(resolved)


def _reject_unsafe_path_source(source: str, *, label: str) -> Path:
    if not source:
        raise CatalogPathError(f"{label} is required")
    if "\\" in source:
        raise CatalogPathError(f"{label} must not contain backslash path separators")
    path = Path(source)
    if path.is_absolute():
        raise CatalogPathError(f"{label} must not be absolute")
    if ".." in path.parts:
        raise CatalogPathError(f"{label} must not contain path traversal")
    return path


def _validate_yaml_path_reference(path: Path, *, label: str) -> Path:
    if not path.parts:
        raise CatalogPathError(f"{label} path is required")

    filename = path.name
    suffix = Path(filename).suffix.lower()
    if suffix not in _YAML_SUFFIXES:
        raise CatalogPathError(f"{label} path must be YAML")

    parts = [validate_catalog_name(part, label=f"{label} directory") for part in path.parts[:-1]]
    stem = validate_catalog_name(Path(filename).stem, label=f"{label} filename")
    return Path(*parts, f"{stem}{suffix}")


def resolve_catalog_reference(
    source: str | Path,
    roots: CatalogRoots,
    *,
    label: str = "catalog reference",
) -> Path:
    """Resolve a ``nodalarc:<path>`` token under the catalog root."""
    raw = str(source)
    if not raw.startswith("nodalarc:"):
        raise CatalogPathError(f"{label} must be a nodalarc:<path> reference")
    relative = raw.split(":", 1)[1]
    reference = _validate_yaml_path_reference(
        _reject_unsafe_path_source(relative, label=label), label=label
    )
    root_resolved = roots.root.resolve(strict=True)
    resolved = (root_resolved / reference).resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise CatalogPathError(f"{label} escapes approved catalog root: {roots.root}") from exc
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
