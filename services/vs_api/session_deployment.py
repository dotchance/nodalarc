"""Server-only preparation of one revision-bound catalog session deployment."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
)
from nodalarc.catalog_refs import SessionRef
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogGeneration,
    CatalogNotFoundError,
)
from nodalarc.catalog_upload import CatalogUpload, encode_catalog_upload
from nodalarc.models.builder_api import Sha256Digest
from nodalarc.prepared_session import (
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)
from nodalarc.workloads.refs import SelectionRef
from pydantic import TypeAdapter, ValidationError

from .catalog_context import CatalogContext
from .catalog_upload_store import (
    CatalogUploadResourceEvidence,
    CatalogUploadStoreReceipt,
    KubernetesCatalogUploadStore,
)


class SessionDeploymentPreparationErrorCode(StrEnum):
    INVALID_PRECONDITION = "session_deployment.invalid_precondition"
    SOURCE_NOT_FOUND = "session_deployment.source_not_found"
    STALE_REPOSITORY = "session_deployment.stale_repository"
    STALE_SOURCE = "session_deployment.stale_source"


@dataclass(frozen=True, slots=True)
class SessionDeploymentPreparationErrorEvidence:
    code: SessionDeploymentPreparationErrorCode
    message: str
    session_ref: str
    expected: str | None = None
    observed: str | None = None
    cause_type: str | None = None


class SessionDeploymentPreparationError(ValueError):
    def __init__(self, evidence: SessionDeploymentPreparationErrorEvidence) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence

    @property
    def code(self) -> SessionDeploymentPreparationErrorCode:
        return self.evidence.code


@dataclass(frozen=True, slots=True)
class PreparedCatalogSessionDeployment:
    repository_generation: CatalogGeneration | None
    prepared: PreparedSessionFiles
    upload: CatalogUpload
    receipt: CatalogUploadStoreReceipt | None = None

    def __post_init__(self) -> None:
        if self.upload.root_yaml != self.prepared.root_yaml:
            raise ValueError("deployment upload root differs from prepared root YAML")
        if self.upload.catalog_files != self.prepared.catalog_files:
            raise ValueError("deployment upload files differ from prepared catalog files")
        if self.upload.selection.closure_digest != self.prepared.closure_digest:
            raise ValueError("deployment upload closure digest differs from preparation")
        if self.upload.selection.file_count != len(self.prepared.catalog_files):
            raise ValueError("deployment upload file count differs from preparation")
        if self.receipt is not None and self.receipt.selection != self.upload.selection:
            raise ValueError("deployment upload receipt describes a different upload")


_DIGEST_ADAPTER = TypeAdapter(Sha256Digest)


def _validated_digest(value: str, *, label: str, session_ref: str) -> str:
    try:
        return _DIGEST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise SessionDeploymentPreparationError(
            SessionDeploymentPreparationErrorEvidence(
                code=SessionDeploymentPreparationErrorCode.INVALID_PRECONDITION,
                message=f"{label} must be sha256:<64 lowercase hex>",
                session_ref=session_ref,
                observed=str(value),
                cause_type=type(exc).__name__,
            )
        ) from exc


def prepare_catalog_session_deployment(
    context: CatalogContext,
    *,
    session_ref: str,
    expected_session_revision: str,
    expected_document_digest: str,
    expected_closure_digest: str,
    available_node_count: int,
    run_id: str | None = None,
) -> PreparedCatalogSessionDeployment:
    """Re-read current repository head and prepare one exact deployable file set."""
    try:
        parsed_session_ref = SessionRef(session_ref)
    except (TypeError, ValueError) as exc:
        raise SessionDeploymentPreparationError(
            SessionDeploymentPreparationErrorEvidence(
                code=SessionDeploymentPreparationErrorCode.INVALID_PRECONDITION,
                message=f"Invalid catalog session ref: {session_ref!r}",
                session_ref=str(session_ref),
                cause_type=type(exc).__name__,
            )
        ) from exc
    session_ref = str(parsed_session_ref)
    expected_revision = _validated_digest(
        expected_session_revision,
        label="expected_session_revision",
        session_ref=session_ref,
    )
    expected_document = _validated_digest(
        expected_document_digest,
        label="expected_document_digest",
        session_ref=session_ref,
    )
    expected_closure = _validated_digest(
        expected_closure_digest,
        label="expected_closure_digest",
        session_ref=session_ref,
    )
    snapshot = context.repository.snapshot(context.scope)
    try:
        source = snapshot.get(session_ref)
    except CatalogNotFoundError as exc:
        raise SessionDeploymentPreparationError(
            SessionDeploymentPreparationErrorEvidence(
                code=SessionDeploymentPreparationErrorCode.SOURCE_NOT_FOUND,
                message=f"Catalog session does not exist: {session_ref}",
                session_ref=session_ref,
                cause_type=type(exc).__name__,
            )
        ) from exc
    if source.family != "sessions":
        raise SessionDeploymentPreparationError(
            SessionDeploymentPreparationErrorEvidence(
                code=SessionDeploymentPreparationErrorCode.SOURCE_NOT_FOUND,
                message=f"Deployment source is not a session: {session_ref}",
                session_ref=session_ref,
            )
        )
    if str(source.revision) != expected_revision:
        raise SessionDeploymentPreparationError(
            SessionDeploymentPreparationErrorEvidence(
                code=SessionDeploymentPreparationErrorCode.STALE_SOURCE,
                message="Saved session revision changed after review",
                session_ref=session_ref,
                expected=expected_revision,
                observed=str(source.revision),
            )
        )

    prepared = prepare_session_files(
        source.content,
        snapshot,
        source=PreparedSessionSource(
            logical_id=parsed_session_ref,
            origin="vs-api.session-deployment",
        ),
        source_revision=str(source.revision),
        expected_source_revision=expected_revision,
        expected_document_digest=expected_document,
        expected_closure_digest=expected_closure,
        available_node_count=available_node_count,
        run_id=run_id,
    )
    return PreparedCatalogSessionDeployment(
        repository_generation=snapshot.generation,
        prepared=prepared,
        upload=encode_catalog_upload(prepared),
    )


def assert_catalog_session_deployment_current(
    context: CatalogContext,
    deployment: PreparedCatalogSessionDeployment,
) -> None:
    """Refuse when the reviewed root or any referenced dependency changed."""
    session_ref = str(deployment.prepared.source.logical_id)
    snapshot = context.repository.snapshot(context.scope)
    try:
        source = snapshot.get(session_ref)
        closure = CatalogClosureCollector.collect(source.content, snapshot)
    except (CatalogNotFoundError, CatalogClosureError) as exc:
        raise SessionDeploymentPreparationError(
            SessionDeploymentPreparationErrorEvidence(
                code=SessionDeploymentPreparationErrorCode.STALE_REPOSITORY,
                message="Saved session or one of its dependencies changed after preparation",
                session_ref=session_ref,
                cause_type=type(exc).__name__,
            )
        ) from exc
    observed = (
        str(source.revision),
        closure.document_digest,
        closure.closure_digest,
    )
    expected = (
        deployment.prepared.source_revision,
        deployment.prepared.document_digest,
        deployment.prepared.closure_digest,
    )
    if observed != expected:
        raise SessionDeploymentPreparationError(
            SessionDeploymentPreparationErrorEvidence(
                code=SessionDeploymentPreparationErrorCode.STALE_REPOSITORY,
                message="Saved session or dependency closure changed after preparation",
                session_ref=session_ref,
                expected="|".join(expected),
                observed="|".join(observed),
            )
        )


def persist_catalog_session_upload(
    deployment: PreparedCatalogSessionDeployment,
    store: KubernetesCatalogUploadStore,
    *,
    resource_observer: Callable[[CatalogUploadResourceEvidence], None] | None = None,
) -> PreparedCatalogSessionDeployment:
    """Persist every referenced YAML file before selecting the upload in the CR."""
    if deployment.receipt is not None:
        raise CatalogConflictError("prepared deployment upload is already persisted")
    receipt = (
        store.put(deployment.upload, resource_observer=resource_observer)
        if resource_observer is not None
        else store.put(deployment.upload)
    )
    return PreparedCatalogSessionDeployment(
        repository_generation=deployment.repository_generation,
        prepared=deployment.prepared,
        upload=deployment.upload,
        receipt=receipt,
    )


def cleanup_unselected_catalog_session_upload(
    deployment: PreparedCatalogSessionDeployment,
    store: KubernetesCatalogUploadStore,
) -> None:
    """Delete the complete upload group after a failed, unselected attempt."""
    if deployment.receipt is None:
        return
    store.delete(deployment.upload.selection)


def constellation_spec_body(
    deployment: PreparedCatalogSessionDeployment,
    *,
    namespace: str,
    workload_selection: SelectionRef | None = None,
) -> dict[str, Any]:
    """Build the exact CR body selected after upload readback succeeds.

    An explicit workload selection appears as the CR field pair; absent
    selection omits both fields completely (built-in FRR default).
    """
    if deployment.receipt is None:
        raise ValueError("catalog upload must be persisted before building the session CR")
    selection = deployment.receipt.selection
    session_ref = str(deployment.prepared.source.logical_id)
    annotations = {
        "nodalarc.io/source-kind": "catalog_session",
        "nodalarc.io/source-id": session_ref,
        "nodalarc.io/source-revision": deployment.prepared.source_revision,
        "nodalarc.io/document-digest": deployment.prepared.document_digest,
        "nodalarc.io/closure-digest": deployment.prepared.closure_digest,
    }
    if deployment.repository_generation is not None:
        annotations["nodalarc.io/catalog-generation"] = str(deployment.repository_generation)
    spec: dict[str, Any] = {
        "sessionYaml": deployment.prepared.root_yaml.decode("utf-8"),
        "catalogUpload": selection.model_dump(mode="json"),
    }
    if workload_selection is not None:
        spec["implementationBindingRef"] = str(workload_selection.binding_ref)
        spec["implementationPackageDigest"] = str(workload_selection.package_digest)
    return {
        "apiVersion": "nodalarc.io/v1alpha1",
        "kind": "ConstellationSpec",
        "metadata": {
            "name": "current-session",
            "namespace": namespace,
            "annotations": annotations,
        },
        "spec": spec,
    }
