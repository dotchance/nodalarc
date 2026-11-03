"""Typed application contracts for Builder authoring APIs.

These models describe browser/VS-API workflow state. They are not NodalArc
configuration grammar and none of them may be persisted or deployed as a
session document. Incomplete authoring values remain generic JSON, while
successful canonical responses reuse and validate against the canonical
configuration models owned by the grammar authority.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    field_validator,
    model_validator,
)

from nodalarc.catalog_refs import (
    CatalogFamily,
    CatalogRef,
    SessionRef,
    parse_catalog_reference,
)
from nodalarc.catalog_registry import catalog_family_spec
from nodalarc.model_validation import Identifier
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.configuration import ConfigurationDocument
from nodalarc.models.resolved_session import ResolutionSubjectKind
from nodalarc.models.segment_session import SegmentSessionConfig

Sha256Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
OpaqueRevision = Annotated[str, Field(min_length=1)]
JsonDocument = dict[str, JsonValue]


def _validated_configuration_json(
    document: ConfigurationDocument,
) -> JsonDocument:
    return document.model_dump(mode="json", by_alias=True, exclude_none=True)


_JSON_IDENTITY_SERIALIZER = PlainSerializer(lambda value: value)
type ValidatedConfigurationJson = Annotated[
    ConfigurationDocument,
    AfterValidator(_validated_configuration_json),
    _JSON_IDENTITY_SERIALIZER,
]
type ValidatedSessionJson = Annotated[
    SegmentSessionConfig,
    AfterValidator(_validated_configuration_json),
    _JSON_IDENTITY_SERIALIZER,
]
type BuilderIssueStage = Literal[
    "draft",
    "structural",
    "reference",
    "semantic",
    "runtime_support",
    "readiness",
    "persistence",
    "deployment",
    "staleness",
]
type BuilderIssueSeverity = Literal["info", "warning", "error"]
type BuilderBlockedOperation = Literal["save", "deploy"]
type BuilderMemberListPointer = Literal["/link_rules", "/routing/domains", "/segments"]
# The session's singleton sections, each edited as one whole object.
type BuilderSectionPointer = Literal["/session", "/time", "/simulation", "/addressing", "/dispatch"]
type BuilderWholeListPointer = Literal["/routing/boundaries"]
type BuilderPendingEditStatus = Literal["applied", "stale", "invalid"]
type BuilderSessionSaveRefusalCode = Literal[
    "builder_session_save.save_blocked",
    "builder_session_save.stale_write",
    "builder_session_save.graph_invalid",
    "builder_session_save.persistence_failed",
    "builder_session_save.storage_verification_failed",
]


class _BuilderApplicationModel(BaseModel):
    """Closed, immutable DTO base for non-grammar Builder contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class BuilderIssue(_BuilderApplicationModel):
    """One typed finding routed to its owning authoring or deployment stage."""

    code: str = Field(min_length=1)
    stage: BuilderIssueStage
    severity: BuilderIssueSeverity
    message: str = Field(min_length=1)
    blocks: tuple[BuilderBlockedOperation, ...] = ()
    source_ref: str | None = None
    json_pointer: str | None = None
    draft_path: str | None = None
    source_line: int | None = Field(default=None, ge=1)
    source_column: int | None = Field(default=None, ge=1)
    # The authored object a resolver refusal names; ``json_pointer`` locates it.
    subject_kind: ResolutionSubjectKind | None = None
    related_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _blocking_issues_are_errors(self) -> BuilderIssue:
        if len(set(self.blocks)) != len(self.blocks):
            raise ValueError("blocked operations must be unique")
        if self.blocks and self.severity != "error":
            raise ValueError("only error issues may block save or deployment")
        return self


class BuilderCatalogDocument(_BuilderApplicationModel):
    """One canonical catalog or session document with opaque revision identity."""

    ref: CatalogRef
    family: CatalogFamily
    canonical_yaml: str = Field(min_length=1)
    canonical_json: ValidatedConfigurationJson
    content_digest: Sha256Digest
    revision: OpaqueRevision

    @model_validator(mode="after")
    def _family_matches_reference(self) -> BuilderCatalogDocument:
        if parse_catalog_reference(self.ref).family != self.family:
            raise ValueError("catalog document family must match its reference path")
        try:
            catalog_family_spec(self.family).validate_document(self.canonical_json)
        except (TypeError, ValueError) as error:
            raise ValueError("canonical document must match its catalog family") from error
        return self


class BuilderProposedCatalogDocument(_BuilderApplicationModel):
    """One complete new catalog document proposed for a ``user:`` ref."""

    ref: CatalogRef
    document: JsonDocument
    origin: Literal["generated", "customized"]

    @model_validator(mode="after")
    def _target_is_a_user_component(self) -> BuilderProposedCatalogDocument:
        parsed = parse_catalog_reference(self.ref)
        if parsed.namespace != "user":
            raise ValueError("proposed catalog documents must target the user: namespace")
        if parsed.family not in get_args(CatalogFamily.__value__) or parsed.family == "sessions":
            raise ValueError(
                "proposed catalog documents must target a registered component catalog family; "
                "the draft session candidate is stored separately"
            )
        return self


class BuilderDraftState(_BuilderApplicationModel):
    """Complete transient configuration state compiled by the backend.

    The inner mappings intentionally remain generic JSON so incomplete or
    invalid configuration can cross the API boundary and return typed compile
    issues instead of being rejected as a malformed application request.
    """

    session: JsonDocument
    catalog_documents: tuple[BuilderProposedCatalogDocument, ...] = ()

    @field_validator("catalog_documents", mode="before")
    @classmethod
    def _accept_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _catalog_targets_are_unique(self) -> BuilderDraftState:
        refs = [document.ref for document in self.catalog_documents]
        if len(set(refs)) != len(refs):
            raise ValueError("proposed catalog document refs must be unique")
        return self


class BuilderDraftEnvelope(_BuilderApplicationModel):
    """One revision of transient editor state accepted by compile/save APIs only."""

    draft_revision: int = Field(ge=0)
    state: BuilderDraftState


class BuilderMemberAddress(_BuilderApplicationModel):
    """One member of an id-bearing session list, by the id it had when the edit began."""

    kind: Literal["member"]
    list_pointer: BuilderMemberListPointer
    id: Identifier


class BuilderWholeListAddress(_BuilderApplicationModel):
    """A session list whose members carry no id, edited as one whole."""

    kind: Literal["whole_list"]
    pointer: BuilderWholeListPointer


class BuilderSectionAddress(_BuilderApplicationModel):
    """One singleton section of the session, by its pointer."""

    kind: Literal["section"]
    pointer: BuilderSectionPointer


class BuilderProposalAddress(_BuilderApplicationModel):
    """One catalog document the session proposes, by its ``user:`` reference."""

    kind: Literal["proposal"]
    ref: CatalogRef

    @model_validator(mode="after")
    def _ref_names_a_user_component(self) -> BuilderProposalAddress:
        if self.ref.namespace != "user":
            raise ValueError("a proposed document is a user: component")
        if self.ref.family is None or catalog_family_spec(self.ref.family).wrapper is None:
            raise ValueError("a proposed document is a wrapped catalog component")
        return self


BuilderSessionObjectAddress = Annotated[
    BuilderMemberAddress | BuilderWholeListAddress | BuilderSectionAddress,
    Field(discriminator="kind"),
]
BuilderEditAddress = Annotated[
    BuilderMemberAddress | BuilderWholeListAddress | BuilderSectionAddress | BuilderProposalAddress,
    Field(discriminator="kind"),
]


def check_value_fits_address(
    address: BuilderMemberAddress
    | BuilderWholeListAddress
    | BuilderSectionAddress
    | BuilderProposalAddress,
    value: JsonDocument | list[JsonValue],
) -> None:
    """Raise ValueError unless ``value`` is what ``address`` holds.

    A whole list holds a list. A list member holds an object that keeps the id
    it is addressed by. A proposal holds its whole wrapped document, whose id
    is the reference's file name.
    """
    if isinstance(address, BuilderWholeListAddress):
        if not isinstance(value, list):
            raise ValueError("an edit to a whole list holds that list")
        return
    if not isinstance(value, dict):
        raise ValueError("an edit to a list member or section holds that object")
    if isinstance(address, BuilderSectionAddress):
        return
    if isinstance(address, BuilderMemberAddress) and address.list_pointer == "/segments":
        # A segment's id is one of its fields: an edit may rename it.
        return
    if isinstance(address, BuilderProposalAddress):
        wrapper = catalog_family_spec(cast(CatalogFamily, address.ref.family)).wrapper
        body = value.get(cast(str, wrapper))
        if set(value) != {wrapper} or not isinstance(body, dict):
            raise ValueError(f"an edit to a proposed document holds its {wrapper!r} object")
        if body.get("id") != address.ref.relative_path.stem:
            raise ValueError(
                "an edit keeps the id its reference fixes; a rename is an edit of the session YAML"
            )
        return
    if value.get("id") != address.id:
        raise ValueError(
            "an edit keeps the id it is addressed by; a rename is an edit of the session YAML"
        )


class BuilderContentEdit(_BuilderApplicationModel):
    """Working grammar JSON for one address, beside the value it began from.

    ``base`` is absent for a new object. Both values stay generic JSON so an
    incomplete object can cross the API boundary and come back with issues.
    """

    kind: Literal["content"]
    address: BuilderEditAddress
    base: JsonDocument | list[JsonValue] | None = None
    working: JsonDocument | list[JsonValue]

    @model_validator(mode="after")
    def _values_fit_the_address(self) -> BuilderContentEdit:
        for value in (self.working,) if self.base is None else (self.base, self.working):
            check_value_fits_address(self.address, value)
        return self


class BuilderListEdit(_BuilderApplicationModel):
    """A removal or reorder of an id-bearing session list's members."""

    kind: Literal["list"]
    list_pointer: BuilderMemberListPointer
    ids_before: tuple[Identifier, ...]
    ids_wanted: tuple[Identifier, ...]

    @field_validator("ids_before", "ids_wanted", mode="before")
    @classmethod
    def _accept_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _only_removes_or_reorders(self) -> BuilderListEdit:
        for ids in (self.ids_before, self.ids_wanted):
            if len(set(ids)) != len(ids):
                raise ValueError("list edit ids must be unique")
        if not set(self.ids_wanted) <= set(self.ids_before):
            raise ValueError("a list edit only removes or reorders the ids it began with")
        return self


BuilderPendingEdit = Annotated[
    BuilderContentEdit | BuilderListEdit,
    Field(discriminator="kind"),
]


def _edit_target(edit: BuilderContentEdit | BuilderListEdit) -> tuple[str, ...]:
    if isinstance(edit, BuilderListEdit):
        return ("list", edit.list_pointer)
    if isinstance(edit.address, BuilderWholeListAddress):
        return ("content", edit.address.pointer)
    if isinstance(edit.address, BuilderProposalAddress):
        return ("content", "proposal", str(edit.address.ref))
    if isinstance(edit.address, BuilderSectionAddress):
        return ("content", "section", edit.address.pointer)
    return ("content", edit.address.list_pointer, edit.address.id)


def _one_edit_per_address(
    edits: tuple[BuilderContentEdit | BuilderListEdit, ...],
) -> tuple[BuilderContentEdit | BuilderListEdit, ...]:
    targets = [_edit_target(edit) for edit in edits]
    if len(set(targets)) != len(targets):
        raise ValueError(
            "pending edits hold one content edit per address and one list edit per list"
        )
    return edits


BuilderPendingEdits = Annotated[
    tuple[BuilderPendingEdit, ...],
    AfterValidator(_one_edit_per_address),
]


class BuilderPendingEditOutcome(_BuilderApplicationModel):
    """What applying one pending edit did, at that edit's position in its batch."""

    status: BuilderPendingEditStatus
    issues: tuple[BuilderIssue, ...] = ()

    @model_validator(mode="after")
    def _issues_match_status(self) -> BuilderPendingEditOutcome:
        if self.status == "applied" and self.issues:
            raise ValueError("an applied edit carries no issues")
        if self.status != "applied" and not self.issues:
            raise ValueError("a stale or invalid edit names at least one issue")
        return self


class DependencyClosureEntry(_BuilderApplicationModel):
    """One backend-discovered document in a transitive reference closure."""

    ref: CatalogRef
    family: CatalogFamily
    revision: OpaqueRevision | None = None
    document_digest: Sha256Digest
    preserved_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _family_matches_reference(self) -> DependencyClosureEntry:
        parsed = parse_catalog_reference(self.ref)
        if parsed.family != self.family:
            raise ValueError("closure entry family must match its reference path")
        expected_path = f"catalog/{parsed.namespace}/{parsed.relative_path.as_posix()}"
        if self.preserved_path != expected_path:
            raise ValueError("closure entry preserved path must match its reference")
        return self


class DependencyClosureInventory(_BuilderApplicationModel):
    """Response-only facts for the exact dependency closure found by the backend."""

    entries: tuple[DependencyClosureEntry, ...]
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    closure_digest: Sha256Digest

    @model_validator(mode="after")
    def _counts_match_entries(self) -> DependencyClosureInventory:
        if self.file_count != len(self.entries):
            raise ValueError("file_count must equal the number of closure entries")
        if self.total_bytes != sum(entry.size_bytes for entry in self.entries):
            raise ValueError("total_bytes must equal the closure entry byte total")
        refs = {entry.ref for entry in self.entries}
        if len(refs) != len(self.entries):
            raise ValueError("dependency closure refs must be unique")
        return self


class BuilderDigests(_BuilderApplicationModel):
    """Content identities reviewed across compile, save, and deployment."""

    document: Sha256Digest
    dependency: Sha256Digest
    resolved_semantic: Sha256Digest | None = None


class BuilderVerdict(_BuilderApplicationModel):
    """Backend-owned allow/block decision for one workflow operation."""

    operation: BuilderBlockedOperation
    allowed: bool
    blockers: tuple[BuilderIssue, ...] = ()

    @model_validator(mode="after")
    def _blockers_match_decision(self) -> BuilderVerdict:
        if self.allowed and self.blockers:
            raise ValueError("an allowed operation cannot carry blockers")
        if not self.allowed and not self.blockers:
            raise ValueError("a blocked operation must identify at least one blocker")
        if any(self.operation not in issue.blocks for issue in self.blockers):
            raise ValueError("verdict blockers must block the verdict operation")
        return self


class BuilderDeployVerdict(_BuilderApplicationModel):
    """Deployment decision bound to one exact saved session revision."""

    allowed: bool
    session_ref: SessionRef
    session_revision: OpaqueRevision
    digests: BuilderDigests
    blockers: tuple[BuilderIssue, ...] = ()

    @model_validator(mode="after")
    def _blockers_match_decision(self) -> BuilderDeployVerdict:
        if self.allowed and self.blockers:
            raise ValueError("an allowed deployment verdict cannot carry blockers")
        if not self.allowed and not self.blockers:
            raise ValueError("a blocked deployment must identify at least one blocker")
        if any("deploy" not in issue.blocks for issue in self.blockers):
            raise ValueError("deployment verdict blockers must block deployment")
        return self


class BuilderCompileRequest(_BuilderApplicationModel):
    """Request to compile one transient draft through backend authorities.

    ``pending_edits`` are applied to the draft's session before it compiles;
    the draft itself is not changed.
    """

    draft: BuilderDraftEnvelope
    target_ref: SessionRef
    pending_edits: BuilderPendingEdits = ()

    @field_validator("pending_edits", mode="before")
    @classmethod
    def _accept_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _target_is_user_owned(self) -> BuilderCompileRequest:
        if parse_catalog_reference(self.target_ref).namespace != "user":
            raise ValueError("Builder session targets must use the user: namespace")
        return self


class BuilderCompileResult(_BuilderApplicationModel):
    """Backend compilation result for a transient Builder draft.

    ``draft`` is the draft the request sent. The canonical facts, the preview
    and the issues describe that draft with its pending edits applied, and
    ``pending_edit_outcomes`` holds one outcome per pending edit, in request
    order.
    """

    draft: BuilderDraftEnvelope
    target_ref: SessionRef
    canonical_session_yaml: str | None = Field(default=None, min_length=1)
    canonical_session_json: ValidatedSessionJson | None = None
    dependency_closure: DependencyClosureInventory | None = None
    resolved_preview: BuilderWorld | None = None
    digests: BuilderDigests | None = None
    issues: tuple[BuilderIssue, ...] = ()
    pending_edit_outcomes: tuple[BuilderPendingEditOutcome, ...] = ()
    save_verdict: BuilderVerdict
    deploy_eligibility_after_save: BuilderVerdict

    @model_validator(mode="after")
    def _compile_facts_are_consistent(self) -> BuilderCompileResult:
        if self.save_verdict.operation != "save":
            raise ValueError("save_verdict must describe the save operation")
        if self.deploy_eligibility_after_save.operation != "deploy":
            raise ValueError("deploy_eligibility_after_save must describe deployment")
        if self.deploy_eligibility_after_save.allowed and not self.save_verdict.allowed:
            raise ValueError("an unsaveable draft cannot be eligible for deployment")
        for outcome in self.pending_edit_outcomes:
            if outcome.status == "stale" and not set(outcome.issues) <= set(self.issues):
                raise ValueError("a stale edit's issues must be among the compile issues")
            if outcome.status == "invalid" and self.save_verdict.allowed:
                raise ValueError("a draft holding an invalid edit cannot be saveable")
        canonical_facts = (
            self.canonical_session_yaml,
            self.canonical_session_json,
            self.dependency_closure,
            self.digests,
        )
        if self.save_verdict.allowed and any(fact is None for fact in canonical_facts):
            raise ValueError("a saveable compile result must include all canonical facts")
        if (
            self.dependency_closure is not None
            and self.digests is not None
            and self.dependency_closure.closure_digest != self.digests.dependency
        ):
            raise ValueError("compile dependency digest must match the closure inventory")
        return self


class BuilderSessionSaveRequest(_BuilderApplicationModel):
    """Transactional request to publish a complete draft as a catalog session."""

    draft: BuilderDraftEnvelope
    target_ref: SessionRef
    expected_session_revision: OpaqueRevision | None = None

    @model_validator(mode="after")
    def _target_is_user_owned(self) -> BuilderSessionSaveRequest:
        if parse_catalog_reference(self.target_ref).namespace != "user":
            raise ValueError("Builder session targets must use the user: namespace")
        return self


class BuilderSessionSaveResult(_BuilderApplicationModel):
    """Exact saved document identity plus its saved-revision deploy verdict."""

    session: BuilderCatalogDocument
    digests: BuilderDigests
    dependency_closure: DependencyClosureInventory
    deploy_verdict: BuilderDeployVerdict
    issues: tuple[BuilderIssue, ...] = ()

    @model_validator(mode="after")
    def _deploy_verdict_matches_session(self) -> BuilderSessionSaveResult:
        if self.session.family != "sessions":
            raise ValueError("a session save result must contain a session catalog document")
        if self.session.content_digest != self.digests.document:
            raise ValueError("saved session digest must match the save result")
        if self.dependency_closure.closure_digest != self.digests.dependency:
            raise ValueError("saved dependency digest must match the closure inventory")
        if self.deploy_verdict.session_ref != self.session.ref:
            raise ValueError("deployment verdict session_ref must match the saved session")
        if self.deploy_verdict.session_revision != self.session.revision:
            raise ValueError("deployment verdict revision must match the saved session")
        if self.deploy_verdict.digests != self.digests:
            raise ValueError("deployment verdict digests must match the save result")
        return self


class BuilderSessionDeployRequest(_BuilderApplicationModel):
    """Request deployment of one exact saved session revision and closure."""

    session_ref: SessionRef
    expected_session_revision: OpaqueRevision
    expected_document_digest: Sha256Digest
    expected_dependency_digest: Sha256Digest
    # Keep a history database for this session run.
    record_history: bool


class BuilderSessionDeployAccepted(_BuilderApplicationModel):
    """Opaque accepted operation bound to the exact requested catalog source."""

    operation_id: OpaqueRevision
    status: Literal["accepted"] = "accepted"
    source: BuilderSessionDeployRequest


class BuilderSessionDeployRefusal(_BuilderApplicationModel):
    """Stable, path-free refusal for one guarded saved-session deployment.

    ``code`` is the refusing component's own vocabulary, the same one the
    ``ApiRefusal`` envelope carries; this model adds the deployment's identity
    and precondition evidence.
    """

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    session_ref: SessionRef
    expected: str | None = None
    observed: str | None = None
    cause_type: str | None = None


class BuilderSessionSaveRefusal(_BuilderApplicationModel):
    """Stable transport evidence for a Builder session save that did not succeed."""

    code: BuilderSessionSaveRefusalCode
    message: str = Field(min_length=1)
    target_ref: SessionRef
    base_generation: OpaqueRevision | None = None
    repository_committed: bool = False
    issues: tuple[BuilderIssue, ...] = ()
    cause_type: str | None = None
    compile_result: BuilderCompileResult | None = None

    @model_validator(mode="after")
    def _commit_state_matches_code(self) -> BuilderSessionSaveRefusal:
        verification_failure = self.code == "builder_session_save.storage_verification_failed"
        if self.repository_committed != verification_failure:
            raise ValueError(
                "repository_committed must be true only for post-commit storage verification "
                "failures"
            )
        return self
