from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nodalarc.catalog_upload import CatalogUploadSelection, sha256_digest
from vs_api.catalog_upload_store import (
    CatalogUploadStoreError,
    CatalogUploadStoreErrorCode,
    CatalogUploadStoreErrorEvidence,
)
from vs_api.session_deployment import (
    SessionDeploymentPreparationError,
    SessionDeploymentPreparationErrorCode,
    SessionDeploymentPreparationErrorEvidence,
)
from vs_api.transition_operations import (
    FilesystemTransitionOperationStore,
    TransitionConstellationSpecObservation,
    TransitionOperationConflictError,
    TransitionOperationFacts,
    TransitionOperationFailure,
    TransitionOperationProvenance,
    TransitionOperationProvenancePatch,
    TransitionOperationReservation,
    TransitionOperationSource,
    TransitionOperationSourceKind,
    TransitionOperationState,
    TransitionOperationStateError,
    TransitionReconciliationDisposition,
    TransitionRuntimePlan,
    TransitionRuntimeResult,
    TransitionRuntimeStatusProof,
    reconcile_transition_operation,
    transition_failure_from_exception,
)

OPERATION_ID = "0123456789abcdef0123456789abcdef"
ROOT_YAML = "session:\n  name: transition-test\n"
DIGEST_A = sha256_digest(ROOT_YAML.encode())
DIGEST_B = f"sha256:{'b' * 64}"
DIGEST_C = f"sha256:{'c' * 64}"


def _selection() -> CatalogUploadSelection:
    return CatalogUploadSelection(
        upload_id="catalog-abcd1234",
        closure_digest=DIGEST_B,
        file_count=3,
    )


def _reservation() -> TransitionOperationReservation:
    return TransitionOperationReservation(
        source=TransitionOperationSource(
            kind=TransitionOperationSourceKind.CATALOG_SESSION,
            logical_id="user:sessions/demo.yaml",
        ),
        facts=TransitionOperationFacts(
            document_digest=DIGEST_A,
            closure_digest=DIGEST_B,
            resolved_semantic_digest=DIGEST_C,
            file_count=4,
            total_bytes=4096,
            release="1.2.3",
            build="build-test",
        ),
        provenance=TransitionOperationProvenance(
            source_revision=DIGEST_A,
            repository_generation=DIGEST_B,
            upload_id="catalog-abcd1234",
            runtime_plan=TransitionRuntimePlan(
                namespace="nodalarc",
                name="current-session",
            ),
        ),
    )


def _matching_cr(*, phase: str = "Ready") -> dict:
    return {
        "metadata": {
            "namespace": "nodalarc",
            "name": "current-session",
            "uid": "uid-current-session",
            "resourceVersion": "42",
            "generation": 7,
            "annotations": {
                "nodalarc.io/source-kind": "catalog_session",
                "nodalarc.io/source-id": "user:sessions/demo.yaml",
                "nodalarc.io/source-revision": DIGEST_A,
                "nodalarc.io/catalog-generation": DIGEST_B,
                "nodalarc.io/document-digest": DIGEST_A,
                "nodalarc.io/closure-digest": DIGEST_B,
            },
        },
        "spec": {
            "sessionYaml": ROOT_YAML,
            "catalogUpload": _selection().model_dump(mode="json"),
        },
        "status": {
            "observedGeneration": 7,
            "phase": phase,
            "sessionRunId": "session-run-7",
            "podCount": 12,
            "readyPods": 12,
            "wiredPods": 12,
            "documentDigest": DIGEST_A,
            "closureDigest": DIGEST_B,
            "resolvedSemanticDigest": DIGEST_C,
            "runtimeRelease": "1.2.3",
            "runtimeBuild": "build-test",
        },
    }


def test_filesystem_store_persists_lifecycle_and_releases_admission(tmp_path: Path) -> None:
    now = datetime(2026, 7, 10, tzinfo=UTC)
    store = FilesystemTransitionOperationStore(tmp_path / "operations")
    reserved = store.reserve(OPERATION_ID, _reservation(), now=now)

    with pytest.raises(TransitionOperationConflictError) as conflict:
        store.reserve("fedcba9876543210fedcba9876543210", _reservation(), now=now)
    assert conflict.value.active_operation_id == OPERATION_ID

    collecting = store.advance(
        OPERATION_ID,
        TransitionOperationState.COLLECTING,
        detail="Collecting reviewed catalog closure",
        now=now + timedelta(seconds=1),
    )
    uploading = store.advance(
        OPERATION_ID,
        TransitionOperationState.UPLOADING,
        detail="Uploading exact catalog files",
        now=now + timedelta(seconds=2),
    )
    verifying = store.advance(
        OPERATION_ID,
        TransitionOperationState.VERIFYING,
        detail="Verifying uploaded catalog files",
        now=now + timedelta(seconds=3),
    )
    switching = store.advance(
        OPERATION_ID,
        TransitionOperationState.SWITCHING,
        detail="Selecting verified runtime",
        now=now + timedelta(seconds=4),
    )
    succeeded = store.advance(
        OPERATION_ID,
        TransitionOperationState.SUCCEEDED,
        detail="Runtime is Ready",
        runtime=TransitionRuntimeResult(session_id="session-run-7", generation=7),
        now=now + timedelta(seconds=5),
    )

    assert reserved.version == 1
    assert collecting.version == 2
    assert uploading.version == 3
    assert verifying.version == 4
    assert switching.version == 5
    assert succeeded.version == 6
    assert store.active() is None

    reopened = FilesystemTransitionOperationStore(tmp_path / "operations")
    restored = reopened.get_operation(OPERATION_ID)
    assert restored == succeeded
    assert [event.state for event in restored.events] == list(TransitionOperationState)[:5] + [
        TransitionOperationState.SUCCEEDED
    ]
    public = restored.public_view().model_dump(mode="json")
    assert "provenance" not in public
    assert "version" not in public
    assert "upload_id" not in str(public)

    next_record = reopened.reserve(
        "fedcba9876543210fedcba9876543210",
        _reservation(),
        now=now + timedelta(seconds=6),
    )
    assert next_record.state is TransitionOperationState.RESERVED


def test_filesystem_store_durably_journals_created_names_and_runtime_provenance(
    tmp_path: Path,
) -> None:
    store = FilesystemTransitionOperationStore(tmp_path / "operations")
    store.reserve(OPERATION_ID, _reservation())
    for name in ("catalog-abcd1234-0", "catalog-abcd1234-1"):
        store.update_provenance(
            OPERATION_ID,
            TransitionOperationProvenancePatch(
                upload_resource_name=name,
            ),
        )
    store.update_provenance(
        OPERATION_ID,
        TransitionOperationProvenancePatch(
            constellation_spec=TransitionConstellationSpecObservation(
                namespace="nodalarc",
                name="current-session",
                generation=7,
                status=TransitionRuntimeStatusProof(
                    observed_generation=7,
                    phase="Ready",
                    session_id="session-run-7",
                    pod_count=12,
                    ready_pods=12,
                    wired_pods=12,
                    document_digest=DIGEST_A,
                    closure_digest=DIGEST_B,
                    resolved_semantic_digest=DIGEST_C,
                    release="1.2.3",
                    build="build-test",
                ),
            )
        ),
    )

    reopened = FilesystemTransitionOperationStore(tmp_path / "operations")
    restored = reopened.get_operation(OPERATION_ID)
    assert restored.provenance.upload_resource_names == (
        "catalog-abcd1234-0",
        "catalog-abcd1234-1",
    )
    assert all(
        token not in str(restored.provenance.model_dump(mode="json"))
        for token in (
            "uid-shard",
            "uid-root",
            "resource_version",
            "manifest",
            "descriptor",
            "scope_binding",
        )
    )
    assert restored.provenance.constellation_spec is not None
    assert restored.provenance.constellation_spec.status.phase == "Ready"
    public = str(restored.public_view().model_dump(mode="json"))
    assert "catalog-abcd1234-0" not in public


def test_provenance_journal_is_idempotent_and_requires_upload_id(tmp_path: Path) -> None:
    store = FilesystemTransitionOperationStore(tmp_path)
    store.reserve(OPERATION_ID, _reservation())
    store.update_provenance(
        OPERATION_ID,
        TransitionOperationProvenancePatch(
            upload_resource_name="catalog-abcd1234-0",
        ),
    )
    unchanged = store.update_provenance(
        OPERATION_ID,
        TransitionOperationProvenancePatch(upload_resource_name="catalog-abcd1234-0"),
    )
    assert unchanged.provenance.upload_resource_names == ("catalog-abcd1234-0",)

    without_upload = FilesystemTransitionOperationStore(tmp_path / "without-upload")
    without_upload.reserve(
        "fedcba9876543210fedcba9876543210",
        TransitionOperationReservation(
            source=TransitionOperationSource(
                kind=TransitionOperationSourceKind.CATALOG_SESSION,
                logical_id="user:sessions/demo.yaml",
            ),
            facts=TransitionOperationFacts(release="1.2.3", build="build-test"),
        ),
    )
    with pytest.raises(TransitionOperationStateError, match="upload id"):
        without_upload.update_provenance(
            "fedcba9876543210fedcba9876543210",
            TransitionOperationProvenancePatch(upload_resource_name="catalog-abcd1234-0"),
        )


def test_terminal_records_are_immutable(tmp_path: Path) -> None:
    store = FilesystemTransitionOperationStore(tmp_path)
    store.reserve(OPERATION_ID, _reservation())
    store.advance(
        OPERATION_ID,
        TransitionOperationState.FAILED,
        detail="Transition failed",
        failure=TransitionOperationFailure(
            code="transition.worker.failed",
            message="Session transition failed",
            cause_type="RuntimeError",
        ),
    )

    with pytest.raises(TransitionOperationStateError, match="immutable"):
        store.advance(OPERATION_ID, TransitionOperationState.COLLECTING)


def test_transition_source_identities_are_path_free_and_typed() -> None:
    source = TransitionOperationSource(
        kind=TransitionOperationSourceKind.CATALOG_SESSION,
        logical_id="user:sessions/demo.yaml",
    )
    assert "/tmp/" not in str(source.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("kind", "logical_id"),
    [
        (TransitionOperationSourceKind.CATALOG_SESSION, "/tmp/session.yaml"),
        (TransitionOperationSourceKind.CATALOG_SESSION, "user:nodes/not-a-session.yaml"),
    ],
)
def test_transition_source_rejects_paths_and_wrong_identity_forms(kind, logical_id) -> None:
    with pytest.raises(ValueError):
        TransitionOperationSource(kind=kind, logical_id=logical_id)


@pytest.mark.parametrize(
    ("cr", "expected"),
    [
        (_matching_cr(phase="Ready"), TransitionReconciliationDisposition.SUCCEEDED),
        (_matching_cr(phase="Wiring"), TransitionReconciliationDisposition.STILL_SWITCHING),
        (_matching_cr(phase="Error"), TransitionReconciliationDisposition.FAILED),
        (None, TransitionReconciliationDisposition.CANCELLED),
    ],
)
def test_restart_reconciliation_uses_live_cr_provenance_and_status(
    tmp_path: Path,
    cr: dict | None,
    expected: TransitionReconciliationDisposition,
) -> None:
    store = FilesystemTransitionOperationStore(tmp_path)
    operation = store.reserve(OPERATION_ID, _reservation())

    result = reconcile_transition_operation(operation, cr)

    assert result.disposition is expected
    if expected is TransitionReconciliationDisposition.SUCCEEDED:
        assert result.runtime == TransitionRuntimeResult(session_id="session-run-7", generation=7)
    if expected in {
        TransitionReconciliationDisposition.FAILED,
        TransitionReconciliationDisposition.CANCELLED,
    }:
        assert result.failure is not None


def test_restart_reconciliation_refuses_different_live_source(tmp_path: Path) -> None:
    store = FilesystemTransitionOperationStore(tmp_path)
    operation = store.reserve(OPERATION_ID, _reservation())
    cr = _matching_cr()
    cr["metadata"]["annotations"]["nodalarc.io/source-id"] = "user:sessions/other.yaml"

    result = reconcile_transition_operation(operation, cr)

    assert result.disposition is TransitionReconciliationDisposition.CANCELLED
    assert result.failure is not None
    assert result.failure.code == "transition.recovery.runtime_mismatch"


def test_restart_reconciliation_refuses_changed_root_yaml(tmp_path: Path) -> None:
    store = FilesystemTransitionOperationStore(tmp_path)
    operation = store.reserve(OPERATION_ID, _reservation())
    cr = _matching_cr()
    cr["spec"]["sessionYaml"] = "session:\n  name: changed\n"

    result = reconcile_transition_operation(operation, cr)

    assert result.disposition is TransitionReconciliationDisposition.CANCELLED
    assert result.failure is not None
    assert result.failure.code == "transition.recovery.runtime_mismatch"


def test_restart_reconciliation_refuses_replaced_constellation_spec_generation(
    tmp_path: Path,
) -> None:
    store = FilesystemTransitionOperationStore(tmp_path)
    store.reserve(OPERATION_ID, _reservation())
    store.update_provenance(
        OPERATION_ID,
        TransitionOperationProvenancePatch(
            constellation_spec=TransitionConstellationSpecObservation(
                namespace="nodalarc",
                name="current-session",
                generation=7,
            )
        ),
    )
    cr = _matching_cr()
    cr["metadata"]["generation"] = 8

    result = reconcile_transition_operation(store.get_operation(OPERATION_ID), cr)

    assert result.disposition is TransitionReconciliationDisposition.CANCELLED
    assert result.failure is not None
    assert result.failure.code == "transition.recovery.runtime_mismatch"


def test_restart_reconciliation_refuses_non_selection_transport_fields(
    tmp_path: Path,
) -> None:
    store = FilesystemTransitionOperationStore(tmp_path)
    operation = store.reserve(OPERATION_ID, _reservation())
    cr = _matching_cr()
    cr["spec"]["catalogUpload"]["manifest_uid"] = "uid-retired-root"

    result = reconcile_transition_operation(operation, cr)

    assert result.disposition is TransitionReconciliationDisposition.CANCELLED
    assert result.failure is not None
    assert result.failure.code == "transition.recovery.runtime_mismatch"


@pytest.mark.parametrize(
    ("field", "stale_value"),
    (
        ("documentDigest", DIGEST_C),
        ("closureDigest", DIGEST_C),
        ("resolvedSemanticDigest", DIGEST_A),
        ("runtimeRelease", "retired-release"),
        ("runtimeBuild", "retired-build"),
    ),
)
def test_ready_reconciliation_requires_exact_runtime_proof(
    tmp_path: Path,
    field: str,
    stale_value: str,
) -> None:
    store = FilesystemTransitionOperationStore(tmp_path)
    operation = store.reserve(OPERATION_ID, _reservation())
    cr = _matching_cr()
    cr["status"][field] = stale_value

    result = reconcile_transition_operation(operation, cr)

    assert result.disposition is TransitionReconciliationDisposition.FAILED
    assert result.failure is not None
    assert result.failure.code == "transition.runtime.proof_mismatch"


def test_typed_post_admission_failure_codes_are_preserved_without_resource_names() -> None:
    stale = SessionDeploymentPreparationError(
        SessionDeploymentPreparationErrorEvidence(
            code=SessionDeploymentPreparationErrorCode.STALE_REPOSITORY,
            message="Saved session or dependency closure changed after preparation",
            session_ref="user:sessions/demo.yaml",
        )
    )
    upload = CatalogUploadStoreError(
        CatalogUploadStoreErrorEvidence(
            code=CatalogUploadStoreErrorCode.READBACK_MISMATCH,
            message="Catalog upload readback did not match ordinary YAML files",
            resource_name="catalog-private-file",
        )
    )

    stale_failure = transition_failure_from_exception(stale)
    upload_failure = transition_failure_from_exception(upload)

    assert stale_failure.code == SessionDeploymentPreparationErrorCode.STALE_REPOSITORY.value
    assert "changed after preparation" in stale_failure.message
    assert upload_failure.code == CatalogUploadStoreErrorCode.READBACK_MISMATCH.value
    assert upload_failure.message == "Catalog upload could not be persisted or verified"
    assert "catalog-private-file" not in upload_failure.message


def test_untyped_and_timeout_failures_keep_stable_transition_taxonomy() -> None:
    assert transition_failure_from_exception(TimeoutError("private timeout detail")).code == (
        "transition.runtime.timeout"
    )
    assert transition_failure_from_exception(RuntimeError("private detail")).code == (
        "transition.worker.failed"
    )
