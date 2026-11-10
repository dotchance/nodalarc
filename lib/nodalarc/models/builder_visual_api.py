# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Typed backend-owned visual Builder draft contracts.

These application DTOs describe authoring gestures, not persisted NodalArc
grammar.  The backend is solely responsible for assembling them into the
generic :class:`BuilderDraftEnvelope` consumed by the existing compiler and
save services.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, get_origin

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from nodalarc.catalog_refs import (
    BodyRef,
    CatalogRef,
    NodeRef,
    SessionRef,
    SiteRef,
    SiteSetRef,
    SpaceSourceRef,
    parse_catalog_reference,
)
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.model_validation import Identifier, TerminalMedium
from nodalarc.models.builder_api import (
    BuilderCompileResult,
    BuilderContentEdit,
    BuilderDraftState,
    BuilderIssue,
    BuilderMemberAddress,
    BuilderPendingEditOutcome,
    BuilderPendingEdits,
    BuilderProposedCatalogDocument,
    BuilderSessionObjectAddress,
    BuilderWholeListAddress,
    JsonDocument,
    OpaqueRevision,
    check_value_fits_address,
)
from nodalarc.models.builder_controls_api import BuilderControlMutation, BuilderControlTree
from nodalarc.models.catalog import PhasingMode
from nodalarc.models.link_rules import MountRole
from nodalarc.models.segment_session import RoutingBoundaryAdapter, RoutingProtocol

type BuilderVisualSchedulingPreset = Literal["leo-fast-handover", "geo-longest-pass"]
type BuilderVisualPhasingMode = PhasingMode
type BuilderVisualOrbitPropagator = Literal["two_body", "j2_mean_elements"]
type BuilderVisualTopologyMode = Literal["visible_candidates", "nearest_n"]
type BuilderVisualDraftCommandOperation = Literal[
    "place_space_reference",
    "place_ground_reference",
    "add_generated_space",
    "set_space_population",
    "add_ground_site_reference",
    "mint_ground_members",
    "add_routing_domain",
    "add_boundary",
    "connect_segments",
    "rederive_link",
    "set_scheduling_preset",
]
type BuilderVisualDraftAffectedKind = Literal[
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


class BuilderVisualGroundBoresight(_BuilderVisualModel):
    """Ground access-terminal pointing authored into a site installation."""

    mode: Literal["local_vertical"]


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
    enabled: bool = True
    a: BuilderVisualLinkEndpoint = Field(default_factory=BuilderVisualLinkEndpoint)
    b: BuilderVisualLinkEndpoint = Field(default_factory=BuilderVisualLinkEndpoint)
    topology_mode: BuilderVisualTopologyMode | None = None
    topology_n: int | None = None
    max_range_km: float | None = None


class BuilderVisualRoutingDomain(_BuilderVisualModel):
    """Editable routing domain; empty membership remains visible to validation."""

    domain_id: str = ""
    protocol: RoutingProtocol | None = None
    member_segment_ids: tuple[str, ...] = ()
    hello_interval_s: int | None = None
    hold_interval_s: int | None = None


class BuilderVisualRoutingBoundary(_BuilderVisualModel):
    """Editable routing boundary assembled without omission."""

    boundary_id: str = ""
    over_rule_id: str = ""
    adapter: RoutingBoundaryAdapter | None = None
    from_domain_id: str = ""
    to_domain_id: str = ""
    export_node_loopbacks: bool = True


class BuilderVisualDraftEnvelope(_BuilderVisualModel):
    """One session draft: its identity, YAML text and accepted session with proposals.

    A new draft holds no accepted session until its first segment gesture; every
    accepted revision is a structurally valid session.
    """

    draft_revision: int = Field(ge=0)
    target_ref: SessionRef
    source_ref: SessionRef | None = None
    expected_session_revision: OpaqueRevision | None = None
    catalog_documents: tuple[BuilderProposedCatalogDocument, ...] = ()
    session_name_is_placeholder: bool
    reserved_authoring_ids: tuple[str, ...]
    session_yaml: str
    applied_revision: int | None = Field(default=None, ge=0)
    applied_session: JsonDocument | None = None
    control_tree: BuilderControlTree | None = None

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
    def _identity_and_accepted_facts_are_consistent(self) -> BuilderVisualDraftEnvelope:
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
        accepted = (self.applied_revision, self.applied_session, self.control_tree)
        if any(item is None for item in accepted) and any(item is not None for item in accepted):
            raise ValueError("an accepted revision, its session and its control tree are one fact")
        if self.applied_session is None:
            # A draft with no accepted session holds the skeleton its first
            # segment gesture completes.
            try:
                skeleton = load_configuration_yaml(self.session_yaml)
            except yaml.YAMLError as error:
                raise ValueError(f"a new draft's session text does not parse: {error}") from error
            if not isinstance(skeleton, dict):
                raise ValueError("a new draft's session text is not a session skeleton")
        if self.applied_revision is not None:
            assert self.control_tree is not None
            if self.applied_revision != self.draft_revision:
                raise ValueError("an accepted session is the draft's current revision")
            if self.control_tree.projection_revision != self.applied_revision:
                raise ValueError("the control tree must belong to applied_revision")
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
        # A refused buffer changes no draft; the page holds the refused text.
        if self.applied and self.draft.session_yaml != self.yaml_text:
            raise ValueError("returned draft must preserve the exact applied YAML buffer")
        if not self.applied and not self.issues:
            raise ValueError("refused YAML application requires typed findings")
        if not self.applied and self.canonicalization_required:
            raise ValueError("refused YAML cannot require canonicalization")
        return self


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
    """Compile a draft's accepted session and list its objects; nothing is accepted."""

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


class BuilderVisualAddGroundSiteReferenceCommand(_BuilderVisualModel):
    """Place one existing site, creating its authored ground segment when needed."""

    operation: Literal["add_ground_site_reference"]
    segment_id: str | None = Field(default=None, min_length=1, max_length=160)
    site_ref: SiteRef


class BuilderVisualGroundSiteIntent(_BuilderVisualModel):
    """One user-entered surface location awaiting backend-owned site allocation."""

    name: str = Field(min_length=1, max_length=160)
    lat_deg: float = Field(ge=-90, le=90)
    lon_deg: float = Field(ge=-180, le=180)
    alt_m: float = 0


class BuilderVisualMintGroundMembersCommand(_BuilderVisualModel):
    """Mint complete sites and addresses from typed locations and one ground stamp."""

    operation: Literal["mint_ground_members"]
    # Absent for the first sites of a new ground segment.
    segment_id: str | None = Field(default=None, min_length=1, max_length=160)
    sites: tuple[BuilderVisualGroundSiteIntent, ...] = Field(min_length=1, max_length=255)
    # The ground card states the node each site installs and the body it stands on.
    # An empty installation installs each mount of the node as its model counts it.
    node_ref: NodeRef
    body_ref: BodyRef
    installed: dict[str, int] = Field(default_factory=dict)
    boresights: dict[str, BuilderVisualGroundBoresight] = Field(default_factory=dict)


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
    # A site's override; absent for the segment's own scheduling.
    site_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def _inheritance_targets_only_sites(self) -> BuilderVisualSetSchedulingPresetCommand:
        if self.preset is None and self.site_id is None:
            raise ValueError("only a site can inherit its segment's scheduling")
        return self


BuilderVisualDraftCommand = Annotated[
    BuilderVisualPlaceSpaceReferenceCommand
    | BuilderVisualPlaceGroundReferenceCommand
    | BuilderVisualAddGeneratedSpaceCommand
    | BuilderVisualSetSpacePopulationCommand
    | BuilderVisualAddGroundSiteReferenceCommand
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
    """Apply one typed command to an exact visual-draft revision.

    ``pending_edits`` are the edits not yet accepted. A command mints no id
    they define or reference.
    """

    draft: BuilderVisualDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    command: BuilderVisualDraftCommand
    pending_edits: BuilderPendingEdits = ()


class BuilderVisualDraftCommandResult(_BuilderVisualModel):
    """One command's result: the next revision of the complete draft, or pending edits.

    A command either accepts its result as the next revision and creates no
    edit, or leaves the draft at its revision and returns the edits it created.
    """

    operation: BuilderVisualDraftCommandOperation
    base_draft_revision: int = Field(ge=0)
    draft: BuilderVisualDraftEnvelope
    affected_kind: BuilderVisualDraftAffectedKind
    affected_id: str = Field(min_length=1, max_length=160)
    scheduling_preset: BuilderVisualSchedulingPreset | None = None
    notice: str | None = None
    created_edits: tuple[BuilderContentEdit, ...] = ()

    @model_validator(mode="after")
    def _accepted_once_or_created_edits(self) -> BuilderVisualDraftCommandResult:
        if self.created_edits:
            if self.draft.draft_revision != self.base_draft_revision:
                raise ValueError("a command that creates edits must leave draft_revision unchanged")
        elif self.draft.draft_revision != self.base_draft_revision + 1:
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


class BuilderVisualLinkRuleListing(_BuilderVisualModel):
    """One link rule of the accepted session and the card value that writes it back."""

    rule_id: Identifier
    segment_ids: tuple[Identifier, ...]
    widget: BuilderVisualLinkRule | None


class BuilderVisualRoutingDomainListing(_BuilderVisualModel):
    """One routing domain of the accepted session and the card value that writes it back."""

    domain_id: Identifier
    protocol: RoutingProtocol
    segment_ids: tuple[Identifier, ...]
    widget: BuilderVisualRoutingDomain | None


class BuilderVisualRoutingBoundaryListing(_BuilderVisualModel):
    """One routing boundary of the accepted session, addressed by its position."""

    index: int = Field(ge=0)
    over_rule_id: Identifier
    domain_ids: tuple[Identifier, ...]
    widget: BuilderVisualRoutingBoundary | None


class BuilderVisualSessionListing(_BuilderVisualModel):
    """Every link rule, routing domain and boundary the accepted session declares, in order.

    The backend derives it from the accepted session and never reads it back. A null
    widget means no card writes that object back unchanged.
    """

    link_rules: tuple[BuilderVisualLinkRuleListing, ...]
    routing_domains: tuple[BuilderVisualRoutingDomainListing, ...]
    boundaries: tuple[BuilderVisualRoutingBoundaryListing, ...]

    @model_validator(mode="after")
    def _boundaries_are_addressed_by_position(self) -> BuilderVisualSessionListing:
        if any(boundary.index != position for position, boundary in enumerate(self.boundaries)):
            raise ValueError("a listed boundary's index must be its position in the session")
        return self


class BuilderVisualDraftAssemblyResult(_BuilderVisualModel):
    """An accepted draft with its compile and the listing of its session objects.

    The listing names every rule, domain and boundary of the accepted session.
    """

    visual_draft: BuilderVisualDraftEnvelope
    compile_result: BuilderCompileResult
    listing: BuilderVisualSessionListing | None

    @model_validator(mode="after")
    def _identities_match(self) -> BuilderVisualDraftAssemblyResult:
        if (self.listing is None) != (self.visual_draft.applied_session is None):
            raise ValueError("the listing exists exactly when the draft has an accepted session")
        if self.compile_result.target_ref != self.visual_draft.target_ref:
            raise ValueError("compile result target must match the visual draft target")
        return self


class BuilderVisualLinkRuleWidget(_BuilderVisualModel):
    """A link rule's card value, which states the whole rule."""

    kind: Literal["link_rule"]
    rule: BuilderVisualLinkRule


class BuilderVisualRoutingDomainWidget(_BuilderVisualModel):
    """A routing domain's card value, which states the whole domain."""

    kind: Literal["routing_domain"]
    domain: BuilderVisualRoutingDomain


class BuilderVisualRoutingBoundariesWidget(_BuilderVisualModel):
    """The card value of each routing boundary, in list order.

    A null entry is a boundary no card writes back unchanged. Sent with an
    edit, a null entry leaves that boundary as the working list holds it.
    """

    kind: Literal["routing_boundaries"]
    boundaries: tuple[BuilderVisualRoutingBoundary | None, ...]


BuilderVisualObjectWidget = Annotated[
    BuilderVisualLinkRuleWidget
    | BuilderVisualRoutingDomainWidget
    | BuilderVisualRoutingBoundariesWidget,
    Field(discriminator="kind"),
]


class BuilderVisualObjectControlEdit(_BuilderVisualModel):
    """A field-editor batch over the tree an earlier object edit answered."""

    kind: Literal["controls"]
    commands: tuple[BuilderControlMutation, ...] = Field(min_length=1)


BuilderVisualObjectChange = Annotated[
    BuilderVisualLinkRuleWidget
    | BuilderVisualRoutingDomainWidget
    | BuilderVisualRoutingBoundariesWidget
    | BuilderVisualObjectControlEdit,
    Field(discriminator="kind"),
]


def _widget_fits_address(
    address: BuilderMemberAddress | BuilderWholeListAddress,
    working: JsonDocument | list[JsonValue],
    widget: BuilderVisualLinkRuleWidget
    | BuilderVisualRoutingDomainWidget
    | BuilderVisualRoutingBoundariesWidget,
) -> None:
    """Raise ValueError unless a card value states the object ``address`` names."""
    if isinstance(address, BuilderWholeListAddress):
        if not isinstance(widget, BuilderVisualRoutingBoundariesWidget):
            raise ValueError("the routing boundary list takes the boundary card values")
        if len(widget.boundaries) != len(working):
            raise ValueError("the boundary card values pair one to one with the working list")
        return
    if address.list_pointer == "/link_rules":
        if not isinstance(widget, BuilderVisualLinkRuleWidget):
            raise ValueError("a link rule takes a link rule card value")
        card_id = widget.rule.rule_id
    else:
        if not isinstance(widget, BuilderVisualRoutingDomainWidget):
            raise ValueError("a routing domain takes a routing domain card value")
        card_id = widget.domain.domain_id
    if card_id != address.id:
        raise ValueError(
            "a card keeps the id it is addressed by; a rename is an edit of the session YAML"
        )


class BuilderVisualObjectEditRequest(_BuilderVisualModel):
    """Edit one link rule, one routing domain or the boundary list without accepting it.

    ``working`` is the object's grammar JSON as its pending edit holds it. With
    no change, the answer describes that JSON. A card value states the whole
    object. A control edit names controls of the tree an earlier answer built
    over this same working JSON at ``applied_revision``.
    """

    target_ref: SessionRef
    applied_revision: int = Field(ge=0)
    address: BuilderSessionObjectAddress
    working: JsonDocument | list[JsonValue]
    change: BuilderVisualObjectChange | None = None

    @model_validator(mode="after")
    def _change_fits_the_address(self) -> BuilderVisualObjectEditRequest:
        if self.target_ref.namespace != "user":
            raise ValueError("Builder session targets must use the user: namespace")
        check_value_fits_address(self.address, self.working)
        if self.change is not None and not isinstance(self.change, BuilderVisualObjectControlEdit):
            _widget_fits_address(self.address, self.working, self.change)
        return self


class BuilderVisualObjectEditResult(_BuilderVisualModel):
    """One object's working JSON after an edit, with the editors that state it.

    Nothing is accepted. ``widget`` is the card value that writes the working
    JSON back unchanged; it is null for an object that does not validate on its
    own or that no card writes back unchanged. The control tree covers the
    working JSON at the accepted revision and keeps the object's id read-only.
    Issue pointers are within the working JSON.
    """

    address: BuilderSessionObjectAddress
    working: JsonDocument | list[JsonValue]
    widget: BuilderVisualObjectWidget | None
    control_tree: BuilderControlTree
    issues: tuple[BuilderIssue, ...] = ()

    @model_validator(mode="after")
    def _editors_fit_the_address(self) -> BuilderVisualObjectEditResult:
        check_value_fits_address(self.address, self.working)
        if self.widget is not None:
            _widget_fits_address(self.address, self.working, self.widget)
        elif isinstance(self.address, BuilderWholeListAddress):
            raise ValueError("the boundary list answers a card value or null for each boundary")
        return self


class BuilderVisualDraftApplyEditsRequest(_BuilderVisualModel):
    """Accept pending link rule, routing domain and boundary edits as one revision."""

    draft: BuilderVisualDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    edits: BuilderPendingEdits = Field(min_length=1)


class BuilderVisualDraftApplyEditsResult(_BuilderVisualModel):
    """The next accepted revision with its compile, or the refusal of the whole batch.

    ``outcomes`` holds one outcome per edit, in request order. One stale or
    invalid edit refuses the batch. ``issues`` holds what the session made from
    every edit refuses that no single edit owns. A refused batch accepts nothing
    and returns no assembly, so every edit in it stays pending.
    """

    applied: bool
    outcomes: tuple[BuilderPendingEditOutcome, ...] = Field(min_length=1)
    issues: tuple[BuilderIssue, ...] = ()
    assembly: BuilderVisualDraftAssemblyResult | None

    @model_validator(mode="after")
    def _applied_exactly_when_every_edit_applies(self) -> BuilderVisualDraftApplyEditsResult:
        if self.applied != (
            all(outcome.status == "applied" for outcome in self.outcomes) and not self.issues
        ):
            raise ValueError(
                "a batch is applied exactly when every edit in it applies and the session "
                "they make has no issue"
            )
        if self.applied != (self.assembly is not None):
            raise ValueError("an applied batch returns its assembly and a refused batch none")
        return self


class BuilderVisualDraftAcceptStateRequest(_BuilderVisualModel):
    """Accept a stored session with its proposals as the draft's next revision.

    Undo sends the state an accepted change replaced. Its session is validated
    as every accepted session is. The allocation history never shrinks.
    """

    draft: BuilderVisualDraftEnvelope
    expected_draft_revision: int = Field(ge=0)
    state: BuilderDraftState
