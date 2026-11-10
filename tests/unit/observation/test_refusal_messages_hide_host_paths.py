# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Typed refusals name catalog paths and declared inputs, never host paths.

Every producer below chains the operating system's error as ``__cause__`` so a
server log with a traceback keeps the host path; the message a client may see
does not.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import (
    CatalogContainmentError,
    CatalogNotFoundError,
    CatalogRepositoryError,
    CatalogScope,
)
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.physics import EphemerisLocation, PhysicsContext, PhysicsDataError
from nodalarc.physics.datasets import production_location

ROOT = Path(__file__).resolve().parents[3]
SESSION_YAML = (ROOT / "catalog/nodalarc/sessions/earth-leo-simple.yaml").read_bytes()
NOT_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")


def _repository(tmp_path: Path) -> tuple[FilesystemCatalogRepository, CatalogScope, Path]:
    shipped = tmp_path / "shipped"
    (shipped / "sessions").mkdir(parents=True)
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=shipped,
        scope_roots={scope: tmp_path / "user"},
    )
    return repository, scope, shipped


def _assert_hides_host(message: str, tmp_path: Path) -> None:
    assert str(tmp_path) not in message
    assert "Errno" not in message


def test_symlinked_document_is_refused_by_catalog_path(tmp_path: Path) -> None:
    repository, scope, shipped = _repository(tmp_path)
    (shipped / "sessions" / "real.yaml").write_bytes(SESSION_YAML)
    (shipped / "sessions" / "link.yaml").symlink_to(shipped / "sessions" / "real.yaml")
    snapshot = repository.snapshot(scope)

    with pytest.raises(CatalogContainmentError) as raised:
        snapshot.get("nodalarc:sessions/link.yaml")

    assert "sessions/link.yaml" in str(raised.value)
    _assert_hides_host(str(raised.value), tmp_path)


@NOT_ROOT
def test_unreadable_document_is_a_storage_failure_named_by_catalog_path(tmp_path: Path) -> None:
    repository, scope, shipped = _repository(tmp_path)
    locked = shipped / "sessions" / "locked.yaml"
    locked.write_bytes(SESSION_YAML)
    locked.chmod(0)
    snapshot = repository.snapshot(scope)

    with pytest.raises(CatalogRepositoryError) as raised:
        snapshot.get("nodalarc:sessions/locked.yaml")

    assert type(raised.value) is CatalogRepositoryError
    assert not isinstance(raised.value, CatalogNotFoundError)
    assert "sessions/locked.yaml" in str(raised.value)
    assert "PermissionError" in str(raised.value)
    assert isinstance(raised.value.__cause__, PermissionError)
    _assert_hides_host(str(raised.value), tmp_path)


@NOT_ROOT
def test_unreadable_document_read_view_carries_the_same_message(tmp_path: Path) -> None:
    repository, scope, shipped = _repository(tmp_path)
    locked = shipped / "sessions" / "locked.yaml"
    locked.write_bytes(SESSION_YAML)
    locked.chmod(0)
    snapshot = repository.snapshot(scope)

    with pytest.raises(Exception) as raised:
        snapshot.read(CatalogRef("nodalarc:sessions/locked.yaml"))

    assert type(raised.value).__name__ == "CatalogReadFailed"
    assert "sessions/locked.yaml" in str(raised.value)
    _assert_hides_host(str(raised.value), tmp_path)


def test_non_regular_entry_is_refused_by_catalog_path(tmp_path: Path) -> None:
    repository, scope, shipped = _repository(tmp_path)
    os.mkfifo(shipped / "sessions" / "pipe.yaml")
    snapshot = repository.snapshot(scope)

    with pytest.raises(CatalogContainmentError) as raised:
        snapshot.list(namespace="nodalarc")

    assert "sessions/pipe.yaml" in str(raised.value)
    _assert_hides_host(str(raised.value), tmp_path)


def test_vanished_root_is_refused_by_namespace(tmp_path: Path) -> None:
    repository, scope, shipped = _repository(tmp_path)
    snapshot = repository.snapshot(scope)
    shutil.rmtree(shipped)

    with pytest.raises(CatalogContainmentError) as raised:
        snapshot.list(namespace="nodalarc")

    assert "'nodalarc'" in str(raised.value)
    _assert_hides_host(str(raised.value), tmp_path)


def _location_with_unreadable(filename: str, tmp_path: Path) -> EphemerisLocation:
    """The platform's data, except that ``filename`` is a directory."""
    real = production_location()
    source = tmp_path / "ephemerides"
    source.mkdir()
    for item in real.source_dir.iterdir():
        if item.is_file() and item.name != filename:
            (source / item.name).symlink_to(item)
    (source / filename).mkdir()
    return EphemerisLocation(source_dir=source, converted_dir=real.converted_dir)


def test_unreadable_ephemeris_file_names_the_file_and_hides_the_host(tmp_path: Path) -> None:
    location = _location_with_unreadable("moon_pa_de440_200625.bpc", tmp_path)

    with pytest.raises(PhysicsDataError) as raised:
        PhysicsContext(["luna"], location=location)

    message = str(raised.value)
    assert "moon_pa_de440_200625.bpc" in message
    assert "could not be read" in message
    assert "IsADirectoryError" in message
    assert isinstance(raised.value.__cause__, OSError)
    _assert_hides_host(message, tmp_path)
