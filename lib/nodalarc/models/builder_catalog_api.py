"""Typed application contracts for Builder catalog authoring operations."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from nodalarc.catalog_refs import (
    BodyRef,
    CatalogFamily,
    CatalogNamespace,
    CatalogRef,
    NodeRef,
    SessionRef,
    TerminalRef,
    parse_catalog_reference,
)
from nodalarc.catalog_registry import catalog_family_spec
from nodalarc.model_validation import Identifier, TerminalMedium
from nodalarc.models.builder_api import (
    BuilderCatalogDocument,
    JsonDocument,
    OpaqueRevision,
    Sha256Digest,
    ValidatedConfigurationJson,
)
from nodalarc.models.builder_controls_api import BuilderControlMutation, BuilderControlTree
from nodalarc.models.builder_visual_api import (
    BuilderVisualGroundBoresight,
    BuilderVisualNode,
    BuilderVisualOrbitPropagator,
    BuilderVisualOrbitShape,
    BuilderVisualPhasingMode,
    BuilderVisualSchedulingPreset,
    BuilderVisualSpaceBoresight,
    BuilderVisualTopologyMode,
)
from nodalarc.models.catalog import ForwardingClass, MountRole
from nodalarc.models.segment_session import RoutingBoundaryAdapter, RoutingProtocol

CatalogImportOutcome = Literal["proposed", "committed", "unchanged", "blocked"]
CatalogImportCollisionReason = Literal[
    "shipped_missing",
    "shipped_content_mismatch",
    "user_content_mismatch",
]
CatalogAuthoringRefusalCode = Literal[
    "catalog_authoring.not_found",
    "catalog_authoring.read_only",
    "catalog_authoring.invalid_document",
    "catalog_authoring.invalid_patch",
    "catalog_authoring.invalid_graph",
    "catalog_authoring.conflict",
    "catalog_authoring.stale_revision",
    "catalog_authoring.invalid_page_token",
    "catalog_authoring.stale_page_token",
    "catalog_authoring.impact_mismatch",
    "catalog_authoring.dependents_exist",
    "catalog_authoring.import_limit",
    "catalog_authoring.import_incomplete",
    "catalog_authoring.import_collision",
    "catalog_authoring.stale_import_proposal",
    "catalog_authoring.persistence_failed",
]
CatalogComponentFamily = Literal[
    "bodies",
    "terminals",
    "payloads",
    "orbits",
    "nodes",
    "sites",
    "site-sets",
    "constellations",
    "space-node-sets",
]
CatalogDraftPatchOperation = Literal["add", "replace", "remove"]
CatalogDraftIssueStage = Literal["structural", "reference", "runtime_support"]
CatalogDraftBlockedOperation = Literal["save", "deploy"]


class _CatalogApplicationModel(BaseModel):
    """Closed immutable DTO base for storage-neutral catalog APIs."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class CatalogFamilyMetadata(_CatalogApplicationModel):
    """Backend-owned authoring capabilities for one public catalog family."""

    family: CatalogFamily
    wrapper: str | None = None
    direct_user_write: bool
    component_fork: bool
    session_draft_save: bool
    suggested_object_id: Identifier | None = None

    @model_validator(mode="after")
    def _family_operations_are_consistent(self) -> CatalogFamilyMetadata:
        is_session = self.family == "sessions"
        if self.session_draft_save != is_session:
            raise ValueError("session_draft_save must be enabled only for sessions")
        if self.direct_user_write == is_session:
            raise ValueError("direct_user_write must be enabled only for component families")
        if self.component_fork == is_session:
            raise ValueError("component_fork must be enabled only for component families")
        if (self.suggested_object_id is None) != is_session:
            raise ValueError("component families require one suggested user object id")
        return self


class BuilderCatalogCapabilities(_CatalogApplicationModel):
    """Factual backend capabilities required by the repaired Builder."""

    user_catalog_write: Literal[True] = True
    deploy_yaml_closure: Literal[True] = True


class BuilderVisualSchedulingPresetMetadata(_CatalogApplicationModel):
    """Typed presentation metadata for one backend-owned scheduling preset."""

    id: BuilderVisualSchedulingPreset
    label: str = Field(min_length=1, max_length=160)


class BuilderVisualMountRoleMetadata(_CatalogApplicationModel):
    """Presentation facts for one canonical terminal-mount role."""

    id: MountRole
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=160)


class BuilderVisualLinkMediumMetadata(_CatalogApplicationModel):
    """Presentation facts for one canonical link-terminal medium."""

    id: TerminalMedium
    label: str = Field(min_length=1, max_length=80)
    signal_seed: JsonDocument


class BuilderVisualForwardingClassMetadata(_CatalogApplicationModel):
    """Presentation facts for one canonical node forwarding class."""

    id: ForwardingClass
    label: str = Field(min_length=1, max_length=80)


class BuilderVisualRuntimeChoiceMetadata(_CatalogApplicationModel):
    """Runtime support evidence for a selectable canonical value."""

    runtime_supported: bool
    support_note: str | None = Field(default=None, min_length=1, max_length=320)


class BuilderVisualRoutingProtocolMetadata(BuilderVisualRuntimeChoiceMetadata):
    """Presentation and runtime facts for one routing protocol."""

    id: RoutingProtocol
    label: str = Field(min_length=1, max_length=80)
    timer_fields: bool


class BuilderVisualBoundaryAdapterMetadata(BuilderVisualRuntimeChoiceMetadata):
    """Presentation and runtime facts for one routing-boundary adapter."""

    id: RoutingBoundaryAdapter
    label: str = Field(min_length=1, max_length=80)


class BuilderVisualPhasingModeMetadata(_CatalogApplicationModel):
    """Presentation facts for one canonical constellation phasing mode."""

    id: BuilderVisualPhasingMode
    label: str = Field(min_length=1, max_length=120)


class BuilderVisualOrbitShapeMetadata(_CatalogApplicationModel):
    """Presentation facts for one visual orbit-shape form."""

    id: BuilderVisualOrbitShape
    label: str = Field(min_length=1, max_length=80)


class BuilderVisualOrbitPropagatorMetadata(BuilderVisualRuntimeChoiceMetadata):
    """Presentation and runtime facts for one visual orbit propagator."""

    id: BuilderVisualOrbitPropagator
    label: str = Field(min_length=1, max_length=120)


class BuilderVisualTopologyModeMetadata(BuilderVisualRuntimeChoiceMetadata):
    """Presentation and runtime facts for one visual link topology mode."""

    id: BuilderVisualTopologyMode
    label: str = Field(min_length=1, max_length=120)
    requires_n: bool


def _literal_members(alias: object) -> set[object]:
    return set(get_args(getattr(alias, "__value__", alias)))


class BuilderVisualAuthoringFacts(_CatalogApplicationModel):
    """Backend-owned choices and seeds used by the visual authoring surface."""

    default_phasing_mode: BuilderVisualPhasingMode
    single_plane_phasing_mode: BuilderVisualPhasingMode
    default_scheduling_preset: BuilderVisualSchedulingPreset
    default_mount_role: MountRole
    default_terminal_mount_count: int = Field(ge=1)
    default_body_ref: BodyRef
    default_node: BuilderVisualNode
    space_access_boresight: BuilderVisualSpaceBoresight
    ground_access_boresight: BuilderVisualGroundBoresight
    mount_roles: tuple[BuilderVisualMountRoleMetadata, ...]
    link_media: tuple[BuilderVisualLinkMediumMetadata, ...]
    forwarding_classes: tuple[BuilderVisualForwardingClassMetadata, ...]
    routing_protocols: tuple[BuilderVisualRoutingProtocolMetadata, ...]
    boundary_adapters: tuple[BuilderVisualBoundaryAdapterMetadata, ...]
    phasing_modes: tuple[BuilderVisualPhasingModeMetadata, ...]
    orbit_shapes: tuple[BuilderVisualOrbitShapeMetadata, ...]
    orbit_propagators: tuple[BuilderVisualOrbitPropagatorMetadata, ...]
    topology_modes: tuple[BuilderVisualTopologyModeMetadata, ...]

    @model_validator(mode="after")
    def _contains_every_visual_choice_once(self) -> BuilderVisualAuthoringFacts:
        choices = (
            ("mount roles", self.mount_roles, MountRole),
            ("link media", self.link_media, TerminalMedium),
            ("forwarding classes", self.forwarding_classes, ForwardingClass),
            ("routing protocols", self.routing_protocols, RoutingProtocol),
            ("boundary adapters", self.boundary_adapters, RoutingBoundaryAdapter),
            ("phasing modes", self.phasing_modes, BuilderVisualPhasingMode),
            ("orbit shapes", self.orbit_shapes, BuilderVisualOrbitShape),
            ("orbit propagators", self.orbit_propagators, BuilderVisualOrbitPropagator),
            ("topology modes", self.topology_modes, BuilderVisualTopologyMode),
        )
        for label, metadata, alias in choices:
            identifiers = tuple(item.id for item in metadata)
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(f"bootstrap {label} must be unique")
            if set(identifiers) != _literal_members(alias):
                raise ValueError(f"bootstrap must describe every visual {label} value")
        if self.default_phasing_mode not in {item.id for item in self.phasing_modes}:
            raise ValueError("default phasing mode must be advertised")
        if self.single_plane_phasing_mode not in {item.id for item in self.phasing_modes}:
            raise ValueError("single-plane phasing mode must be advertised")
        if self.default_mount_role not in {item.id for item in self.mount_roles}:
            raise ValueError("default mount role must be advertised")
        return self


class BuilderCatalogBootstrap(_CatalogApplicationModel):
    """Public documentation and catalog metadata needed to start Builder authoring."""

    contract_version: Literal[1] = 1
    authoring_context_binding: str = Field(min_length=1)
    public_grammar_href: str = Field(min_length=1)
    capabilities: BuilderCatalogCapabilities
    families: tuple[CatalogFamilyMetadata, ...]
    scheduling_presets: tuple[BuilderVisualSchedulingPresetMetadata, ...]
    authoring: BuilderVisualAuthoringFacts

    @model_validator(mode="after")
    def _contains_every_family_once(self) -> BuilderCatalogBootstrap:
        families = tuple(item.family for item in self.families)
        if len(set(families)) != len(families):
            raise ValueError("bootstrap catalog families must be unique")
        if set(families) != set(get_args(CatalogFamily)):
            raise ValueError("bootstrap must describe every public catalog family")
        presets = tuple(item.id for item in self.scheduling_presets)
        if len(set(presets)) != len(presets):
            raise ValueError("bootstrap scheduling presets must be unique")
        if set(presets) != set(get_args(BuilderVisualSchedulingPreset)):
            raise ValueError("bootstrap must describe every visual scheduling preset")
        if self.authoring.default_scheduling_preset not in set(presets):
            raise ValueError("default scheduling preset must be advertised")
        return self


class CatalogListRequest(_CatalogApplicationModel):
    """Bounded catalog-list request using an opaque server cursor."""

    family: CatalogFamily | None = None
    namespace: CatalogNamespace | None = None
    page_size: int = Field(default=50, ge=1, le=100)
    page_token: str | None = Field(default=None, min_length=1, max_length=2048)


class CatalogDocumentSummary(_CatalogApplicationModel):
    """Revisioned metadata for one catalog library row."""

    ref: CatalogRef
    namespace: CatalogNamespace
    family: CatalogFamily
    revision: OpaqueRevision
    size_bytes: int = Field(ge=0)
    display_name: str = Field(min_length=1, max_length=256)
    summary: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _reference_metadata_matches(self) -> CatalogDocumentSummary:
        parsed = parse_catalog_reference(self.ref)
        if parsed.namespace != self.namespace or parsed.family != self.family:
            raise ValueError("catalog summary metadata must match its reference")
        return self


class CatalogListPage(_CatalogApplicationModel):
    """One deterministic page pinned to an immutable catalog generation."""

    generation: OpaqueRevision
    items: tuple[CatalogDocumentSummary, ...]
    next_page_token: str | None = Field(default=None, min_length=1)


class CatalogGetRequest(_CatalogApplicationModel):
    """Request one catalog document by namespace-qualified identity."""

    ref: CatalogRef


class CatalogDocumentWriteRequest(_CatalogApplicationModel):
    """Create or compare-and-swap replace one complete user component."""

    ref: CatalogRef
    document: JsonDocument
    expected_revision: OpaqueRevision | None = None

    @model_validator(mode="after")
    def _targets_a_component_family(self) -> CatalogDocumentWriteRequest:
        family = parse_catalog_reference(self.ref).family
        if family not in get_args(CatalogFamily) or family == "sessions":
            raise ValueError("direct catalog writes must target a registered component family")
        return self


class CatalogForkRequest(_CatalogApplicationModel):
    """Fork one complete component document to a new user-owned identity."""

    source_ref: CatalogRef
    target_ref: CatalogRef
    expected_source_revision: OpaqueRevision | None = None

    @model_validator(mode="after")
    def _fork_identity_is_valid(self) -> CatalogForkRequest:
        source = parse_catalog_reference(self.source_ref)
        target = parse_catalog_reference(self.target_ref)
        if source.family not in get_args(CatalogFamily) or source.family == "sessions":
            raise ValueError("sessions are saved through Builder drafts and cannot be forked")
        if target.namespace != "user":
            raise ValueError("fork targets must use the user: namespace")
        if target.family != source.family:
            raise ValueError("fork source and target must use the same catalog family")
        return self


class CatalogDependentsRequest(_CatalogApplicationModel):
    """Request current overwrite and delete impact for one exact catalog ref."""

    ref: CatalogRef


class CatalogDependent(_CatalogApplicationModel):
    """One typed reverse dependency and its shortest distance from the target."""

    ref: CatalogRef
    family: CatalogFamily
    revision: OpaqueRevision
    minimum_depth: int = Field(ge=1)

    @model_validator(mode="after")
    def _family_matches_reference(self) -> CatalogDependent:
        if parse_catalog_reference(self.ref).family != self.family:
            raise ValueError("dependent family must match its reference")
        return self


class CatalogDependencyImpact(_CatalogApplicationModel):
    """Current typed reverse graph used for overwrite review and delete fencing."""

    target_ref: CatalogRef
    target_revision: OpaqueRevision
    direct_dependents: tuple[CatalogDependent, ...]
    transitive_dependents: tuple[CatalogDependent, ...]
    overwrite_affects_dependents: bool
    delete_allowed: bool
    acknowledgement: Sha256Digest

    @model_validator(mode="after")
    def _impact_is_self_consistent(self) -> CatalogDependencyImpact:
        direct_refs = {item.ref for item in self.direct_dependents}
        transitive_by_ref = {item.ref: item for item in self.transitive_dependents}
        if len(transitive_by_ref) != len(self.transitive_dependents):
            raise ValueError("transitive dependent refs must be unique")
        if direct_refs != {
            item.ref for item in self.transitive_dependents if item.minimum_depth == 1
        }:
            raise ValueError("direct dependents must equal depth-one transitive dependents")
        if self.overwrite_affects_dependents != bool(self.transitive_dependents):
            raise ValueError("overwrite impact must reflect transitive dependents")
        if self.delete_allowed != (not self.transitive_dependents):
            raise ValueError("delete is allowed only when no dependents exist")
        return self


class CatalogMutationResult(_CatalogApplicationModel):
    """Canonical saved component plus its current downstream impact."""

    document: BuilderCatalogDocument
    impact: CatalogDependencyImpact

    @model_validator(mode="after")
    def _identities_match(self) -> CatalogMutationResult:
        if self.document.ref != self.impact.target_ref:
            raise ValueError("mutation document and impact must describe the same ref")
        if self.document.revision != self.impact.target_revision:
            raise ValueError("mutation document and impact revisions must match")
        return self


class CatalogForkResult(_CatalogApplicationModel):
    """Fork provenance and the complete newly persisted component."""

    source_ref: CatalogRef
    result: CatalogMutationResult


class CatalogDeleteRequest(_CatalogApplicationModel):
    """Fenced deletion request bound to exact bytes and reviewed graph impact."""

    ref: CatalogRef
    expected_revision: OpaqueRevision
    impact_acknowledgement: Sha256Digest


class CatalogDeleteResult(_CatalogApplicationModel):
    """Evidence that one exact user document was removed atomically."""

    deleted_ref: CatalogRef
    deleted_revision: OpaqueRevision
    impact_acknowledgement: Sha256Digest
    generation: OpaqueRevision


class CatalogSessionYamlExportRequest(_CatalogApplicationModel):
    """Request ordinary YAML files for one saved session."""

    session_ref: SessionRef
    expected_session_revision: OpaqueRevision | None = None


class CatalogYamlFile(_CatalogApplicationModel):
    """One ordinary YAML file and its backend-derived logical path."""

    logical_path: str = Field(min_length=1)
    yaml_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _path_is_contained_yaml(self) -> CatalogYamlFile:
        path = PurePosixPath(self.logical_path)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("logical YAML path must be a contained relative path")
        if path.suffix not in {".yaml", ".yml"}:
            raise ValueError("logical YAML path must identify a YAML file")
        return self


class CatalogSessionYamlExport(_CatalogApplicationModel):
    """Ordinary root and catalog YAML files for browser-side file writing."""

    session_ref: SessionRef
    session_revision: OpaqueRevision
    files: tuple[CatalogYamlFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _paths_are_unique_and_rooted(self) -> CatalogSessionYamlExport:
        paths = tuple(file.logical_path for file in self.files)
        expected_root = (
            f"catalog/{self.session_ref.namespace}/{self.session_ref.relative_path.as_posix()}"
        )
        if paths[0] != expected_root:
            raise ValueError("session YAML export must begin with its catalog path")
        if len(set(paths)) != len(paths):
            raise ValueError("session YAML export paths must be unique")
        if any(not path.startswith("catalog/") for path in paths):
            raise ValueError("YAML export paths must begin with catalog/")
        return self


class CatalogYamlImportFile(_CatalogApplicationModel):
    """One YAML text with an optional graph-verified placement hint."""

    yaml_text: str = Field(min_length=1)
    logical_path_hint: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _hint_is_contained_yaml(self) -> CatalogYamlImportFile:
        if self.logical_path_hint is None:
            return self
        path = PurePosixPath(self.logical_path_hint)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("logical YAML path hint must be a contained relative path")
        if path.suffix not in {".yaml", ".yml"}:
            raise ValueError("logical YAML path hint must identify a YAML file")
        return self


class CatalogSessionYamlImportRequest(_CatalogApplicationModel):
    """Ordinary YAML files proposed for identity-derived catalog import."""

    yaml_files: tuple[CatalogYamlImportFile, ...] = Field(min_length=1)
    commit: bool = False
    proposal_token: str | None = Field(default=None, min_length=1)

    @field_validator("yaml_files", mode="before")
    @classmethod
    def _accept_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _commit_requires_reviewed_proposal(self) -> CatalogSessionYamlImportRequest:
        if self.commit != (self.proposal_token is not None):
            raise ValueError(
                "commit requires a proposal token and proposal requests cannot carry one"
            )
        return self


class CatalogYamlImportWrite(_CatalogApplicationModel):
    """One canonical user document proposed for transactional creation."""

    ref: CatalogRef
    family: CatalogFamily
    logical_path: str = Field(min_length=1)
    canonical_yaml: str = Field(min_length=1)
    canonicalization_changed: bool

    @model_validator(mode="after")
    def _is_user_owned_and_typed(self) -> CatalogYamlImportWrite:
        parsed = parse_catalog_reference(self.ref)
        if parsed.namespace != "user" or parsed.family != self.family:
            raise ValueError("import writes must be typed user: catalog documents")
        expected_path = f"catalog/{self.ref.namespace}/{self.ref.relative_path.as_posix()}"
        if self.logical_path != expected_path:
            raise ValueError("import write path must be derived from its reference")
        return self


class CatalogYamlImportCollision(_CatalogApplicationModel):
    """Exact identity collision that import will never rename or deep-match."""

    ref: CatalogRef
    reason: CatalogImportCollisionReason
    existing_revision: OpaqueRevision | None = None


class CatalogSessionYamlImportResult(_CatalogApplicationModel):
    """Proposed, committed, unchanged, or blocked YAML-file import result."""

    root_ref: SessionRef
    outcome: CatalogImportOutcome
    generation: OpaqueRevision
    proposal_token: str | None = Field(default=None, min_length=1)
    proposed_writes: tuple[CatalogYamlImportWrite, ...]
    identical_refs: tuple[CatalogRef, ...]
    collisions: tuple[CatalogYamlImportCollision, ...]

    @model_validator(mode="after")
    def _outcome_matches_contents(self) -> CatalogSessionYamlImportResult:
        if (self.outcome == "blocked") != bool(self.collisions):
            raise ValueError("blocked import outcome must match collision presence")
        if self.outcome == "unchanged" and self.proposed_writes:
            raise ValueError("unchanged imports cannot contain proposed writes")
        if self.outcome in {"proposed", "committed"} and not self.proposed_writes:
            raise ValueError("proposed or committed imports require a write set")
        if (self.outcome == "proposed") != (self.proposal_token is not None):
            raise ValueError("only proposed imports carry a proposal token")
        return self


class CatalogOperationRefusal(_CatalogApplicationModel):
    """Stable typed evidence for an authoring operation refused by the backend."""

    code: CatalogAuthoringRefusalCode
    message: str = Field(min_length=1)
    ref: str | None = None
    expected_revision: OpaqueRevision | None = None
    current_revision: OpaqueRevision | None = None
    impact: CatalogDependencyImpact | None = None
    collisions: tuple[CatalogYamlImportCollision, ...] = ()
    cause_type: str | None = None


class CatalogDraftIssue(_CatalogApplicationModel):
    """One backend-produced component-draft finding at an exact JSON pointer."""

    code: str = Field(min_length=1)
    stage: CatalogDraftIssueStage
    message: str = Field(min_length=1)
    pointer: str = Field(min_length=1, max_length=2048)
    source_line: int | None = Field(default=None, ge=1)
    source_column: int | None = Field(default=None, ge=1)
    blocks: tuple[CatalogDraftBlockedOperation, ...]

    @field_validator("blocks", mode="before")
    @classmethod
    def _accept_json_blocks(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _blocking_contract_is_consistent(self) -> CatalogDraftIssue:
        if not self.blocks or len(set(self.blocks)) != len(self.blocks):
            raise ValueError("draft issues must block one or more unique operations")
        if self.stage == "structural" and set(self.blocks) != {"save", "deploy"}:
            raise ValueError("structural draft issues must block save and deploy")
        if self.stage == "reference" and set(self.blocks) != {"save", "deploy"}:
            raise ValueError("reference draft issues must block save and deploy")
        if self.stage == "runtime_support" and self.blocks != ("deploy",):
            raise ValueError("runtime-support draft issues must block deploy only")
        return self


class CatalogComponentDraftEnvelope(_CatalogApplicationModel):
    """Versioned full-document component draft owned by backend authoring APIs."""

    contract_version: Literal[1] = 1
    draft_revision: int = Field(ge=0)
    family: CatalogComponentFamily
    target_ref: CatalogRef
    source_ref: CatalogRef | None = None
    expected_source_revision: OpaqueRevision | None = None
    expected_target_revision: OpaqueRevision | None = None
    document: JsonDocument
    projected_yaml: str = Field(min_length=1)
    control_tree: BuilderControlTree
    issues: tuple[CatalogDraftIssue, ...]

    @field_validator("issues", mode="before")
    @classmethod
    def _accept_json_issues(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _identity_and_family_are_fixed(self) -> CatalogComponentDraftEnvelope:
        target = parse_catalog_reference(self.target_ref)
        if target.namespace != "user":
            raise ValueError("component draft targets must use the user: namespace")
        if target.family != self.family:
            raise ValueError("component draft family must match target_ref")
        if self.source_ref is not None:
            source = parse_catalog_reference(self.source_ref)
            if source.family != self.family:
                raise ValueError("component draft source and target families must match")
            if self.expected_source_revision is None:
                raise ValueError("component draft sources require an expected revision")
            if self.source_ref == self.target_ref:
                if source.namespace != "user":
                    raise ValueError("in-place component drafts must edit a user: source")
                if self.expected_target_revision != self.expected_source_revision:
                    raise ValueError(
                        "in-place component drafts require matching source and target revisions"
                    )
            elif self.expected_target_revision is not None:
                raise ValueError("component fork targets must not already exist")
        elif self.expected_source_revision is not None:
            raise ValueError("expected_source_revision requires source_ref")
        elif self.expected_target_revision is not None:
            raise ValueError("new component drafts cannot replace an existing target")

        wrapper = catalog_family_spec(self.family).wrapper
        if wrapper is None:
            raise ValueError("component draft family must use a catalog object wrapper")
        if set(self.document) != {wrapper} or not isinstance(self.document[wrapper], dict):
            raise ValueError("component draft document must retain its family wrapper")
        if self.document[wrapper].get("id") != self.target_ref.relative_path.stem:
            raise ValueError("component draft object id must match target_ref")
        if self.control_tree.projection_revision != self.draft_revision:
            raise ValueError("component controls must match the draft revision")
        return self


class CatalogDraftNewRequest(_CatalogApplicationModel):
    """Create one new, intentionally incomplete user-owned component draft."""

    family: CatalogComponentFamily
    object_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=128)


class CatalogDraftOpenRequest(_CatalogApplicationModel):
    """Open one complete component and optionally select a user-owned fork target."""

    source_ref: CatalogRef
    target_ref: CatalogRef | None = None

    @model_validator(mode="after")
    def _component_refs_are_compatible(self) -> CatalogDraftOpenRequest:
        source = parse_catalog_reference(self.source_ref)
        if source.family not in get_args(CatalogComponentFamily):
            raise ValueError("catalog component drafts do not accept sessions")
        if self.target_ref is not None:
            target = parse_catalog_reference(self.target_ref)
            if target.namespace != "user":
                raise ValueError("component draft targets must use the user: namespace")
            if target.family != source.family:
                raise ValueError("component draft source and target families must match")
        return self


class CatalogDraftPatchCommand(_CatalogApplicationModel):
    """One bounded JSON-pointer mutation applied to a full backend draft document."""

    operation: CatalogDraftPatchOperation
    pointer: str = Field(min_length=2, max_length=2048)
    value: JsonValue | None = None

    @model_validator(mode="after")
    def _value_presence_matches_operation(self) -> CatalogDraftPatchCommand:
        supplied = "value" in self.model_fields_set
        if self.operation == "remove" and supplied:
            raise ValueError("remove commands must not include value")
        if self.operation != "remove" and not supplied:
            raise ValueError("add and replace commands require value")
        return self


class CatalogDraftPatchRequest(_CatalogApplicationModel):
    """Apply an ordered, revision-fenced mutation set to one component draft."""

    draft: CatalogComponentDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    commands: tuple[CatalogDraftPatchCommand, ...] = Field(min_length=1, max_length=64)

    @field_validator("commands", mode="before")
    @classmethod
    def _accept_json_commands(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class CatalogDraftControlMutationRequest(_CatalogApplicationModel):
    """Apply canonical graphical commands under one component draft revision."""

    draft: CatalogComponentDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    commands: tuple[BuilderControlMutation, ...] = Field(min_length=1, max_length=64)

    @field_validator("commands", mode="before")
    @classmethod
    def _accept_json_commands(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class CatalogDraftAddSiteNodeRequest(_CatalogApplicationModel):
    """Add one explicitly identified node using backend-derived persisted fields."""

    draft: CatalogComponentDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    node_id: Identifier
    node_ref: NodeRef

    @model_validator(mode="after")
    def _site_draft_only(self) -> CatalogDraftAddSiteNodeRequest:
        if self.draft.family != "sites":
            raise ValueError("site-node commands require a sites component draft")
        return self


class CatalogDraftAddNodeTerminalMountRequest(_CatalogApplicationModel):
    """Add one node terminal mount with backend-generated persisted fields."""

    draft: CatalogComponentDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    terminal_ref: TerminalRef
    role: MountRole

    @model_validator(mode="after")
    def _node_draft_only(self) -> CatalogDraftAddNodeTerminalMountRequest:
        if self.draft.family != "nodes":
            raise ValueError("node terminal-mount commands require a nodes component draft")
        return self


class CatalogDraftAddNodeEthernetPortRequest(_CatalogApplicationModel):
    """Add one node Ethernet port with a backend-generated unique identifier."""

    draft: CatalogComponentDraftEnvelope
    expected_draft_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def _node_draft_only(self) -> CatalogDraftAddNodeEthernetPortRequest:
        if self.draft.family != "nodes":
            raise ValueError("node Ethernet-port commands require a nodes component draft")
        return self


class CatalogDraftApplyYamlRequest(_CatalogApplicationModel):
    """Apply one exact YAML buffer under backend grammar authority."""

    draft: CatalogComponentDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    yaml_text: str = Field(max_length=1_048_576)


class CatalogDraftApplyYamlResult(_CatalogApplicationModel):
    """Applied projection or typed refusal for one exact YAML buffer."""

    draft: CatalogComponentDraftEnvelope
    yaml_text: str
    applied: bool
    canonicalization_required: bool
    issues: tuple[CatalogDraftIssue, ...]

    @field_validator("issues", mode="before")
    @classmethod
    def _accept_json_issues(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _result_matches_application_state(self) -> CatalogDraftApplyYamlResult:
        if self.applied and self.issues != self.draft.issues:
            raise ValueError("applied YAML findings must match the returned draft")
        if not self.applied and not self.issues:
            raise ValueError("refused YAML application requires typed findings")
        if self.canonicalization_required != (
            self.applied and self.yaml_text != self.draft.projected_yaml
        ):
            raise ValueError("canonicalization verdict must match the applied YAML projection")
        return self


class CatalogDraftCompileRequest(_CatalogApplicationModel):
    """Validate one exact draft without mutating catalog persistence."""

    draft: CatalogComponentDraftEnvelope
    expected_draft_revision: int = Field(ge=0)


class CatalogDraftCompileResult(_CatalogApplicationModel):
    """Canonical component bytes plus structural, reference, and support findings."""

    draft: CatalogComponentDraftEnvelope
    save_allowed: bool
    runtime_supported: bool
    canonical_yaml: str | None = Field(default=None, min_length=1)
    canonical_json: ValidatedConfigurationJson | None = None
    content_digest: Sha256Digest | None = None
    issues: tuple[CatalogDraftIssue, ...]

    @field_validator("issues", mode="before")
    @classmethod
    def _accept_json_compile_issues(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _verdict_matches_evidence(self) -> CatalogDraftCompileResult:
        canonical = (
            self.canonical_yaml is not None,
            self.canonical_json is not None,
            self.content_digest is not None,
        )
        if len(set(canonical)) != 1:
            raise ValueError("canonical component outputs must be present or absent together")
        if all(canonical) == any(issue.stage == "structural" for issue in self.issues):
            raise ValueError("canonical output presence must match structural findings")
        if self.save_allowed == any("save" in issue.blocks for issue in self.issues):
            raise ValueError("save verdict must match save blockers")
        if self.runtime_supported == any("deploy" in issue.blocks for issue in self.issues):
            raise ValueError("runtime verdict must match deploy blockers")
        if self.draft.issues != self.issues:
            raise ValueError("compile findings must match the returned draft")
        if self.canonical_json is not None:
            try:
                catalog_family_spec(self.draft.family).validate_document(self.canonical_json)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "canonical document must match the component draft family"
                ) from error
        return self


class CatalogDraftSaveRequest(_CatalogApplicationModel):
    """Persist one structurally valid component draft through scoped CAS storage."""

    draft: CatalogComponentDraftEnvelope
    expected_draft_revision: int = Field(ge=0)


class CatalogDraftSaveResult(_CatalogApplicationModel):
    """Saved canonical component, updated draft revision fence, and graph impact."""

    draft: CatalogComponentDraftEnvelope
    result: CatalogMutationResult
    compile_result: CatalogDraftCompileResult

    @model_validator(mode="after")
    def _saved_identity_matches(self) -> CatalogDraftSaveResult:
        if self.draft.target_ref != self.result.document.ref:
            raise ValueError("saved draft and canonical document refs must match")
        if self.draft.expected_target_revision != self.result.document.revision:
            raise ValueError("saved draft must carry the committed target revision")
        if self.compile_result.canonical_yaml != self.result.document.canonical_yaml:
            raise ValueError("save and compile canonical YAML must match")
        return self
