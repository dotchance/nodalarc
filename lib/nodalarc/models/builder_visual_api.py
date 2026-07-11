"""Typed backend-owned visual Builder draft contracts.

These application DTOs describe authoring gestures, not persisted NodalArc
grammar.  The backend is solely responsible for assembling them into the
generic :class:`BuilderDraftEnvelope` consumed by the existing compiler and
save services.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nodalarc.catalog_refs import (
    BodyRef,
    CatalogRef,
    NodeRef,
    SessionRef,
    SiteRef,
    SiteSetRef,
    SpaceSourceRef,
    TerminalRef,
    parse_catalog_reference,
)
from nodalarc.model_validation import Identifier, TerminalMedium
from nodalarc.models.builder_api import (
    BuilderCompileResult,
    BuilderDraftEnvelope,
    BuilderIssue,
    BuilderProposedCatalogDocument,
    BuilderSessionSaveRequest,
    JsonDocument,
    OpaqueRevision,
)
from nodalarc.models.builder_controls_api import BuilderControlMutation, BuilderControlTree
from nodalarc.models.catalog import ForwardingClass, PhasingMode
from nodalarc.models.link_rules import MountRole
from nodalarc.models.segment_session import RoutingBoundaryAdapter, RoutingProtocol

BuilderVisualDraftProjectionStatus = Literal[
    "applied",
    "incomplete_authoring",
    "no_valid_projection",
    "pending_authoring",
]
BuilderVisualSchedulingPreset = Literal["leo-fast-handover", "geo-longest-pass"]
type BuilderVisualPhasingMode = PhasingMode
BuilderVisualOrbitShape = Literal["circular", "elliptical"]
BuilderVisualOrbitPropagator = Literal["two_body", "j2_mean_elements"]
BuilderVisualTopologyMode = Literal["visible_candidates", "nearest_n"]
BuilderVisualDraftCommandOperation = Literal[
    "place_space_reference",
    "place_ground_reference",
    "add_generated_space",
    "set_space_population",
    "author_inline_space_node",
    "add_or_increment_node_terminal",
    "set_node_terminal_role",
    "add_node_ethernet_port",
    "add_ground",
    "add_ground_site_reference",
    "set_ground_stamp_node_model",
    "set_ground_site_node_model",
    "add_ground_site_node",
    "mint_ground_members",
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
    phase_offset_deg: float | None = None

    @model_validator(mode="after")
    def _phasing_matches_population(self) -> BuilderVisualSpaceDraft:
        if self.planes is None:
            return self
        if self.phasing_mode == "evenly_spaced_mean_anomaly":
            if self.planes != 1:
                raise ValueError(
                    "evenly_spaced_mean_anomaly phasing requires exactly one orbital plane"
                )
            if self.phase_offset_deg not in {None, 0}:
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
    site_set_ref: SiteSetRef | None = None
    label: str = ""
    scheduling: JsonDocument = Field(default_factory=dict)


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
    projection_revision: int | None = Field(default=None, ge=0)
    control_tree: BuilderControlTree | None = None

    @model_validator(mode="after")
    def _control_tree_matches_projection_revision(self) -> BuilderVisualWorkspace:
        if self.projection_revision is None:
            if self.control_tree is not None:
                raise ValueError("unapplied workspaces must not claim a control tree")
        elif self.control_tree is None:
            raise ValueError("applied workspaces require a backend-derived control tree")
        elif self.control_tree.projection_revision != self.projection_revision:
            raise ValueError("control tree revision must match the workspace projection")
        return self


class BuilderVisualDraftEnvelope(_BuilderVisualModel):
    """Versioned stateless YAML buffer plus explicit authoring and applied facts."""

    contract_version: Literal[2] = 2
    draft_revision: int = Field(ge=0)
    projection_status: BuilderVisualDraftProjectionStatus
    target_ref: SessionRef
    source_ref: SessionRef | None = None
    expected_session_revision: OpaqueRevision | None = None
    catalog_documents: tuple[BuilderProposedCatalogDocument, ...] = ()
    session_name_is_placeholder: bool
    reserved_authoring_ids: tuple[str, ...]
    session_yaml: str
    authoring_workspace: BuilderVisualWorkspace | None = None
    applied_workspace: BuilderVisualWorkspace | None = None
    applied_revision: int | None = Field(default=None, ge=0)
    applied_session: JsonDocument | None = None

    @field_validator("applied_session")
    @classmethod
    def _canonical_applied_session(cls, value: JsonDocument | None) -> JsonDocument | None:
        if value is None:
            return None
        from nodalarc.models.segment_session import SegmentSessionConfig

        return SegmentSessionConfig.model_validate(value).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )

    @model_validator(mode="after")
    def _mode_payload_and_authority_are_consistent(self) -> BuilderVisualDraftEnvelope:
        if parse_catalog_reference(self.target_ref).namespace != "user":
            raise ValueError("visual draft targets must use the user: namespace")
        if self.source_ref == self.target_ref:
            if self.source_ref is None:
                raise ValueError("visual draft source identity is inconsistent")
            if self.expected_session_revision is None:
                raise ValueError("in-place visual drafts require an expected session revision")
        elif self.expected_session_revision is not None:
            raise ValueError(
                "new and copied visual drafts cannot replace an existing session target"
            )
        applied_facts = (
            self.applied_workspace,
            self.applied_revision,
            self.applied_session,
        )
        if any(item is None for item in applied_facts) and any(
            item is not None for item in applied_facts
        ):
            raise ValueError("applied revision, canonical session, and workspace are one fact")
        if self.projection_status == "applied":
            if any(item is None for item in applied_facts):
                raise ValueError("applied drafts require canonical session and workspace facts")
            assert self.applied_workspace is not None and self.applied_revision is not None
            if self.applied_revision != self.draft_revision:
                raise ValueError(
                    "fully applied drafts require matching current and applied revisions"
                )
            if self.applied_workspace.projection_revision != self.applied_revision:
                raise ValueError("applied workspace must be stamped with applied_revision")
            if self.authoring_workspace != self.applied_workspace:
                raise ValueError("authoring workspace must equal the last applied workspace")
        elif self.projection_status == "pending_authoring":
            if any(item is None for item in applied_facts) or self.authoring_workspace is None:
                raise ValueError("pending authoring requires both last-applied and authoring facts")
            assert self.applied_workspace is not None and self.applied_revision is not None
            if self.applied_revision > self.draft_revision:
                raise ValueError("applied revision cannot be newer than current draft revision")
            if self.applied_workspace.projection_revision != self.applied_revision:
                raise ValueError("last-applied workspace must be stamped with applied_revision")
            if self.authoring_workspace.projection_revision is not None:
                raise ValueError("pending authoring workspace must not claim an applied revision")
        elif self.projection_status == "incomplete_authoring":
            if any(item is not None for item in applied_facts):
                raise ValueError("incomplete authoring drafts must not claim applied facts")
            if self.authoring_workspace is None:
                raise ValueError("incomplete authoring drafts require an authoring workspace")
            if self.authoring_workspace.projection_revision is not None:
                raise ValueError("incomplete authoring workspaces must not claim a revision")
        elif any(
            item is not None
            for item in (
                self.authoring_workspace,
                self.applied_workspace,
                self.applied_revision,
                self.applied_session,
            )
        ):
            raise ValueError("no-valid-projection drafts must not carry graphical facts")
        if len({item.ref for item in self.catalog_documents}) != len(self.catalog_documents):
            raise ValueError("visual draft catalog documents must target unique refs")
        if len(set(self.reserved_authoring_ids)) != len(self.reserved_authoring_ids):
            raise ValueError("reserved visual authoring identities must be unique")
        if any(not value or len(value) > 160 for value in self.reserved_authoring_ids):
            raise ValueError("reserved visual authoring identities must be non-empty and bounded")
        return self


class BuilderVisualDraftCreateRequest(_BuilderVisualModel):
    """Request a backend-created blank structured visual draft."""

    session_name: Identifier | None = None
    display_name: str | None = None
    description: str | None = None


class BuilderVisualDraftOpenRequest(_BuilderVisualModel):
    """Open any stored session as synchronized YAML and graphical projections."""

    source_ref: SessionRef
    target_ref: SessionRef | None = None

    @model_validator(mode="after")
    def _optional_target_is_user_owned(self) -> BuilderVisualDraftOpenRequest:
        if self.target_ref is not None and self.target_ref.namespace != "user":
            raise ValueError("opened sessions must target a user: session ref")
        return self


class BuilderVisualDraftApplyYamlRequest(_BuilderVisualModel):
    """Apply one exact session YAML buffer to a fenced visual draft revision."""

    draft: BuilderVisualDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    buffer_generation: int = Field(ge=0)
    yaml_text: str = Field(max_length=1_048_576)


class BuilderVisualDraftApplyYamlResult(_BuilderVisualModel):
    """Applied projection or typed refusal for one exact session YAML buffer."""

    draft: BuilderVisualDraftEnvelope
    buffer_generation: int = Field(ge=0)
    yaml_text: str
    applied: bool
    canonicalization_required: bool
    issues: tuple[BuilderIssue, ...] = ()

    @model_validator(mode="after")
    def _result_matches_buffer_state(self) -> BuilderVisualDraftApplyYamlResult:
        if self.draft.session_yaml != self.yaml_text:
            raise ValueError("returned draft must preserve the exact applied YAML buffer")
        if not self.applied and not self.issues:
            raise ValueError("refused YAML application requires typed findings")
        if not self.applied and self.canonicalization_required:
            raise ValueError("refused YAML cannot require canonicalization")
        return self


class BuilderVisualDraftApplyWorkspaceRequest(_BuilderVisualModel):
    """Apply one complete graphical workspace to a fenced draft revision."""

    draft: BuilderVisualDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    workspace: BuilderVisualWorkspace


class BuilderVisualDraftRetargetRequest(_BuilderVisualModel):
    """Prepare a fenced draft for saving under one exact user session ref."""

    draft: BuilderVisualDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    target_ref: SessionRef

    @model_validator(mode="after")
    def _target_is_user_owned(self) -> BuilderVisualDraftRetargetRequest:
        if self.target_ref.namespace != "user":
            raise ValueError("retargeted sessions must use the user: namespace")
        if self.target_ref == self.draft.target_ref:
            raise ValueError("retargeted sessions must choose a different session ref")
        return self


class BuilderVisualControlMutationRequest(_BuilderVisualModel):
    """Apply an atomic batch through revision-scoped backend control identities."""

    draft: BuilderVisualDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    commands: tuple[BuilderControlMutation, ...] = Field(min_length=1)


class BuilderVisualDraftCompileRequest(_BuilderVisualModel):
    """Compile a complete visual draft through backend assembly and grammar authorities."""

    draft: BuilderVisualDraftEnvelope


class BuilderVisualAddGeneratedSpaceCommand(_BuilderVisualModel):
    """Add one backend-seeded generated constellation draft."""

    operation: Literal["add_generated_space"]
    node_ref: NodeRef | None = None
    phasing_mode: BuilderVisualPhasingMode


class BuilderVisualPlaceSpaceReferenceCommand(_BuilderVisualModel):
    """Place one existing constellation or space-node-set by catalog reference."""

    operation: Literal["place_space_reference"]
    source_ref: SpaceSourceRef


class BuilderVisualPlaceGroundReferenceCommand(_BuilderVisualModel):
    """Place one existing site set with backend-owned scheduling."""

    operation: Literal["place_ground_reference"]
    site_set_ref: SiteSetRef


class BuilderVisualSetSpacePopulationCommand(_BuilderVisualModel):
    """Change one population input and let the backend derive its complete phasing."""

    operation: Literal["set_space_population"]
    segment_id: str = Field(min_length=1, max_length=160)
    phasing_mode: BuilderVisualPhasingMode | None = None
    planes: int | None = Field(default=None, ge=1)
    slots_per_plane: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _changes_exactly_one_population_input(self) -> BuilderVisualSetSpacePopulationCommand:
        changes = (
            self.phasing_mode is not None,
            self.planes is not None,
            self.slots_per_plane is not None,
        )
        if sum(changes) != 1:
            raise ValueError("space population command must change exactly one input")
        return self


class BuilderVisualAuthorInlineSpaceNodeCommand(_BuilderVisualModel):
    """Create one backend-seeded inline node for an authored space segment."""

    operation: Literal["author_inline_space_node"]
    segment_id: str = Field(min_length=1, max_length=160)


class BuilderVisualAddOrIncrementNodeTerminalCommand(_BuilderVisualModel):
    """Mount a selected terminal or increment the matching backend-owned mount."""

    operation: Literal["add_or_increment_node_terminal"]
    segment_id: str = Field(min_length=1, max_length=160)
    terminal_ref: TerminalRef
    role: MountRole


class BuilderVisualSetNodeTerminalRoleCommand(_BuilderVisualModel):
    """Change one inline-node mount role with backend-owned pointing semantics."""

    operation: Literal["set_node_terminal_role"]
    segment_id: str = Field(min_length=1, max_length=160)
    mount_id: str = Field(min_length=1, max_length=160)
    role: MountRole


class BuilderVisualAddNodeEthernetPortCommand(_BuilderVisualModel):
    """Add one uniquely identified Ethernet port to an authored inline node."""

    operation: Literal["add_node_ethernet_port"]
    segment_id: str = Field(min_length=1, max_length=160)


class BuilderVisualAddGroundCommand(_BuilderVisualModel):
    """Add one backend-seeded authored ground-segment draft."""

    operation: Literal["add_ground"]
    node_ref: NodeRef | None = None
    installed: dict[str, int] = Field(default_factory=dict)
    boresights: dict[str, BuilderVisualGroundBoresight] = Field(default_factory=dict)
    body_ref: BodyRef | None = None


class BuilderVisualAddGroundSiteReferenceCommand(_BuilderVisualModel):
    """Place one existing site, creating its authored ground segment when needed."""

    operation: Literal["add_ground_site_reference"]
    segment_id: str | None = Field(default=None, min_length=1, max_length=160)
    site_ref: SiteRef


class BuilderVisualSetGroundStampNodeModelCommand(_BuilderVisualModel):
    """Select a ground stamp node and derive its installed terminal inventory."""

    operation: Literal["set_ground_stamp_node_model"]
    segment_id: str = Field(min_length=1, max_length=160)
    node_ref: NodeRef


class BuilderVisualSetGroundSiteNodeModelCommand(_BuilderVisualModel):
    """Select one authored site's node model and derive its installed inventory."""

    operation: Literal["set_ground_site_node_model"]
    segment_id: str = Field(min_length=1, max_length=160)
    member_id: str = Field(min_length=1, max_length=160)
    node_id: str = Field(min_length=1, max_length=160)
    node_ref: NodeRef


class BuilderVisualAddGroundSiteNodeCommand(_BuilderVisualModel):
    """Add one backend-seeded node installation to an authored site."""

    operation: Literal["add_ground_site_node"]
    segment_id: str = Field(min_length=1, max_length=160)
    member_id: str = Field(min_length=1, max_length=160)
    node_ref: NodeRef | None = None


class BuilderVisualGroundSiteIntent(_BuilderVisualModel):
    """One user-entered surface location awaiting backend-owned site allocation."""

    name: str = Field(min_length=1, max_length=160)
    lat_deg: float = Field(ge=-90, le=90)
    lon_deg: float = Field(ge=-180, le=180)
    alt_m: float = 0


class BuilderVisualMintGroundMembersCommand(_BuilderVisualModel):
    """Mint complete sites and addresses from typed locations and one ground stamp."""

    operation: Literal["mint_ground_members"]
    segment_id: str = Field(min_length=1, max_length=160)
    sites: tuple[BuilderVisualGroundSiteIntent, ...] = Field(min_length=1, max_length=255)


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
    BuilderVisualPlaceSpaceReferenceCommand
    | BuilderVisualPlaceGroundReferenceCommand
    | BuilderVisualAddGeneratedSpaceCommand
    | BuilderVisualSetSpacePopulationCommand
    | BuilderVisualAuthorInlineSpaceNodeCommand
    | BuilderVisualAddOrIncrementNodeTerminalCommand
    | BuilderVisualSetNodeTerminalRoleCommand
    | BuilderVisualAddNodeEthernetPortCommand
    | BuilderVisualAddGroundCommand
    | BuilderVisualAddGroundSiteReferenceCommand
    | BuilderVisualSetGroundStampNodeModelCommand
    | BuilderVisualSetGroundSiteNodeModelCommand
    | BuilderVisualAddGroundSiteNodeCommand
    | BuilderVisualMintGroundMembersCommand
    | BuilderVisualAddRoutingDomainCommand
    | BuilderVisualAddBoundaryCommand
    | BuilderVisualConnectSegmentsCommand
    | BuilderVisualRederiveLinkCommand
    | BuilderVisualSetSchedulingPresetCommand,
    Field(discriminator="operation"),
]


class BuilderVisualWalkerLayoutRequest(_BuilderVisualModel):
    """Walker population intent whose derived angular values remain backend-owned."""

    pattern: Literal["walker_delta", "walker_star"]
    planes: int = Field(ge=2)
    slots_per_plane: int = Field(ge=1)


class BuilderVisualWalkerLayoutResult(_BuilderVisualModel):
    """Backend-issued angular layout for one Walker population intent."""

    raan_spacing_deg: float
    phase_offset_deg: float


def derive_walker_layout(
    request: BuilderVisualWalkerLayoutRequest,
) -> BuilderVisualWalkerLayoutResult:
    """Derive one Walker layout from typed population intent."""

    raan_span = Decimal(180 if request.pattern == "walker_star" else 360)
    quantizer = Decimal("0.001")
    raan_spacing = (raan_span / Decimal(request.planes)).quantize(
        quantizer,
        rounding=ROUND_HALF_UP,
    )
    phase_offset = (Decimal(360) / Decimal(request.planes * request.slots_per_plane)).quantize(
        quantizer,
        rounding=ROUND_HALF_UP,
    )
    return BuilderVisualWalkerLayoutResult(
        raan_spacing_deg=float(raan_spacing),
        phase_offset_deg=float(phase_offset),
    )


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
    expected_draft_revision: int = Field(ge=0)
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
