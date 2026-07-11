"""Contracts for ordinary prepared-session tree discovery."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from nodalarc import prepared_tree as prepared_tree_module
from nodalarc.prepared_tree import (
    PreparedTreeError,
    PreparedTreeErrorCode,
    load_prepared_tree_session_resolution,
)
from ome.main import _load_session_config

from tests.catalog_session_fixtures import CatalogSessionFixture, build_catalog_session_fixture
from tools.na_reconfig import _selected_resolution

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"


def _prepared_user_tree(tmp_path: Path) -> tuple[Path, CatalogSessionFixture]:
    source = build_catalog_session_fixture(
        name="prepared-tree-user-session",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
        base_path=tmp_path,
    )
    tree = tmp_path / "prepared"
    user_root = tree / "catalog" / "user"
    user_root.parent.mkdir(parents=True)
    assert source.roots.user_root is not None
    shutil.copytree(source.roots.user_root, user_root)
    session_path = tree / "session.yaml"
    session_path.write_bytes(source.session_path.read_bytes())
    return session_path, source


def _exported_user_tree(tmp_path: Path) -> tuple[Path, CatalogSessionFixture]:
    source = build_catalog_session_fixture(
        name="exported-tree-user-session",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
        base_path=tmp_path,
    )
    tree = tmp_path / "exported"
    user_root = tree / "catalog" / "user"
    user_root.parent.mkdir(parents=True)
    assert source.roots.user_root is not None
    shutil.copytree(source.roots.user_root, user_root)
    session_path = user_root / "sessions" / "nested" / "exported-tree-user-session.yaml"
    session_path.parent.mkdir(parents=True)
    session_path.write_bytes(source.session_path.read_bytes())
    return session_path, source


def test_bare_nodalarc_only_session_needs_no_adjacent_tree() -> None:
    resolution = load_prepared_tree_session_resolution(
        SIMPLE_SESSION,
        installed_shipped_root=SHIPPED_ROOT,
        origin="test.prepared-tree.bare",
    )

    assert resolution.resolved.session.name == "earth-leo-simple"
    assert resolution.resolved.source_context.origin == "test.prepared-tree.bare"


def test_adjacent_user_catalog_resolves_for_ome_batch_and_reconfig(tmp_path: Path) -> None:
    session_path, _source = _prepared_user_tree(tmp_path)

    ome_bundle = _load_session_config(
        session_path,
        run_id="run-prepared-tree-0001",
        installed_shipped_root=SHIPPED_ROOT,
    )
    reconfig_resolution = _selected_resolution(
        str(session_path),
        None,
        installed_shipped_root=SHIPPED_ROOT,
    )

    assert ome_bundle.resolved.session.name == "prepared-tree-user-session"
    assert ome_bundle.session_id == "run-prepared-tree-0001"
    assert reconfig_resolution.resolved.session.name == "prepared-tree-user-session"
    assert reconfig_resolution.resolved.source_context.origin == "na-reconfig.offline"


def test_exported_catalog_session_path_discovers_prepared_tree_for_both_consumers(
    tmp_path: Path,
) -> None:
    session_path, _source = _exported_user_tree(tmp_path)

    ome_bundle = _load_session_config(
        session_path,
        run_id="run-exported-tree-0001",
        installed_shipped_root=SHIPPED_ROOT,
    )
    reconfig_resolution = _selected_resolution(
        str(session_path),
        None,
        installed_shipped_root=SHIPPED_ROOT,
    )

    assert ome_bundle.resolved.session.name == "exported-tree-user-session"
    assert ome_bundle.session_id == "run-exported-tree-0001"
    assert reconfig_resolution.resolved.session.name == "exported-tree-user-session"


def test_missing_user_reference_has_typed_expected_path(tmp_path: Path) -> None:
    session_path, source = _prepared_user_tree(tmp_path)
    assert source.constellation_ref is not None
    missing_path = session_path.parent / "catalog" / "user" / source.constellation_ref.relative_path
    missing_path.unlink()

    with pytest.raises(PreparedTreeError) as raised:
        load_prepared_tree_session_resolution(
            session_path,
            installed_shipped_root=SHIPPED_ROOT,
        )

    assert raised.value.code is PreparedTreeErrorCode.MISSING_CATALOG_REFERENCE
    assert raised.value.evidence.ref == str(source.constellation_ref)
    assert raised.value.evidence.expected_path == str(missing_path)
    assert str(raised.value) == (
        f"Session {session_path} references {source.constellation_ref}; expected it at "
        f"{missing_path}; not found"
    )


def test_exported_catalog_session_missing_ref_names_tree_catalog_path(tmp_path: Path) -> None:
    session_path, source = _exported_user_tree(tmp_path)
    assert source.constellation_ref is not None
    missing_path = session_path.parents[3] / "user" / source.constellation_ref.relative_path
    missing_path.unlink()

    with pytest.raises(PreparedTreeError) as raised:
        load_prepared_tree_session_resolution(
            session_path,
            installed_shipped_root=SHIPPED_ROOT,
        )

    assert raised.value.code is PreparedTreeErrorCode.MISSING_CATALOG_REFERENCE
    assert raised.value.evidence.ref == str(source.constellation_ref)
    assert raised.value.evidence.expected_path == str(missing_path)


def test_user_reference_without_adjacent_tree_names_conventional_path(tmp_path: Path) -> None:
    source = build_catalog_session_fixture(
        name="prepared-tree-missing-user-root",
        constellation={"planes": {"count": 1, "sats_per_plane": 1}},
        ground_stations={"stations": ["a"]},
        base_path=tmp_path,
    )
    assert source.constellation_ref is not None
    session_path = tmp_path / "bare" / "session.yaml"
    session_path.parent.mkdir()
    session_path.write_bytes(source.session_path.read_bytes())
    expected_path = (
        session_path.parent / "catalog" / "user" / source.constellation_ref.relative_path
    )

    with pytest.raises(PreparedTreeError) as raised:
        load_prepared_tree_session_resolution(
            session_path,
            installed_shipped_root=SHIPPED_ROOT,
        )

    assert raised.value.code is PreparedTreeErrorCode.MISSING_CATALOG_REFERENCE
    assert raised.value.evidence.ref == str(source.constellation_ref)
    assert raised.value.evidence.expected_path == str(expected_path)


def test_exact_tree_shipped_copy_is_verified_then_installed_catalog_resolves(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.yaml"
    session_path.write_bytes(SIMPLE_SESSION.read_bytes())
    copied_path = tmp_path / "catalog" / "nodalarc" / "bodies" / "earth.yaml"
    copied_path.parent.mkdir(parents=True)
    copied_path.write_bytes((SHIPPED_ROOT / "bodies" / "earth.yaml").read_bytes())

    resolution = load_prepared_tree_session_resolution(
        session_path,
        installed_shipped_root=SHIPPED_ROOT,
    )

    assert resolution.resolved.nodes


def test_tree_shipped_drift_is_refused_before_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_path = tmp_path / "session.yaml"
    session_path.write_bytes(SIMPLE_SESSION.read_bytes())
    copied_path = tmp_path / "catalog" / "nodalarc" / "bodies" / "earth.yaml"
    copied_path.parent.mkdir(parents=True)
    copied_path.write_bytes((SHIPPED_ROOT / "bodies" / "earth.yaml").read_bytes() + b"\n# drift\n")
    monkeypatch.setattr(
        prepared_tree_module,
        "load_session_resolution_from_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resolver ran")),
    )

    with pytest.raises(PreparedTreeError) as raised:
        load_prepared_tree_session_resolution(
            session_path,
            installed_shipped_root=SHIPPED_ROOT,
        )

    assert raised.value.code is PreparedTreeErrorCode.SHIPPED_ASSET_MISMATCH
    assert raised.value.evidence.ref == "nodalarc:bodies/earth.yaml"
