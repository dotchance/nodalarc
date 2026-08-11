from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import BuilderDraftEnvelope, BuilderSessionSaveRequest
from nodalarc.models.session_sources import CatalogSessionSwitchRequest
from pydantic import ValidationError
from vs_api.builder_compiler import canonicalize_persisted_configuration
from vs_api.builder_session_service import save_builder_session
from vs_api.catalog_context import CatalogContext
from vs_api.catalog_upload_store import (
    CatalogUploadResourceEvidence,
    CatalogUploadStoreReceipt,
)
from vs_api.session_deployment import (
    SessionDeploymentPreparationError,
    SessionDeploymentPreparationErrorCode,
    assert_catalog_session_deployment_current,
    cleanup_unselected_catalog_session_upload,
    constellation_spec_body,
    persist_catalog_session_upload,
    prepare_catalog_session_deployment,
)

from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"


def _context(tmp_path: Path) -> CatalogContext:
    scope = CatalogScope()
    return CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / "user"},
        ),
        scope=scope,
    )


def _save_user_session(context: CatalogContext):
    raw = yaml.safe_load((SHIPPED_ROOT / "sessions/earth-leo-simple.yaml").read_bytes())
    raw = deepcopy(raw)
    raw["session"]["name"] = "prepared-user-session"
    request = BuilderSessionSaveRequest(
        draft=BuilderDraftEnvelope(draft_revision=1, state={"session": raw}),
        target_ref="user:sessions/prepared-user-session.yaml",
    )
    return save_builder_session(
        request,
        context,
        available_node_count=1_000_000,
        preview_factory=lambda *_: builder_world_preview(),
    )


def test_preparation_rechecks_saved_revision_and_exact_digests(tmp_path: Path) -> None:
    context = _context(tmp_path)
    saved = _save_user_session(context)

    deployment = prepare_catalog_session_deployment(
        context,
        session_ref=str(saved.session.ref),
        expected_session_revision=saved.session.revision,
        expected_document_digest=saved.digests.document,
        expected_closure_digest=saved.digests.dependency,
        available_node_count=1_000_000,
    )

    assert deployment.prepared.root_yaml == saved.session.canonical_yaml.encode()
    assert deployment.upload.root_yaml == deployment.prepared.root_yaml
    assert deployment.upload.catalog_files == deployment.prepared.catalog_files
    assert deployment.upload.selection.closure_digest == saved.digests.dependency
    assert deployment.upload.selection.file_count == len(deployment.prepared.catalog_files)
    assert deployment.receipt is None


@pytest.mark.parametrize(
    "field,value,expected_code",
    [
        (
            "expected_session_revision",
            f"sha256:{'1' * 64}",
            SessionDeploymentPreparationErrorCode.STALE_SOURCE,
        ),
        (
            "expected_document_digest",
            f"sha256:{'2' * 64}",
            None,
        ),
        (
            "expected_closure_digest",
            f"sha256:{'3' * 64}",
            None,
        ),
    ],
)
def test_stale_review_fails_before_upload(
    tmp_path: Path,
    field: str,
    value: str,
    expected_code: SessionDeploymentPreparationErrorCode | None,
) -> None:
    context = _context(tmp_path)
    saved = _save_user_session(context)
    arguments = {
        "session_ref": str(saved.session.ref),
        "expected_session_revision": saved.session.revision,
        "expected_document_digest": saved.digests.document,
        "expected_closure_digest": saved.digests.dependency,
        "available_node_count": 1_000_000,
    }
    arguments[field] = value

    with pytest.raises(Exception) as raised:
        prepare_catalog_session_deployment(context, **arguments)

    if expected_code is not None:
        assert isinstance(raised.value, SessionDeploymentPreparationError)
        assert raised.value.code is expected_code
    else:
        assert type(raised.value).__name__ == "PreparedSessionError"


def test_cr_body_requires_persisted_upload(tmp_path: Path) -> None:
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = prepare_catalog_session_deployment(
        context,
        session_ref=str(saved.session.ref),
        expected_session_revision=saved.session.revision,
        expected_document_digest=saved.digests.document,
        expected_closure_digest=saved.digests.dependency,
        available_node_count=1_000_000,
    )

    with pytest.raises(ValueError, match="persisted"):
        constellation_spec_body(deployment, namespace="nodalarc")


def test_final_staleness_check_ignores_unrelated_writes_but_blocks_root_change(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    saved = _save_user_session(context)
    deployment = prepare_catalog_session_deployment(
        context,
        session_ref=str(saved.session.ref),
        expected_session_revision=saved.session.revision,
        expected_document_digest=saved.digests.document,
        expected_closure_digest=saved.digests.dependency,
        available_node_count=1_000_000,
    )

    body = canonicalize_persisted_configuration(
        CatalogRef("user:bodies/unrelated.yaml"),
        {
            "body": {
                "id": "unrelated",
                "display_name": "Unrelated",
                "gravitational_parameter_km3_s2": 398600.4418,
                "mean_radius_km": 6371.0088,
                "equatorial_radius_km": 6378.137,
                "polar_radius_km": 6356.752,
                "reference": "urn:nodalarc:test",
            }
        },
    )
    transaction = context.repository.begin(context.scope)
    transaction.write_bytes(body.ref, body.yaml_bytes, expected_revision=None)
    transaction.commit()
    assert_catalog_session_deployment_current(context, deployment)

    raw = yaml.safe_load(saved.session.canonical_yaml)
    raw["session"]["description"] = "Changed after deployment review"
    changed = save_builder_session(
        BuilderSessionSaveRequest(
            draft=BuilderDraftEnvelope(draft_revision=2, state={"session": raw}),
            target_ref=saved.session.ref,
            expected_session_revision=saved.session.revision,
        ),
        context,
        available_node_count=1_000_000,
        preview_factory=lambda *_: builder_world_preview(),
    )
    assert changed.session.revision != saved.session.revision

    with pytest.raises(SessionDeploymentPreparationError) as raised:
        assert_catalog_session_deployment_current(context, deployment)
    assert raised.value.code is SessionDeploymentPreparationErrorCode.STALE_REPOSITORY


def test_persisted_upload_builds_exact_cr_and_cleanup_deletes_unselected_group(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    saved = _save_user_session(context)
    prepared = prepare_catalog_session_deployment(
        context,
        session_ref=str(saved.session.ref),
        expected_session_revision=saved.session.revision,
        expected_document_digest=saved.digests.document,
        expected_closure_digest=saved.digests.dependency,
        available_node_count=1_000_000,
    )

    class Store:
        def __init__(self) -> None:
            self.deleted = None

        def put(self, upload):
            return CatalogUploadStoreReceipt(
                selection=upload.selection,
                resources=tuple(
                    CatalogUploadResourceEvidence(
                        name=f"catalog-upload-{index}",
                        ref=entry.ref,
                        uid=f"uid-{index}",
                    )
                    for index, entry in enumerate(upload.catalog_files)
                ),
            )

        def delete(self, upload):
            self.deleted = upload

    store = Store()
    persisted = persist_catalog_session_upload(prepared, store)  # type: ignore[arg-type]
    body = constellation_spec_body(persisted, namespace="nodalarc")

    # The CR spec is exactly the session and its upload; workload facts are
    # session truth, never CR fields.
    assert set(body["spec"]) == {"sessionYaml", "catalogUpload"}

    assert body["spec"]["sessionYaml"].encode() == saved.session.canonical_yaml.encode()
    assert body["spec"]["catalogUpload"] == persisted.receipt.selection.model_dump(mode="json")
    assert body["spec"]["catalogUpload"] == {
        "upload_id": persisted.upload.selection.upload_id,
        "closure_digest": saved.digests.dependency,
        "file_count": len(persisted.prepared.catalog_files),
    }
    assert body["metadata"]["annotations"]["nodalarc.io/source-id"] == str(saved.session.ref)
    assert body["metadata"]["annotations"]["nodalarc.io/closure-digest"] == (
        saved.digests.dependency
    )

    cleanup_unselected_catalog_session_upload(persisted, store)  # type: ignore[arg-type]
    assert store.deleted == persisted.upload.selection
