"""Tests for required-selection runtime service startup and health."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_upload import CatalogUpload, encode_catalog_upload
from nodalarc.content_identity import canonical_json_bytes, sha256_digest
from nodalarc.prepared_session import (
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)
from nodalarc.runtime_config import (
    RUNTIME_DEPLOYMENT_CONTEXT_FILENAME,
    RuntimeDeploymentContext,
)
from nodalarc.runtime_service_config import (
    CATALOG_UPLOAD_SELECTION_FILENAME,
    SESSION_RUN_ID_FILENAME,
    SESSION_YAML_FILENAME,
    RuntimeConfigHealth,
    read_mounted_session_config,
)

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
RUN_ID = "run-runtime-service-0001"
RELEASE = "nodalarc-test"
BUILD = "build-test"


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


@pytest.fixture(scope="module")
def prepared() -> PreparedSessionFiles:
    root_yaml = SIMPLE_SESSION.read_bytes()
    return prepare_session_files(
        root_yaml,
        FilesystemCatalogReadView(CatalogRoots.from_catalog_root(SHIPPED_ROOT)),
        source=PreparedSessionSource(
            logical_id="nodalarc:sessions/earth-leo-simple.yaml",
            origin="test.runtime_service_config.prepare",
        ),
        source_revision=_digest(root_yaml),
        available_node_count=100,
        run_id=RUN_ID,
    )


@pytest.fixture(scope="module")
def upload(prepared: PreparedSessionFiles) -> CatalogUpload:
    return encode_catalog_upload(prepared, upload_id="service-test-upload")


def _context(upload: CatalogUpload, prepared: PreparedSessionFiles) -> RuntimeDeploymentContext:
    return RuntimeDeploymentContext(
        cr_uid="cr-runtime-service-0001",
        cr_generation=7,
        session_run_id=RUN_ID,
        upload_id=upload.upload_id,
        document_digest=sha256_digest(upload.root_yaml),
        closure_digest=upload.selection.closure_digest,
        resolved_semantic_digest=prepared.resolved_semantic_digest,
        release=RELEASE,
        build=BUILD,
    )


def _write_mount(
    directory: Path,
    upload: CatalogUpload,
    context: RuntimeDeploymentContext,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SESSION_YAML_FILENAME).write_bytes(upload.root_yaml)
    (directory / SESSION_RUN_ID_FILENAME).write_text(RUN_ID + "\n", encoding="utf-8")
    (directory / CATALOG_UPLOAD_SELECTION_FILENAME).write_bytes(
        canonical_json_bytes(upload.selection.model_dump(mode="json"))
    )
    (directory / RUNTIME_DEPLOYMENT_CONTEXT_FILENAME).write_bytes(
        canonical_json_bytes(context.model_dump(mode="json"))
    )


def test_mounted_config_requires_and_reads_one_selection(
    upload: CatalogUpload,
    prepared: PreparedSessionFiles,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "mounted"
    context = _context(upload, prepared)
    _write_mount(directory, upload, context)

    mounted = read_mounted_session_config(directory)

    assert mounted.root_yaml == upload.root_yaml
    assert mounted.run_id == RUN_ID
    assert mounted.catalog_upload == upload.selection
    assert mounted.deployment_context == context

    (directory / CATALOG_UPLOAD_SELECTION_FILENAME).unlink()
    with pytest.raises(FileNotFoundError):
        read_mounted_session_config(directory)


def test_a_runtime_serves_only_after_its_config_loaded(tmp_path: Path) -> None:
    health = RuntimeConfigHealth(tmp_path / "empty", pod_uid="pod-runtime-service-0001")
    with pytest.raises(RuntimeError, match="only after its config is loaded"):
        health.mark_serving()


def test_health_waits_without_a_mounted_session(tmp_path: Path) -> None:
    health = RuntimeConfigHealth(tmp_path / "empty", pod_uid="pod-runtime-service-0001")

    assert health.liveness().ready is True
    readiness = health.readiness()
    assert readiness.ready is True
    assert readiness.detail == "waiting for session"
