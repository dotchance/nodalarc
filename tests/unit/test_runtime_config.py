"""Tests for authoritative runtime loading from ordinary catalog uploads."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from nodalarc import runtime_config as runtime_module
from nodalarc.catalog_closure import (
    FilesystemCatalogReadView,
    catalog_closure_digest,
)
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_upload import (
    CatalogUpload,
    CatalogUploadError,
    CatalogUploadSelection,
    encode_catalog_upload,
)
from nodalarc.models.resolved_session import SourceContext
from nodalarc.prepared_session import (
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)
from nodalarc.runtime_config import (
    RuntimeConfigError,
    RuntimeConfigErrorCode,
    RuntimeConfigProof,
    RuntimeDeploymentContext,
    load_runtime_config,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"


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
            origin="test.runtime_config.prepare",
        ),
        source_revision=_digest(root_yaml),
        available_node_count=100,
    )


@pytest.fixture(scope="module")
def upload(prepared: PreparedSessionFiles) -> CatalogUpload:
    return encode_catalog_upload(prepared, upload_id="runtime-test-upload")


def test_upload_materializes_exact_paths_and_resolves_once(
    upload: CatalogUpload,
    prepared: PreparedSessionFiles,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = runtime_module.resolve_session_with_assets
    calls = []

    def tracking_resolver(raw_session, **kwargs):
        roots = kwargs["catalog_roots"]
        assert roots.root.is_dir()
        assert roots.user_root.is_dir()
        assert kwargs["source_context"].session_path is None
        calls.append(roots)
        return resolver(raw_session, **kwargs)

    monkeypatch.setattr(runtime_module, "resolve_session_with_assets", tracking_resolver)
    destination = tmp_path / "runtime"
    loaded = load_runtime_config(
        upload,
        destination=destination,
        installed_shipped_root=SHIPPED_ROOT,
        source_context=SourceContext(origin="test.runtime_config", run_id="run-runtime-0001"),
    )

    assert len(calls) == 1
    assert loaded.session_path.read_bytes() == prepared.root_yaml
    assert loaded.catalog_roots.root == destination / "catalog" / "nodalarc"
    assert loaded.catalog_roots.user_root == destination / "catalog" / "user"
    for entry in prepared.catalog_files:
        assert (destination / entry.preserved_path).read_bytes() == entry.yaml_bytes
    assert loaded.proof.upload_id == upload.upload_id
    assert loaded.proof.document_digest == prepared.document_digest
    assert loaded.proof.closure_digest == prepared.closure_digest
    assert loaded.proof.resolved_semantic_digest == prepared.resolved_semantic_digest
    assert loaded.proof.file_count == len(prepared.catalog_files)
    assert loaded.proof.total_bytes == prepared.total_bytes
    assert loaded.resolution.resolved.source_context.session_path is None


def test_invalid_upload_fails_before_resolver_or_materialization(
    upload: CatalogUpload,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = replace(upload, catalog_files=upload.catalog_files[:-1])
    monkeypatch.setattr(
        runtime_module,
        "resolve_session_with_assets",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resolver ran")),
    )
    destination = tmp_path / "invalid"

    with pytest.raises(CatalogUploadError):
        load_runtime_config(
            incomplete,
            destination=destination,
            installed_shipped_root=SHIPPED_ROOT,
            source_context=SourceContext(origin="test.runtime_config"),
        )

    assert not destination.exists()


def test_modified_shipped_asset_is_rejected_before_resolver(
    upload: CatalogUpload,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = upload.catalog_files[0]
    changed_bytes = first.yaml_bytes + b"\n# altered shipped asset\n"
    changed = replace(
        first,
        yaml_bytes=changed_bytes,
        document_digest=_digest(changed_bytes),
        size_bytes=len(changed_bytes),
    )
    entries = (changed, *upload.catalog_files[1:])
    altered = CatalogUpload(
        selection=CatalogUploadSelection(
            upload_id=upload.upload_id,
            closure_digest=catalog_closure_digest(entries),
            file_count=len(entries),
        ),
        root_yaml=upload.root_yaml,
        catalog_files=entries,
    )
    monkeypatch.setattr(
        runtime_module,
        "resolve_session_with_assets",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resolver ran")),
    )
    destination = tmp_path / "altered"

    with pytest.raises(RuntimeConfigError) as raised:
        load_runtime_config(
            altered,
            destination=destination,
            installed_shipped_root=SHIPPED_ROOT,
            source_context=SourceContext(origin="test.runtime_config"),
        )

    assert raised.value.code is RuntimeConfigErrorCode.SHIPPED_ASSET_MISMATCH
    assert not destination.exists()


def test_runtime_proof_is_strict_closed_and_has_no_transport_mode_fields() -> None:
    data = {
        "source_origin": "test",
        "upload_id": "proof-upload",
        "document_digest": f"sha256:{'2' * 64}",
        "closure_digest": f"sha256:{'3' * 64}",
        "resolved_semantic_digest": f"sha256:{'4' * 64}",
        "file_count": 1,
        "total_bytes": 1,
        "resolved_node_count": 1,
    }
    proof = RuntimeConfigProof.model_validate(data)
    assert "mode" not in type(proof).model_fields
    assert "upload_descriptor_digest" not in type(proof).model_fields
    assert "source_revision" not in type(proof).model_fields
    with pytest.raises(ValidationError):
        RuntimeConfigProof.model_validate({**data, "hidden": True})
    with pytest.raises(ValidationError):
        RuntimeConfigProof.model_validate({**data, "file_count": "1"})


def test_runtime_proof_binds_exact_selection_deployment_and_pod_identity() -> None:
    def digest(character: str) -> str:
        return f"sha256:{character * 64}"

    proof = RuntimeConfigProof(
        source_origin="ome",
        run_id="run-proof-0001",
        upload_id="proof-upload",
        document_digest=digest("2"),
        closure_digest=digest("3"),
        resolved_semantic_digest=digest("4"),
        file_count=1,
        total_bytes=1,
        resolved_node_count=1,
    )
    context = RuntimeDeploymentContext(
        cr_uid="cr-proof-0001",
        cr_generation=8,
        session_run_id="run-proof-0001",
        upload_id="proof-upload",
        document_digest=digest("2"),
        closure_digest=digest("3"),
        resolved_semantic_digest=digest("4"),
        release="nodalarc-test",
        build="test-build",
    )

    bound = proof.bind_deployment_identity(context, pod_uid="pod-proof-0001")

    assert bound.deployment_identity_bound is True
    assert bound.cr_uid == "cr-proof-0001"
    assert bound.cr_generation == 8
    assert bound.pod_uid == "pod-proof-0001"
    assert bound.upload_id == "proof-upload"
    with pytest.raises(ValueError, match="already deployment-bound"):
        bound.bind_deployment_identity(context, pod_uid="pod-proof-0001")
    with pytest.raises(ValueError, match="differs"):
        proof.bind_deployment_identity(
            context.model_copy(update={"upload_id": "different-upload"}),
            pod_uid="pod-proof-0001",
        )
