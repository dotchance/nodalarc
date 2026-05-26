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
    """Approved roots for NodalArc config catalog references."""

    config_root: Path
    sessions: Path
    constellations: Path
    ground_stations: Path
    ground_station_sets: Path
    constellation_presets: Path

    @classmethod
    def from_config_root(cls, config_root: str | Path) -> CatalogRoots:
        root = Path(config_root)
        return cls(
            config_root=root,
            sessions=root / "sessions",
            constellations=root / "constellations",
            ground_stations=root / "ground-stations",
            ground_station_sets=root / "ground-stations" / "sets",
            constellation_presets=root / "presets" / "constellations",
        )

    @property
    def constellation_ephemeral(self) -> Path:
        return self.constellations / "_ephemeral"

    @property
    def ground_station_ephemeral(self) -> Path:
        return self.ground_stations / "_ephemeral"


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


def _strip_catalog_root_prefix(candidate: Path, root_resolved: Path) -> Path:
    cwd = Path.cwd().resolve(strict=True)
    try:
        root_from_cwd = root_resolved.relative_to(cwd)
    except ValueError:
        root_from_cwd = None

    if root_from_cwd is not None:
        try:
            return candidate.relative_to(root_from_cwd)
        except ValueError:
            pass

    if candidate.parts[:1] == (root_resolved.name,):
        return Path(*candidate.parts[1:])
    return candidate


def _resolve_existing_under(source: str | Path, root: Path, *, label: str) -> Path:
    raw = str(source)
    candidate = _reject_unsafe_path_source(raw, label=label)
    root_resolved = root.resolve(strict=True)
    reference = _validate_yaml_path_reference(
        _strip_catalog_root_prefix(candidate, root_resolved), label=label
    )

    for yaml_path in sorted(
        path for suffix in _YAML_SUFFIXES for path in root_resolved.rglob(f"*{suffix}")
    ):
        if yaml_path.relative_to(root_resolved) != reference:
            continue
        resolved = yaml_path.resolve(strict=True)
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise CatalogPathError(f"{label} escapes approved root: {root}") from exc
        return resolved

    raise FileNotFoundError(f"{label} file not found: {root / reference}")


def _resolve_named_yaml_under(name: str, root: Path, *, label: str) -> Path:
    name = validate_catalog_name(name, label=label)
    root_resolved = root.resolve(strict=True)
    resolved = (root_resolved / f"{name}.yaml").resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise CatalogPathError(f"{label} escapes approved root: {root}") from exc
    return resolved


def _looks_like_path(source: str) -> bool:
    return "/" in source or "\\" in source or source.endswith((".yaml", ".yml"))


def resolve_constellation_reference(source: str | Path, roots: CatalogRoots) -> Path:
    """Resolve an API/session constellation reference under approved roots."""
    raw = str(source)
    if _looks_like_path(raw):
        return _resolve_existing_under(raw, roots.constellations, label="constellation")

    try:
        return _resolve_named_yaml_under(raw, roots.constellations, label="constellation")
    except FileNotFoundError as exc:
        preset = _resolve_named_yaml_under(
            raw, roots.constellation_presets, label="constellation preset"
        )
        import yaml

        data = yaml.safe_load(preset.read_text()) or {}
        nested = data.get("constellation")
        if not isinstance(nested, str):
            raise CatalogPathError(
                f"constellation preset {raw!r} has no constellation path"
            ) from exc
        return resolve_constellation_reference(nested, roots)


def resolve_ground_station_reference(source: str | Path, roots: CatalogRoots) -> Path:
    """Resolve an API/session ground-station reference under approved roots."""
    raw = str(source)
    if _looks_like_path(raw):
        return _resolve_existing_under(raw, roots.ground_stations, label="ground_stations")
    return _resolve_named_yaml_under(raw, roots.ground_station_sets, label="ground_stations")


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
