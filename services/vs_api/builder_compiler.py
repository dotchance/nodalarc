"""Read-only Builder compilation through NodalArc configuration authorities."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml
from nodalarc.catalog_closure import (
    CatalogClosure,
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogReadDocument,
    CatalogReadView,
)
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_refs import CatalogFamily, CatalogRef
from nodalarc.catalog_registry import validate_referenced_configuration_document
from nodalarc.catalog_repository import (
    CatalogNotFoundError,
    CatalogReadSnapshot,
)
from nodalarc.models.builder_api import (
    BuilderBlockedOperation,
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderDigests,
    BuilderIssue,
    BuilderProposedCatalogDocument,
    BuilderVerdict,
    DependencyClosureEntry,
    DependencyClosureInventory,
    JsonDocument,
)
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import (
    SessionResolution,
    SessionResolutionError,
    resolve_session_with_assets,
)
from nodalarc.runtime_support import RuntimeSupport, UnsupportedFeatureError
from nodalarc.semantic_projection import resolved_session_semantic_digest
from nodalarc.session_validator import validate_session_readiness
from pydantic import ValidationError

PreviewFactory = Callable[[dict[str, Any], CatalogRoots], BuilderWorld]


@dataclass(frozen=True, slots=True)
class CanonicalConfigurationDocument:
    """One strict persisted document in deterministic JSON and YAML forms."""

    ref: CatalogRef
    family: CatalogFamily
    canonical_json: JsonDocument
    yaml_bytes: bytes
    document_digest: str


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _canonical_yaml(document: JsonDocument) -> bytes:
    return yaml.safe_dump(
        document,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    ).encode("utf-8")


def canonicalize_persisted_configuration(
    ref: CatalogRef,
    document: JsonDocument,
) -> CanonicalConfigurationDocument:
    """Validate and canonicalize one full persisted configuration document."""

    family = cast(CatalogFamily, ref.family)
    wrapper, model = validate_referenced_configuration_document(ref, document)

    normalized = cast(
        JsonDocument,
        model.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    canonical_json: JsonDocument = normalized if wrapper is None else {wrapper: normalized}
    yaml_bytes = _canonical_yaml(canonical_json)
    return CanonicalConfigurationDocument(
        ref=ref,
        family=family,
        canonical_json=canonical_json,
        yaml_bytes=yaml_bytes,
        document_digest=_sha256(yaml_bytes),
    )


class _ReachableProposalValidationError(ValueError):
    def __init__(
        self,
        proposal: BuilderProposedCatalogDocument,
        index: int,
        cause: ValidationError | TypeError | ValueError,
    ) -> None:
        super().__init__(str(cause))
        self.proposal = proposal
        self.index = index
        self.cause = cause


@dataclass(frozen=True, slots=True)
class _OverlayCatalogReadView(CatalogReadView):
    base: CatalogReadSnapshot
    proposals: Mapping[CatalogRef, tuple[int, BuilderProposedCatalogDocument]]
    canonicalized: dict[CatalogRef, CanonicalConfigurationDocument] = field(default_factory=dict)
    reached: set[CatalogRef] = field(default_factory=set)

    def read(self, ref: CatalogRef) -> CatalogReadDocument:
        indexed = self.proposals.get(ref)
        if indexed is not None:
            self.reached.add(ref)
            index, proposal = indexed
            document = self.canonicalized.get(ref)
            if document is None:
                try:
                    document = canonicalize_persisted_configuration(
                        proposal.ref,
                        proposal.document,
                    )
                except (ValidationError, TypeError, ValueError) as error:
                    raise _ReachableProposalValidationError(
                        proposal,
                        index,
                        error,
                    ) from error
                self.canonicalized[ref] = document
            return CatalogReadDocument(
                family=document.family,
                preserved_path=f"catalog/{ref.namespace}/{ref.relative_path.as_posix()}",
                yaml_bytes=document.yaml_bytes,
            )
        return self.base.read(ref)

    def revision(self, ref: CatalogRef) -> str | None:
        if ref in self.proposals:
            return None
        return str(self.base.get(ref).revision)

    def reached_proposals(self) -> tuple[BuilderProposedCatalogDocument, ...]:
        return tuple(
            proposal
            for index, proposal in sorted(self.proposals.values(), key=lambda item: item[0])
            if proposal.ref in self.reached
        )


def _json_pointer(parts: tuple[object, ...]) -> str:
    if not parts:
        return ""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _validation_issues(
    error: ValidationError,
    *,
    source_ref: str,
    draft_path: str,
) -> list[BuilderIssue]:
    issues: list[BuilderIssue] = []
    for item in error.errors(include_url=False):
        pointer = _json_pointer(tuple(item["loc"]))
        issues.append(
            BuilderIssue(
                code=f"builder.structural.{item['type']}",
                stage="structural",
                severity="error",
                message=item["msg"],
                blocks=("save", "deploy"),
                source_ref=source_ref,
                json_pointer=pointer or None,
                draft_path=f"{draft_path}{pointer}",
            )
        )
    return issues


def _structural_issue(
    message: str,
    *,
    source_ref: str,
    draft_path: str,
    code: str = "builder.structural.invalid_document",
) -> BuilderIssue:
    return BuilderIssue(
        code=code,
        stage="structural",
        severity="error",
        message=message,
        blocks=("save", "deploy"),
        source_ref=source_ref,
        draft_path=draft_path,
    )


def _reference_issue(error: CatalogClosureError, *, fallback_ref: str) -> BuilderIssue:
    evidence = error.evidence
    return BuilderIssue(
        code=f"builder.{error.code.value}",
        stage="reference",
        severity="error",
        message=evidence.message,
        blocks=("save", "deploy"),
        source_ref=evidence.ref or fallback_ref,
        related_refs=evidence.dependency_chain,
    )


def _staleness_issue(
    proposal: BuilderProposedCatalogDocument,
    message: str,
) -> BuilderIssue:
    return BuilderIssue(
        code="builder.staleness.catalog_revision",
        stage="staleness",
        severity="error",
        message=message,
        blocks=("save", "deploy"),
        source_ref=str(proposal.ref),
    )


def _check_proposed_revisions(
    proposals: tuple[BuilderProposedCatalogDocument, ...],
    snapshot: CatalogReadSnapshot,
) -> list[BuilderIssue]:
    issues: list[BuilderIssue] = []
    for proposal in sorted(proposals, key=lambda item: str(item.ref)):
        try:
            existing = snapshot.get(proposal.ref)
        except CatalogNotFoundError:
            existing = None

        if proposal.expected_revision is None:
            if existing is not None:
                issues.append(
                    _staleness_issue(
                        proposal,
                        f"Catalog document {proposal.ref} already exists; an expected revision "
                        "is required to replace it",
                    )
                )
        elif existing is None:
            issues.append(
                _staleness_issue(
                    proposal,
                    f"Catalog document {proposal.ref} does not exist at expected revision "
                    f"{proposal.expected_revision}",
                )
            )
        elif str(existing.revision) != proposal.expected_revision:
            issues.append(
                _staleness_issue(
                    proposal,
                    f"Catalog document {proposal.ref} is stale: expected "
                    f"{proposal.expected_revision}, current revision is {existing.revision}",
                )
            )
    return issues


def _reachable_proposal_validation_issues(
    error: _ReachableProposalValidationError,
) -> list[BuilderIssue]:
    draft_path = f"state.catalog_documents.{error.index}.document"
    if isinstance(error.cause, ValidationError):
        return _validation_issues(
            error.cause,
            source_ref=str(error.proposal.ref),
            draft_path=draft_path,
        )
    return [
        _structural_issue(
            str(error.cause),
            source_ref=str(error.proposal.ref),
            draft_path=draft_path,
        )
    ]


def _scope_request_to_proposals(
    request: BuilderCompileRequest,
    proposals: tuple[BuilderProposedCatalogDocument, ...],
) -> BuilderCompileRequest:
    state = request.draft.state.model_copy(update={"catalog_documents": proposals})
    draft = request.draft.model_copy(update={"state": state})
    return request.model_copy(update={"draft": draft})


def _excluded_proposals_issue(
    request: BuilderCompileRequest,
    proposals: tuple[BuilderProposedCatalogDocument, ...],
) -> BuilderIssue:
    refs = tuple(sorted(str(proposal.ref) for proposal in proposals))
    return BuilderIssue(
        code="builder.draft.unreferenced_catalog_documents",
        stage="draft",
        severity="warning",
        message=(
            "Excluded catalog proposals outside the canonical session dependency closure: "
            + ", ".join(refs)
        ),
        source_ref=str(request.target_ref),
        draft_path="state.catalog_documents",
        related_refs=refs,
    )


def _materialize_closure(root: Path, closure: CatalogClosure) -> CatalogRoots:
    shipped_root = root / "catalog" / "nodalarc"
    user_root = root / "catalog" / "user"
    shipped_root.mkdir(parents=True)
    user_root.mkdir(parents=True)
    for entry in closure.entries:
        relative = PurePosixPath(entry.preserved_path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) < 3
            or relative.parts[0] != "catalog"
            or relative.parts[1] not in {"nodalarc", "user"}
        ):
            raise ValueError(f"invalid preserved catalog path {entry.preserved_path!r}")
        destination = root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(entry.yaml_bytes)
    return CatalogRoots.from_catalog_root(shipped_root, user_root=user_root)


def _default_preview(raw_session: dict[str, Any], roots: CatalogRoots) -> BuilderWorld:
    from vs_api.builder_world import build_builder_world

    return build_builder_world(raw_session, catalog_roots=roots)


def _closure_inventory(
    closure: CatalogClosure,
    overlay: _OverlayCatalogReadView,
) -> DependencyClosureInventory:
    entries = tuple(
        DependencyClosureEntry(
            ref=entry.ref,
            family=entry.family,
            revision=overlay.revision(entry.ref),
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


def _verdict(
    operation: BuilderBlockedOperation,
    issues: list[BuilderIssue],
) -> BuilderVerdict:
    blockers = tuple(issue for issue in issues if operation in issue.blocks)
    return BuilderVerdict(
        operation=operation,
        allowed=not blockers,
        blockers=blockers,
    )


def _compile_result(
    request: BuilderCompileRequest,
    *,
    canonical_session: CanonicalConfigurationDocument | None,
    dependency_closure: DependencyClosureInventory | None,
    resolved_preview: BuilderWorld | None,
    digests: BuilderDigests | None,
    issues: list[BuilderIssue],
) -> BuilderCompileResult:
    return BuilderCompileResult(
        draft=request.draft,
        target_ref=request.target_ref,
        canonical_session_yaml=(
            canonical_session.yaml_bytes.decode("utf-8") if canonical_session is not None else None
        ),
        canonical_session_json=(
            canonical_session.canonical_json if canonical_session is not None else None
        ),
        dependency_closure=dependency_closure,
        resolved_preview=resolved_preview,
        digests=digests,
        issues=tuple(issues),
        save_verdict=_verdict("save", issues),
        deploy_eligibility_after_save=_verdict("deploy", issues),
    )


def compile_builder_draft(
    request: BuilderCompileRequest,
    snapshot: CatalogReadSnapshot,
    *,
    available_node_count: int = 1,
    runtime_support: RuntimeSupport | None = None,
    preview_factory: PreviewFactory | None = None,
) -> BuilderCompileResult:
    """Compile one complete draft without mutating its catalog snapshot."""

    if not isinstance(request, BuilderCompileRequest):
        raise TypeError("request must be a BuilderCompileRequest")
    if not isinstance(snapshot, CatalogReadSnapshot):
        raise TypeError("snapshot must be a CatalogReadSnapshot")
    if not isinstance(available_node_count, int) or isinstance(available_node_count, bool):
        raise TypeError("available_node_count must be an integer")
    if available_node_count < 0:
        raise ValueError("available_node_count must be non-negative")
    issues: list[BuilderIssue] = []
    canonical_session: CanonicalConfigurationDocument | None = None
    try:
        canonical_session = canonicalize_persisted_configuration(
            request.target_ref,
            request.draft.state.session,
        )
    except ValidationError as error:
        issues.extend(
            _validation_issues(
                error,
                source_ref=str(request.target_ref),
                draft_path="state.session",
            )
        )
    except (TypeError, ValueError) as error:
        issues.append(
            _structural_issue(
                str(error),
                source_ref=str(request.target_ref),
                draft_path="state.session",
            )
        )

    if canonical_session is None:
        return _compile_result(
            request,
            canonical_session=canonical_session,
            dependency_closure=None,
            resolved_preview=None,
            digests=None,
            issues=issues,
        )

    overlay = _OverlayCatalogReadView(
        snapshot,
        {
            proposal.ref: (index, proposal)
            for index, proposal in enumerate(request.draft.state.catalog_documents)
        },
    )

    try:
        closure = CatalogClosureCollector.collect(canonical_session.yaml_bytes, overlay)
    except _ReachableProposalValidationError as error:
        issues.extend(_reachable_proposal_validation_issues(error))
        issues.extend(_check_proposed_revisions(overlay.reached_proposals(), snapshot))
        return _compile_result(
            request,
            canonical_session=canonical_session,
            dependency_closure=None,
            resolved_preview=None,
            digests=None,
            issues=issues,
        )
    except CatalogClosureError as error:
        issues.append(_reference_issue(error, fallback_ref=str(request.target_ref)))
        issues.extend(_check_proposed_revisions(overlay.reached_proposals(), snapshot))
        return _compile_result(
            request,
            canonical_session=canonical_session,
            dependency_closure=None,
            resolved_preview=None,
            digests=None,
            issues=issues,
        )

    reached_proposals = overlay.reached_proposals()
    reached_refs = {proposal.ref for proposal in reached_proposals}
    excluded_proposals = tuple(
        proposal
        for proposal in request.draft.state.catalog_documents
        if proposal.ref not in reached_refs
    )
    request = _scope_request_to_proposals(request, reached_proposals)
    issues.extend(_check_proposed_revisions(reached_proposals, snapshot))
    if excluded_proposals:
        issues.append(_excluded_proposals_issue(request, excluded_proposals))

    inventory = _closure_inventory(closure, overlay)
    digests = BuilderDigests(
        document=closure.document_digest,
        dependency=closure.closure_digest,
    )

    resolution: SessionResolution | None = None
    preview: BuilderWorld | None = None
    with tempfile.TemporaryDirectory(prefix="nodalarc-builder-compile-") as temporary:
        roots = _materialize_closure(Path(temporary), closure)
        try:
            resolution = resolve_session_with_assets(
                cast(dict[str, Any], canonical_session.canonical_json),
                catalog_roots=roots,
                runtime_support=runtime_support,
                source_context=SourceContext(origin="builder.compile"),
            )
        except UnsupportedFeatureError as error:
            for feature in error.features:
                issues.append(
                    BuilderIssue(
                        code=(f"builder.runtime_support.{feature.category.value}.{feature.value}"),
                        stage="runtime_support",
                        severity="error",
                        message=feature.message,
                        blocks=("deploy",),
                        source_ref=str(request.target_ref),
                    )
                )
        except ValidationError as error:
            issues.extend(
                _validation_issues(
                    error,
                    source_ref=str(request.target_ref),
                    draft_path="state.session",
                )
            )
        except SessionResolutionError as error:
            issues.append(
                BuilderIssue(
                    code="builder.semantic.session_resolution",
                    stage="semantic",
                    severity="error",
                    message=str(error),
                    blocks=("save", "deploy"),
                    source_ref=str(request.target_ref),
                    related_refs=tuple(
                        value
                        for value in (error.subject_id, error.segment_id, error.node_id)
                        if value is not None
                    ),
                )
            )
        except FileNotFoundError as error:
            issues.append(
                BuilderIssue(
                    code="builder.reference.resolver_read",
                    stage="reference",
                    severity="error",
                    message=str(error),
                    blocks=("save", "deploy"),
                    source_ref=str(request.target_ref),
                )
            )
        except ValueError as error:
            issues.append(
                BuilderIssue(
                    code="builder.semantic.invalid_session",
                    stage="semantic",
                    severity="error",
                    message=str(error),
                    blocks=("save", "deploy"),
                    source_ref=str(request.target_ref),
                )
            )

        if resolution is not None:
            readiness = validate_session_readiness(
                resolution.resolved,
                available_node_count=available_node_count,
            )
            for result in readiness:
                if result.level not in {"error", "warning"}:
                    raise ValueError(f"readiness validator returned unknown level {result.level!r}")
                blocks = ("deploy",) if result.level == "error" else ()
                issues.append(
                    BuilderIssue(
                        code=f"builder.readiness.{result.code}",
                        stage="readiness",
                        severity=cast(Any, result.level),
                        message=result.message,
                        blocks=blocks,
                        source_ref=str(request.target_ref),
                        draft_path=result.field_path,
                    )
                )
            if not any(node.kind == "satellite" for node in resolution.resolved.nodes):
                issues.append(
                    BuilderIssue(
                        code="builder.readiness.no_satellites",
                        stage="readiness",
                        severity="error",
                        message="The session contains no satellites and cannot start",
                        blocks=("deploy",),
                        source_ref=str(request.target_ref),
                        draft_path="state.session.segments",
                    )
                )
            try:
                preview = (preview_factory or _default_preview)(
                    cast(dict[str, Any], canonical_session.canonical_json),
                    roots,
                )
            except ValidationError as error:
                issues.extend(
                    _validation_issues(
                        error,
                        source_ref=str(request.target_ref),
                        draft_path="state.session",
                    )
                )
            except UnsupportedFeatureError as error:
                for feature in error.features:
                    issues.append(
                        BuilderIssue(
                            code=(
                                f"builder.runtime_support.{feature.category.value}.{feature.value}"
                            ),
                            stage="runtime_support",
                            severity="error",
                            message=feature.message,
                            blocks=("deploy",),
                            source_ref=str(request.target_ref),
                        )
                    )
            except SessionResolutionError as error:
                issues.append(
                    BuilderIssue(
                        code="builder.semantic.preview_resolution",
                        stage="semantic",
                        severity="error",
                        message=str(error),
                        blocks=("save", "deploy"),
                        source_ref=str(request.target_ref),
                    )
                )
            except ValueError as error:
                issues.append(
                    BuilderIssue(
                        code="builder.readiness.preview_unavailable",
                        stage="readiness",
                        severity="error",
                        message=f"Runtime preview could not be built: {error}",
                        blocks=("deploy",),
                        source_ref=str(request.target_ref),
                    )
                )

    if resolution is not None:
        digests = BuilderDigests(
            document=closure.document_digest,
            dependency=closure.closure_digest,
            resolved_semantic=resolved_session_semantic_digest(resolution.resolved),
        )

    return _compile_result(
        request,
        canonical_session=canonical_session,
        dependency_closure=inventory,
        resolved_preview=preview,
        digests=digests,
        issues=issues,
    )
