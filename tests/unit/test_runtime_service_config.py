"""Tests for required-selection runtime service startup and health."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_upload import CatalogUpload, encode_catalog_upload
from nodalarc.content_identity import canonical_json_bytes, sha256_digest
from nodalarc.kubernetes_runtime_config import (
    CATALOG_DOCUMENT_KEY,
    CATALOG_REF_ANNOTATION,
    CATALOG_UPLOAD_LABEL,
)
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
    load_mounted_runtime_config,
    read_mounted_session_config,
)

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
NAMESPACE = "nodalarc-test"
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


class FakeCoreV1:
    def __init__(self, config_maps: list[Any]) -> None:
        self.config_maps = config_maps
        self.lists: list[tuple[str, str]] = []

    def list_namespaced_config_map(self, namespace: str, *, label_selector: str) -> Any:
        self.lists.append((namespace, label_selector))
        key, value = label_selector.split("=", 1)
        return SimpleNamespace(
            items=[
                config_map
                for config_map in self.config_maps
                if config_map.metadata.labels.get(key) == value
            ]
        )


def _client_for(upload: CatalogUpload) -> FakeCoreV1:
    config_maps = []
    for order, entry in enumerate(upload.catalog_files):
        config_maps.append(
            SimpleNamespace(
                api_version="v1",
                kind="ConfigMap",
                metadata=SimpleNamespace(
                    name=f"{upload.upload_id}-{order:06d}",
                    namespace=NAMESPACE,
                    labels={CATALOG_UPLOAD_LABEL: upload.upload_id},
                    annotations={CATALOG_REF_ANNOTATION: str(entry.ref)},
                    owner_references=None,
                ),
                immutable=None,
                data={CATALOG_DOCUMENT_KEY: entry.yaml_bytes.decode("utf-8")},
                binary_data=None,
            )
        )
    return FakeCoreV1(config_maps)


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


def test_load_binds_proof_and_health_tracks_the_same_selection(
    upload: CatalogUpload,
    prepared: PreparedSessionFiles,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "mounted"
    context = _context(upload, prepared)
    _write_mount(directory, upload, context)
    client = _client_for(upload)
    runtime_parent = tmp_path / "processes"
    runtime_parent.mkdir()

    loaded = load_mounted_runtime_config(
        config_directory=directory,
        installed_shipped_root=SHIPPED_ROOT,
        origin="test.runtime_service_config",
        namespace=NAMESPACE,
        pod_uid="pod-runtime-service-0001",
        release=RELEASE,
        build=BUILD,
        core_v1=client,
        runtime_parent=runtime_parent,
        poll_seconds=0.01,
    )

    assert client.lists == [(NAMESPACE, f"{CATALOG_UPLOAD_LABEL}={upload.upload_id}")]
    assert loaded.proof.deployment_identity_bound is True
    assert loaded.proof.upload_id == upload.upload_id
    assert loaded.proof.cr_uid == context.cr_uid
    assert loaded.proof.pod_uid == "pod-runtime-service-0001"

    health = RuntimeConfigHealth(directory, pod_uid="pod-runtime-service-0001")
    health.mark_loaded(loaded)
    readiness = health.readiness()
    assert readiness.ready is True
    assert readiness.proof == loaded.proof

    changed = upload.selection.model_copy(update={"upload_id": "different-upload"})
    (directory / CATALOG_UPLOAD_SELECTION_FILENAME).write_bytes(
        canonical_json_bytes(changed.model_dump(mode="json"))
    )
    stale = health.readiness()
    assert stale.ready is False
    assert "selection" in stale.detail


def test_context_selection_mismatch_refuses_before_kubernetes_fetch(
    upload: CatalogUpload,
    prepared: PreparedSessionFiles,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "mismatch"
    context = _context(upload, prepared).model_copy(update={"upload_id": "wrong-upload"})
    _write_mount(directory, upload, context)
    client = _client_for(upload)
    runtime_parent = tmp_path / "processes"
    runtime_parent.mkdir()

    with pytest.raises(ValueError, match="wrong upload ID"):
        load_mounted_runtime_config(
            config_directory=directory,
            installed_shipped_root=SHIPPED_ROOT,
            origin="test.runtime_service_config",
            namespace=NAMESPACE,
            pod_uid="pod-runtime-service-0001",
            release=RELEASE,
            build=BUILD,
            core_v1=client,
            runtime_parent=runtime_parent,
            poll_seconds=0.01,
        )

    assert client.lists == []
    assert not any(runtime_parent.iterdir())


def test_health_waits_without_a_mounted_session(tmp_path: Path) -> None:
    health = RuntimeConfigHealth(tmp_path / "empty", pod_uid="pod-runtime-service-0001")

    assert health.liveness().ready is True
    readiness = health.readiness()
    assert readiness.ready is True
    assert readiness.detail == "waiting for session"
