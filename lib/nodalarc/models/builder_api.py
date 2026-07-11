"""Typed application contracts for Builder authoring APIs.

These models describe browser/VS-API workflow state. They are not NodalArc
configuration grammar and none of them may be persisted or deployed as a
session document. Incomplete authoring values remain generic JSON, while
successful canonical responses reuse and validate against the canonical
configuration models owned by the grammar authority.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

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
    NodeRef,
    SessionRef,
    SiteRef,
    SiteSetRef,
    SpaceSourceRef,
    parse_catalog_reference,
)
from nodalarc.catalog_registry import catalog_family_spec
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.configuration import ConfigurationDocument
from nodalarc.models.link_rules import MountRole
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
BuilderIssueStage = Literal[
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
BuilderIssueSeverity = Literal["info", "warning", "error"]
BuilderBlockedOperation = Literal["save", "deploy"]
BuilderSessionSaveRefusalCode = Literal[
    "builder_session_save.save_blocked",
    "builder_session_save.stale_write",
    "builder_session_save.graph_invalid",
    "builder_session_save.persistence_failed",
    "builder_session_save.storage_verification_failed",
]
BuilderSessionDeployRefusalCode = Literal[
    "builder_session_deploy.invalid_precondition",
    "builder_session_deploy.source_not_found",
    "builder_session_deploy.stale_source",
    "builder_session_deploy.not_ready",
    "builder_session_deploy.conflict",
    "builder_session_deploy.repository_unavailable",
    "builder_session_deploy.unsupported",
    "builder_session_deploy.preparation_failed",
]
WizardOrbitPropagator = Literal["two_body", "j2_mean_elements", "sgp4_tle"]
WizardConstellationSourceKind = Literal[
    "constellation",
    "space_node_set",
    "custom_geometry",
]
type WizardTerminalRole = MountRole
WizardExtension = Literal["sr", "te", "mpls"]
WizardAreaStrategy = Literal["flat", "stripe", "per_plane"]
WizardRoutingProtocol = Literal["isis", "ospf"]
WizardWalkerPattern = Literal["walker_delta", "walker_star"]
WizardRoutingBooleanField = Literal["bfd"]
WizardRoutingTimerField = Literal[
    "bfd_detect_multiplier",
    "bfd_rx_interval",
    "bfd_tx_interval",
    "isis_hello_interval",
    "isis_hello_multiplier",
    "spf_init_delay",
    "spf_short_delay",
    "spf_long_delay",
    "spf_holddown",
    "ospf_hello_interval",
    "ospf_dead_interval",
    "ospf_spf_delay",
    "ospf_spf_initial_hold",
    "ospf_spf_max_hold",
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
    """One complete draft catalog document proposed for a ``user:`` ref."""

    ref: CatalogRef
    document: JsonDocument
    expected_revision: OpaqueRevision | None = None

    @model_validator(mode="after")
    def _target_is_a_user_component(self) -> BuilderProposedCatalogDocument:
        parsed = parse_catalog_reference(self.ref)
        if parsed.namespace != "user":
            raise ValueError("proposed catalog documents must target the user: namespace")
        if parsed.family not in get_args(CatalogFamily) or parsed.family == "sessions":
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
    """Versioned transient editor state accepted by compile/save APIs only."""

    contract_version: Literal[1] = 1
    draft_revision: int = Field(ge=0)
    state: BuilderDraftState


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
    """Request to compile one transient draft through backend authorities."""

    draft: BuilderDraftEnvelope
    target_ref: SessionRef

    @model_validator(mode="after")
    def _target_is_user_owned(self) -> BuilderCompileRequest:
        if parse_catalog_reference(self.target_ref).namespace != "user":
            raise ValueError("Builder session targets must use the user: namespace")
        return self


class WizardConstellationGeometry(_BuilderApplicationModel):
    """Typed orbital geometry authored by the Wizard, not persisted grammar."""

    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1024)
    altitude_km: float = Field(ge=160, le=40_000)
    inclination_deg: float = Field(ge=0, le=180)
    pattern: WizardWalkerPattern
    planes: int = Field(ge=2, le=72)
    slots_per_plane: int = Field(ge=1, le=60)
    raan_spacing_deg: float = Field(ge=0, le=360)
    phase_offset_deg: float = Field(ge=0, le=360)


class WizardConstellationCapability(_BuilderApplicationModel):
    """Backend-owned runtime capability for one Wizard space source."""

    source_kind: WizardConstellationSourceKind
    runtime_supported_propagators: tuple[WizardOrbitPropagator, ...]
    default_propagator: WizardOrbitPropagator | None
    unavailable_reason: str | None

    @model_validator(mode="after")
    def _default_and_availability_match_support(self) -> WizardConstellationCapability:
        supported = self.runtime_supported_propagators
        if len(set(supported)) != len(supported):
            raise ValueError("runtime-supported propagators must be unique")
        if self.default_propagator is not None and self.default_propagator not in supported:
            raise ValueError("default propagator must be runtime-supported for the source")
        if supported and self.unavailable_reason is not None:
            raise ValueError("an available source cannot carry an unavailable reason")
        if not supported and self.unavailable_reason is None:
            raise ValueError("an unavailable source must explain why it is unavailable")
        return self


class WizardOrbitModelMetadata(_BuilderApplicationModel):
    """Presentation metadata for one backend-supported Wizard orbit choice."""

    id: WizardOrbitPropagator
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1024)


class WizardWalkerPatternMetadata(_BuilderApplicationModel):
    """Presentation facts for one backend-supported custom Walker pattern."""

    id: WizardWalkerPattern
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1024)


class WizardConstellationPreset(_BuilderApplicationModel):
    """One catalog-backed constellation choice exposed to the Wizard."""

    name: str = Field(min_length=1)
    description: str
    satellite_count: int = Field(ge=1)
    constellation: SpaceSourceRef
    ground_stations: SiteSetRef
    default_node: str | None
    capability: WizardConstellationCapability

    @model_validator(mode="after")
    def _source_kind_matches_reference(self) -> WizardConstellationPreset:
        expected = (
            "constellation" if self.constellation.family == "constellations" else "space_node_set"
        )
        if self.capability.source_kind != expected:
            raise ValueError("preset capability source kind must match its catalog reference")
        return self


class WizardConstellationPresetResponse(_BuilderApplicationModel):
    """Closed Wizard constellation catalog plus custom-geometry capability."""

    presets: tuple[WizardConstellationPreset, ...]
    custom_geometry: WizardConstellationCapability
    custom_geometry_seed: WizardConstellationGeometry
    custom_geometry_default_node: NodeRef
    custom_geometry_patterns: tuple[WizardWalkerPatternMetadata, ...]
    orbit_models: tuple[WizardOrbitModelMetadata, ...]

    @model_validator(mode="after")
    def _response_members_are_consistent(self) -> WizardConstellationPresetResponse:
        if self.custom_geometry.source_kind != "custom_geometry":
            raise ValueError("custom_geometry capability must identify custom geometry")
        names = [preset.name for preset in self.presets]
        if len(set(names)) != len(names):
            raise ValueError("constellation preset names must be unique")
        refs = [preset.constellation for preset in self.presets]
        if len(set(refs)) != len(refs):
            raise ValueError("constellation preset references must be unique")
        orbit_model_ids = tuple(model.id for model in self.orbit_models)
        if len(set(orbit_model_ids)) != len(orbit_model_ids):
            raise ValueError("Wizard orbit-model metadata ids must be unique")
        if set(orbit_model_ids) != set(get_args(WizardOrbitPropagator)):
            raise ValueError("Wizard orbit-model metadata must describe every orbit choice")
        pattern_ids = tuple(pattern.id for pattern in self.custom_geometry_patterns)
        if len(set(pattern_ids)) != len(pattern_ids):
            raise ValueError("Wizard Walker-pattern metadata ids must be unique")
        if set(pattern_ids) != set(get_args(WizardWalkerPattern)):
            raise ValueError("Wizard Walker-pattern metadata must describe every pattern choice")
        if self.custom_geometry_seed.pattern not in pattern_ids:
            raise ValueError("custom geometry seed must use an available Walker pattern")
        return self


class WizardSatelliteTerminalSummary(_BuilderApplicationModel):
    """One installed terminal mount summarized for a satellite preset card."""

    id: str = Field(min_length=1)
    role: WizardTerminalRole
    count: int = Field(ge=1)


class WizardSatelliteTypePreset(_BuilderApplicationModel):
    """One catalog node primitive that can fly a Wizard constellation."""

    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    notes: str
    file: NodeRef
    terminals: tuple[WizardSatelliteTerminalSummary, ...]


class WizardSatelliteTypePresetResponse(_BuilderApplicationModel):
    """Closed catalog-node response for the Wizard satellite picker."""

    presets: tuple[WizardSatelliteTypePreset, ...]


class WizardGroundStationSetPreset(_BuilderApplicationModel):
    """One catalog site-set choice for the Wizard ground picker."""

    name: str = Field(min_length=1)
    description: str
    stations: tuple[str, ...]
    file: SiteSetRef


class WizardGroundStationSetPresetResponse(_BuilderApplicationModel):
    """Closed catalog site-set response for the Wizard."""

    presets: tuple[WizardGroundStationSetPreset, ...]


class WizardAvailableStation(_BuilderApplicationModel):
    """One located catalog site available to a custom Wizard site set."""

    name: str = Field(min_length=1)
    lat_deg: float = Field(ge=-90, le=90)
    lon_deg: float = Field(ge=-180, le=180)
    file: SiteRef


class WizardAvailableStationResponse(_BuilderApplicationModel):
    """Closed catalog-site response for custom Wizard ground selection."""

    stations: tuple[WizardAvailableStation, ...]


class WizardExtensionMetadata(_BuilderApplicationModel):
    """Presentation facts for one backend-supported Wizard extension."""

    id: WizardExtension
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1024)


class WizardRoutingTimerFieldMetadata(_BuilderApplicationModel):
    """Presentation and validation facts for one numeric protocol timer control."""

    id: WizardRoutingTimerField
    label: str = Field(min_length=1, max_length=160)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=1024)
    guidance: str = Field(min_length=1, max_length=1024)
    minimum: int = Field(ge=0)


class WizardBfdMetadata(_BuilderApplicationModel):
    """Backend-owned presentation and field facts for Wizard BFD controls."""

    heading: str = Field(min_length=1, max_length=160)
    enabled_field: WizardRoutingBooleanField
    enable_label: str = Field(min_length=1, max_length=160)
    enable_description: str = Field(min_length=1, max_length=1024)
    timer_fields: tuple[WizardRoutingTimerFieldMetadata, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _fields_are_complete_and_unique(self) -> WizardBfdMetadata:
        timer_ids = [field.id for field in self.timer_fields]
        if len(set(timer_ids)) != len(timer_ids):
            raise ValueError("Wizard BFD timer fields must be unique")
        expected = {"bfd_detect_multiplier", "bfd_rx_interval", "bfd_tx_interval"}
        if set(timer_ids) != expected:
            raise ValueError("Wizard BFD metadata must describe every BFD timer field")
        return self


class WizardProtocolMetadata(_BuilderApplicationModel):
    """One selectable routing protocol and its backend-owned Wizard behavior."""

    id: WizardRoutingProtocol
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1024)
    extensions: tuple[WizardExtension, ...]
    extension_constraints: dict[WizardExtension, tuple[WizardExtension, ...]]
    timer_label: str = Field(min_length=1, max_length=160)
    timer_fields: tuple[WizardRoutingTimerFieldMetadata, ...] = Field(min_length=1)
    non_flat_area_warning: str | None = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def _facts_are_consistent(self) -> WizardProtocolMetadata:
        if len(set(self.extensions)) != len(self.extensions):
            raise ValueError("Wizard protocol extensions must be unique")
        available = set(self.extensions)
        for extension, dependencies in self.extension_constraints.items():
            if extension not in available or any(item not in available for item in dependencies):
                raise ValueError("Wizard extension constraints must reference available extensions")
            if len(set(dependencies)) != len(dependencies):
                raise ValueError("Wizard extension dependencies must be unique")
        timer_ids = [field.id for field in self.timer_fields]
        if len(set(timer_ids)) != len(timer_ids):
            raise ValueError("Wizard protocol timer fields must be unique")
        if any(field.startswith("bfd_") for field in timer_ids):
            raise ValueError("Wizard protocol timer fields must not redefine BFD controls")
        return self


class WizardRoutingTimerIntent(_BuilderApplicationModel):
    """Raw Wizard routing controls mapped to session grammar only by the backend."""

    bfd: bool
    bfd_detect_multiplier: int = Field(ge=1)
    bfd_rx_interval: int = Field(ge=1)
    bfd_tx_interval: int = Field(ge=1)
    isis_hello_interval: int = Field(ge=1)
    isis_hello_multiplier: int = Field(ge=1)
    spf_init_delay: int = Field(ge=0)
    spf_short_delay: int = Field(ge=0)
    spf_long_delay: int = Field(ge=0)
    spf_holddown: int = Field(ge=0)
    spf_time_to_learn: int = Field(ge=0)
    ospf_hello_interval: int = Field(ge=1)
    ospf_dead_interval: int = Field(ge=1)
    ospf_spf_delay: int = Field(ge=0)
    ospf_spf_initial_hold: int = Field(ge=0)
    ospf_spf_max_hold: int = Field(ge=0)


class WizardExtensionRulesResponse(_BuilderApplicationModel):
    """Closed backend-owned Wizard protocol and area-strategy rules."""

    protocols: tuple[WizardProtocolMetadata, ...] = Field(min_length=1)
    extensions: tuple[WizardExtensionMetadata, ...] = Field(min_length=1)
    area_strategies: tuple[WizardAreaStrategy, ...]
    default_area_strategy: WizardAreaStrategy
    bfd: WizardBfdMetadata
    routing_timer_defaults: WizardRoutingTimerIntent

    @model_validator(mode="after")
    def _defaults_are_available(self) -> WizardExtensionRulesResponse:
        if len(set(self.area_strategies)) != len(self.area_strategies):
            raise ValueError("Wizard area strategies must be unique")
        if self.default_area_strategy not in self.area_strategies:
            raise ValueError("default Wizard area strategy must be available")
        protocol_ids = [protocol.id for protocol in self.protocols]
        if len(set(protocol_ids)) != len(protocol_ids):
            raise ValueError("Wizard routing protocols must be unique")
        if set(protocol_ids) != set(get_args(WizardRoutingProtocol)):
            raise ValueError("Wizard protocol metadata must describe every protocol choice")
        extension_ids = [extension.id for extension in self.extensions]
        if len(set(extension_ids)) != len(extension_ids):
            raise ValueError("Wizard extension metadata ids must be unique")
        if set(extension_ids) != set(get_args(WizardExtension)):
            raise ValueError("Wizard extension metadata must describe every extension choice")
        return self


class WizardPhysicalIntent(_BuilderApplicationModel):
    """Wizard-selected physical sources used for preview and authoring."""

    constellation_ref: SpaceSourceRef | None = None
    custom_constellation: WizardConstellationGeometry | None = None
    satellite_node_ref: NodeRef | None = None
    ground_site_set_ref: SiteSetRef | None = None
    custom_site_refs: tuple[SiteRef, ...] = ()
    orbit_propagator: WizardOrbitPropagator

    @field_validator("custom_site_refs", mode="before")
    @classmethod
    def _accept_json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _selection_sources_are_explicit(self) -> WizardPhysicalIntent:
        if (self.constellation_ref is None) == (self.custom_constellation is None):
            raise ValueError(
                "Wizard intent requires exactly one constellation_ref or custom_constellation"
            )
        if (self.ground_site_set_ref is None) == (not self.custom_site_refs):
            raise ValueError(
                "Wizard intent requires exactly one ground_site_set_ref or custom_site_refs"
            )
        if len(set(self.custom_site_refs)) != len(self.custom_site_refs):
            raise ValueError("Wizard custom site references must be unique")
        return self


class WizardSessionIntent(WizardPhysicalIntent):
    """One Wizard selection set compiled into an ordinary Builder draft."""

    protocol: WizardRoutingProtocol
    extensions: tuple[WizardExtension, ...] = ()
    area_strategy: WizardAreaStrategy = "flat"
    routing_timers: WizardRoutingTimerIntent

    @field_validator("extensions", mode="before")
    @classmethod
    def _accept_extension_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _extensions_are_unique(self) -> WizardSessionIntent:
        if len(set(self.extensions)) != len(self.extensions):
            raise ValueError("Wizard extensions must be unique")
        return self


class WizardCompileRequest(_BuilderApplicationModel):
    """Request backend construction and compilation of one Wizard intent."""

    draft_revision: int = Field(ge=0)
    intent: WizardSessionIntent


class WizardCoverageRequest(_BuilderApplicationModel):
    """Request one physical coverage preview from backend-selected catalog facts."""

    intent: WizardPhysicalIntent


class WizardCompileRefusal(_BuilderApplicationModel):
    """Stable refusal when Wizard intent cannot become a Builder draft."""

    code: Literal[
        "wizard_compile.invalid_selection",
        "wizard_compile.reference_error",
        "wizard_compile.repository_unavailable",
    ]
    message: str = Field(min_length=1)
    cause_type: str | None = None


class BuilderCompileResult(_BuilderApplicationModel):
    """Backend compilation result for a transient Builder draft."""

    draft: BuilderDraftEnvelope
    target_ref: SessionRef
    canonical_session_yaml: str | None = Field(default=None, min_length=1)
    canonical_session_json: ValidatedSessionJson | None = None
    dependency_closure: DependencyClosureInventory | None = None
    resolved_preview: BuilderWorld | None = None
    digests: BuilderDigests | None = None
    issues: tuple[BuilderIssue, ...] = ()
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


class BuilderSessionDeployAccepted(_BuilderApplicationModel):
    """Opaque accepted operation bound to the exact requested catalog source."""

    operation_id: OpaqueRevision
    status: Literal["accepted"] = "accepted"
    source: BuilderSessionDeployRequest


class BuilderSessionDeployRefusal(_BuilderApplicationModel):
    """Stable, path-free refusal for one guarded saved-session deployment."""

    code: BuilderSessionDeployRefusalCode
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
