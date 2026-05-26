"""Tests for API-facing catalog path containment helpers."""

from __future__ import annotations

import os

import pytest
from nodalarc.catalog_paths import (
    CatalogPathError,
    CatalogRoots,
    generated_file_path,
    generated_file_stem,
    resolve_constellation_reference,
    resolve_ground_station_reference,
    safe_display_stem,
    write_text_exclusive,
)


def _make_roots(tmp_path, monkeypatch) -> CatalogRoots:
    monkeypatch.chdir(tmp_path)
    roots = CatalogRoots.from_config_root("configs")
    roots.constellations.mkdir(parents=True)
    roots.ground_station_sets.mkdir(parents=True)
    roots.ground_stations.mkdir(parents=True, exist_ok=True)
    roots.sessions.mkdir(parents=True)
    roots.constellation_presets.mkdir(parents=True)
    return roots


def test_resolves_known_constellation_name_under_root(tmp_path, monkeypatch):
    roots = _make_roots(tmp_path, monkeypatch)
    (roots.constellations / "demo.yaml").write_text("mode: parametric\n")

    resolved = resolve_constellation_reference("demo", roots)

    assert resolved == (roots.constellations / "demo.yaml").resolve()


@pytest.mark.parametrize(
    "source",
    [
        "demo.yaml",
        "constellations/demo.yaml",
        "configs/constellations/demo.yaml",
    ],
)
def test_resolves_existing_constellation_paths_from_approved_bases(tmp_path, monkeypatch, source):
    roots = _make_roots(tmp_path, monkeypatch)
    (roots.constellations / "demo.yaml").write_text("mode: parametric\n")

    resolved = resolve_constellation_reference(source, roots)

    assert resolved == (roots.constellations / "demo.yaml").resolve()


@pytest.mark.parametrize(
    "source",
    [
        "../../outside",
        "foo/../../outside",
        "/tmp/outside",
        "name\\with\\separators",
    ],
)
def test_rejects_unsafe_constellation_references(tmp_path, monkeypatch, source):
    roots = _make_roots(tmp_path, monkeypatch)

    with pytest.raises((CatalogPathError, FileNotFoundError)):
        resolve_constellation_reference(source, roots)


def test_rejects_symlink_escape_under_constellation_root(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink not supported on this platform")
    roots = _make_roots(tmp_path, monkeypatch)
    outside = tmp_path / "outside.yaml"
    outside.write_text("mode: parametric\n")
    try:
        (roots.constellations / "escape.yaml").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation not permitted: {exc}")

    with pytest.raises(CatalogPathError):
        resolve_constellation_reference("configs/constellations/escape.yaml", roots)


def test_resolves_ground_station_set_name_under_root(tmp_path, monkeypatch):
    roots = _make_roots(tmp_path, monkeypatch)
    (roots.ground_station_sets / "global.yaml").write_text("ground_station_set:\n  stations: []\n")

    resolved = resolve_ground_station_reference("global", roots)

    assert resolved == (roots.ground_station_sets / "global.yaml").resolve()


def test_generated_file_stem_uses_defined_sanitization():
    assert safe_display_stem(" My Constellation! ") == "My_Constellation"
    assert generated_file_stem(" My Constellation! ", "abc123") == "My_Constellation-abc123"


def test_generated_file_stem_rejects_path_names():
    with pytest.raises(CatalogPathError):
        generated_file_stem("../../outside", "abc123")


def test_generated_file_path_writes_exclusively_under_root(tmp_path):
    root = tmp_path / "configs" / "sessions"
    target = generated_file_path(root, "_wizard-demo-abc123.yaml")
    write_text_exclusive(target, "session: {}\n")

    with pytest.raises(FileExistsError):
        write_text_exclusive(target, "session: {}\n")

    assert target.read_text() == "session: {}\n"
