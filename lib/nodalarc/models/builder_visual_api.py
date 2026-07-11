"""Typed backend-owned visual Builder draft contracts.

These application DTOs describe authoring gestures, not persisted NodalArc
grammar.  The backend is solely responsible for assembling them into the
generic :class:`BuilderDraftEnvelope` consumed by the existing compiler and
save services.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nodalarc.catalog_refs import (
    BodyRef,
    CatalogRef,
    NodeRef,
    SessionRef,
    SiteRef,
    SpaceSourceRef,
    TerminalRef,
    parse_catalog_reference,
)
from nodalarc.model_validation import TerminalMedium
from nodalarc.models.builder_api import (
    BuilderCompileResult,
    BuilderDraftEnvelope,
    BuilderIssue,
    BuilderProposedCatalogDocument,
    BuilderSessionSaveRequest,
    JsonDocument,
    OpaqueRevision,
)
from nodalarc.models.catalog import ForwardingClass, PhasingMode
from nodalarc.models.link_rules import MountRole
from nodalarc.models.segment_session import RoutingBoundaryAdapter, RoutingProtocol

BuilderVisualDraftMode = Literal["structured", "opaque_yaml"]
BuilderVisualSchedulingPreset = Literal["leo-fast-handover", "geo-longest-pass"]
type BuilderVisualPhasingMode = PhasingMode
BuilderVisualOrbitShape = Literal["circular", "elliptical"]
BuilderVisualOrbitPropagator = Literal["two_body", "j2_mean_elements"]
BuilderVisualTopologyMode = Literal["visible_candidates", "nearest_n"]
BuilderVisualDraftCommandOperation = Literal[
    "add_generated_space",
    "add_ground",
    "add_routing_domain",
    "add_boundary",
    "connect_segments",
    "rederive_link",
    "set_scheduling_preset",
]
BuilderVisualDraftAffectedKind = Literal[
    "space",
    "ground",
    "routing_domain",
    "boundary",
    "link",
    "ground_member",
]


class _BuilderVisualModel(BaseModel):
    """Closed immutable base for visual-authoring application state."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _accept_json_arrays_for_tuple_fields(cls, value: object) -> object:
        """Accept the JSON array representation emitted for tuple fields."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field_name, field in cls.model_fields.items():
            if get_origin(field.annotation) is tuple and isinstance(
                normalized.get(field_name), list
            ):
                normalized[field_name] = tuple(normalized[field_name])
        return normalized


class BuilderVisualCatalogRevision(_BuilderVisualModel):
    """Expected revision for one component the structured draft may replace."""

    ref: CatalogRef
    expected_revision: OpaqueRevision

    @model_validator(mode="after")
    def _component_only(self) -> BuilderVisualCatalogRevision:
        if self.ref.family == "sessions":
            raise ValueError("component revision expectations cannot target sessions")
        return self


class BuilderVisualSpaceBoresight(_BuilderVisualModel):
    """Spacecraft access-terminal pointing authored into a node mount."""

    mode: Literal["nadir"]


class BuilderVisualGroundBoresight(_BuilderVisualModel):
    """Ground access-terminal pointing authored into a site installation."""

    mode: Literal["local_vertical"]


class BuilderVisualTerminalMount(_BuilderVisualModel):
    """Editable terminal mount on a visual node draft."""

    mount_id: str = ""
    role: MountRole | None = None
    terminal_ref: TerminalRef | None = None
    count: int | None = None
    boresight: BuilderVisualSpaceBoresight | None = None

    @model_validator(mode="after")
    def _boresight_matches_role(self) -> BuilderVisualTerminalMount:
        if self.role == "access" and self.boresight is None:
            raise ValueError(
                "spacecraft access terminal mounts require an explicit nadir boresight"
            )
        if self.role not in {None, "access"} and self.boresight is not None:
            raise ValueError("non-access terminal mounts must not declare a spacecraft boresight")
        return self


class BuilderVisualNode(_BuilderVisualModel):
    """Editable node object nested in a generated space component."""

    id: str = ""
    display_name: str = ""
    forwarding: ForwardingClass | None = None
    ethernet: tuple[str, ...] = ()
    terminals: tuple[BuilderVisualTerminalMount, ...] = ()


class BuilderVisualOrbit(_BuilderVisualModel):
    """Editable orbital geometry for one generated constellation."""

    central_body: BodyRef | None = None
    shape_kind: BuilderVisualOrbitShape | None = None
    altitude_km: float | None = None
    perigee_altitude_km: float | None = None
    apogee_altitude_km: float | None = None
    inclination_deg: float | None = None
    raan_deg: float | None = None
    argument_of_perigee_deg: float | None = None
    mean_anomaly_deg: float | None = None
    propagator: BuilderVisualOrbitPropagator | None = None


class BuilderVisualSpaceDraft(_BuilderVisualModel):
    """Editable generated space segment that becomes referenced catalog objects."""

    segment_id: str = ""
    display_name: str = ""
    node_ref: NodeRef | None = None
    node_draft: BuilderVisualNode | None = None
    orbit: BuilderVisualOrbit = Field(default_factory=BuilderVisualOrbit)
    planes: int | None = None
    raan_spacing_deg: float | None = None
    slots_per_plane: int | None = None
    phasing_mode: BuilderVisualPhasingMode
    phase_offset_deg: float

    @model_validator(mode="after")
    def _phasing_matches_population(self) -> BuilderVisualSpaceDraft:
        if self.planes is None:
            return self
        if self.phasing_mode == "evenly_spaced_mean_anomaly":
            if self.planes != 1:
                raise ValueError(
                    "evenly_spaced_mean_anomaly phasing requires exactly one orbital plane"
                )
            if self.phase_offset_deg != 0:
                raise ValueError(
                    "single-plane evenly_spaced_mean_anomaly phasing requires a zero "
                    "phase_offset_deg"
                )
        elif self.planes < 2:
            raise ValueError(f"{self.phasing_mode} phasing requires at least two planes")
        return self


class BuilderVisualSpaceReference(_BuilderVisualModel):
    """One library space source placed by reference."""

    segment_id: str = ""
    source_ref: SpaceSourceRef | None = None
    label: str = ""


class BuilderVisualSiteNode(_BuilderVisualModel):
    """One installed node in an editable site object."""

    node_id: str = ""
    model_ref: NodeRef | None = None
    installed: dict[str, int] = Field(default_factory=dict)
    boresights: dict[str, BuilderVisualGroundBoresight]
    lo0_ipv4: str = ""
    terr0_ipv4: str = ""


class BuilderVisualSite(_BuilderVisualModel):
    """Editable complete site object."""

    site_id: str = ""
    display_name: str = ""
    body: BodyRef | None = None
    lat_deg: float | None = None
    lon_deg: float | None = None
    alt_m: float | None = None
    lan_ipv4: str = ""
    tags: tuple[str, ...] = ()
    nodes: tuple[BuilderVisualSiteNode, ...] = ()


class BuilderVisualGroundMember(_BuilderVisualModel):
    """Referenced or authored member of an editable ground site set."""

    member_id: str = ""
    kind: Literal["ref", "draft"]
    ref: SiteRef | None = None
    site_id: str = ""
    label: str = ""
    summary: str | None = None
    site: BuilderVisualSite | None = None
    scheduling_override: JsonDocument | None = None


class BuilderVisualGroundStamp(_BuilderVisualModel):
    """Backend-issued minting facts retained as visual state, never persisted."""

    node_ref: NodeRef | None = None
    installed: dict[str, int] = Field(default_factory=dict)
    boresights: dict[str, BuilderVisualGroundBoresight]
    body: BodyRef | None = None
    lan_base: str = ""
    loopback_base: str = ""


class BuilderVisualGroundDraft(_BuilderVisualModel):
    """Editable ground segment assembled into site and site-set refs."""

    segment_id: str = ""
    display_name: str = ""
    members: tuple[BuilderVisualGroundMember, ...] = ()
    stamp: BuilderVisualGroundStamp
    scheduling: JsonDocument = Field(default_factory=dict)
    originated_ipv4: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


class BuilderVisualGroundReference(_BuilderVisualModel):
    """One library site set placed by reference with session scheduling."""

    segment_id: str = ""
    site_set_ref: CatalogRef | None = None
    label: str = ""
    scheduling: JsonDocument = Field(default_factory=dict)

    @model_validator(mode="after")
    def _site_set_only(self) -> BuilderVisualGroundReference:
        if self.site_set_ref is not None and self.site_set_ref.family != "site-sets":
            raise ValueError("site_set_ref must reference the site-sets catalog family")
        return self


class BuilderVisualLinkEndpoint(_BuilderVisualModel):
    """Editable endpoint selector for one visual link rule."""

    segment_id: str = ""
    tag: str | None = None
    role: MountRole | None = None
    medium: TerminalMedium | None = None
    min_elevation_deg: float | None = None


class BuilderVisualLinkRule(_BuilderVisualModel):
    """Editable physical link rule; every authored rule is assembled."""

    rule_id: str = ""
    label: str = ""
    enabled: bool = True
    a: BuilderVisualLinkEndpoint = Field(default_factory=BuilderVisualLinkEndpoint)
    b: BuilderVisualLinkEndpoint = Field(default_factory=BuilderVisualLinkEndpoint)
    topology_mode: BuilderVisualTopologyMode | None = None
    topology_n: int | None = None
    max_range_km: float | None = None


class BuilderVisualRoutingDomain(_BuilderVisualModel):
    """Editable routing domain; empty membership remains visible to validation."""

    domain_id: str = ""
    label: str = ""
    protocol: RoutingProtocol | None = None
    member_segment_ids: tuple[str, ...] = ()
    hello_interval_s: float | None = None
    hold_interval_s: float | None = None


class BuilderVisualRoutingBoundary(_BuilderVisualModel):
    """Editable routing boundary assembled without omission."""

    boundary_id: str = ""
    over_rule_id: str = ""
    adapter: RoutingBoundaryAdapter | None = None
    from_domain_id: str = ""
    to_domain_id: str = ""
    export_node_loopbacks: bool = True


class BuilderVisualWorkspace(_BuilderVisualModel):
    """Closed visual workspace whose persisted grammar is assembled by VS-API."""

    session_name: str = ""
    display_name: str | None = None
    description: str | None = None
    space: tuple[BuilderVisualSpaceDraft, ...] = ()
    space_refs: tuple[BuilderVisualSpaceReference, ...] = ()
    ground: tuple[BuilderVisualGroundDraft, ...] = ()
    ground_refs: tuple[BuilderVisualGroundReference, ...] = ()
    links: tuple[BuilderVisualLinkRule, ...] = ()
    routing_domains: tuple[BuilderVisualRoutingDomain, ...] = ()
    boundaries: tuple[BuilderVisualRoutingBoundary, ...] = ()
    max_pairs_per_rule: int | None = 2_000
    max_pairs_per_tick: int | None = 10_000
    start_time: str = ""
    step_seconds: float | None = 1.0
    compression: float | None = 1.0


class BuilderVisualDraftEnvelope(_BuilderVisualModel):
    """Versioned visual draft in structured or lossless opaque-YAML mode."""

    contract_version: Literal[1] = 1
    draft_revision: int = Field(ge=0)
    mode: BuilderVisualDraftMode
    target_ref: SessionRef
    source_ref: SessionRef | None = None
    expected_session_revision: OpaqueRevision | None = None
    expected_catalog_revisions: tuple[BuilderVisualCatalogRevision, ...] = ()
    catalog_documents: tuple[BuilderProposedCatalogDocument, ...] = ()
    workspace: BuilderVisualWorkspace | None = None
    session_yaml: str | None = None

    @model_validator(mode="after")
    def _mode_payload_and_authority_are_consistent(self) -> BuilderVisualDraftEnvelope:
        if parse_catalog_reference(self.target_ref).namespace != "user":
            raise ValueError("visual draft targets must use the user: namespace")
        if self.mode == "structured":
            if self.workspace is None or self.session_yaml is not None:
                raise ValueError("structured drafts require workspace and forbid session_yaml")
        elif self.workspace is not None or self.session_yaml is None:
            raise ValueError("opaque_yaml drafts require session_yaml and forbid workspace")
        if len({item.ref for item in self.expected_catalog_revisions}) != len(
            self.expected_catalog_revisions
        ):
            raise ValueError("expected catalog revisions must target unique refs")
        if len({item.ref for item in self.catalog_documents}) != len(self.catalog_documents):
            raise ValueError("visual draft catalog documents must target unique refs")
        return self


class BuilderVisualDraftCreateRequest(_BuilderVisualModel):
    """Request a backend-created blank structured visual draft."""

    session_name: str = "untitled-session"
    display_name: str | None = None
    description: str | None = None


class BuilderVisualDraftOpenRequest(_BuilderVisualModel):
    """Open any stored session in lossless opaque-YAML authoring mode."""

    source_ref: SessionRef
    target_ref: SessionRef | None = None

    @model_validator(mode="after")
    def _optional_target_is_user_owned(self) -> BuilderVisualDraftOpenRequest:
        if self.target_ref is not None and self.target_ref.namespace != "user":
            raise ValueError("opened sessions must target a user: session ref")
        return self


class BuilderVisualDraftCompileRequest(_BuilderVisualModel):
    """Compile a complete visual draft through backend assembly and grammar authorities."""

    draft: BuilderVisualDraftEnvelope


class BuilderVisualAddGeneratedSpaceCommand(_BuilderVisualModel):
    """Add one backend-seeded generated constellation draft."""

    operation: Literal["add_generated_space"]
    node_ref: NodeRef | None = None
    phasing_mode: BuilderVisualPhasingMode


class BuilderVisualAddGroundCommand(_BuilderVisualModel):
    """Add one backend-seeded authored ground-segment draft."""

    operation: Literal["add_ground"]
    node_ref: NodeRef | None = None
    installed: dict[str, int] = Field(default_factory=dict)
    boresights: dict[str, BuilderVisualGroundBoresight] = Field(default_factory=dict)
    body_ref: BodyRef | None = None


class BuilderVisualAddRoutingDomainCommand(_BuilderVisualModel):
    """Add one backend-seeded routing domain over uncovered segments."""

    operation: Literal["add_routing_domain"]


class BuilderVisualAddBoundaryCommand(_BuilderVisualModel):
    """Add one backend-seeded routing boundary."""

    operation: Literal["add_boundary"]


class BuilderVisualConnectSegmentsCommand(_BuilderVisualModel):
    """Create a link rule whose initial physics comes from both endpoints."""

    operation: Literal["connect_segments"]
    from_segment_id: str = Field(min_length=1, max_length=160)
    to_segment_id: str = Field(min_length=1, max_length=160)


class BuilderVisualRederiveLinkCommand(_BuilderVisualModel):
    """Repoint one link endpoint and explicitly rederive its physical seed."""

    operation: Literal["rederive_link"]
    rule_id: str = Field(min_length=1, max_length=160)
    side: Literal["a", "b"]
    segment_id: str = Field(min_length=1, max_length=160)


class BuilderVisualSetSchedulingPresetCommand(_BuilderVisualModel):
    """Apply one complete backend-owned scheduling block or inherit at a site."""

    operation: Literal["set_scheduling_preset"]
    segment_id: str = Field(min_length=1, max_length=160)
    preset: BuilderVisualSchedulingPreset | None
    member_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def _inheritance_targets_only_members(self) -> BuilderVisualSetSchedulingPresetCommand:
        if self.preset is None and self.member_id is None:
            raise ValueError("only a ground member can inherit segment scheduling")
        return self


BuilderVisualDraftCommand = Annotated[
    BuilderVisualAddGeneratedSpaceCommand
    | BuilderVisualAddGroundCommand
    | BuilderVisualAddRoutingDomainCommand
    | BuilderVisualAddBoundaryCommand
    | BuilderVisualConnectSegmentsCommand
    | BuilderVisualRederiveLinkCommand
    | BuilderVisualSetSchedulingPresetCommand,
    Field(discriminator="operation"),
]


class BuilderVisualDraftCommandRequest(_BuilderVisualModel):
    """Apply one typed command to an exact visual-draft revision."""

    draft: BuilderVisualDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    command: BuilderVisualDraftCommand


class BuilderVisualDraftCommandResult(_BuilderVisualModel):
    """One applied command and the next revision of the complete draft."""

    contract_version: Literal[1] = 1
    operation: BuilderVisualDraftCommandOperation
    base_draft_revision: int = Field(ge=0)
    draft: BuilderVisualDraftEnvelope
    affected_kind: BuilderVisualDraftAffectedKind
    affected_id: str = Field(min_length=1, max_length=160)
    scheduling_preset: BuilderVisualSchedulingPreset | None = None
    notice: str | None = None

    @model_validator(mode="after")
    def _revision_advanced_exactly_once(self) -> BuilderVisualDraftCommandResult:
        if self.draft.draft_revision != self.base_draft_revision + 1:
            raise ValueError("an applied visual command must advance draft_revision exactly once")
        return self


class BuilderVisualCustomizeChainRequest(_BuilderVisualModel):
    """Fork the minimal catalog ancestor path for one placed nested component."""

    draft: BuilderVisualDraftEnvelope
    segment_id: str = Field(min_length=1, max_length=160)
    leaf_ref: CatalogRef
    target_leaf_ref: CatalogRef | None = None

    @model_validator(mode="after")
    def _leaf_target_is_valid(self) -> BuilderVisualCustomizeChainRequest:
        if self.leaf_ref.family == "sessions":
            raise ValueError("customize-chain leaves must be catalog components")
        if self.target_leaf_ref is not None:
            if self.target_leaf_ref.namespace != "user":
                raise ValueError("customize-chain targets must use the user: namespace")
            if self.target_leaf_ref.family != self.leaf_ref.family:
                raise ValueError("customize-chain leaf refs must remain in the same family")
        return self


class BuilderVisualCustomizeChainEntry(_BuilderVisualModel):
    """One source-to-user fork in a minimal nested customization path."""

    source_ref: CatalogRef
    target_ref: CatalogRef

    @model_validator(mode="after")
    def _families_match(self) -> BuilderVisualCustomizeChainEntry:
        if self.target_ref.namespace != "user":
            raise ValueError("customize-chain entries must target user: refs")
        if self.source_ref.family != self.target_ref.family:
            raise ValueError("customize-chain entries must preserve catalog family")
        return self


class BuilderVisualCustomizeChainResult(_BuilderVisualModel):
    """Updated draft or typed refusal evidence for one customize-chain command."""

    applied: bool
    draft: BuilderVisualDraftEnvelope
    root_source_ref: CatalogRef | None = None
    root_target_ref: CatalogRef | None = None
    forked_chain: tuple[BuilderVisualCustomizeChainEntry, ...] = ()
    issues: tuple[BuilderIssue, ...] = ()

    @model_validator(mode="after")
    def _result_is_consistent(self) -> BuilderVisualCustomizeChainResult:
        if self.applied:
            if self.issues or not self.forked_chain:
                raise ValueError("applied customize-chain results require a chain and no issues")
            if self.root_source_ref is None or self.root_target_ref is None:
                raise ValueError("applied customize-chain results require root identities")
            if self.forked_chain[0].source_ref != self.root_source_ref:
                raise ValueError("the first customize-chain entry must be the placed root")
            if self.forked_chain[0].target_ref != self.root_target_ref:
                raise ValueError("the first customize-chain target must be the placed root target")
        elif not self.issues or self.forked_chain:
            raise ValueError("refused customize-chain results require issues and no forked chain")
        return self


class BuilderVisualDraftAssemblyResult(_BuilderVisualModel):
    """Backend assembly, typed issues, save request, and authoritative compile facts."""

    visual_draft: BuilderVisualDraftEnvelope
    assembled_draft: BuilderDraftEnvelope
    save_request: BuilderSessionSaveRequest
    compile_result: BuilderCompileResult
    assembly_issues: tuple[BuilderIssue, ...] = ()

    @model_validator(mode="after")
    def _identities_match(self) -> BuilderVisualDraftAssemblyResult:
        if self.save_request.target_ref != self.visual_draft.target_ref:
            raise ValueError("save request target must match the visual draft target")
        if self.save_request.draft != self.assembled_draft:
            raise ValueError("save request draft must equal the assembled draft")
        if self.compile_result.draft != self.assembled_draft:
            raise ValueError("compile result draft must equal the assembled draft")
        if self.compile_result.target_ref != self.visual_draft.target_ref:
            raise ValueError("compile result target must match the visual draft target")
        return self
