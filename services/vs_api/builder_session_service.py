"""Transactional application service for truthful Builder session saves."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from nodalarc.catalog_closure import CatalogClosure, CatalogClosureCollector, CatalogClosureError
from nodalarc.catalog_refs import SessionRef
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogDocument,
    CatalogGeneration,
    CatalogReadSnapshot,
    CatalogRepositoryError,
    CatalogValidationError,
)
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.builder_api import (
    BuilderCatalogDocument,
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderDeployVerdict,
    BuilderDigests,
    BuilderIssue,
    BuilderIssueStage,
    BuilderSessionSaveRequest,
    BuilderSessionSaveResult,
    DependencyClosureEntry,
    DependencyClosureInventory,
    JsonDocument,
)
from nodalarc.prepared_session import (
    PreparedSessionError,
    PreparedSessionErrorCode,
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)
from nodalarc.resolve_session import SessionResolutionError
from nodalarc.runtime_support import UnsupportedFeatureError
from pydantic import ValidationError

from .builder_compiler import (
    PreviewFactory,
    canonicalize_persisted_configuration,
    compile_builder_draft,
)
from .catalog_context import CatalogContext

SessionPreparer = Callable[..., PreparedSessionFiles]


class BuilderSessionSaveErrorCode(StrEnum):
    """Stable application error classes for failed save attempts."""

    SAVE_BLOCKED = "builder_session_save.save_blocked"
    STALE_WRITE = "builder_session_save.stale_write"
    GRAPH_INVALID = "builder_session_save.graph_invalid"
    PERSISTENCE_FAILED = "builder_session_save.persistence_failed"
    STORAGE_VERIFICATION_FAILED = "builder_session_save.storage_verification_failed"


@dataclass(frozen=True, slots=True)
class BuilderSessionSaveErrorEvidence:
    """Typed evidence safe for an API error mapper."""

    code: BuilderSessionSaveErrorCode
    message: str
    target_ref: str
    base_generation: str | None = None
    repository_committed: bool = False
    issues: tuple[BuilderIssue, ...] = ()
    cause_type: str | None = None


class BuilderSessionSaveError(RuntimeError):
    """Base error for a save attempt, with explicit commit-state evidence."""

    def __init__(
        self,
        evidence: BuilderSessionSaveErrorEvidence,
        *,
        compile_result: BuilderCompileResult | None = None,
    ) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence
        self.compile_result = compile_result

    @property
    def code(self) -> BuilderSessionSaveErrorCode:
        return self.evidence.code


class BuilderSessionSaveBlockedError(BuilderSessionSaveError):
    """Raised when the authoritative compile verdict refuses persistence."""


class BuilderSessionSaveStaleError(BuilderSessionSaveError):
    """Raised when a generation or document compare-and-swap check is stale."""


class BuilderSessionSavePersistenceError(BuilderSessionSaveError):
    """Raised when validation, commit, or exact-byte verification fails."""


def _save_error(
    error_type: type[BuilderSessionSaveError],
    code: BuilderSessionSaveErrorCode,
    message: str,
    *,
    request: BuilderSessionSaveRequest,
    generation: CatalogGeneration | None,
    issues: tuple[BuilderIssue, ...] = (),
    repository_committed: bool = False,
    cause: BaseException | None = None,
    compile_result: BuilderCompileResult | None = None,
) -> BuilderSessionSaveError:
    return error_type(
        BuilderSessionSaveErrorEvidence(
            code=code,
            message=message,
            target_ref=str(request.target_ref),
            base_generation=str(generation) if generation is not None else None,
            repository_committed=repository_committed,
            issues=issues,
            cause_type=type(cause).__name__ if cause is not None else None,
        ),
        compile_result=compile_result,
    )


def _inventory(
    closure: CatalogClosure,
    snapshot: CatalogReadSnapshot,
) -> DependencyClosureInventory:
    entries = tuple(
        DependencyClosureEntry(
            ref=entry.ref,
            family=entry.family,
            revision=str(snapshot.get(entry.ref).revision),
            document_digest=entry.document_digest,
            preserved_path=entry.preserved_path,
            size_bytes=entry.size_bytes,
        )
        for entry in closure.entries
    )
    return DependencyClosureInventory(
        entries=entries,
        file_count=len(entries),
        total_bytes=sum(entry.size_bytes for entry in entries),
        closure_digest=closure.closure_digest,
    )


def _runtime_support_issues(
    error: UnsupportedFeatureError,
    *,
    source_ref: str,
) -> tuple[BuilderIssue, ...]:
    return tuple(
        BuilderIssue(
            code=f"builder.runtime_support.{feature.category.value}.{feature.value}",
            stage="runtime_support",
            severity="error",
            message=feature.message,
            blocks=("deploy",),
            source_ref=source_ref,
        )
        for feature in error.features
    )


def _prepared_session_issues(
    error: PreparedSessionError,
    *,
    source_ref: str,
) -> tuple[BuilderIssue, ...]:
    if error.code is PreparedSessionErrorCode.NOT_READY and error.evidence.readiness_errors:
        return tuple(
            BuilderIssue(
                code=f"builder.readiness.{result.code}",
                stage="readiness",
                severity="error",
                message=result.message,
                blocks=("deploy",),
                source_ref=source_ref,
                draft_path=result.field_path,
            )
            for result in error.evidence.readiness_errors
        )
    return (
        BuilderIssue(
            code=f"builder.persistence.post_commit.{error.code.value}",
            stage="persistence",
            severity="error",
            message=str(error),
            blocks=("deploy",),
            source_ref=source_ref,
        ),
    )


def _post_commit_issue(
    code: str,
    message: str,
    *,
    source_ref: str,
    stage: BuilderIssueStage = "persistence",
) -> BuilderIssue:
    return BuilderIssue(
        code=code,
        stage=stage,
        severity="error",
        message=message,
        blocks=("deploy",),
        source_ref=source_ref,
    )


def _merge_issues(*groups: tuple[BuilderIssue, ...]) -> tuple[BuilderIssue, ...]:
    merged: list[BuilderIssue] = []
    identities: set[tuple[object, ...]] = set()
    for group in groups:
        for issue in group:
            identity = (
                issue.code,
                issue.stage,
                issue.message,
                issue.blocks,
                issue.source_ref,
                issue.json_pointer,
                issue.draft_path,
                issue.related_refs,
            )
            if identity not in identities:
                identities.add(identity)
                merged.append(issue)
    return tuple(merged)


def _prepare_saved_session(
    saved: CatalogDocument,
    snapshot: CatalogReadSnapshot,
    compile_result: BuilderCompileResult,
    *,
    available_node_count: int,
    preparer: SessionPreparer,
) -> tuple[PreparedSessionFiles | None, tuple[BuilderIssue, ...]]:
    assert compile_result.digests is not None
    source_ref = str(saved.ref)
    try:
        prepared = preparer(
            saved.content,
            snapshot,
            source=PreparedSessionSource(
                logical_id=SessionRef(source_ref),
                origin="vs-api.builder-session-save",
            ),
            source_revision=str(saved.revision),
            expected_source_revision=str(saved.revision),
            expected_document_digest=compile_result.digests.document,
            expected_closure_digest=compile_result.digests.dependency,
            available_node_count=available_node_count,
        )
    except UnsupportedFeatureError as error:
        return None, _runtime_support_issues(error, source_ref=source_ref)
    except PreparedSessionError as error:
        return None, _prepared_session_issues(error, source_ref=source_ref)
    except CatalogClosureError as error:
        return None, (
            _post_commit_issue(
                f"builder.persistence.post_commit.{error.code.value}",
                str(error),
                source_ref=source_ref,
            ),
        )
    except ValidationError as error:
        return None, (
            _post_commit_issue(
                "builder.persistence.post_commit.structural",
                str(error),
                source_ref=source_ref,
            ),
        )
    except SessionResolutionError as error:
        return None, (
            _post_commit_issue(
                "builder.semantic.post_commit.session_resolution",
                str(error),
                source_ref=source_ref,
                stage="semantic",
            ),
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        return None, (
            _post_commit_issue(
                "builder.persistence.post_commit.preparation",
                str(error),
                source_ref=source_ref,
            ),
        )
    return prepared, ()


def save_builder_session(
    request: BuilderSessionSaveRequest,
    context: CatalogContext,
    *,
    available_node_count: int,
    preview_factory: PreviewFactory | None = None,
    preparer: SessionPreparer = prepare_session_files,
) -> BuilderSessionSaveResult:
    """Compile and atomically save one complete ref-composed Builder draft."""

    if not isinstance(request, BuilderSessionSaveRequest):
        raise TypeError("request must be a BuilderSessionSaveRequest")
    if not isinstance(context, CatalogContext):
        raise TypeError("context must be a CatalogContext")

    try:
        snapshot = context.repository.snapshot(context.scope)
    except (CatalogRepositoryError, OSError) as error:
        raise _save_error(
            BuilderSessionSavePersistenceError,
            BuilderSessionSaveErrorCode.PERSISTENCE_FAILED,
            str(error),
            request=request,
            generation=None,
            cause=error,
        ) from error
    compile_request = BuilderCompileRequest(
        draft=request.draft,
        target_ref=request.target_ref,
    )
    compile_result = compile_builder_draft(
        compile_request,
        snapshot,
        available_node_count=available_node_count,
        preview_factory=preview_factory,
    )
    if not compile_result.save_verdict.allowed:
        stale = any(issue.stage == "staleness" for issue in compile_result.save_verdict.blockers)
        invalid_graph = any(
            issue.stage == "reference" for issue in compile_result.save_verdict.blockers
        )
        error_type: type[BuilderSessionSaveError]
        error_code: BuilderSessionSaveErrorCode
        if stale:
            error_type = BuilderSessionSaveStaleError
            error_code = BuilderSessionSaveErrorCode.STALE_WRITE
        elif invalid_graph:
            error_type = BuilderSessionSavePersistenceError
            error_code = BuilderSessionSaveErrorCode.GRAPH_INVALID
        else:
            error_type = BuilderSessionSaveBlockedError
            error_code = BuilderSessionSaveErrorCode.SAVE_BLOCKED
        raise _save_error(
            error_type,
            error_code,
            "Builder draft is not saveable",
            request=request,
            generation=snapshot.generation,
            issues=compile_result.save_verdict.blockers,
            compile_result=compile_result,
        )

    if (
        compile_result.canonical_session_yaml is None
        or compile_result.canonical_session_json is None
        or compile_result.digests is None
    ):
        raise _save_error(
            BuilderSessionSavePersistenceError,
            BuilderSessionSaveErrorCode.PERSISTENCE_FAILED,
            "Saveable compile result omitted canonical persistence facts",
            request=request,
            generation=snapshot.generation,
            compile_result=compile_result,
        )

    try:
        proposed = tuple(
            canonicalize_persisted_configuration(proposal.ref, proposal.document)
            for proposal in request.draft.state.catalog_documents
        )
        transaction = context.repository.begin(
            context.scope,
            base_generation=snapshot.generation,
        )
    except (CatalogRepositoryError, ValidationError, ValueError) as error:
        raise _save_error(
            BuilderSessionSavePersistenceError,
            BuilderSessionSaveErrorCode.PERSISTENCE_FAILED,
            str(error),
            request=request,
            generation=snapshot.generation,
            cause=error,
            compile_result=compile_result,
        ) from error
    try:
        for document, proposal in sorted(
            zip(proposed, request.draft.state.catalog_documents, strict=True),
            key=lambda item: str(item[0].ref),
        ):
            transaction.write_bytes(
                document.ref,
                document.yaml_bytes,
                expected_revision=proposal.expected_revision,
            )
        transaction.write_bytes(
            request.target_ref,
            compile_result.canonical_session_yaml.encode("utf-8"),
            expected_revision=request.expected_session_revision,
        )
        committed = transaction.commit()
    except CatalogConflictError as error:
        transaction.abort()
        raise _save_error(
            BuilderSessionSaveStaleError,
            BuilderSessionSaveErrorCode.STALE_WRITE,
            str(error),
            request=request,
            generation=snapshot.generation,
            cause=error,
            compile_result=compile_result,
        ) from error
    except CatalogValidationError as error:
        transaction.abort()
        code = (
            BuilderSessionSaveErrorCode.GRAPH_INVALID
            if isinstance(error.__cause__, CatalogClosureError)
            else BuilderSessionSaveErrorCode.PERSISTENCE_FAILED
        )
        raise _save_error(
            BuilderSessionSavePersistenceError,
            code,
            str(error),
            request=request,
            generation=snapshot.generation,
            cause=error,
            compile_result=compile_result,
        ) from error
    except CatalogRepositoryError as error:
        transaction.abort()
        raise _save_error(
            BuilderSessionSavePersistenceError,
            BuilderSessionSaveErrorCode.PERSISTENCE_FAILED,
            str(error),
            request=request,
            generation=snapshot.generation,
            cause=error,
            compile_result=compile_result,
        ) from error
    except OSError as error:
        transaction.abort()
        raise _save_error(
            BuilderSessionSavePersistenceError,
            BuilderSessionSaveErrorCode.PERSISTENCE_FAILED,
            str(error),
            request=request,
            generation=snapshot.generation,
            cause=error,
            compile_result=compile_result,
        ) from error

    try:
        saved = committed.get(request.target_ref)
        canonical = canonicalize_persisted_configuration(
            request.target_ref,
            cast(JsonDocument, load_configuration_yaml(saved.content)),
        )
        if saved.content != compile_result.canonical_session_yaml.encode("utf-8"):
            raise ValueError("stored session bytes differ from the compiled canonical YAML")
        if canonical.yaml_bytes != saved.content:
            raise ValueError("stored session bytes are not canonical persisted YAML")
        closure = CatalogClosureCollector.collect(saved.content, committed)
        inventory = _inventory(closure, committed)
        if closure.document_digest != compile_result.digests.document:
            raise ValueError("stored session digest differs from the compiled document digest")
        if closure.closure_digest != compile_result.digests.dependency:
            raise ValueError("stored closure digest differs from the compiled dependency digest")
    except (CatalogRepositoryError, CatalogClosureError, ValidationError, ValueError) as error:
        raise _save_error(
            BuilderSessionSavePersistenceError,
            BuilderSessionSaveErrorCode.STORAGE_VERIFICATION_FAILED,
            f"Committed Builder session failed exact-byte verification: {error}",
            request=request,
            generation=committed.generation,
            repository_committed=True,
            cause=error,
            compile_result=compile_result,
        ) from error

    prepared, preparation_issues = _prepare_saved_session(
        saved,
        committed,
        compile_result,
        available_node_count=available_node_count,
        preparer=preparer,
    )
    if (
        prepared is not None
        and compile_result.digests.resolved_semantic is not None
        and prepared.resolved_semantic_digest != compile_result.digests.resolved_semantic
    ):
        preparation_issues = (
            *preparation_issues,
            _post_commit_issue(
                "builder.persistence.post_commit.semantic_digest_mismatch",
                "Prepared semantic digest differs from the authoritative compile result",
                source_ref=str(saved.ref),
            ),
        )
    issues = _merge_issues(compile_result.issues, preparation_issues)
    resolved_semantic = (
        prepared.resolved_semantic_digest
        if prepared is not None
        else compile_result.digests.resolved_semantic
    )
    digests = BuilderDigests(
        document=closure.document_digest,
        dependency=closure.closure_digest,
        resolved_semantic=resolved_semantic,
    )
    deploy_blockers = tuple(issue for issue in issues if "deploy" in issue.blocks)
    deploy_verdict = BuilderDeployVerdict(
        allowed=not deploy_blockers,
        session_ref=request.target_ref,
        session_revision=str(saved.revision),
        digests=digests,
        blockers=deploy_blockers,
    )
    return BuilderSessionSaveResult(
        session=BuilderCatalogDocument(
            ref=request.target_ref,
            family="sessions",
            canonical_yaml=saved.content.decode("utf-8"),
            canonical_json=canonical.canonical_json,
            content_digest=closure.document_digest,
            revision=str(saved.revision),
        ),
        digests=digests,
        dependency_closure=inventory,
        deploy_verdict=deploy_verdict,
        issues=issues,
    )
