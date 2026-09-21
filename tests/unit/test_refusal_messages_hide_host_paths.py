"""Typed refusals name catalog paths and declared inputs, never host paths.

Every producer below chains the operating system's error as ``__cause__`` so a
server log with a traceback keeps the host path; the message a client may see
does not.
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import (
    CatalogContainmentError,
    CatalogNotFoundError,
    CatalogRepositoryError,
    CatalogScope,
)
from nodalarc.ephemeris_runtime import EphemerisValidationError, validate_ephemeris_manifest
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.ephemeris import EphemerisConfig, EphemerisKernel

ROOT = Path(__file__).resolve().parents[2]
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


def _manifest(declared: str) -> EphemerisConfig:
    return EphemerisConfig(
        provider="skyfield_bsp",
        quality_tier="de440s",
        kernels=[
            EphemerisKernel(
                id="k",
                path=declared,
                checksum="sha256:" + "0" * 64,
                targets=["luna"],
                frame="gcrs",
                coverage_start=datetime(2000, 1, 1, tzinfo=UTC),
                coverage_end=datetime(2100, 1, 1, tzinfo=UTC),
            )
        ],
    )


def _validate(declared: str, base_dir: Path) -> str:
    with pytest.raises(EphemerisValidationError) as raised:
        validate_ephemeris_manifest(
            _manifest(declared),
            required_bodies={"luna"},
            epoch_unix=datetime(2026, 6, 8, tzinfo=UTC).timestamp(),
            base_dir=base_dir,
        )
    assert isinstance(raised.value.__cause__, OSError)
    return str(raised.value)


def test_unresolvable_kernel_path_names_the_declared_path(tmp_path: Path) -> None:
    (tmp_path / "kernels").mkdir()
    (tmp_path / "kernels" / "de440s.bsp").write_bytes(b"not a directory")
    declared = "kernels/de440s.bsp/inner.bsp"

    message = _validate(declared, tmp_path)

    assert "could not be resolved" in message
    assert declared in message
    assert "NotADirectoryError" in message
    _assert_hides_host(message, tmp_path)


def test_unreadable_kernel_names_the_declared_path(tmp_path: Path) -> None:
    declared = "kernels/de440s.bsp"
    (tmp_path / declared).mkdir(parents=True)

    message = _validate(declared, tmp_path)

    assert "could not be read" in message
    assert declared in message
    assert "IsADirectoryError" in message
    _assert_hides_host(message, tmp_path)
