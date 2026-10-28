// GENERATED FILE — DO NOT EDIT BY HAND.
// Sources of truth: backend Pydantic application contracts in
// lib/nodalarc/models.
// Regenerate: make generate-contracts
//
// These are non-grammar application contracts. Configuration fields remain
// generic JSON in TypeScript and are validated by backend Pydantic models.

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | ReadonlyArray<JsonValue>
  | { readonly [key: string]: JsonValue };

/** Self-describing sha256:<64 lowercase hex> content identity. */
export type Sha256Digest = string;
/** Namespace-qualified catalog reference validated by the backend. */
export type CatalogRef = string;
/** Catalog reference whose family is sessions. */
export type SessionRef = CatalogRef;
/** Path-free deployable source selected by the browser. */
export type SessionSourceId = CatalogSessionSourceId;

/** Stable, path-free refusal evidence for one request. ``code`` is the refusing component's own vocabulary (an exception family's enum value or one fixed name per family). ``message`` names the declared inputs and catalog references involved; the host filesystem and other exceptions' text never appear in it. ``cause_type`` is the class name of a wrapped cause when the refusal carries one. */
export interface ApiRefusal {
  readonly code: string;
  readonly message: string;
  readonly cause_type: string | null;
}

/**  */
export type BuilderBlockedOperation = "save" | "deploy";

/** Public documentation and catalog metadata needed to start Builder authoring. */
export interface BuilderCatalogBootstrap {
  readonly public_grammar_href: string;
  readonly capabilities: BuilderCatalogCapabilities;
  readonly families: ReadonlyArray<CatalogFamilyMetadata>;
  readonly scheduling_presets: ReadonlyArray<BuilderVisualSchedulingPresetMetadata>;
  readonly authoring: BuilderVisualAuthoringFacts;
}

/** Backend capabilities available to the Builder. */
export interface BuilderCatalogCapabilities {
  readonly user_catalog_write: true;
  readonly deploy_yaml_closure: true;
}

/** One canonical catalog or session document with opaque revision identity. */
export interface BuilderCatalogDocument {
  readonly ref: CatalogRef;
  readonly family: CatalogFamily;
  readonly canonical_yaml: string;
  readonly canonical_json: Readonly<Record<string, JsonValue>>;
  readonly content_digest: Sha256Digest;
  readonly revision: string;
}

/** One selectable Literal, union type, or null branch. */
export interface BuilderChoiceBranch {
  readonly branch_id: string;
  readonly label: string;
  readonly branch_kind: BuilderControlChoiceKind;
  readonly selected: boolean;
  readonly literal_value?: string | number | boolean | null;
  readonly control?: BuilderScalarControl | BuilderObjectControl | BuilderChoiceControl | BuilderSequenceControl | BuilderMapControl | null;
}

/** A compact selector whose branches remain fully typed. */
export interface BuilderChoiceControl {
  readonly control_id: string;
  readonly json_pointer: string;
  readonly label: string;
  readonly required: boolean;
  readonly present: boolean;
  readonly editable?: true;
  readonly specialized?: boolean;
  readonly description?: string | null;
  readonly kind?: "choice";
  readonly branches: ReadonlyArray<BuilderChoiceBranch>;
}

/** Request to compile one transient draft through backend authorities. ``pending_edits`` are applied to the draft's session before it compiles; the draft itself is not changed. */
export interface BuilderCompileRequest {
  readonly draft: BuilderDraftEnvelope;
  readonly target_ref: SessionRef;
  readonly pending_edits?: ReadonlyArray<BuilderContentEdit | BuilderListEdit>;
}

/** Backend compilation result for a transient Builder draft. ``draft`` is the draft the request sent. The canonical facts, the preview and the issues describe that draft with its pending edits applied, and ``pending_edit_outcomes`` holds one outcome per pending edit, in request order. */
export interface BuilderCompileResult {
  readonly draft: BuilderDraftEnvelope;
  readonly target_ref: SessionRef;
  readonly canonical_session_yaml: string | null;
  readonly canonical_session_json: Readonly<Record<string, JsonValue>> | null;
  readonly dependency_closure: DependencyClosureInventory | null;
  readonly resolved_preview: BuilderWorld | null;
  readonly digests: BuilderDigests | null;
  readonly issues: ReadonlyArray<BuilderIssue>;
  readonly pending_edit_outcomes: ReadonlyArray<BuilderPendingEditOutcome>;
  readonly save_verdict: BuilderVerdict;
  readonly deploy_eligibility_after_save: BuilderVerdict;
}

/** Working grammar JSON for one address, beside the value it began from. ``base`` is absent for a new object. Both values stay generic JSON so an incomplete object can cross the API boundary and come back with issues. */
export interface BuilderContentEdit {
  readonly kind: "content";
  readonly address: BuilderMemberAddress | BuilderWholeListAddress;
  readonly base?: Readonly<Record<string, JsonValue>> | ReadonlyArray<JsonValue> | null;
  readonly working: Readonly<Record<string, JsonValue>> | ReadonlyArray<JsonValue>;
}

/**  */
export type BuilderControlChoiceKind = "literal" | "type" | "null";

/**  */
export type BuilderControlNumberKind = "integer" | "float";

/**  */
export type BuilderControlScalarKind = "text" | "number" | "boolean" | "datetime" | "reference";

/** Complete graphical control projection for one applied configuration revision. */
export interface BuilderControlTree {
  readonly projection_revision: number;
  readonly root: BuilderObjectControl;
}

/** Deployment decision bound to one exact saved session revision. */
export interface BuilderDeployVerdict {
  readonly allowed: boolean;
  readonly session_ref: SessionRef;
  readonly session_revision: string;
  readonly digests: BuilderDigests;
  readonly blockers: ReadonlyArray<BuilderIssue>;
}

/** Content identities reviewed across compile, save, and deployment. */
export interface BuilderDigests {
  readonly document: Sha256Digest;
  readonly dependency: Sha256Digest;
  readonly resolved_semantic: Sha256Digest | null;
}

/** One revision of transient editor state accepted by compile/save APIs only. */
export interface BuilderDraftEnvelope {
  readonly draft_revision: number;
  readonly state: BuilderDraftState;
}

/** Complete transient configuration state compiled by the backend. The inner mappings intentionally remain generic JSON so incomplete or invalid configuration can cross the API boundary and return typed compile issues instead of being rejected as a malformed application request. */
export interface BuilderDraftState {
  readonly session: Readonly<Record<string, JsonValue>>;
  readonly catalog_documents?: ReadonlyArray<BuilderProposedCatalogDocument>;
}

/**  */
export interface BuilderInsertItemCommand {
  readonly operation: "insert_item";
  readonly control_id: string;
  readonly index: number;
}

/**  */
export interface BuilderInsertMapEntryCommand {
  readonly operation: "insert_map_entry";
  readonly control_id: string;
  readonly key: string | number | boolean;
  readonly value?: string | number | boolean | null;
}

/** One typed finding routed to its owning authoring or deployment stage. */
export interface BuilderIssue {
  readonly code: string;
  readonly stage: BuilderIssueStage;
  readonly severity: BuilderIssueSeverity;
  readonly message: string;
  readonly blocks: ReadonlyArray<BuilderBlockedOperation>;
  readonly source_ref: string | null;
  readonly json_pointer: string | null;
  readonly draft_path: string | null;
  readonly source_line: number | null;
  readonly source_column: number | null;
  readonly related_refs: ReadonlyArray<string>;
}

/**  */
export type BuilderIssueSeverity = "info" | "warning" | "error";

/**  */
export type BuilderIssueStage = "draft" | "structural" | "reference" | "semantic" | "runtime_support" | "readiness" | "persistence" | "deployment" | "staleness";

/** One resolved link-rule endpoint, flattened for builder display. */
export interface BuilderLinkEndpoint {
  readonly segment_id: string;
  readonly terminal_role: MountRole;
  readonly terminal_medium: string | null;
  readonly min_elevation_deg: number | null;
  readonly node_ids: ReadonlyArray<string>;
}

/** Display projection of one ``ResolvedLinkRule``. The resolved rule's topology is a discriminated union; the builder needs the flat facts (mode, n, explicit pairs, range cap) to preview candidate geometry. This is a projection for display — candidate truth at runtime stays with OME. */
export interface BuilderLinkRule {
  readonly rule_id: string;
  readonly kind: LinkLabel;
  readonly enabled: boolean;
  readonly endpoints: readonly [BuilderLinkEndpoint, BuilderLinkEndpoint];
  readonly topology_mode: TopologyMode;
  readonly topology_n: number | null;
  readonly explicit_pairs: ReadonlyArray<readonly [string, string]>;
  readonly max_range_km: number | null;
}

/** A removal or reorder of an id-bearing session list's members. */
export interface BuilderListEdit {
  readonly kind: "list";
  readonly list_pointer: BuilderMemberListPointer;
  readonly ids_before: ReadonlyArray<string>;
  readonly ids_wanted: ReadonlyArray<string>;
}

/** A typed mapping editor with explicit key and value controls. */
export interface BuilderMapControl {
  readonly control_id: string;
  readonly json_pointer: string;
  readonly label: string;
  readonly required: boolean;
  readonly present: boolean;
  readonly editable?: true;
  readonly specialized?: boolean;
  readonly description?: string | null;
  readonly kind?: "map";
  readonly entries?: ReadonlyArray<BuilderMapEntry>;
  readonly add_key_control: BuilderScalarControl | BuilderObjectControl | BuilderChoiceControl | BuilderSequenceControl | BuilderMapControl;
  readonly add_value_control: BuilderScalarControl | BuilderObjectControl | BuilderChoiceControl | BuilderSequenceControl | BuilderMapControl;
  readonly can_add?: true;
  readonly can_remove?: true;
  readonly can_rename_keys?: true;
  readonly min_entries?: number | null;
  readonly max_entries?: number | null;
}

/** One typed key/value row in a mapping. */
export interface BuilderMapEntry {
  readonly key: BuilderScalarControl | BuilderObjectControl | BuilderChoiceControl | BuilderSequenceControl | BuilderMapControl;
  readonly value: BuilderScalarControl | BuilderObjectControl | BuilderChoiceControl | BuilderSequenceControl | BuilderMapControl;
}

/** One member of an id-bearing session list, by the id it had when the edit began. */
export interface BuilderMemberAddress {
  readonly kind: "member";
  readonly list_pointer: BuilderMemberListPointer;
  readonly id: string;
}

/**  */
export type BuilderMemberListPointer = "/link_rules" | "/routing/domains";

/**  */
export interface BuilderMoveItemCommand {
  readonly operation: "move_item";
  readonly control_id: string;
  readonly from_index: number;
  readonly to_index: number;
}

/** One node's fixed-interface capacity for one rule, as allocated. */
export interface BuilderNodeInterfaceFacts {
  readonly node_id: string;
  readonly segment_id: string;
  readonly matching: number;
  readonly free: number;
}

/** A structured object editor, including explicit empty parameter objects. */
export interface BuilderObjectControl {
  readonly control_id: string;
  readonly json_pointer: string;
  readonly label: string;
  readonly required: boolean;
  readonly present: boolean;
  readonly editable?: true;
  readonly specialized?: boolean;
  readonly description?: string | null;
  readonly kind?: "object";
  readonly model_name?: string | null;
  readonly fields?: ReadonlyArray<BuilderObjectField>;
  readonly empty_parameters?: boolean;
  readonly recursive_reference?: boolean;
}

/** One canonical model field and its graphical control. */
export interface BuilderObjectField {
  readonly field_name: string;
  readonly wire_name: string;
  readonly control: BuilderScalarControl | BuilderObjectControl | BuilderChoiceControl | BuilderSequenceControl | BuilderMapControl;
}

/** What applying one pending edit did, at that edit's position in its batch. */
export interface BuilderPendingEditOutcome {
  readonly status: BuilderPendingEditStatus;
  readonly issues: ReadonlyArray<BuilderIssue>;
}

/**  */
export type BuilderPendingEditStatus = "applied" | "stale" | "invalid";

/** One drawn preview pair: a runtime node pair whose frozen-epoch geometry passed every armed gate, oriented to the rule's endpoints server-side. canvas draws these directly and never re-derives pair identities. */
export interface BuilderPreviewPair {
  readonly rule_id: string;
  readonly kind: LinkLabel;
  readonly node_a: string;
  readonly node_b: string;
}

/** How many tested pairs one reject reason accounts for. ``reason`` is the runtime's reject_reason verbatim (or ``no_geometry``); counts sum over ``pairs_tested`` — never an untested remainder. */
export interface BuilderPreviewReasonCount {
  readonly reason: BuilderPreviewRejectReason;
  readonly count: number;
}

/**  */
export type BuilderPreviewRejectReason = "los_blocked" | "range_exceeded" | "elevation_below_min" | "field_of_regard" | "terminal_type_mismatch" | "no_geometry";

/** One complete new catalog document proposed for a ``user:`` ref. */
export interface BuilderProposedCatalogDocument {
  readonly ref: CatalogRef;
  readonly document: Readonly<Record<string, JsonValue>>;
  readonly origin: "generated" | "customized";
}

/**  */
export interface BuilderRemoveItemCommand {
  readonly operation: "remove_item";
  readonly control_id: string;
  readonly index: number;
}

/**  */
export interface BuilderRemoveMapEntryCommand {
  readonly operation: "remove_map_entry";
  readonly control_id: string;
  readonly index: number;
}

/**  */
export interface BuilderRenameMapKeyCommand {
  readonly operation: "rename_map_key";
  readonly control_id: string;
  readonly index: number;
  readonly key: string | number | boolean;
}

/** The allocator's own outcome for one rule — the single capacity truth every display reports instead of re-deriving. For access rules nothing is consumed at resolve time (the runtime schedules access within terminal capacity): ``allocated_pairs`` counts the declared candidate universe and ``free`` always mirrors ``matching``. Displays must not present access facts as fixed allocation. */
export interface BuilderRuleAllocation {
  readonly rule_id: string;
  readonly kind: LinkLabel;
  readonly allocated_pairs: number;
  readonly per_node: ReadonlyArray<BuilderNodeInterfaceFacts>;
}

/** The server's frozen-epoch visibility verdict for one link rule. NodalArc computes preview geometry through the same OME visibility composites the runtime uses; the builder renders these facts and never runs a second physics engine. ``preview_scope`` says whether geometry ran and, if not, why. Only ``computed`` carries reason counts and drawn pairs. The preview is bounded, not a simulation. ``pairs_total`` is the candidate universe size (a closed-form count, never a materialized pair set); ``pairs_tested`` is the deterministic subset geometry actually ran on (``min(pairs_total, budget)``, first pairs in authored/node-id order — never distance-ranked); ``pairs_drawn`` is how many tested pairs passed (the first such, capped to the draw cap). ``capped`` is true when the preview is partial on either axis (``pairs_tested < pairs_total`` or more passed than were drawn). Reason counts and drawn pairs describe the tested subset only. */
export interface BuilderRulePreview {
  readonly rule_id: string;
  readonly preview_scope: PreviewScope;
  readonly pairs_total: number;
  readonly pairs_tested: number;
  readonly pairs_drawn: number;
  readonly capped: boolean;
  readonly reason_counts: ReadonlyArray<BuilderPreviewReasonCount>;
  readonly drawable_pairs: ReadonlyArray<BuilderPreviewPair>;
}

/** Pydantic-derived restrictions used to configure one scalar widget. */
export interface BuilderScalarConstraints {
  readonly minimum?: number | null;
  readonly exclusive_minimum?: number | null;
  readonly maximum?: number | null;
  readonly exclusive_maximum?: number | null;
  readonly multiple_of?: number | null;
  readonly min_length?: number | null;
  readonly max_length?: number | null;
  readonly pattern?: string | null;
  readonly format?: string | null;
}

/** One scalar editor with a backend-derived widget and restrictions. */
export interface BuilderScalarControl {
  readonly control_id: string;
  readonly json_pointer: string;
  readonly label: string;
  readonly required: boolean;
  readonly present: boolean;
  readonly editable?: true;
  readonly specialized?: boolean;
  readonly description?: string | null;
  readonly kind?: "scalar";
  readonly scalar_kind: BuilderControlScalarKind;
  readonly value?: string | number | boolean | null;
  readonly number_kind?: BuilderControlNumberKind | null;
  readonly constraints?: BuilderScalarConstraints;
  readonly reference_families?: ReadonlyArray<CatalogFamily>;
}

/** One terminal role and medium that some node in a segment carries. */
export interface BuilderSegmentCapability {
  readonly endpoint_role: MountRole;
  readonly medium: TerminalMediumLiteral;
}

/**  */
export interface BuilderSelectChoiceCommand {
  readonly operation: "select_choice";
  readonly control_id: string;
  readonly branch_id: string;
}

/** A typed row editor for variable or fixed-length sequences. */
export interface BuilderSequenceControl {
  readonly control_id: string;
  readonly json_pointer: string;
  readonly label: string;
  readonly required: boolean;
  readonly present: boolean;
  readonly editable?: true;
  readonly specialized?: boolean;
  readonly description?: string | null;
  readonly kind?: "sequence";
  readonly items?: ReadonlyArray<BuilderSequenceItem>;
  readonly add_item_control?: BuilderScalarControl | BuilderObjectControl | BuilderChoiceControl | BuilderSequenceControl | BuilderMapControl | null;
  readonly can_add: boolean;
  readonly can_remove: boolean;
  readonly can_reorder: boolean;
  readonly min_items?: number | null;
  readonly max_items?: number | null;
}

/** One typed row in an ordered sequence. */
export interface BuilderSequenceItem {
  readonly index: number;
  readonly control: BuilderScalarControl | BuilderObjectControl | BuilderChoiceControl | BuilderSequenceControl | BuilderMapControl;
}

/** Opaque accepted operation bound to the exact requested catalog source. */
export interface BuilderSessionDeployAccepted {
  readonly operation_id: string;
  readonly status: "accepted";
  readonly source: BuilderSessionDeployRequest;
}

/** Stable, path-free refusal for one guarded saved-session deployment. ``code`` is the refusing component's own vocabulary, the same one the ``ApiRefusal`` envelope carries; this model adds the deployment's identity and precondition evidence. */
export interface BuilderSessionDeployRefusal {
  readonly code: string;
  readonly message: string;
  readonly session_ref: SessionRef;
  readonly expected: string | null;
  readonly observed: string | null;
  readonly cause_type: string | null;
}

/** Request deployment of one exact saved session revision and closure. */
export interface BuilderSessionDeployRequest {
  readonly session_ref: SessionRef;
  readonly expected_session_revision: string;
  readonly expected_document_digest: Sha256Digest;
  readonly expected_dependency_digest: Sha256Digest;
  readonly record_history: boolean;
}

/** Stable transport evidence for a Builder session save that did not succeed. */
export interface BuilderSessionSaveRefusal {
  readonly code: BuilderSessionSaveRefusalCode;
  readonly message: string;
  readonly target_ref: SessionRef;
  readonly base_generation: string | null;
  readonly repository_committed: boolean;
  readonly issues: ReadonlyArray<BuilderIssue>;
  readonly cause_type: string | null;
  readonly compile_result: BuilderCompileResult | null;
}

/**  */
export type BuilderSessionSaveRefusalCode = "builder_session_save.save_blocked" | "builder_session_save.stale_write" | "builder_session_save.graph_invalid" | "builder_session_save.persistence_failed" | "builder_session_save.storage_verification_failed";

/** Transactional request to publish a complete draft as a catalog session. */
export interface BuilderSessionSaveRequest {
  readonly draft: BuilderDraftEnvelope;
  readonly target_ref: SessionRef;
  readonly expected_session_revision?: string | null;
}

/** Exact saved document identity plus its saved-revision deploy verdict. */
export interface BuilderSessionSaveResult {
  readonly session: BuilderCatalogDocument;
  readonly digests: BuilderDigests;
  readonly dependency_closure: DependencyClosureInventory;
  readonly deploy_verdict: BuilderDeployVerdict;
  readonly issues: ReadonlyArray<BuilderIssue>;
}

/**  */
export interface BuilderSetPresentCommand {
  readonly operation: "set_present";
  readonly control_id: string;
  readonly present: boolean;
}

/**  */
export interface BuilderSetScalarCommand {
  readonly operation: "set_scalar";
  readonly control_id: string;
  readonly value: string | number | boolean;
}

/** Backend-owned allow/block decision for one workflow operation. */
export interface BuilderVerdict {
  readonly operation: BuilderBlockedOperation;
  readonly allowed: boolean;
  readonly blockers: ReadonlyArray<BuilderIssue>;
}

/** Add one backend-seeded routing boundary. */
export interface BuilderVisualAddBoundaryCommand {
  readonly operation: "add_boundary";
}

/** Add one backend-seeded generated constellation draft. */
export interface BuilderVisualAddGeneratedSpaceCommand {
  readonly operation: "add_generated_space";
  readonly node_ref?: string | null;
  readonly phasing_mode: BuilderVisualPhasingMode;
}

/** Add one backend-seeded authored ground-segment draft. */
export interface BuilderVisualAddGroundCommand {
  readonly operation: "add_ground";
  readonly node_ref?: string | null;
  readonly installed?: Readonly<Record<string, number>>;
  readonly boresights?: Readonly<Record<string, BuilderVisualGroundBoresight>>;
  readonly body_ref?: string | null;
}

/** Add one backend-seeded node installation to an authored site. */
export interface BuilderVisualAddGroundSiteNodeCommand {
  readonly operation: "add_ground_site_node";
  readonly segment_id: string;
  readonly member_id: string;
  readonly node_ref?: string | null;
}

/** Place one existing site, creating its authored ground segment when needed. */
export interface BuilderVisualAddGroundSiteReferenceCommand {
  readonly operation: "add_ground_site_reference";
  readonly segment_id?: string | null;
  readonly site_ref: string;
}

/** Add one uniquely identified Ethernet port to an authored inline node. */
export interface BuilderVisualAddNodeEthernetPortCommand {
  readonly operation: "add_node_ethernet_port";
  readonly segment_id: string;
}

/** Mount a selected terminal or increment the matching backend-owned mount. */
export interface BuilderVisualAddOrIncrementNodeTerminalCommand {
  readonly operation: "add_or_increment_node_terminal";
  readonly segment_id: string;
  readonly terminal_ref: string;
  readonly role: MountRole;
}

/** Add one backend-seeded routing domain over uncovered segments. */
export interface BuilderVisualAddRoutingDomainCommand {
  readonly operation: "add_routing_domain";
}

/** Create one backend-seeded inline node for an authored space segment. */
export interface BuilderVisualAuthorInlineSpaceNodeCommand {
  readonly operation: "author_inline_space_node";
  readonly segment_id: string;
}

/** Backend-owned choices and seeds used by the visual authoring surface. */
export interface BuilderVisualAuthoringFacts {
  readonly default_phasing_mode: BuilderVisualPhasingMode;
  readonly single_plane_phasing_mode: BuilderVisualPhasingMode;
  readonly default_mount_role: MountRole;
  readonly default_body_ref: string;
  readonly space_access_boresight: BuilderVisualSpaceBoresight;
  readonly ground_access_boresight: BuilderVisualGroundBoresight;
  readonly mount_roles: ReadonlyArray<BuilderVisualMountRoleMetadata>;
  readonly link_media: ReadonlyArray<BuilderVisualLinkMediumMetadata>;
  readonly forwarding_classes: ReadonlyArray<BuilderVisualForwardingClassMetadata>;
  readonly routing_protocols: ReadonlyArray<BuilderVisualRoutingProtocolMetadata>;
  readonly boundary_adapters: ReadonlyArray<BuilderVisualBoundaryAdapterMetadata>;
  readonly phasing_modes: ReadonlyArray<BuilderVisualPhasingModeMetadata>;
  readonly orbit_shapes: ReadonlyArray<BuilderVisualOrbitShapeMetadata>;
  readonly topology_modes: ReadonlyArray<BuilderVisualTopologyModeMetadata>;
}

/** Presentation and runtime facts for one routing-boundary adapter. */
export interface BuilderVisualBoundaryAdapterMetadata {
  readonly runtime_supported: boolean;
  readonly support_note: string | null;
  readonly id: RoutingBoundaryAdapter;
  readonly label: string;
}

/** Create a link rule whose initial physics comes from both endpoints. */
export interface BuilderVisualConnectSegmentsCommand {
  readonly operation: "connect_segments";
  readonly from_segment_id: string;
  readonly to_segment_id: string;
}

/** Apply an atomic batch through revision-scoped backend control identities. */
export interface BuilderVisualControlMutationRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly commands: ReadonlyArray<BuilderSetScalarCommand | BuilderSetPresentCommand | BuilderSelectChoiceCommand | BuilderInsertItemCommand | BuilderRemoveItemCommand | BuilderMoveItemCommand | BuilderInsertMapEntryCommand | BuilderRemoveMapEntryCommand | BuilderRenameMapKeyCommand>;
}

/** One source-to-user fork in a minimal nested customization path. */
export interface BuilderVisualCustomizeChainEntry {
  readonly source_ref: CatalogRef;
  readonly target_ref: CatalogRef;
}

/** Fork the minimal catalog ancestor path for one placed nested component. */
export interface BuilderVisualCustomizeChainRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly segment_id: string;
  readonly leaf_ref: CatalogRef;
  readonly target_leaf_ref?: CatalogRef | null;
}

/** Updated draft or typed refusal evidence for one customize-chain command. */
export interface BuilderVisualCustomizeChainResult {
  readonly applied: boolean;
  readonly draft: BuilderVisualDraftEnvelope;
  readonly root_source_ref: CatalogRef | null;
  readonly root_target_ref: CatalogRef | null;
  readonly forked_chain: ReadonlyArray<BuilderVisualCustomizeChainEntry>;
  readonly issues: ReadonlyArray<BuilderIssue>;
}

/**  */
export type BuilderVisualDraftAffectedKind = "space" | "ground" | "routing_domain" | "boundary" | "link" | "ground_member";

/** Apply one complete graphical workspace to a fenced draft revision. */
export interface BuilderVisualDraftApplyWorkspaceRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly workspace: BuilderVisualWorkspace;
}

/** Apply one exact session YAML buffer to a fenced visual draft revision. */
export interface BuilderVisualDraftApplyYamlRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly buffer_generation: number;
  readonly yaml_text: string;
}

/** Applied projection or typed refusal for one exact session YAML buffer. */
export interface BuilderVisualDraftApplyYamlResult {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly buffer_generation: number;
  readonly yaml_text: string;
  readonly applied: boolean;
  readonly canonicalization_required: boolean;
  readonly issues: ReadonlyArray<BuilderIssue>;
}

/** Backend assembly, typed issues, save request, and authoritative compile facts. The listing names every rule, domain and boundary of the accepted session. */
export interface BuilderVisualDraftAssemblyResult {
  readonly visual_draft: BuilderVisualDraftEnvelope;
  readonly assembled_draft: BuilderDraftEnvelope;
  readonly save_request: BuilderSessionSaveRequest;
  readonly compile_result: BuilderCompileResult;
  readonly listing: BuilderVisualSessionListing | null;
  readonly assembly_issues: ReadonlyArray<BuilderIssue>;
}

/**  */
export type BuilderVisualDraftCommandOperation = "place_space_reference" | "place_ground_reference" | "add_generated_space" | "set_space_population" | "author_inline_space_node" | "add_or_increment_node_terminal" | "set_node_terminal_role" | "add_node_ethernet_port" | "add_ground" | "add_ground_site_reference" | "set_ground_stamp_node" | "set_ground_site_node" | "add_ground_site_node" | "mint_ground_members" | "add_routing_domain" | "add_boundary" | "connect_segments" | "rederive_link" | "set_scheduling_preset";

/** Apply one typed command to an exact visual-draft revision. */
export interface BuilderVisualDraftCommandRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly command: BuilderVisualPlaceSpaceReferenceCommand | BuilderVisualPlaceGroundReferenceCommand | BuilderVisualAddGeneratedSpaceCommand | BuilderVisualSetSpacePopulationCommand | BuilderVisualAuthorInlineSpaceNodeCommand | BuilderVisualAddOrIncrementNodeTerminalCommand | BuilderVisualSetNodeTerminalRoleCommand | BuilderVisualAddNodeEthernetPortCommand | BuilderVisualAddGroundCommand | BuilderVisualAddGroundSiteReferenceCommand | BuilderVisualSetGroundStampNodeCommand | BuilderVisualSetGroundSiteNodeCommand | BuilderVisualAddGroundSiteNodeCommand | BuilderVisualMintGroundMembersCommand | BuilderVisualAddRoutingDomainCommand | BuilderVisualAddBoundaryCommand | BuilderVisualConnectSegmentsCommand | BuilderVisualRederiveLinkCommand | BuilderVisualSetSchedulingPresetCommand;
}

/** One applied command and the next revision of the complete draft. */
export interface BuilderVisualDraftCommandResult {
  readonly operation: BuilderVisualDraftCommandOperation;
  readonly base_draft_revision: number;
  readonly draft: BuilderVisualDraftEnvelope;
  readonly affected_kind: BuilderVisualDraftAffectedKind;
  readonly affected_id: string;
  readonly scheduling_preset: BuilderVisualSchedulingPreset | null;
  readonly notice: string | null;
}

/** Compile a complete visual draft through backend assembly and grammar authorities. With a workspace, the compile assembles that workspace over the draft and accepts nothing: the draft and its revision stay as they were. */
export interface BuilderVisualDraftCompileRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly workspace?: BuilderVisualWorkspace | null;
}

/** Request a backend-created blank structured visual draft. */
export interface BuilderVisualDraftCreateRequest {
  readonly session_name?: string | null;
  readonly display_name?: string | null;
  readonly description?: string | null;
}

/** One revision of a stateless YAML buffer plus explicit authoring and applied facts. */
export interface BuilderVisualDraftEnvelope {
  readonly draft_revision: number;
  readonly projection_status: BuilderVisualDraftProjectionStatus;
  readonly target_ref: SessionRef;
  readonly source_ref?: SessionRef | null;
  readonly expected_session_revision?: string | null;
  readonly catalog_documents?: ReadonlyArray<BuilderProposedCatalogDocument>;
  readonly session_name_is_placeholder: boolean;
  readonly reserved_authoring_ids: ReadonlyArray<string>;
  readonly session_yaml: string;
  readonly authoring_workspace?: BuilderVisualWorkspace | null;
  readonly applied_workspace?: BuilderVisualWorkspace | null;
  readonly applied_revision?: number | null;
  readonly applied_session?: Readonly<Record<string, JsonValue>> | null;
  readonly control_tree?: BuilderControlTree | null;
}

/** Open any stored session as synchronized YAML and graphical projections. */
export interface BuilderVisualDraftOpenRequest {
  readonly source_ref: SessionRef;
  readonly target_ref?: SessionRef | null;
}

/**  */
export type BuilderVisualDraftProjectionStatus = "applied" | "incomplete_authoring" | "no_valid_projection" | "pending_authoring";

/** Prepare a fenced draft for saving under one exact user session ref. */
export interface BuilderVisualDraftRetargetRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly target_ref: SessionRef;
}

/** Presentation facts for one canonical node forwarding class. */
export interface BuilderVisualForwardingClassMetadata {
  readonly id: ForwardingClass;
  readonly label: string;
}

/** Ground access-terminal pointing authored into a site installation. */
export interface BuilderVisualGroundBoresight {
  readonly mode: "local_vertical";
}

/** Editable ground segment assembled into site and site-set refs. */
export interface BuilderVisualGroundDraft {
  readonly segment_id?: string;
  readonly display_name?: string;
  readonly members?: ReadonlyArray<BuilderVisualGroundMember>;
  readonly stamp: BuilderVisualGroundStamp;
  readonly scheduling?: Readonly<Record<string, JsonValue>>;
  readonly originated_ipv4?: ReadonlyArray<string>;
  readonly tags?: ReadonlyArray<string>;
}

/** Referenced or authored member of an editable ground site set. */
export interface BuilderVisualGroundMember {
  readonly member_id?: string;
  readonly kind: "ref" | "draft";
  readonly ref?: string | null;
  readonly site_id?: string;
  readonly label?: string;
  readonly summary?: string | null;
  readonly site?: BuilderVisualSite | null;
  readonly scheduling_override?: Readonly<Record<string, JsonValue>> | null;
}

/** One library site set placed by reference with session scheduling. */
export interface BuilderVisualGroundReference {
  readonly segment_id?: string;
  readonly site_set_ref?: string | null;
  readonly label?: string;
  readonly scheduling?: Readonly<Record<string, JsonValue>>;
}

/** One user-entered surface location awaiting backend-owned site allocation. */
export interface BuilderVisualGroundSiteIntent {
  readonly name: string;
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly alt_m?: number;
}

/** Backend-issued minting facts retained as visual state, never persisted. */
export interface BuilderVisualGroundStamp {
  readonly node_ref?: string | null;
  readonly installed?: Readonly<Record<string, number>>;
  readonly boresights: Readonly<Record<string, BuilderVisualGroundBoresight>>;
  readonly body?: string | null;
}

/** Editable endpoint selector for one visual link rule. */
export interface BuilderVisualLinkEndpoint {
  readonly segment_id?: string;
  readonly tag?: string | null;
  readonly role?: MountRole | null;
  readonly medium?: TerminalMedium | null;
  readonly min_elevation_deg?: number | null;
}

/** Presentation facts for one canonical link-terminal medium. */
export interface BuilderVisualLinkMediumMetadata {
  readonly id: TerminalMedium;
  readonly label: string;
}

/** Editable physical link rule; every authored rule is assembled. */
export interface BuilderVisualLinkRule {
  readonly rule_id?: string;
  readonly enabled?: boolean;
  readonly a?: BuilderVisualLinkEndpoint;
  readonly b?: BuilderVisualLinkEndpoint;
  readonly topology_mode?: BuilderVisualTopologyMode | null;
  readonly topology_n?: number | null;
  readonly max_range_km?: number | null;
}

/** One link rule of the accepted session and the card value that writes it back. */
export interface BuilderVisualLinkRuleListing {
  readonly rule_id: string;
  readonly segment_ids: ReadonlyArray<string>;
  readonly widget: BuilderVisualLinkRule | null;
}

/** Mint complete sites and addresses from typed locations and one ground stamp. */
export interface BuilderVisualMintGroundMembersCommand {
  readonly operation: "mint_ground_members";
  readonly segment_id: string;
  readonly sites: ReadonlyArray<BuilderVisualGroundSiteIntent>;
}

/** Presentation facts for one canonical terminal-mount role. */
export interface BuilderVisualMountRoleMetadata {
  readonly id: MountRole;
  readonly label: string;
  readonly description: string;
}

/** Editable node object nested in a generated space component. */
export interface BuilderVisualNode {
  readonly id?: string;
  readonly display_name?: string;
  readonly forwarding?: ForwardingClass | null;
  readonly profile?: string | null;
  readonly ethernet?: ReadonlyArray<string>;
  readonly terminals?: ReadonlyArray<BuilderVisualTerminalMount>;
}

/** Editable orbital geometry for one generated constellation. */
export interface BuilderVisualOrbit {
  readonly central_body?: string | null;
  readonly shape_kind?: BuilderVisualOrbitShape | null;
  readonly altitude_km?: number | null;
  readonly perigee_altitude_km?: number | null;
  readonly apogee_altitude_km?: number | null;
  readonly inclination_deg?: number | null;
  readonly raan_deg?: number | null;
  readonly argument_of_perigee_deg?: number | null;
  readonly mean_anomaly_deg?: number | null;
  readonly propagator?: BuilderVisualOrbitPropagator | null;
}

/**  */
export type BuilderVisualOrbitPropagator = "two_body" | "j2_mean_elements";

/**  */
export type BuilderVisualOrbitShape = "circular" | "elliptical";

/** Presentation facts for one visual orbit-shape form. */
export interface BuilderVisualOrbitShapeMetadata {
  readonly id: BuilderVisualOrbitShape;
  readonly label: string;
}

/**  */
export type BuilderVisualPhasingMode = "walker_delta" | "walker_star" | "evenly_spaced_mean_anomaly";

/** Presentation facts for one canonical constellation phasing mode. */
export interface BuilderVisualPhasingModeMetadata {
  readonly id: BuilderVisualPhasingMode;
  readonly label: string;
}

/** Place one existing site set with backend-owned scheduling. */
export interface BuilderVisualPlaceGroundReferenceCommand {
  readonly operation: "place_ground_reference";
  readonly site_set_ref: string;
}

/** Place one existing constellation or space-node-set by catalog reference. */
export interface BuilderVisualPlaceSpaceReferenceCommand {
  readonly operation: "place_space_reference";
  readonly source_ref: string;
}

/** Repoint one link endpoint and explicitly rederive its physical seed. */
export interface BuilderVisualRederiveLinkCommand {
  readonly operation: "rederive_link";
  readonly rule_id: string;
  readonly side: "a" | "b";
  readonly segment_id: string;
}

/** Editable routing boundary assembled without omission. */
export interface BuilderVisualRoutingBoundary {
  readonly boundary_id?: string;
  readonly over_rule_id?: string;
  readonly adapter?: RoutingBoundaryAdapter | null;
  readonly from_domain_id?: string;
  readonly to_domain_id?: string;
  readonly export_node_loopbacks?: boolean;
}

/** One routing boundary of the accepted session, addressed by its position. */
export interface BuilderVisualRoutingBoundaryListing {
  readonly index: number;
  readonly over_rule_id: string;
  readonly domain_ids: ReadonlyArray<string>;
  readonly widget: BuilderVisualRoutingBoundary | null;
}

/** Editable routing domain; empty membership remains visible to validation. */
export interface BuilderVisualRoutingDomain {
  readonly domain_id?: string;
  readonly protocol?: RoutingProtocol | null;
  readonly member_segment_ids?: ReadonlyArray<string>;
  readonly hello_interval_s?: number | null;
  readonly hold_interval_s?: number | null;
}

/** One routing domain of the accepted session and the card value that writes it back. */
export interface BuilderVisualRoutingDomainListing {
  readonly domain_id: string;
  readonly protocol: RoutingProtocol;
  readonly segment_ids: ReadonlyArray<string>;
  readonly widget: BuilderVisualRoutingDomain | null;
}

/** Presentation and runtime facts for one routing protocol. */
export interface BuilderVisualRoutingProtocolMetadata {
  readonly runtime_supported: boolean;
  readonly support_note: string | null;
  readonly id: RoutingProtocol;
  readonly label: string;
  readonly timer_fields: boolean;
}

/**  */
export type BuilderVisualSchedulingPreset = "leo-fast-handover" | "geo-longest-pass";

/** Typed presentation metadata for one backend-owned scheduling preset. */
export interface BuilderVisualSchedulingPresetMetadata {
  readonly id: BuilderVisualSchedulingPreset;
  readonly label: string;
}

/** Every link rule, routing domain and boundary the accepted session declares, in order. The backend derives it from the accepted session and never reads it back. A null widget means no card writes that object back unchanged. */
export interface BuilderVisualSessionListing {
  readonly link_rules: ReadonlyArray<BuilderVisualLinkRuleListing>;
  readonly routing_domains: ReadonlyArray<BuilderVisualRoutingDomainListing>;
  readonly boundaries: ReadonlyArray<BuilderVisualRoutingBoundaryListing>;
}

/** Select one authored site's node model and derive its installed inventory. */
export interface BuilderVisualSetGroundSiteNodeCommand {
  readonly operation: "set_ground_site_node";
  readonly segment_id: string;
  readonly member_id: string;
  readonly node_id: string;
  readonly node_ref: string;
}

/** Select a ground stamp node and derive its installed terminal inventory. */
export interface BuilderVisualSetGroundStampNodeCommand {
  readonly operation: "set_ground_stamp_node";
  readonly segment_id: string;
  readonly node_ref: string;
}

/** Change one inline-node mount role with backend-owned pointing semantics. */
export interface BuilderVisualSetNodeTerminalRoleCommand {
  readonly operation: "set_node_terminal_role";
  readonly segment_id: string;
  readonly mount_id: string;
  readonly role: MountRole;
}

/** Apply one complete backend-owned scheduling block or inherit at a site. */
export interface BuilderVisualSetSchedulingPresetCommand {
  readonly operation: "set_scheduling_preset";
  readonly segment_id: string;
  readonly preset: BuilderVisualSchedulingPreset | null;
  readonly member_id?: string | null;
}

/** Change one population input and let the backend derive its complete phasing. */
export interface BuilderVisualSetSpacePopulationCommand {
  readonly operation: "set_space_population";
  readonly segment_id: string;
  readonly phasing_mode?: BuilderVisualPhasingMode | null;
  readonly planes?: number | null;
  readonly slots_per_plane?: number | null;
}

/** Editable complete site object. */
export interface BuilderVisualSite {
  readonly site_id?: string;
  readonly display_name?: string;
  readonly body?: string | null;
  readonly lat_deg?: number | null;
  readonly lon_deg?: number | null;
  readonly alt_m?: number | null;
  readonly tags?: ReadonlyArray<string>;
  readonly nodes?: ReadonlyArray<BuilderVisualSiteNode>;
}

/** One installed node in an editable site object. */
export interface BuilderVisualSiteNode {
  readonly node_id?: string;
  readonly node_ref?: string | null;
  readonly installed?: Readonly<Record<string, number>>;
  readonly boresights: Readonly<Record<string, BuilderVisualGroundBoresight>>;
}

/** Spacecraft access-terminal pointing authored into a node mount. */
export interface BuilderVisualSpaceBoresight {
  readonly mode: "nadir";
}

/** Editable generated space segment that becomes referenced catalog objects. */
export interface BuilderVisualSpaceDraft {
  readonly segment_id?: string;
  readonly display_name?: string;
  readonly node_ref?: string | null;
  readonly node_draft?: BuilderVisualNode | null;
  readonly orbit?: BuilderVisualOrbit;
  readonly planes?: number | null;
  readonly raan_spacing_deg?: number | null;
  readonly slots_per_plane?: number | null;
  readonly phasing_mode: BuilderVisualPhasingMode;
  readonly phase_offset_deg?: number | null;
}

/** One library space source placed by reference. */
export interface BuilderVisualSpaceReference {
  readonly segment_id?: string;
  readonly source_ref?: string | null;
  readonly label?: string;
}

/** Editable terminal mount on a visual node draft. */
export interface BuilderVisualTerminalMount {
  readonly mount_id?: string;
  readonly role?: MountRole | null;
  readonly terminal_ref?: string | null;
  readonly count?: number | null;
  readonly boresight?: BuilderVisualSpaceBoresight | null;
}

/**  */
export type BuilderVisualTopologyMode = "visible_candidates" | "nearest_n";

/** Presentation and runtime facts for one visual link topology mode. */
export interface BuilderVisualTopologyModeMetadata {
  readonly runtime_supported: boolean;
  readonly support_note: string | null;
  readonly id: BuilderVisualTopologyMode;
  readonly label: string;
  readonly requires_n: boolean;
}

/** Walker population intent whose derived angular values remain backend-owned. */
export interface BuilderVisualWalkerLayoutRequest {
  readonly pattern: "walker_delta" | "walker_star";
  readonly planes: number;
  readonly slots_per_plane: number;
}

/** Backend-issued angular layout for one Walker population intent. */
export interface BuilderVisualWalkerLayoutResult {
  readonly raan_spacing_deg: number;
  readonly phase_offset_deg: number;
}

/** Closed visual workspace whose persisted grammar is assembled by VS-API. */
export interface BuilderVisualWorkspace {
  readonly session_name?: string;
  readonly display_name?: string | null;
  readonly description?: string | null;
  readonly space?: ReadonlyArray<BuilderVisualSpaceDraft>;
  readonly space_refs?: ReadonlyArray<BuilderVisualSpaceReference>;
  readonly ground?: ReadonlyArray<BuilderVisualGroundDraft>;
  readonly ground_refs?: ReadonlyArray<BuilderVisualGroundReference>;
  readonly links?: ReadonlyArray<BuilderVisualLinkRule>;
  readonly routing_domains?: ReadonlyArray<BuilderVisualRoutingDomain>;
  readonly boundaries?: ReadonlyArray<BuilderVisualRoutingBoundary>;
  readonly max_pairs_per_rule?: number | null;
  readonly max_pairs_per_tick?: number | null;
  readonly start_time?: string;
  readonly step_seconds?: number | null;
  readonly compression?: number | null;
}

/** A session list whose members carry no id, edited as one whole. */
export interface BuilderWholeListAddress {
  readonly kind: "whole_list";
  readonly pointer: BuilderWholeListPointer;
}

/**  */
export type BuilderWholeListPointer = "/routing/boundaries";

/** One resolved session as a render-ready, read-only world. */
export interface BuilderWorld {
  readonly session: SessionMeta;
  readonly epoch_unix: number;
  readonly ephemeris: SessionEphemeris;
  readonly nodes: ReadonlyArray<BuilderWorldNode>;
  readonly link_rules: ReadonlyArray<BuilderLinkRule>;
  readonly segments: ReadonlyArray<BuilderWorldSegment>;
  readonly allocations: ReadonlyArray<BuilderRuleAllocation>;
  readonly rule_previews: ReadonlyArray<BuilderRulePreview>;
}

/** One resolved node's facts for builder display, mirrored from ``ResolvedNode``. ``kind`` is carried explicitly so no consumer infers it from the ephemeris variant shape. Satellite placement (orbital elements, frames) lives in the ephemeris; ``epoch_position`` is the OME-propagated state used to seed renderers that cannot propagate that ephemeris variant locally. Ground placement is the resolver's ``surface_position``: the ephemeris only carries ground nodes that participate in space-link physics, while the world contains every resolved node — a gateway with no space links still exists. Hardware and network facts reuse the resolved models verbatim — they are the wire truth; the builder never re-shapes them. */
export interface BuilderWorldNode {
  readonly node_id: string;
  readonly local_node_id: string;
  readonly segment_id: string;
  readonly namespace: string;
  readonly tenant_id: string;
  readonly kind: NodeKind;
  readonly plane: number | null;
  readonly slot: number | null;
  readonly tags: ReadonlyArray<string>;
  readonly surface_position: ResolvedSurfacePosition | null;
  readonly epoch_position: NodePosition | null;
  readonly forwarding: "routed" | "host" | "bridge" | "control_only" | null;
  readonly role: NodeRole;
  readonly routing_instances: ReadonlyArray<NodeRoutingInstance>;
  readonly terminal_inventory: ReadonlyArray<ResolvedTerminalBlock>;
  readonly interfaces: ResolvedNodeInterfaces | null;
  readonly originated_prefixes: ResolvedOriginatedPrefixes | null;
}

/** One segment as the user named it — the world tree speaks their words, never bare runtime ids. ``capabilities`` lists each pair of terminal role and medium that the resolved terminal blocks of the segment's nodes carry, once per pair. */
export interface BuilderWorldSegment {
  readonly segment_id: string;
  readonly display_name: string;
  readonly capabilities: ReadonlyArray<BuilderSegmentCapability>;
}

/**  */
export type CatalogAuthoringRefusalCode = "catalog_authoring.not_found" | "catalog_authoring.read_only" | "catalog_authoring.invalid_document" | "catalog_authoring.invalid_patch" | "catalog_authoring.invalid_graph" | "catalog_authoring.conflict" | "catalog_authoring.stale_revision" | "catalog_authoring.invalid_page_token" | "catalog_authoring.stale_page_token" | "catalog_authoring.impact_mismatch" | "catalog_authoring.dependents_exist" | "catalog_authoring.import_limit" | "catalog_authoring.import_incomplete" | "catalog_authoring.import_collision" | "catalog_authoring.stale_import_proposal" | "catalog_authoring.persistence_failed";

/** One component draft state with the backend's projection of it. */
export interface CatalogComponentDraftEnvelope {
  readonly state: CatalogComponentDraftState;
  readonly projected_yaml: string;
  readonly control_tree: BuilderControlTree;
  readonly issues: ReadonlyArray<CatalogDraftIssue>;
}

/** One revision of a full-document component draft: the state every draft request carries. */
export interface CatalogComponentDraftState {
  readonly draft_revision: number;
  readonly family: CatalogComponentFamily;
  readonly target_ref: CatalogRef;
  readonly source_ref?: CatalogRef | null;
  readonly expected_source_revision?: string | null;
  readonly expected_target_revision?: string | null;
  readonly document: Readonly<Record<string, JsonValue>>;
}

/**  */
export type CatalogComponentFamily = "bodies" | "terminals" | "payloads" | "orbits" | "nodes" | "sites" | "site-sets" | "constellations" | "space-node-sets";

/** Fenced deletion request bound to exact bytes and reviewed graph impact. */
export interface CatalogDeleteRequest {
  readonly ref: CatalogRef;
  readonly expected_revision: string;
  readonly impact_acknowledgement: Sha256Digest;
}

/** Evidence that one exact user document was removed atomically. */
export interface CatalogDeleteResult {
  readonly deleted_ref: CatalogRef;
  readonly deleted_revision: string;
  readonly impact_acknowledgement: Sha256Digest;
  readonly generation: string;
}

/** Current typed reverse graph used for overwrite review and delete fencing. */
export interface CatalogDependencyImpact {
  readonly target_ref: CatalogRef;
  readonly target_revision: string;
  readonly direct_dependents: ReadonlyArray<CatalogDependent>;
  readonly transitive_dependents: ReadonlyArray<CatalogDependent>;
  readonly overwrite_affects_dependents: boolean;
  readonly delete_allowed: boolean;
  readonly acknowledgement: Sha256Digest;
}

/** One typed reverse dependency and its shortest distance from the target. */
export interface CatalogDependent {
  readonly ref: CatalogRef;
  readonly family: CatalogFamily;
  readonly revision: string;
  readonly minimum_depth: number;
}

/** Request current overwrite and delete impact for one exact catalog ref. */
export interface CatalogDependentsRequest {
  readonly ref: CatalogRef;
}

/** Revisioned metadata for one catalog library row. */
export interface CatalogDocumentSummary {
  readonly ref: CatalogRef;
  readonly namespace: CatalogNamespace;
  readonly family: CatalogFamily;
  readonly revision: string;
  readonly size_bytes: number;
  readonly display_name: string;
  readonly summary: string | null;
}

/** Create or compare-and-swap replace one complete user component. */
export interface CatalogDocumentWriteRequest {
  readonly ref: CatalogRef;
  readonly document: Readonly<Record<string, JsonValue>>;
  readonly expected_revision: string | null;
}

/** Add one node Ethernet port with a backend-generated unique identifier. */
export interface CatalogDraftAddNodeEthernetPortRequest {
  readonly draft: CatalogComponentDraftState;
  readonly expected_draft_revision: number;
}

/** Add one node terminal mount with backend-generated persisted fields. */
export interface CatalogDraftAddNodeTerminalMountRequest {
  readonly draft: CatalogComponentDraftState;
  readonly expected_draft_revision: number;
  readonly terminal_ref: string;
  readonly role: MountRole;
}

/** Add one explicitly identified node using backend-derived persisted fields. */
export interface CatalogDraftAddSiteNodeRequest {
  readonly draft: CatalogComponentDraftState;
  readonly expected_draft_revision: number;
  readonly node_id: string;
  readonly node_ref: string;
}

/** Apply one exact YAML buffer under backend grammar authority. */
export interface CatalogDraftApplyYamlRequest {
  readonly draft: CatalogComponentDraftState;
  readonly expected_draft_revision: number;
  readonly yaml_text: string;
}

/** Applied projection or typed refusal for one exact YAML buffer. */
export interface CatalogDraftApplyYamlResult {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly yaml_text: string;
  readonly applied: boolean;
  readonly canonicalization_required: boolean;
  readonly issues: ReadonlyArray<CatalogDraftIssue>;
}

/**  */
export type CatalogDraftBlockedOperation = "save" | "deploy";

/** Validate one exact draft without mutating catalog persistence. */
export interface CatalogDraftCompileRequest {
  readonly draft: CatalogComponentDraftState;
  readonly expected_draft_revision: number;
}

/** Canonical component bytes plus structural, reference, and support findings. */
export interface CatalogDraftCompileResult {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly save_allowed: boolean;
  readonly runtime_supported: boolean;
  readonly canonical_yaml: string | null;
  readonly canonical_json: Readonly<Record<string, JsonValue>> | null;
  readonly content_digest: Sha256Digest | null;
  readonly issues: ReadonlyArray<CatalogDraftIssue>;
}

/** Apply canonical graphical commands under one component draft revision. */
export interface CatalogDraftControlMutationRequest {
  readonly draft: CatalogComponentDraftState;
  readonly expected_draft_revision: number;
  readonly commands: ReadonlyArray<BuilderSetScalarCommand | BuilderSetPresentCommand | BuilderSelectChoiceCommand | BuilderInsertItemCommand | BuilderRemoveItemCommand | BuilderMoveItemCommand | BuilderInsertMapEntryCommand | BuilderRemoveMapEntryCommand | BuilderRenameMapKeyCommand>;
}

/** One backend-produced component-draft finding at an exact JSON pointer. */
export interface CatalogDraftIssue {
  readonly code: string;
  readonly stage: CatalogDraftIssueStage;
  readonly message: string;
  readonly pointer: string;
  readonly source_line: number | null;
  readonly source_column: number | null;
  readonly blocks: ReadonlyArray<CatalogDraftBlockedOperation>;
}

/**  */
export type CatalogDraftIssueStage = "structural" | "reference" | "runtime_support";

/** Create one new, intentionally incomplete user-owned component draft. */
export interface CatalogDraftNewRequest {
  readonly family: CatalogComponentFamily;
  readonly object_id: string;
}

/** Open one complete component and optionally select a user-owned fork target. */
export interface CatalogDraftOpenRequest {
  readonly source_ref: CatalogRef;
  readonly target_ref?: CatalogRef | null;
}

/** Persist one structurally valid component draft through scoped CAS storage. */
export interface CatalogDraftSaveRequest {
  readonly draft: CatalogComponentDraftState;
  readonly expected_draft_revision: number;
}

/** Saved canonical component, updated draft revision fence, and graph impact. */
export interface CatalogDraftSaveResult {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly result: CatalogMutationResult;
  readonly compile_result: CatalogDraftCompileResult;
}

/**  */
export type CatalogFamily = "bodies" | "terminals" | "payloads" | "profiles" | "orbits" | "nodes" | "sites" | "site-sets" | "constellations" | "space-node-sets" | "sessions";

/** Backend-owned authoring capabilities for one public catalog family. */
export interface CatalogFamilyMetadata {
  readonly family: CatalogFamily;
  readonly wrapper: string | null;
  readonly direct_user_write: boolean;
  readonly component_fork: boolean;
  readonly session_draft_save: boolean;
  readonly suggested_object_id: string | null;
}

/** Request one catalog document by namespace-qualified identity. */
export interface CatalogGetRequest {
  readonly ref: CatalogRef;
}

/**  */
export type CatalogImportCollisionReason = "shipped_missing" | "shipped_content_mismatch" | "user_content_mismatch";

/**  */
export type CatalogImportOutcome = "proposed" | "committed" | "unchanged" | "blocked";

/** One deterministic page pinned to an immutable catalog generation. */
export interface CatalogListPage {
  readonly generation: string;
  readonly items: ReadonlyArray<CatalogDocumentSummary>;
  readonly next_page_token: string | null;
}

/** Bounded catalog-list request using an opaque server cursor. */
export interface CatalogListRequest {
  readonly family?: CatalogFamily | null;
  readonly namespace?: CatalogNamespace | null;
  readonly page_size?: number;
  readonly page_token?: string | null;
}

/** Canonical saved component plus its current downstream impact. */
export interface CatalogMutationResult {
  readonly document: BuilderCatalogDocument;
  readonly impact: CatalogDependencyImpact;
}

/**  */
export type CatalogNamespace = "nodalarc" | "user";

/** Stable typed evidence for an authoring operation refused by the backend. */
export interface CatalogOperationRefusal {
  readonly code: CatalogAuthoringRefusalCode;
  readonly message: string;
  readonly ref: string | null;
  readonly expected_revision: string | null;
  readonly current_revision: string | null;
  readonly impact: CatalogDependencyImpact | null;
  readonly collisions: ReadonlyArray<CatalogYamlImportCollision>;
  readonly cause_type: string | null;
}

/** First-class catalog session selected inside a server-owned scope. */
export interface CatalogSessionSourceId {
  readonly kind?: "catalog";
  readonly session_ref: SessionRef;
}

/** Revisioned browser listing for one scoped catalog session. */
export interface CatalogSessionSummary {
  readonly source_id: CatalogSessionSourceId;
  readonly name: string;
  readonly source: "nodalarc" | "user";
  readonly constellation: string;
  readonly routing_stack: string;
  readonly deploy_allowed: boolean;
  readonly source_revision: Sha256Digest | null;
  readonly document_digest: Sha256Digest | null;
  readonly dependency_digest: Sha256Digest | null;
  readonly blockers: ReadonlyArray<ApiRefusal>;
  readonly active: boolean;
}

/** Accepted catalog deployment operation. */
export interface CatalogSessionSwitchAccepted {
  readonly status: "accepted";
  readonly operation_id: string;
  readonly source: CatalogSessionSourceId;
}

/** Deploy one reviewed session revision from the request catalog scope. */
export interface CatalogSessionSwitchRequest {
  readonly source: CatalogSessionSourceId;
  readonly expected_source_revision: Sha256Digest;
  readonly expected_document_digest: Sha256Digest;
  readonly expected_dependency_digest: Sha256Digest;
  readonly record_history: boolean;
}

/** Ordinary root and catalog YAML files for browser-side file writing. */
export interface CatalogSessionYamlExport {
  readonly session_ref: SessionRef;
  readonly session_revision: string;
  readonly files: ReadonlyArray<CatalogYamlFile>;
}

/** Request ordinary YAML files for one saved session. */
export interface CatalogSessionYamlExportRequest {
  readonly session_ref: SessionRef;
  readonly expected_session_revision?: string | null;
}

/** Ordinary YAML files proposed for identity-derived catalog import. */
export interface CatalogSessionYamlImportRequest {
  readonly yaml_files: ReadonlyArray<CatalogYamlImportFile>;
  readonly commit?: boolean;
  readonly proposal_token?: string | null;
}

/** Proposed, committed, unchanged, or blocked YAML-file import result. */
export interface CatalogSessionYamlImportResult {
  readonly root_ref: SessionRef;
  readonly outcome: CatalogImportOutcome;
  readonly generation: string;
  readonly proposal_token: string | null;
  readonly proposed_writes: ReadonlyArray<CatalogYamlImportWrite>;
  readonly identical_refs: ReadonlyArray<CatalogRef>;
  readonly collisions: ReadonlyArray<CatalogYamlImportCollision>;
}

/** The scoped catalog session whose stored YAML to download. */
export interface CatalogSessionYamlQuery {
  readonly session_ref: string;
}

/** One standard persisted session document to save into the user catalog. */
export interface CatalogSessionYamlUploadRequest {
  readonly yaml: string;
  readonly record_history: boolean;
}

/** One ordinary YAML file and its backend-derived logical path. */
export interface CatalogYamlFile {
  readonly logical_path: string;
  readonly yaml_text: string;
}

/** Exact identity collision that import will never rename or deep-match. */
export interface CatalogYamlImportCollision {
  readonly ref: CatalogRef;
  readonly reason: CatalogImportCollisionReason;
  readonly existing_revision: string | null;
}

/** One YAML text with an optional graph-verified placement hint. */
export interface CatalogYamlImportFile {
  readonly yaml_text: string;
  readonly logical_path_hint?: string | null;
}

/** One canonical user document proposed for transactional creation. */
export interface CatalogYamlImportWrite {
  readonly ref: CatalogRef;
  readonly family: CatalogFamily;
  readonly logical_path: string;
  readonly canonical_yaml: string;
  readonly canonicalization_changed: boolean;
}

/** A single insight about the constellation configuration. Severity levels: - "info": expected physics behavior, normal operation (e.g., Earth occlusion, range limits) - "note": topology characteristic worth knowing (e.g., full mesh, terminal allocation) - "warning": potential issue that may affect routing (e.g., tracking rate dropouts, coverage gaps) - "error": configuration problem that will prevent connectivity (e.g., no cross-plane links, station beyond visibility) */
export interface CoverageInsight {
  readonly severity: "info" | "note" | "warning" | "error";
  readonly message: string;
}

/** Complete coverage preview result. */
export interface CoveragePreviewResult {
  readonly orbital_period_s: number;
  readonly preview_step_s: number;
  readonly isl: IslPreview;
  readonly ground_stations: GsPreview;
  readonly warnings: ReadonlyArray<CoverageInsight>;
}

/** One backend-discovered document in a transitive reference closure. */
export interface DependencyClosureEntry {
  readonly ref: CatalogRef;
  readonly family: CatalogFamily;
  readonly revision: string | null;
  readonly document_digest: Sha256Digest;
  readonly preserved_path: string;
  readonly size_bytes: number;
}

/** Response-only facts for the exact dependency closure found by the backend. */
export interface DependencyClosureInventory {
  readonly entries: ReadonlyArray<DependencyClosureEntry>;
  readonly file_count: number;
  readonly total_bytes: number;
  readonly closure_digest: Sha256Digest;
}

/** Body origin in the session common frame at ``epoch_unix``. Positions and velocities are Earth-relative GCRS-like km vectors supplied by the backend ephemeris provider. The renderer may apply a visual scale or camera-relative transform, but these numbers remain the authoritative physical frame facts used to place bodies relative to one another. */
export interface EphemerisBodyFrame {
  readonly body_id: SupportedSurfaceBody;
  readonly mean_radius_km: number;
  readonly equatorial_radius_km: number;
  readonly polar_radius_km: number;
  readonly gravitational_parameter_km3_s2: number;
  readonly rotation_rate_rad_s: number;
  readonly j2: number;
  readonly origin_x_km: number;
  readonly origin_y_km: number;
  readonly origin_z_km: number;
  readonly vel_x_km_s: number;
  readonly vel_y_km_s: number;
  readonly vel_z_km_s: number;
  readonly provider: string;
  readonly kernel_id: string;
  readonly quality_tier: string;
  readonly frame: string;
}

/** Fixed geodetic position for a ground station. */
export interface EphemerisNodeFixed {
  readonly type: "fixed";
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly alt_km: number;
  readonly segment_id: string;
  readonly local_node_id: string;
  readonly namespace: string;
  readonly tags: ReadonlyArray<string>;
  readonly reference_body: SupportedSurfaceBody;
  readonly frame_id: SupportedSurfaceBody;
}

/** Mean-element ephemeris for a satellite. Fields mirror ``nodalarc.orbital.OrbitalElements``. The ``propagator`` field is part of the contract because the same element fields can drive either the two-body Keplerian engine or the J2 mean-element engine. Consumers must not silently treat J2 sessions as Keplerian. */
export interface EphemerisNodeKeplerian {
  readonly type: "keplerian";
  readonly propagator: "two-body" | "keplerian-circular" | "j2-mean-elements";
  readonly semi_major_axis_km: number;
  readonly eccentricity: number;
  readonly inclination_deg: number;
  readonly raan_deg: number;
  readonly argument_of_perigee_deg: number;
  readonly mean_anomaly_deg: number;
  readonly plane: number;
  readonly slot: number;
  readonly segment_id: string;
  readonly local_node_id: string;
  readonly namespace: string;
  readonly tags: ReadonlyArray<string>;
  readonly reference_body: SupportedSurfaceBody;
  readonly frame_id: SupportedSurfaceBody;
}

/** TLE-backed satellite ephemeris for SGP4 propagation. */
export interface EphemerisNodeTLE {
  readonly type: "tle";
  readonly tle_line_1: string;
  readonly tle_line_2: string;
  readonly plane: number;
  readonly slot: number;
  readonly norad_id: number | null;
  readonly segment_id: string;
  readonly local_node_id: string;
  readonly namespace: string;
  readonly tags: ReadonlyArray<string>;
  readonly reference_body: SupportedSurfaceBody;
  readonly frame_id: SupportedSurfaceBody;
}

/**  */
export type ForwardingClass = "routed" | "host" | "bridge" | "control_only";

/**  */
export type GroundBoresightMode = "local_vertical" | "configured_topocentric" | "steerable_envelope";

/** Ground station coverage statistics for one orbital period. */
export interface GsPreview {
  readonly per_station: Readonly<Record<string, GsStationPreview>>;
  readonly simultaneous_min: number;
  readonly simultaneous_max: number;
  readonly simultaneous_mean: number;
  readonly max_gap_s: number;
}

/** Per-ground-station coverage statistics. The preview samples one finite window; nothing here asserts periodic recurrence. ``longest_gap_s`` is the longest contiguous disconnected run inside that window. Leading and trailing runs count as sampled, and are never merged across the window boundary. ``total_disconnected_s`` is the window's disconnected time summed across every gap. */
export interface GsStationPreview {
  readonly coverage_pct: number;
  readonly longest_gap_s: number;
  readonly total_disconnected_s: number;
  readonly reason: string | null;
}

/** Why ISLs fail to form — per-reason counts from visibility checks. */
export interface IslFailureBreakdown {
  readonly range_exceeded: number;
  readonly tracking_exceeded: number;
  readonly field_of_regard: number;
  readonly los_blocked: number;
  readonly polar_seam: number;
  readonly terminal_exhausted: number;
}

/** ISL link feasibility statistics for one orbital period. */
export interface IslPreview {
  readonly total_possible: number;
  readonly formed_at_least_once: number;
  readonly never_formed: number;
  readonly feasibility_pct: number;
  readonly min_active: number;
  readonly max_active: number;
  readonly failure_reasons: IslFailureBreakdown | null;
}

/**  */
export type LinkLabel = "access" | "isl" | "inter_body";

/**  */
export type MountRole = "access" | "isl" | "crosslink" | "backbone";

/** One interface a node runs a routing instance on. */
export interface NodeInstanceInterface {
  readonly name: string;
  readonly area_id: string | null;
}

/**  */
export type NodeKind = "satellite" | "ground_station";

/** Position and velocity of a single node. Position is geodetic (WGS84). Velocity is ECEF (Earth-Centered Earth-Fixed) in km/s — includes Earth rotation subtraction, so it represents motion relative to the rotating Earth. Ground stations have zero velocity. The frontend's worldVelocity() function in astronomy.ts expects ECEF velocity and applies the view-frame rotation to produce world-frame velocity. */
export interface NodePosition {
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly alt_km: number;
  readonly vel_x_km_s: number;
  readonly vel_y_km_s: number;
  readonly vel_z_km_s: number;
}

/**  */
export type NodeRole = "router" | "host" | "forwarding_only";

/** A node's participation in one routing instance (a ``routing.domains`` entry). */
export interface NodeRoutingInstance {
  readonly domain_id: string;
  readonly protocol: RoutingProtocol;
  readonly areas: ReadonlyArray<string>;
  readonly interfaces: ReadonlyArray<NodeInstanceInterface>;
  readonly area_border: boolean;
  readonly as_boundary: boolean;
}

/**  */
export type PreviewScope = "computed" | "inter_body_pending" | "terrestrial_pending" | "disabled";

/** A numbered interface address set. */
export interface ResolvedInterfaceAddress {
  readonly ipv4: string | null;
  readonly ipv6: string | null;
}

/** Numbered interfaces allocated by the resolver. `ethernet` maps interface name to addresses: a node environment's own declared port ids, or a mounted payload's single attach-named interface. Every address is allocated on its segment's allocated subnet; nothing here is authored. */
export interface ResolvedNodeInterfaces {
  readonly lo0: ResolvedInterfaceAddress;
  readonly ethernet: Readonly<Record<string, ResolvedInterfaceAddress>>;
}

/** Concrete routing-injection facts, resolved from symbolic intent. Authored origination names segments; resolution replaces each name with the segment's allocated subnet (and `default` with the default route), so every downstream consumer reads literal prefixes. */
export interface ResolvedOriginatedPrefixes {
  readonly ipv4: ReadonlyArray<string> | null;
  readonly ipv6: ReadonlyArray<string> | null;
}

/** Fixed body-surface position for one placed node. */
export interface ResolvedSurfacePosition {
  readonly body: SupportedSurfaceBody;
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly alt_m: number;
}

/** Materialized terminal truth for one terminal block on one node. Built from the resolved satellite_type (satellites) or station/ground-set terminal config (ground stations). Consumers read this; they do not reload the source file. The catalog requires every physical fact a block carries (capacity, range, elevation limit, field of regard, tracking rate and both rates), so every block carries them. ``boresight`` exists exactly on access terminals. */
export interface ResolvedTerminalBlock {
  readonly terminal_id: string;
  readonly owner_node_id: string;
  readonly endpoint_role: MountRole;
  readonly medium: TerminalMediumLiteral;
  readonly source_terminal_id: string | null;
  readonly link_role: string | null;
  readonly count: number;
  readonly tracking_capacity: number;
  readonly max_range_km: number;
  readonly min_elevation_deg: number;
  readonly field_of_regard_deg: number;
  readonly tracking_rate_deg_s: number;
  readonly transmit_mbps: number;
  readonly receive_mbps: number;
  readonly boresight: TerminalBoresight | SatGroundTerminalBoresight | null;
  readonly source_ref: string;
}

/**  */
export type RoutingBoundaryAdapter = "static_ip" | "bgp" | "dtn_bundle";

/**  */
export type RoutingProtocol = "isis" | "ospf" | "bgp" | "static";

/**  */
export type SatGroundBoresightMode = "nadir";

/** Boresight reference for a satellite ground-terminal FoR cone. ``target_body`` identifies which body the nadir vector points toward. The cone width itself lives on the terminal's ``field_of_regard_deg`` field as the full apex angle. */
export interface SatGroundTerminalBoresight {
  readonly target_body: SupportedSurfaceBody;
  readonly mode: SatGroundBoresightMode;
}

/** Orbital elements for all nodes, distributed once per epoch. Published to NODALARC_SESSION stream (MaxMsgsPerSubject=1) at session start (epoch_id=0) and immediately after each Tier 2 seek. Late-joining subscribers always get the current ephemeris. Edges instantiate local propagators from this payload and compute positions on demand. No per-tick position data is broadcast. */
export interface SessionEphemeris {
  readonly epoch_id: number;
  readonly sim_time: string;
  readonly epoch_unix: number;
  readonly nodes: Readonly<Record<string, EphemerisNodeKeplerian | EphemerisNodeTLE | EphemerisNodeFixed>>;
  readonly body_frames: Readonly<Record<string, EphemerisBodyFrame>>;
}

/**  */
export interface SessionMeta {
  readonly name: string;
  readonly display_name: string | null;
  readonly description: string | null;
}

/**  */
export type SupportedSurfaceBody = "earth" | "luna";

/** Boresight reference for a ground terminal field-of-regard cone. The cone width itself lives on the terminal's ``field_of_regard_deg`` field as the full apex angle. This model only states what direction that cone is centered on. */
export interface TerminalBoresight {
  readonly mode: GroundBoresightMode;
  readonly configured_az_deg: number | null;
  readonly configured_el_deg: number | null;
  readonly min_az_deg: number | null;
  readonly max_az_deg: number | null;
  readonly min_el_deg: number | null;
  readonly max_el_deg: number | null;
}

/**  */
export type TerminalMedium = "rf" | "optical";

/**  */
export type TerminalMediumLiteral = "rf" | "optical";

/**  */
export type TopologyMode = "visible_candidates" | "nearest_visible" | "nearest_n" | "explicit_pairs";

/** Public, path-free representation returned for an opaque operation ID. */
export interface TransitionOperation {
  readonly operation_id: string;
  readonly state: TransitionOperationState;
  readonly source: TransitionOperationSource;
  readonly facts: TransitionOperationFacts;
  readonly created_at: string;
  readonly updated_at: string;
  readonly events: ReadonlyArray<TransitionOperationEvent>;
  readonly failure: TransitionOperationFailure | null;
  readonly runtime: TransitionRuntimeResult | null;
}

/**  */
export interface TransitionOperationEvent {
  readonly state: TransitionOperationState;
  readonly occurred_at: string;
  readonly detail: string | null;
}

/** Reviewed, browser-safe facts bound to one operation. */
export interface TransitionOperationFacts {
  readonly document_digest: Sha256Digest | null;
  readonly closure_digest: Sha256Digest | null;
  readonly resolved_semantic_digest: Sha256Digest | null;
  readonly file_count: number | null;
  readonly total_bytes: number | null;
  readonly release: string;
  readonly build: string;
}

/**  */
export interface TransitionOperationFailure {
  readonly code: string;
  readonly message: string;
  readonly cause_type: string | null;
}

/**  */
export interface TransitionOperationSource {
  readonly kind: TransitionOperationSourceKind;
  readonly logical_id: string;
}

/** Path-free source classes admitted by VS-API. */
export type TransitionOperationSourceKind = "catalog_session";

/** Persisted lifecycle states for one admitted session transition. */
export type TransitionOperationState = "reserved" | "collecting" | "uploading" | "verifying" | "switching" | "succeeded" | "failed" | "cancelled";

/**  */
export interface TransitionRuntimeResult {
  readonly session_id: string;
  readonly generation: number;
}

/**  */
export type WizardAreaStrategy = "flat" | "stripe" | "per_plane";

/** One located catalog site available to a custom Wizard site set. */
export interface WizardAvailableStation {
  readonly name: string;
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly file: string;
}

/** Closed catalog-site response for custom Wizard ground selection. */
export interface WizardAvailableStationResponse {
  readonly stations: ReadonlyArray<WizardAvailableStation>;
}

/** Backend-owned presentation facts for the Wizard BFD controls. The timer controls and their bounds belong to each protocol, because the bounds are what the runtime renders for that protocol. */
export interface WizardBfdMetadata {
  readonly heading: string;
  readonly enabled_field: WizardRoutingBooleanField;
  readonly enable_label: string;
  readonly enable_description: string;
}

/** Stable refusal when Wizard intent cannot become a Builder draft. */
export interface WizardCompileRefusal {
  readonly code: "wizard_compile.invalid_selection" | "wizard_compile.reference_error" | "wizard_compile.repository_unavailable";
  readonly message: string;
  readonly cause_type: string | null;
}

/** Request backend construction and compilation of one Wizard intent. */
export interface WizardCompileRequest {
  readonly draft_revision: number;
  readonly intent: WizardSessionIntent;
}

/** Backend-owned runtime capability for one Wizard space source. */
export interface WizardConstellationCapability {
  readonly source_kind: WizardConstellationSourceKind;
  readonly runtime_supported_propagators: ReadonlyArray<WizardOrbitPropagator>;
  readonly default_propagator: WizardOrbitPropagator | null;
  readonly unavailable_reason: string | null;
}

/** Typed orbital geometry authored by the Wizard, not persisted grammar. */
export interface WizardConstellationGeometry {
  readonly display_name: string;
  readonly description: string;
  readonly altitude_km: number;
  readonly inclination_deg: number;
  readonly pattern: WizardWalkerPattern;
  readonly planes: number;
  readonly slots_per_plane: number;
  readonly raan_spacing_deg: number;
  readonly phase_offset_deg: number;
}

/** One catalog-backed constellation choice exposed to the Wizard. */
export interface WizardConstellationPreset {
  readonly name: string;
  readonly description: string;
  readonly satellite_count: number;
  readonly constellation: string;
  readonly ground_stations: string;
  readonly default_node: string | null;
  readonly capability: WizardConstellationCapability;
}

/** Closed Wizard constellation catalog plus custom-geometry capability. */
export interface WizardConstellationPresetResponse {
  readonly presets: ReadonlyArray<WizardConstellationPreset>;
  readonly custom_geometry: WizardConstellationCapability;
  readonly custom_geometry_seed: WizardConstellationGeometry;
  readonly custom_geometry_default_node: string;
  readonly custom_geometry_patterns: ReadonlyArray<WizardWalkerPatternMetadata>;
  readonly orbit_models: ReadonlyArray<WizardOrbitModelMetadata>;
}

/**  */
export type WizardConstellationSourceKind = "constellation" | "space_node_set" | "custom_geometry";

/** Request one physical coverage preview from backend-selected catalog facts. */
export interface WizardCoverageRequest {
  readonly intent: WizardPhysicalIntent;
}

/**  */
export type WizardExtension = "sr" | "te" | "mpls";

/** Presentation facts for one backend-supported Wizard extension. */
export interface WizardExtensionMetadata {
  readonly id: WizardExtension;
  readonly label: string;
  readonly description: string;
}

/** Closed backend-owned Wizard protocol and area-strategy rules. */
export interface WizardExtensionRulesResponse {
  readonly protocols: ReadonlyArray<WizardProtocolMetadata>;
  readonly extensions: ReadonlyArray<WizardExtensionMetadata>;
  readonly bfd: WizardBfdMetadata;
  readonly routing_timer_defaults: WizardRoutingTimerIntent;
}

/** One catalog site-set choice for the Wizard ground picker. */
export interface WizardGroundStationSetPreset {
  readonly name: string;
  readonly description: string;
  readonly stations: ReadonlyArray<string>;
  readonly file: string;
}

/** Closed catalog site-set response for the Wizard. */
export interface WizardGroundStationSetPresetResponse {
  readonly presets: ReadonlyArray<WizardGroundStationSetPreset>;
}

/** Presentation metadata for one backend-supported Wizard orbit choice. */
export interface WizardOrbitModelMetadata {
  readonly id: WizardOrbitPropagator;
  readonly label: string;
  readonly description: string;
}

/**  */
export type WizardOrbitPropagator = "two_body" | "j2_mean_elements" | "sgp4_tle";

/** Wizard-selected physical sources used for preview and authoring. */
export interface WizardPhysicalIntent {
  readonly constellation_ref?: string | null;
  readonly custom_constellation?: WizardConstellationGeometry | null;
  readonly satellite_node_ref?: string | null;
  readonly ground_site_set_ref?: string | null;
  readonly custom_site_refs?: ReadonlyArray<string>;
  readonly orbit_propagator: WizardOrbitPropagator;
}

/** One selectable routing protocol and its backend-owned Wizard behavior. */
export interface WizardProtocolMetadata {
  readonly id: WizardRoutingProtocol;
  readonly label: string;
  readonly description: string;
  readonly extensions: ReadonlyArray<WizardExtension>;
  readonly extension_constraints: Readonly<Record<string, ReadonlyArray<WizardExtension>>>;
  readonly timer_label: string;
  readonly timer_fields: ReadonlyArray<WizardRoutingTimerFieldMetadata>;
  readonly bfd_timer_fields: ReadonlyArray<WizardRoutingTimerFieldMetadata> | null;
  readonly area_strategies: ReadonlyArray<WizardAreaStrategy>;
  readonly default_area_strategy: WizardAreaStrategy;
}

/**  */
export type WizardRoutingBooleanField = "bfd";

/**  */
export type WizardRoutingProtocol = "isis" | "ospf";

/**  */
export type WizardRoutingTimerField = "bfd_detect_multiplier" | "bfd_rx_interval" | "bfd_tx_interval" | "isis_hello_interval" | "isis_hello_multiplier" | "spf_init_delay" | "spf_short_delay" | "spf_long_delay" | "spf_holddown" | "ospf_hello_interval" | "ospf_dead_interval" | "ospf_spf_delay" | "ospf_spf_initial_hold" | "ospf_spf_max_hold";

/** Presentation and validation facts for one numeric protocol timer control. */
export interface WizardRoutingTimerFieldMetadata {
  readonly id: WizardRoutingTimerField;
  readonly label: string;
  readonly unit: string | null;
  readonly description: string;
  readonly guidance: string;
  readonly minimum: number;
  readonly maximum: number | null;
}

/** Raw Wizard routing controls mapped to session grammar only by the backend. */
export interface WizardRoutingTimerIntent {
  readonly bfd: boolean;
  readonly bfd_detect_multiplier: number;
  readonly bfd_rx_interval: number;
  readonly bfd_tx_interval: number;
  readonly isis_hello_interval: number;
  readonly isis_hello_multiplier: number;
  readonly spf_init_delay: number;
  readonly spf_short_delay: number;
  readonly spf_long_delay: number;
  readonly spf_holddown: number;
  readonly spf_time_to_learn: number;
  readonly ospf_hello_interval: number;
  readonly ospf_dead_interval: number;
  readonly ospf_spf_delay: number;
  readonly ospf_spf_initial_hold: number;
  readonly ospf_spf_max_hold: number;
}

/** One installed terminal mount summarized for a satellite preset card. */
export interface WizardSatelliteTerminalSummary {
  readonly id: string;
  readonly role: WizardTerminalRole;
  readonly count: number;
}

/** One catalog node primitive that can fly a Wizard constellation. */
export interface WizardSatelliteTypePreset {
  readonly name: string;
  readonly display_name: string;
  readonly notes: string;
  readonly file: string;
  readonly terminals: ReadonlyArray<WizardSatelliteTerminalSummary>;
}

/** Closed catalog-node response for the Wizard satellite picker. */
export interface WizardSatelliteTypePresetResponse {
  readonly presets: ReadonlyArray<WizardSatelliteTypePreset>;
}

/** One Wizard selection set compiled into an ordinary Builder draft. */
export interface WizardSessionIntent {
  readonly constellation_ref?: string | null;
  readonly custom_constellation?: WizardConstellationGeometry | null;
  readonly satellite_node_ref?: string | null;
  readonly ground_site_set_ref?: string | null;
  readonly custom_site_refs?: ReadonlyArray<string>;
  readonly orbit_propagator: WizardOrbitPropagator;
  readonly protocol: WizardRoutingProtocol;
  readonly extensions?: ReadonlyArray<WizardExtension>;
  readonly area_strategy?: WizardAreaStrategy;
  readonly routing_timers: WizardRoutingTimerIntent;
}

/**  */
export type WizardTerminalRole = "access" | "isl" | "crosslink" | "backbone";

/**  */
export type WizardWalkerPattern = "walker_delta" | "walker_star";

/** Presentation facts for one backend-supported custom Walker pattern. */
export interface WizardWalkerPatternMetadata {
  readonly id: WizardWalkerPattern;
  readonly label: string;
  readonly description: string;
}
