"""Tests for catalog path containment helpers."""

from __future__ import annotations

import os

import pytest
from nodalarc.catalog_paths import (
    CatalogPathError,
    CatalogRoots,
    catalog_reference_scheme,
    generated_file_path,
    generated_file_stem,
    resolve_catalog_reference,
    resolve_constellation_reference,
    resolve_site_set_reference,
    safe_display_stem,
    validate_catalog_name,
    write_text_exclusive,
)


def _make_roots(tmp_path, monkeypatch) -> CatalogRoots:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "catalog" / "nodalarc"
    (root / "constellations" / "earth" / "leo").mkdir(parents=True)
    (root / "site-sets" / "earth" / "leo").mkdir(parents=True)
    (root / "sessions").mkdir(parents=True)
    return CatalogRoots.from_catalog_root(root)


def test_resolves_nodalarc_constellation_reference_under_catalog_root(tmp_path, monkeypatch):
    roots = _make_roots(tmp_path, monkeypatch)
    target = roots.root / "constellations" / "earth" / "leo" / "earth-leo-ring-36.yaml"
    target.write_text("constellation: {}\n", encoding="utf-8")

    resolved = resolve_constellation_reference(
        "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
        roots,
    )

    assert resolved == target.resolve()


def test_resolves_nodalarc_site_set_reference_under_catalog_root(tmp_path, monkeypatch):
    roots = _make_roots(tmp_path, monkeypatch)
    target = roots.root / "site-sets" / "earth" / "leo" / "earth-leo-sites.yaml"
    target.write_text("site_set: {}\n", encoding="utf-8")

    resolved = resolve_site_set_reference(
        "nodalarc:site-sets/earth/leo/earth-leo-sites.yaml",
        roots,
    )

    assert resolved == target.resolve()


@pytest.mark.parametrize(
    "source",
    [
        "constellations/earth/leo/earth-leo-ring-36.yaml",
        "configs/constellations/earth-leo-ring-36.yaml",
        "earth-leo-ring-36",
        "/tmp/outside.yaml",
        "nodalarc:/tmp/outside.yaml",
        "nodalarc:../../outside.yaml",
        "nodalarc:constellations/../outside.yaml",
        "nodalarc:constellations/name with spaces.yaml",
        "nodalarc:constellations/not-yaml.txt",
        "nodalarc:constellations\\windows.yaml",
    ],
)
def test_catalog_reference_rejects_non_token_or_unsafe_paths(tmp_path, monkeypatch, source):
    roots = _make_roots(tmp_path, monkeypatch)

    with pytest.raises(CatalogPathError):
        resolve_catalog_reference(source, roots)


def test_rejects_symlink_escape_under_catalog_root(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink not supported on this platform")
    roots = _make_roots(tmp_path, monkeypatch)
    outside = tmp_path / "outside.yaml"
    outside.write_text("constellation: {}\n", encoding="utf-8")
    link = roots.root / "constellations" / "earth" / "leo" / "escape.yaml"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation not permitted: {exc}")

    with pytest.raises(CatalogPathError):
        resolve_constellation_reference("nodalarc:constellations/earth/leo/escape.yaml", roots)


def test_validate_catalog_name_rejects_path_syntax():
    assert validate_catalog_name("earth-leo-ring-36") == "earth-leo-ring-36"
    with pytest.raises(CatalogPathError):
        validate_catalog_name("../outside")
    with pytest.raises(CatalogPathError):
        validate_catalog_name("bad name")


def test_generated_file_stem_uses_defined_sanitization():
    assert safe_display_stem(" My Session! ") == "my_session"
    assert generated_file_stem(" My Session! ", "abc123") == "my_session-abc123"


def test_generated_file_stem_rejects_path_names():
    with pytest.raises(CatalogPathError):
        generated_file_stem("../../outside", "abc123")


def test_generated_file_path_writes_exclusively_under_root(tmp_path):
    root = tmp_path / "catalog" / "nodalarc" / "sessions"
    target = generated_file_path(root, "_wizard-demo-abc123.yaml")
    write_text_exclusive(target, "session: {}\n")

    with pytest.raises(FileExistsError):
        write_text_exclusive(target, "session: {}\n")

    assert target.read_text(encoding="utf-8") == "session: {}\n"


def _make_two_root_setup(tmp_path, monkeypatch) -> CatalogRoots:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "catalog" / "nodalarc"
    (root / "nodes" / "space").mkdir(parents=True)
    (root / "sessions").mkdir(parents=True)
    user_root = tmp_path / "data" / "user-catalog"
    (user_root / "nodes").mkdir(parents=True)
    return CatalogRoots.from_catalog_root(root, user_root=user_root)


def test_resolves_user_reference_under_user_root(tmp_path, monkeypatch):
    roots = _make_two_root_setup(tmp_path, monkeypatch)
    target = roots.user_root / "nodes" / "my-router.yaml"
    target.write_text("node: {}\n", encoding="utf-8")

    resolved = resolve_catalog_reference("user:nodes/my-router.yaml", roots)

    assert resolved == target.resolve()


def test_resolves_session_references_in_both_catalog_roots(tmp_path, monkeypatch):
    roots = _make_two_root_setup(tmp_path, monkeypatch)
    shipped = roots.root / "sessions" / "shipped-session.yaml"
    shipped.write_text("session: {}\n", encoding="utf-8")
    user_sessions = roots.user_root / "sessions"
    user_sessions.mkdir()
    user = user_sessions / "user-session.yaml"
    user.write_text("session: {}\n", encoding="utf-8")

    assert (
        resolve_catalog_reference("nodalarc:sessions/shipped-session.yaml", roots)
        == shipped.resolve()
    )
    assert resolve_catalog_reference("user:sessions/user-session.yaml", roots) == user.resolve()


def test_generic_resolver_rejects_unknown_or_noncanonical_family_paths(tmp_path, monkeypatch):
    roots = _make_two_root_setup(tmp_path, monkeypatch)
    with pytest.raises(CatalogPathError):
        resolve_catalog_reference(
            "nodalarc:Custom_Family/Nested/Example_Name.YML",
            roots,
        )


def test_catalog_reference_scheme_delegates_namespace_recognition():
    assert catalog_reference_scheme("nodalarc:nodes/router.yaml") == "nodalarc"
    assert catalog_reference_scheme("user:nodes/router.yaml") == "user"
    assert catalog_reference_scheme("catalog:nodes/router.yaml") is None


def test_user_reference_rejected_without_configured_user_root(tmp_path, monkeypatch):
    roots = _make_roots(tmp_path, monkeypatch)
    with pytest.raises(CatalogPathError, match="user catalog"):
        resolve_catalog_reference("user:nodes/my-router.yaml", roots)


def test_user_reference_cannot_reach_shipped_root(tmp_path, monkeypatch):
    """The schemes are containment boundaries: a user: token looks only under
    the user root — a file that exists in the shipped catalog is a miss, not
    a fallback, when addressed with the user scheme."""
    roots = _make_two_root_setup(tmp_path, monkeypatch)
    shipped = roots.root / "nodes" / "space" / "shared-name.yaml"
    shipped.write_text("node: {}\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        resolve_catalog_reference("user:nodes/space/shared-name.yaml", roots)


def test_user_reference_rejects_traversal_and_symlink_escape(tmp_path, monkeypatch):
    roots = _make_two_root_setup(tmp_path, monkeypatch)
    with pytest.raises(CatalogPathError):
        resolve_catalog_reference("user:../secrets.yaml", roots)

    outside = tmp_path / "outside.yaml"
    outside.write_text("node: {}\n", encoding="utf-8")
    link = roots.user_root / "nodes" / "sneaky.yaml"
    os.symlink(outside, link)
    with pytest.raises(CatalogPathError):
        resolve_catalog_reference("user:nodes/sneaky.yaml", roots)
