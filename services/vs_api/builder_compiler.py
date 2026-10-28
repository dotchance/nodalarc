"""Read-only Builder compilation through NodalArc configuration authorities."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast

import yaml
from nodalarc.catalog_closure import (
    CatalogClosure,
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogReadDocument,
    CatalogReadError,
    CatalogReadView,
)
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
    BuilderContentEdit,
    BuilderDigests,
    BuilderIssue,
    BuilderIssueStage,
    BuilderListEdit,
    BuilderMemberListPointer,
    BuilderPendingEditOutcome,
    BuilderPendingEdits,
    BuilderProposedCatalogDocument,
    BuilderVerdict,
    BuilderWholeListAddress,
    DependencyClosureEntry,
    DependencyClosureInventory,
    JsonDocument,
)
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.link_rules import LinkRule
from nodalarc.models.resolved_session import SourceContext
from nodalarc.models.segment_session import RoutingBoundary, RoutingDomain
from nodalarc.resolve_session import (
    SessionResolution,
    SessionResolutionError,
    resolve_session_with_assets,
)
from nodalarc.runtime_support import RuntimeSupport, UnsupportedFeatureError
from nodalarc.semantic_projection import resolved_session_semantic_digest
from nodalarc.session_validator import validate_session_readiness
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

PreviewFactory = Callable[[SessionResolution], BuilderWorld]


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
class OverlayCatalogReadView(CatalogReadView):
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
    place: Callable[[str], str] | None = None,
) -> list[BuilderIssue]:
    """One structural issue per validation error. ``place`` gives the draft path of a
    pointer when the validated document is not the one at ``draft_path``."""
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
                draft_path=place(pointer) if place is not None else f"{draft_path}{pointer}",
            )
        )
    return issues


def _candidate_place(
    session: JsonDocument,
    candidate: JsonDocument,
    edits: BuilderPendingEdits,
    outcomes: tuple[BuilderPendingEditOutcome, ...],
) -> Callable[[str], str]:
    """Where a pointer into the session with pending edits applied sits in the request:
    inside an applied edit's object at ``pending_edits.N``, elsewhere at the same object
    in ``state.session``. List edits and new members move objects, so positions differ."""
    applied = [
        (index, edit)
        for index, (edit, outcome) in enumerate(zip(edits, outcomes, strict=True))
        if outcome.status != "stale"
    ]

    def place(pointer: str) -> str:
        for index, edit in applied:
            if isinstance(edit, BuilderListEdit) and pointer == edit.list_pointer:
                return f"pending_edits.{index}"
            if isinstance(edit, BuilderContentEdit) and isinstance(
                edit.address, BuilderWholeListAddress
            ):
                whole = edit.address.pointer
                if pointer == whole or pointer.startswith(f"{whole}/"):
                    return f"pending_edits.{index}"
        for list_pointer in ("/link_rules", "/routing/domains"):
            position, _, rest = pointer.removeprefix(f"{list_pointer}/").partition("/")
            if not pointer.startswith(f"{list_pointer}/") or not position.isdigit():
                continue
            members = _list_at(candidate, list_pointer) or []
            if int(position) >= len(members):
                continue
            member = members[int(position)]
            member_id = member.get("id") if isinstance(member, dict) else None
            for index, edit in applied:
                if (
                    isinstance(edit, BuilderContentEdit)
                    and not isinstance(edit.address, BuilderWholeListAddress)
                    and edit.address.list_pointer == list_pointer
                    and edit.address.id == member_id
                ):
                    return f"pending_edits.{index}"
            accepted = [
                cast(JsonDocument, accepted_member).get("id")
                for accepted_member in _list_at(session, list_pointer) or []
            ]
            if member_id in accepted:
                tail = f"/{rest}" if rest else ""
                return f"state.session{list_pointer}/{accepted.index(member_id)}{tail}"
        return f"state.session{pointer}"

    return place


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


def _check_proposed_creates(
    proposals: tuple[BuilderProposedCatalogDocument, ...],
    snapshot: CatalogReadSnapshot,
) -> list[BuilderIssue]:
    issues: list[BuilderIssue] = []
    for proposal in sorted(proposals, key=lambda item: str(item.ref)):
        try:
            existing = snapshot.get(proposal.ref)
        except CatalogNotFoundError:
            existing = None

        if existing is not None:
            issues.append(
                _staleness_issue(
                    proposal,
                    f"Catalog document {proposal.ref} already exists; session proposals are "
                    "create-only and existing components must be edited through the catalog",
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


def _default_preview(resolution: SessionResolution) -> BuilderWorld:
    from vs_api.builder_world import world_from_resolution

    return world_from_resolution(resolution)


def _closure_inventory(
    closure: CatalogClosure,
    overlay: OverlayCatalogReadView,
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
    pending_edit_outcomes: tuple[BuilderPendingEditOutcome, ...],
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
        pending_edit_outcomes=pending_edit_outcomes,
        save_verdict=_verdict("save", issues),
        deploy_eligibility_after_save=_verdict("deploy", issues),
    )


_MEMBER_LISTS: Mapping[BuilderMemberListPointer, tuple[str, type[BaseModel]]] = MappingProxyType(
    {
        "/link_rules": ("Link rule", LinkRule),
        "/routing/domains": ("Routing domain", RoutingDomain),
    }
)
_ROUTING_BOUNDARIES = TypeAdapter(tuple[RoutingBoundary, ...])
_PENDING_EDITS = TypeAdapter(BuilderPendingEdits)


class _UnaddressableList(ValueError):
    """The session holds something other than a list where a closed pointer names one."""


def _list_at(document: JsonDocument, pointer: str) -> list[JsonValue] | None:
    """The list at one closed pointer, or None when the document does not declare it."""
    value: JsonValue = document
    for part in pointer.split("/")[1:]:
        if not isinstance(value, dict):
            raise _UnaddressableList(f"The session holds no list at {pointer}")
        value = value.get(part)
        if value is None:
            return None
    if not isinstance(value, list):
        raise _UnaddressableList(f"The session holds no list at {pointer}")
    return value


def _write_list(document: JsonDocument, pointer: str, values: list[JsonValue]) -> None:
    """Write a list at a closed pointer whose parents the base check found to be objects."""
    *parents, name = pointer.split("/")[1:]
    container = document
    for part in parents:
        child = container.get(part)
        if child is None:
            child = {}
            container[part] = child
        container = cast(JsonDocument, child)
    container[name] = values


def _remove_list(document: JsonDocument, pointer: str) -> None:
    *parents, name = pointer.split("/")[1:]
    container: JsonValue = document
    for part in parents:
        if not isinstance(container, dict):
            return
        container = container.get(part)
    if isinstance(container, dict):
        container.pop(name, None)


def _member_index(members: list[JsonValue] | None, member_id: str) -> int | None:
    for index, member in enumerate(members or ()):
        if isinstance(member, dict) and member.get("id") == member_id:
            return index
    return None


def _member_pointer(document: JsonDocument, list_pointer: str, member_id: str) -> str:
    """Where one member sits in a document, or its list's pointer when it is not there."""
    try:
        index = _member_index(_list_at(document, list_pointer), member_id)
    except _UnaddressableList:
        return list_pointer
    return list_pointer if index is None else f"{list_pointer}/{index}"


def _edit_pointer(document: JsonDocument, edit: BuilderContentEdit | BuilderListEdit) -> str:
    """Where an edit's object or list sits in a document."""
    if isinstance(edit, BuilderListEdit):
        return edit.list_pointer
    if isinstance(edit.address, BuilderWholeListAddress):
        return edit.address.pointer
    return _member_pointer(document, edit.address.list_pointer, edit.address.id)


def _stale_reason(session: JsonDocument, edit: BuilderContentEdit | BuilderListEdit) -> str | None:
    """Why an edit no longer fits the session it is applied to, or None when its base holds."""
    try:
        if isinstance(edit, BuilderListEdit):
            label, _model = _MEMBER_LISTS[edit.list_pointer]
            members = _list_at(session, edit.list_pointer) or []
            ids = tuple(
                member.get("id") if isinstance(member, dict) else None for member in members
            )
            if ids != edit.ids_before:
                return f"The {label.lower()} list changed since the edit began"
            return None
        if isinstance(edit.address, BuilderWholeListAddress):
            if _list_at(session, edit.address.pointer) != edit.base:
                return "The routing boundary list changed since the edit began"
            return None
        label, _model = _MEMBER_LISTS[edit.address.list_pointer]
        member_id = edit.address.id
        members = _list_at(session, edit.address.list_pointer) or []
        matches = [
            member
            for member in members
            if isinstance(member, dict) and member.get("id") == member_id
        ]
    except _UnaddressableList as error:
        return str(error)
    if edit.base is None:
        return f"{label} '{member_id}' already exists" if matches else None
    if not matches:
        return f"{label} '{member_id}' no longer exists"
    if matches != [edit.base]:
        return f"{label} '{member_id}' changed since the edit began"
    return None


def _pending_edit_issue(
    code: str,
    stage: BuilderIssueStage,
    message: str,
    *,
    index: int,
    json_pointer: str,
    source_ref: str,
) -> BuilderIssue:
    return BuilderIssue(
        code=code,
        stage=stage,
        severity="error",
        message=message,
        blocks=("save", "deploy"),
        source_ref=source_ref,
        json_pointer=json_pointer,
        draft_path=f"pending_edits.{index}",
    )


def _pending_edit_validation_issues(
    error: ValidationError,
    *,
    index: int,
    json_pointer: str,
    source_ref: str,
) -> tuple[BuilderIssue, ...]:
    return tuple(
        _pending_edit_issue(
            f"builder.structural.{item['type']}",
            "structural",
            item["msg"],
            index=index,
            json_pointer=json_pointer + _json_pointer(tuple(item["loc"])),
            source_ref=source_ref,
        )
        for item in error.errors(include_url=False)
    )


def apply_pending_edits(
    session: JsonDocument,
    edits: BuilderPendingEdits,
    *,
    source_ref: str,
) -> tuple[JsonDocument, tuple[BuilderPendingEditOutcome, ...]]:
    """Apply pending object edits to a copy of one session without accepting them.

    Every base is checked against ``session`` first. List edits then remove or
    reorder members, and content edits replace their object by id. A new object
    appends to its list in id order, so the order of the batch never changes
    where an edit lands. A stale edit is left out of the candidate. An invalid
    edit is written into it, so a compile of the candidate shows the refusal.
    Each object is validated as its standalone grammar model, and every issue
    names its edit by ``pending_edits.N`` and its place in the candidate.
    The routing section is created with the first domain. Removing the last
    domain removes the section, and is refused while boundaries remain.
    """
    edits = _PENDING_EDITS.validate_python(edits)
    stale = {
        index: reason
        for index, edit in enumerate(edits)
        if (reason := _stale_reason(session, edit)) is not None
    }
    invalid: dict[int, tuple[BuilderIssue, ...]] = {}
    candidate = deepcopy(session)

    emptied_domains: int | None = None
    for index, edit in enumerate(edits):
        if index in stale or not isinstance(edit, BuilderListEdit):
            continue
        by_id = {
            cast(str, cast(JsonDocument, member)["id"]): member
            for member in _list_at(candidate, edit.list_pointer) or []
        }
        wanted = [by_id[member_id] for member_id in edit.ids_wanted]
        if wanted:
            _write_list(candidate, edit.list_pointer, wanted)
        elif edit.list_pointer == "/routing/domains":
            # Settled after the content edits, since a new domain may still land here.
            _write_list(candidate, edit.list_pointer, wanted)
            emptied_domains = index
        else:
            # Removing the last rule removes the section, as removing the last boundary does.
            _remove_list(candidate, edit.list_pointer)

    content = [
        (index, edit)
        for index, edit in enumerate(edits)
        if index not in stale and isinstance(edit, BuilderContentEdit)
    ]
    new_members: list[tuple[str, str, JsonValue]] = []
    for index, edit in content:
        working = deepcopy(edit.working)
        if isinstance(edit.address, BuilderWholeListAddress):
            if working:
                _write_list(candidate, edit.address.pointer, cast(list[JsonValue], working))
            else:
                _remove_list(candidate, edit.address.pointer)
            continue
        label, _model = _MEMBER_LISTS[edit.address.list_pointer]
        if edit.base is None:
            new_members.append((edit.address.list_pointer, edit.address.id, working))
            continue
        members = _list_at(candidate, edit.address.list_pointer)
        position = _member_index(members, edit.address.id)
        if members is None or position is None:
            stale[index] = f"{label} '{edit.address.id}' is removed by a list edit in this batch"
            continue
        members[position] = working
    for list_pointer, _member_id, working in sorted(new_members, key=lambda item: item[:2]):
        members = _list_at(candidate, list_pointer)
        if members is None:
            _write_list(candidate, list_pointer, [working])
        else:
            members.append(working)

    if emptied_domains is not None and not _list_at(candidate, "/routing/domains"):
        if cast(JsonDocument, candidate["routing"]).get("boundaries"):
            invalid[emptied_domains] = (
                _pending_edit_issue(
                    "builder.draft.routing_boundaries_remain",
                    "draft",
                    "The last routing domain cannot be removed while routing boundaries remain",
                    index=emptied_domains,
                    json_pointer="/routing/domains",
                    source_ref=source_ref,
                ),
            )
        else:
            candidate.pop("routing")

    for index, edit in content:
        if index in stale:
            continue
        try:
            if isinstance(edit.address, BuilderWholeListAddress):
                _ROUTING_BOUNDARIES.validate_python(edit.working)
            else:
                _label, model = _MEMBER_LISTS[edit.address.list_pointer]
                model.model_validate(edit.working)
        except ValidationError as error:
            invalid[index] = _pending_edit_validation_issues(
                error,
                index=index,
                json_pointer=_edit_pointer(candidate, edit),
                source_ref=source_ref,
            )

    outcomes: list[BuilderPendingEditOutcome] = []
    for index, edit in enumerate(edits):
        if index in stale:
            issue = _pending_edit_issue(
                "builder.staleness.pending_edit",
                "staleness",
                stale[index],
                index=index,
                json_pointer=_edit_pointer(candidate, edit),
                source_ref=source_ref,
            )
            outcomes.append(BuilderPendingEditOutcome(status="stale", issues=(issue,)))
        elif index in invalid:
            outcomes.append(BuilderPendingEditOutcome(status="invalid", issues=invalid[index]))
        else:
            outcomes.append(BuilderPendingEditOutcome(status="applied"))
    return candidate, tuple(outcomes)


def compile_builder_draft(
    request: BuilderCompileRequest,
    snapshot: CatalogReadSnapshot,
    *,
    available_node_count: int,
    runtime_support: RuntimeSupport | None = None,
    preview_factory: PreviewFactory | None = None,
) -> BuilderCompileResult:
    """Compile one draft with its pending edits applied, without mutating its catalog snapshot."""

    if not isinstance(request, BuilderCompileRequest):
        raise TypeError("request must be a BuilderCompileRequest")
    if not isinstance(snapshot, CatalogReadSnapshot):
        raise TypeError("snapshot must be a CatalogReadSnapshot")
    if not isinstance(available_node_count, int) or isinstance(available_node_count, bool):
        raise TypeError("available_node_count must be an integer")
    if available_node_count < 0:
        raise ValueError("available_node_count must be non-negative")
    session, outcomes = apply_pending_edits(
        request.draft.state.session,
        request.pending_edits,
        source_ref=str(request.target_ref),
    )
    place = _candidate_place(request.draft.state.session, session, request.pending_edits, outcomes)
    # A stale edit is left out of the session, so only its outcome can report it.
    # An invalid edit is written into the session, whose validation below refuses it.
    issues: list[BuilderIssue] = [
        issue for outcome in outcomes if outcome.status == "stale" for issue in outcome.issues
    ]
    canonical_session: CanonicalConfigurationDocument | None = None
    try:
        canonical_session = canonicalize_persisted_configuration(
            request.target_ref,
            session,
        )
    except ValidationError as error:
        issues.extend(
            _validation_issues(
                error,
                source_ref=str(request.target_ref),
                draft_path="state.session",
                place=place,
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
            pending_edit_outcomes=outcomes,
        )

    overlay = OverlayCatalogReadView(
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
        issues.extend(_check_proposed_creates(overlay.reached_proposals(), snapshot))
        return _compile_result(
            request,
            canonical_session=canonical_session,
            dependency_closure=None,
            resolved_preview=None,
            digests=None,
            issues=issues,
            pending_edit_outcomes=outcomes,
        )
    except CatalogClosureError as error:
        issues.append(_reference_issue(error, fallback_ref=str(request.target_ref)))
        issues.extend(_check_proposed_creates(overlay.reached_proposals(), snapshot))
        return _compile_result(
            request,
            canonical_session=canonical_session,
            dependency_closure=None,
            resolved_preview=None,
            digests=None,
            issues=issues,
            pending_edit_outcomes=outcomes,
        )

    reached_proposals = overlay.reached_proposals()
    reached_refs = {proposal.ref for proposal in reached_proposals}
    excluded_proposals = tuple(
        proposal
        for proposal in request.draft.state.catalog_documents
        if proposal.ref not in reached_refs
    )
    request = _scope_request_to_proposals(request, reached_proposals)
    issues.extend(_check_proposed_creates(reached_proposals, snapshot))
    if excluded_proposals:
        issues.append(_excluded_proposals_issue(request, excluded_proposals))

    inventory = _closure_inventory(closure, overlay)
    digests = BuilderDigests(
        document=closure.document_digest,
        dependency=closure.closure_digest,
    )

    resolution: SessionResolution | None = None
    preview: BuilderWorld | None = None
    try:
        resolution = resolve_session_with_assets(
            cast(dict[str, Any], canonical_session.canonical_json),
            catalog=closure.read_view(),
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
                place=place,
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
    except CatalogReadError as error:
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
            preview = (preview_factory or _default_preview)(resolution)
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
                        code=(f"builder.runtime_support.{feature.category.value}.{feature.value}"),
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
        pending_edit_outcomes=outcomes,
    )
