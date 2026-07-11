// GENERATED FILE — DO NOT EDIT BY HAND.
// Sources of truth: backend Pydantic application contracts in
// lib/nodalarc/models and services/vs_api/transition_operations.py.
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
/** Runtime validation descriptor for generated backend application DTOs. */
export type BuilderVisualRuntimeDescriptor =
  | { readonly kind: "json" }
  | { readonly kind: "literal"; readonly value: JsonValue }
  | { readonly kind: "enum"; readonly values: ReadonlyArray<JsonValue> }
  | { readonly kind: "null" }
  | { readonly kind: "boolean" }
  | { readonly kind: "string"; readonly pattern?: string; readonly min_length?: number; readonly max_length?: number }
  | { readonly kind: "integer" | "number"; readonly minimum?: number; readonly maximum?: number; readonly exclusive_minimum?: number; readonly exclusive_maximum?: number; readonly multiple_of?: number }
  | { readonly kind: "array"; readonly items: BuilderVisualRuntimeDescriptor; readonly min_items?: number; readonly max_items?: number; readonly unique?: boolean }
  | { readonly kind: "tuple"; readonly items: ReadonlyArray<BuilderVisualRuntimeDescriptor>; readonly rest: false | BuilderVisualRuntimeDescriptor; readonly min_items?: number; readonly max_items?: number; readonly unique?: boolean }
  | { readonly kind: "object"; readonly fields: Readonly<Record<string, BuilderVisualRuntimeDescriptor>>; readonly additional: false | BuilderVisualRuntimeDescriptor; readonly patterns?: ReadonlyArray<{ readonly pattern: string; readonly values: BuilderVisualRuntimeDescriptor }> }
  | { readonly kind: "union"; readonly options: ReadonlyArray<BuilderVisualRuntimeDescriptor>; readonly exclusive: boolean }
  | { readonly kind: "intersection"; readonly options: ReadonlyArray<BuilderVisualRuntimeDescriptor> };

export type CatalogFamily = "bodies" | "terminals" | "payloads" | "orbits" | "nodes" | "sites" | "site-sets" | "constellations" | "space-node-sets" | "sessions";
export type BuilderIssueStage = "draft" | "structural" | "reference" | "semantic" | "runtime_support" | "readiness" | "persistence" | "deployment" | "staleness";
export type BuilderIssueSeverity = "info" | "warning" | "error";
export type BuilderBlockedOperation = "save" | "deploy";
export type WizardOrbitPropagator = "two_body" | "j2_mean_elements" | "sgp4_tle";
export type WizardConstellationSourceKind = "constellation" | "space_node_set" | "custom_geometry";
export type WizardTerminalRole = "access" | "isl" | "crosslink" | "backbone";
export type WizardExtension = "sr" | "te" | "mpls";
export type WizardAreaStrategy = "flat" | "stripe" | "per_plane";
export type WizardRoutingProtocol = "isis" | "ospf";
export type WizardWalkerPattern = "walker_delta" | "walker_star";
export type WizardRoutingBooleanField = "bfd";
export type WizardRoutingTimerField = "bfd_detect_multiplier" | "bfd_rx_interval" | "bfd_tx_interval" | "isis_hello_interval" | "isis_hello_multiplier" | "spf_init_delay" | "spf_short_delay" | "spf_long_delay" | "spf_holddown" | "ospf_hello_interval" | "ospf_dead_interval" | "ospf_spf_delay" | "ospf_spf_initial_hold" | "ospf_spf_max_hold";
export type BuilderVisualDraftMode = "structured" | "opaque_yaml";
export type BuilderVisualSchedulingPreset = "leo-fast-handover" | "geo-longest-pass";
export type BuilderVisualPhasingMode = "walker_delta" | "walker_star" | "evenly_spaced_mean_anomaly";
export type BuilderVisualOrbitShape = "circular" | "elliptical";
export type BuilderVisualOrbitPropagator = "two_body" | "j2_mean_elements";
export type BuilderVisualTopologyMode = "visible_candidates" | "nearest_n";
export type BuilderVisualDraftCommandOperation = "place_space_reference" | "place_ground_reference" | "add_generated_space" | "set_space_population" | "author_inline_space_node" | "add_or_increment_node_terminal" | "set_node_terminal_role" | "add_node_ethernet_port" | "add_ground" | "add_ground_site_reference" | "set_ground_stamp_node_model" | "set_ground_site_node_model" | "add_ground_site_node" | "mint_ground_members" | "add_routing_domain" | "add_boundary" | "connect_segments" | "rederive_link" | "set_scheduling_preset";
export type BuilderVisualDraftAffectedKind = "space" | "ground" | "routing_domain" | "boundary" | "link" | "ground_member";
export type CatalogComponentFamily = "bodies" | "terminals" | "payloads" | "orbits" | "nodes" | "sites" | "site-sets" | "constellations" | "space-node-sets";
export type CatalogDraftPatchOperation = "add" | "replace" | "remove";
export type CatalogDraftIssueStage = "structural" | "reference" | "runtime_support";
export type TransitionOperationState = "reserved" | "collecting" | "uploading" | "verifying" | "switching" | "succeeded" | "failed" | "cancelled";
export type TransitionOperationSourceKind = "catalog_session";

/** One typed finding routed to its owning authoring or deployment stage. */
export interface BuilderIssue {
  readonly code: string;
  readonly stage: BuilderIssueStage;
  readonly severity: BuilderIssueSeverity;
  readonly message: string;
  readonly blocks?: ReadonlyArray<BuilderBlockedOperation>;
  readonly source_ref?: string | null;
  readonly json_pointer?: string | null;
  readonly draft_path?: string | null;
  readonly related_refs?: ReadonlyArray<string>;
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

/** One complete draft catalog document proposed for a ``user:`` ref. */
export interface BuilderProposedCatalogDocument {
  readonly ref: CatalogRef;
  readonly document: Readonly<Record<string, JsonValue>>;
  readonly expected_revision?: string | null;
}

/** Complete transient configuration state compiled by the backend. The inner mappings intentionally remain generic JSON so incomplete or invalid configuration can cross the API boundary and return typed compile issues instead of being rejected as a malformed application request. */
export interface BuilderDraftState {
  readonly session: Readonly<Record<string, JsonValue>>;
  readonly catalog_documents?: ReadonlyArray<BuilderProposedCatalogDocument>;
}

/** Versioned transient editor state accepted by compile/save APIs only. */
export interface BuilderDraftEnvelope {
  readonly contract_version?: 1;
  readonly draft_revision: number;
  readonly state: BuilderDraftState;
}

/** One backend-discovered document in a transitive reference closure. */
export interface DependencyClosureEntry {
  readonly ref: CatalogRef;
  readonly family: CatalogFamily;
  readonly revision?: string | null;
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

/** Content identities reviewed across compile, save, and deployment. */
export interface BuilderDigests {
  readonly document: Sha256Digest;
  readonly dependency: Sha256Digest;
  readonly resolved_semantic?: Sha256Digest | null;
}

/** Backend-owned allow/block decision for one workflow operation. */
export interface BuilderVerdict {
  readonly operation: BuilderBlockedOperation;
  readonly allowed: boolean;
  readonly blockers?: ReadonlyArray<BuilderIssue>;
}

/** Deployment decision bound to one exact saved session revision. */
export interface BuilderDeployVerdict {
  readonly allowed: boolean;
  readonly session_ref: SessionRef;
  readonly session_revision: string;
  readonly digests: BuilderDigests;
  readonly blockers?: ReadonlyArray<BuilderIssue>;
}

/** Request to compile one transient draft through backend authorities. */
export interface BuilderCompileRequest {
  readonly draft: BuilderDraftEnvelope;
  readonly target_ref: SessionRef;
}

/** Backend compilation result for a transient Builder draft. */
export interface BuilderCompileResult {
  readonly draft: BuilderDraftEnvelope;
  readonly target_ref: SessionRef;
  readonly canonical_session_yaml?: string | null;
  readonly canonical_session_json?: Readonly<Record<string, JsonValue>> | null;
  readonly dependency_closure?: DependencyClosureInventory | null;
  readonly resolved_preview?: BuilderWorld | null;
  readonly digests?: BuilderDigests | null;
  readonly issues?: ReadonlyArray<BuilderIssue>;
  readonly save_verdict: BuilderVerdict;
  readonly deploy_eligibility_after_save: BuilderVerdict;
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

/** Backend-owned runtime capability for one Wizard space source. */
export interface WizardConstellationCapability {
  readonly source_kind: WizardConstellationSourceKind;
  readonly runtime_supported_propagators: ReadonlyArray<WizardOrbitPropagator>;
  readonly default_propagator: WizardOrbitPropagator | null;
  readonly unavailable_reason: string | null;
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

/** Presentation metadata for one backend-supported Wizard orbit choice. */
export interface WizardOrbitModelMetadata {
  readonly id: WizardOrbitPropagator;
  readonly label: string;
  readonly description: string;
}

/** Presentation facts for one backend-supported custom Walker pattern. */
export interface WizardWalkerPatternMetadata {
  readonly id: WizardWalkerPattern;
  readonly label: string;
  readonly description: string;
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

/** Backend-owned presentation and field facts for Wizard BFD controls. */
export interface WizardBfdMetadata {
  readonly heading: string;
  readonly enabled_field: "bfd";
  readonly enable_label: string;
  readonly enable_description: string;
  readonly timer_fields: ReadonlyArray<WizardRoutingTimerFieldMetadata>;
}

/** Presentation facts for one backend-supported Wizard extension. */
export interface WizardExtensionMetadata {
  readonly id: WizardExtension;
  readonly label: string;
  readonly description: string;
}

/** Presentation and validation facts for one numeric protocol timer control. */
export interface WizardRoutingTimerFieldMetadata {
  readonly id: WizardRoutingTimerField;
  readonly label: string;
  readonly unit?: string | null;
  readonly description: string;
  readonly guidance: string;
  readonly minimum: number;
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
  readonly non_flat_area_warning?: string | null;
}

/** Closed backend-owned Wizard protocol and area-strategy rules. */
export interface WizardExtensionRulesResponse {
  readonly protocols: ReadonlyArray<WizardProtocolMetadata>;
  readonly extensions: ReadonlyArray<WizardExtensionMetadata>;
  readonly area_strategies: ReadonlyArray<WizardAreaStrategy>;
  readonly default_area_strategy: WizardAreaStrategy;
  readonly bfd: WizardBfdMetadata;
  readonly routing_timer_defaults: WizardRoutingTimerIntent;
}

/** Wizard-selected physical sources used for preview and authoring. */
export interface WizardPhysicalIntent {
  readonly constellation_ref?: string | null;
  readonly custom_constellation?: WizardConstellationGeometry | null;
  readonly satellite_node_ref?: string | null;
  readonly ground_site_set_ref?: string | null;
  readonly custom_site_refs?: ReadonlyArray<string>;
  readonly orbit_propagator: WizardOrbitPropagator;
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

/** Request backend construction and compilation of one Wizard intent. */
export interface WizardCompileRequest {
  readonly draft_revision: number;
  readonly intent: WizardSessionIntent;
}

/** Request one physical coverage preview from backend-selected catalog facts. */
export interface WizardCoverageRequest {
  readonly intent: WizardPhysicalIntent;
}

/** Stable refusal when Wizard intent cannot become a Builder draft. */
export interface WizardCompileRefusal {
  readonly code: "wizard_compile.invalid_selection" | "wizard_compile.reference_error" | "wizard_compile.repository_unavailable";
  readonly message: string;
  readonly cause_type?: string | null;
}

/** Request deployment of one exact saved session revision and closure. */
export interface BuilderSessionDeployRequest {
  readonly session_ref: SessionRef;
  readonly expected_session_revision: string;
  readonly expected_document_digest: Sha256Digest;
  readonly expected_dependency_digest: Sha256Digest;
}

/** Opaque accepted operation bound to the exact requested catalog source. */
export interface BuilderSessionDeployAccepted {
  readonly operation_id: string;
  readonly status?: "accepted";
  readonly source: BuilderSessionDeployRequest;
}

/** Stable, path-free refusal for one guarded saved-session deployment. */
export interface BuilderSessionDeployRefusal {
  readonly code: "builder_session_deploy.invalid_precondition" | "builder_session_deploy.source_not_found" | "builder_session_deploy.stale_source" | "builder_session_deploy.not_ready" | "builder_session_deploy.conflict" | "builder_session_deploy.repository_unavailable" | "builder_session_deploy.unsupported" | "builder_session_deploy.preparation_failed";
  readonly message: string;
  readonly session_ref: SessionRef;
  readonly expected?: string | null;
  readonly observed?: string | null;
  readonly cause_type?: string | null;
}

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
  readonly issues?: ReadonlyArray<BuilderIssue>;
}

/** Stable transport evidence for a Builder session save that did not succeed. */
export interface BuilderSessionSaveRefusal {
  readonly code: "builder_session_save.save_blocked" | "builder_session_save.stale_write" | "builder_session_save.graph_invalid" | "builder_session_save.persistence_failed" | "builder_session_save.storage_verification_failed";
  readonly message: string;
  readonly target_ref: SessionRef;
  readonly base_generation?: string | null;
  readonly repository_committed?: boolean;
  readonly issues?: ReadonlyArray<BuilderIssue>;
  readonly cause_type?: string | null;
  readonly compile_result?: BuilderCompileResult | null;
}

/** Expected revision for one component the structured draft may replace. */
export interface BuilderVisualCatalogRevision {
  readonly ref: CatalogRef;
  readonly expected_revision: string;
}

/** Fork the minimal catalog ancestor path for one placed nested component. */
export interface BuilderVisualCustomizeChainRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly segment_id: string;
  readonly leaf_ref: CatalogRef;
  readonly target_leaf_ref?: CatalogRef | null;
}

/** One source-to-user fork in a minimal nested customization path. */
export interface BuilderVisualCustomizeChainEntry {
  readonly source_ref: CatalogRef;
  readonly target_ref: CatalogRef;
}

/** Updated draft or typed refusal evidence for one customize-chain command. */
export interface BuilderVisualCustomizeChainResult {
  readonly applied: boolean;
  readonly draft: BuilderVisualDraftEnvelope;
  readonly root_source_ref?: CatalogRef | null;
  readonly root_target_ref?: CatalogRef | null;
  readonly forked_chain?: ReadonlyArray<BuilderVisualCustomizeChainEntry>;
  readonly issues?: ReadonlyArray<BuilderIssue>;
}

/** Place one existing constellation or space-node-set by catalog reference. */
export interface BuilderVisualPlaceSpaceReferenceCommand {
  readonly operation: "place_space_reference";
  readonly source_ref: string;
}

/** Place one existing site set with backend-owned scheduling. */
export interface BuilderVisualPlaceGroundReferenceCommand {
  readonly operation: "place_ground_reference";
  readonly site_set_ref: string;
}

/** Add one backend-seeded generated constellation draft. */
export interface BuilderVisualAddGeneratedSpaceCommand {
  readonly operation: "add_generated_space";
  readonly node_ref?: string | null;
  readonly phasing_mode: BuilderVisualPhasingMode;
}

/** Change one population input and let the backend derive its complete phasing. */
export interface BuilderVisualSetSpacePopulationCommand {
  readonly operation: "set_space_population";
  readonly segment_id: string;
  readonly phasing_mode?: BuilderVisualPhasingMode | null;
  readonly planes?: number | null;
  readonly slots_per_plane?: number | null;
}

/** Create one backend-seeded inline node for an authored space segment. */
export interface BuilderVisualAuthorInlineSpaceNodeCommand {
  readonly operation: "author_inline_space_node";
  readonly segment_id: string;
}

/** Mount a selected terminal or increment the matching backend-owned mount. */
export interface BuilderVisualAddOrIncrementNodeTerminalCommand {
  readonly operation: "add_or_increment_node_terminal";
  readonly segment_id: string;
  readonly terminal_ref: string;
  readonly role: WizardTerminalRole;
}

/** Change one inline-node mount role with backend-owned pointing semantics. */
export interface BuilderVisualSetNodeTerminalRoleCommand {
  readonly operation: "set_node_terminal_role";
  readonly segment_id: string;
  readonly mount_id: string;
  readonly role: WizardTerminalRole;
}

/** Add one uniquely identified Ethernet port to an authored inline node. */
export interface BuilderVisualAddNodeEthernetPortCommand {
  readonly operation: "add_node_ethernet_port";
  readonly segment_id: string;
}

/** Add one backend-seeded authored ground-segment draft. */
export interface BuilderVisualAddGroundCommand {
  readonly operation: "add_ground";
  readonly node_ref?: string | null;
  readonly installed?: Readonly<Record<string, number>>;
  readonly boresights?: Readonly<Record<string, BuilderVisualGroundBoresight>>;
  readonly body_ref?: string | null;
}

/** Place one existing site, creating its authored ground segment when needed. */
export interface BuilderVisualAddGroundSiteReferenceCommand {
  readonly operation: "add_ground_site_reference";
  readonly segment_id?: string | null;
  readonly site_ref: string;
}

/** Select a ground stamp node and derive its installed terminal inventory. */
export interface BuilderVisualSetGroundStampNodeModelCommand {
  readonly operation: "set_ground_stamp_node_model";
  readonly segment_id: string;
  readonly node_ref: string;
}

/** Select one authored site's node model and derive its installed inventory. */
export interface BuilderVisualSetGroundSiteNodeModelCommand {
  readonly operation: "set_ground_site_node_model";
  readonly segment_id: string;
  readonly member_id: string;
  readonly node_id: string;
  readonly node_ref: string;
}

/** Add one backend-seeded node installation to an authored site. */
export interface BuilderVisualAddGroundSiteNodeCommand {
  readonly operation: "add_ground_site_node";
  readonly segment_id: string;
  readonly member_id: string;
  readonly node_ref?: string | null;
}

/** One user-entered surface location awaiting backend-owned site allocation. */
export interface BuilderVisualGroundSiteIntent {
  readonly name: string;
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly alt_m?: number;
}

/** Mint complete sites and addresses from typed locations and one ground stamp. */
export interface BuilderVisualMintGroundMembersCommand {
  readonly operation: "mint_ground_members";
  readonly segment_id: string;
  readonly sites: ReadonlyArray<BuilderVisualGroundSiteIntent>;
}

/** Add one backend-seeded routing domain over uncovered segments. */
export interface BuilderVisualAddRoutingDomainCommand {
  readonly operation: "add_routing_domain";
}

/** Add one backend-seeded routing boundary. */
export interface BuilderVisualAddBoundaryCommand {
  readonly operation: "add_boundary";
}

/** Create a link rule whose initial physics comes from both endpoints. */
export interface BuilderVisualConnectSegmentsCommand {
  readonly operation: "connect_segments";
  readonly from_segment_id: string;
  readonly to_segment_id: string;
}

/** Repoint one link endpoint and explicitly rederive its physical seed. */
export interface BuilderVisualRederiveLinkCommand {
  readonly operation: "rederive_link";
  readonly rule_id: string;
  readonly side: "a" | "b";
  readonly segment_id: string;
}

/** Apply one complete backend-owned scheduling block or inherit at a site. */
export interface BuilderVisualSetSchedulingPresetCommand {
  readonly operation: "set_scheduling_preset";
  readonly segment_id: string;
  readonly preset: BuilderVisualSchedulingPreset | null;
  readonly member_id?: string | null;
}

/** Apply one typed command to an exact visual-draft revision. */
export interface BuilderVisualDraftCommandRequest {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly command: BuilderVisualPlaceSpaceReferenceCommand | BuilderVisualPlaceGroundReferenceCommand | BuilderVisualAddGeneratedSpaceCommand | BuilderVisualSetSpacePopulationCommand | BuilderVisualAuthorInlineSpaceNodeCommand | BuilderVisualAddOrIncrementNodeTerminalCommand | BuilderVisualSetNodeTerminalRoleCommand | BuilderVisualAddNodeEthernetPortCommand | BuilderVisualAddGroundCommand | BuilderVisualAddGroundSiteReferenceCommand | BuilderVisualSetGroundStampNodeModelCommand | BuilderVisualSetGroundSiteNodeModelCommand | BuilderVisualAddGroundSiteNodeCommand | BuilderVisualMintGroundMembersCommand | BuilderVisualAddRoutingDomainCommand | BuilderVisualAddBoundaryCommand | BuilderVisualConnectSegmentsCommand | BuilderVisualRederiveLinkCommand | BuilderVisualSetSchedulingPresetCommand;
}

/** One applied command and the next revision of the complete draft. */
export interface BuilderVisualDraftCommandResult {
  readonly contract_version?: 1;
  readonly operation: BuilderVisualDraftCommandOperation;
  readonly base_draft_revision: number;
  readonly draft: BuilderVisualDraftEnvelope;
  readonly affected_kind: BuilderVisualDraftAffectedKind;
  readonly affected_id: string;
  readonly scheduling_preset?: BuilderVisualSchedulingPreset | null;
  readonly notice?: string | null;
}

/** Walker population intent whose derived angular values remain backend-owned. */
export interface BuilderVisualWalkerLayoutRequest {
  readonly pattern: WizardWalkerPattern;
  readonly planes: number;
  readonly slots_per_plane: number;
}

/** Backend-issued angular layout for one Walker population intent. */
export interface BuilderVisualWalkerLayoutResult {
  readonly raan_spacing_deg: number;
  readonly phase_offset_deg: number;
}

/** Spacecraft access-terminal pointing authored into a node mount. */
export interface BuilderVisualSpaceBoresight {
  readonly mode: "nadir";
}

/** Ground access-terminal pointing authored into a site installation. */
export interface BuilderVisualGroundBoresight {
  readonly mode: "local_vertical";
}

/** Editable terminal mount on a visual node draft. */
export interface BuilderVisualTerminalMount {
  readonly mount_id?: string;
  readonly role?: WizardTerminalRole | null;
  readonly terminal_ref?: string | null;
  readonly count?: number | null;
  readonly boresight?: BuilderVisualSpaceBoresight | null;
}

/** Editable node object nested in a generated space component. */
export interface BuilderVisualNode {
  readonly id?: string;
  readonly display_name?: string;
  readonly forwarding?: "routed" | "host" | "bridge" | "control_only" | null;
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

/** One installed node in an editable site object. */
export interface BuilderVisualSiteNode {
  readonly node_id?: string;
  readonly model_ref?: string | null;
  readonly installed?: Readonly<Record<string, number>>;
  readonly boresights: Readonly<Record<string, BuilderVisualGroundBoresight>>;
  readonly lo0_ipv4?: string;
  readonly terr0_ipv4?: string;
}

/** Editable complete site object. */
export interface BuilderVisualSite {
  readonly site_id?: string;
  readonly display_name?: string;
  readonly body?: string | null;
  readonly lat_deg?: number | null;
  readonly lon_deg?: number | null;
  readonly alt_m?: number | null;
  readonly lan_ipv4?: string;
  readonly tags?: ReadonlyArray<string>;
  readonly nodes?: ReadonlyArray<BuilderVisualSiteNode>;
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

/** Backend-issued minting facts retained as visual state, never persisted. */
export interface BuilderVisualGroundStamp {
  readonly node_ref?: string | null;
  readonly installed?: Readonly<Record<string, number>>;
  readonly boresights: Readonly<Record<string, BuilderVisualGroundBoresight>>;
  readonly body?: string | null;
  readonly lan_base?: string;
  readonly loopback_base?: string;
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

/** One library site set placed by reference with session scheduling. */
export interface BuilderVisualGroundReference {
  readonly segment_id?: string;
  readonly site_set_ref?: string | null;
  readonly label?: string;
  readonly scheduling?: Readonly<Record<string, JsonValue>>;
}

/** Editable endpoint selector for one visual link rule. */
export interface BuilderVisualLinkEndpoint {
  readonly segment_id?: string;
  readonly tag?: string | null;
  readonly role?: WizardTerminalRole | null;
  readonly medium?: "rf" | "optical" | null;
  readonly min_elevation_deg?: number | null;
}

/** Editable physical link rule; every authored rule is assembled. */
export interface BuilderVisualLinkRule {
  readonly rule_id?: string;
  readonly label?: string;
  readonly enabled?: boolean;
  readonly a?: BuilderVisualLinkEndpoint;
  readonly b?: BuilderVisualLinkEndpoint;
  readonly topology_mode?: BuilderVisualTopologyMode | null;
  readonly topology_n?: number | null;
  readonly max_range_km?: number | null;
}

/** Editable routing domain; empty membership remains visible to validation. */
export interface BuilderVisualRoutingDomain {
  readonly domain_id?: string;
  readonly label?: string;
  readonly protocol?: "isis" | "ospf" | "bgp" | "static" | null;
  readonly member_segment_ids?: ReadonlyArray<string>;
  readonly hello_interval_s?: number | null;
  readonly hold_interval_s?: number | null;
}

/** Editable routing boundary assembled without omission. */
export interface BuilderVisualRoutingBoundary {
  readonly boundary_id?: string;
  readonly over_rule_id?: string;
  readonly adapter?: "static_ip" | "bgp" | "dtn_bundle" | null;
  readonly from_domain_id?: string;
  readonly to_domain_id?: string;
  readonly export_node_loopbacks?: boolean;
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

/** Versioned visual draft in structured or lossless opaque-YAML mode. */
export interface BuilderVisualDraftEnvelope {
  readonly contract_version?: 1;
  readonly draft_revision: number;
  readonly mode: BuilderVisualDraftMode;
  readonly target_ref: SessionRef;
  readonly source_ref?: SessionRef | null;
  readonly expected_session_revision?: string | null;
  readonly expected_catalog_revisions?: ReadonlyArray<BuilderVisualCatalogRevision>;
  readonly catalog_documents?: ReadonlyArray<BuilderProposedCatalogDocument>;
  readonly session_name_is_placeholder: boolean;
  readonly reserved_authoring_ids: ReadonlyArray<string>;
  readonly workspace?: BuilderVisualWorkspace | null;
  readonly session_yaml?: string | null;
}

/** Request a backend-created blank structured visual draft. */
export interface BuilderVisualDraftCreateRequest {
  readonly session_name?: string | null;
  readonly display_name?: string | null;
  readonly description?: string | null;
}

/** Open any stored session in lossless opaque-YAML authoring mode. */
export interface BuilderVisualDraftOpenRequest {
  readonly source_ref: SessionRef;
  readonly target_ref?: SessionRef | null;
}

/** Compile a complete visual draft through backend assembly and grammar authorities. */
export interface BuilderVisualDraftCompileRequest {
  readonly draft: BuilderVisualDraftEnvelope;
}

/** Backend assembly, typed issues, save request, and authoritative compile facts. */
export interface BuilderVisualDraftAssemblyResult {
  readonly visual_draft: BuilderVisualDraftEnvelope;
  readonly assembled_draft: BuilderDraftEnvelope;
  readonly save_request: BuilderSessionSaveRequest;
  readonly compile_result: BuilderCompileResult;
  readonly assembly_issues?: ReadonlyArray<BuilderIssue>;
}

/** First-class catalog session selected inside a server-owned scope. */
export interface CatalogSessionSourceId {
  readonly kind?: "catalog";
  readonly session_ref: SessionRef;
}

/** Safe typed reason one catalog session cannot deploy. */
export interface CatalogSessionBlocker {
  readonly code: string;
  readonly message: string;
  readonly cause_type?: string | null;
}

/** Revisioned browser listing for one scoped catalog session. */
export interface CatalogSessionSummary {
  readonly source_id: CatalogSessionSourceId;
  readonly name: string;
  readonly source: "nodalarc" | "user";
  readonly constellation: string;
  readonly routing_stack: string;
  readonly deploy_allowed: boolean;
  readonly source_revision?: Sha256Digest | null;
  readonly document_digest?: Sha256Digest | null;
  readonly dependency_digest?: Sha256Digest | null;
  readonly blockers?: ReadonlyArray<CatalogSessionBlocker>;
  readonly active?: boolean;
}

/** Deploy one reviewed session revision from the request catalog scope. */
export interface CatalogSessionSwitchRequest {
  readonly source: CatalogSessionSourceId;
  readonly expected_source_revision: Sha256Digest;
  readonly expected_document_digest: Sha256Digest;
  readonly expected_dependency_digest: Sha256Digest;
}

/** Accepted catalog deployment operation. */
export interface CatalogSessionSwitchAccepted {
  readonly status?: "accepted";
  readonly operation_id: string;
  readonly source: CatalogSessionSourceId;
}

/** One standard persisted session document to save into the user catalog. */
export interface CatalogSessionYamlUploadRequest {
  readonly yaml: string;
}

/**  */
export interface TransitionOperationSource {
  readonly kind: TransitionOperationSourceKind;
  readonly logical_id: string;
}

/** Reviewed, browser-safe facts bound to one operation. */
export interface TransitionOperationFacts {
  readonly document_digest?: Sha256Digest | null;
  readonly closure_digest?: Sha256Digest | null;
  readonly resolved_semantic_digest?: Sha256Digest | null;
  readonly file_count?: number | null;
  readonly total_bytes?: number | null;
  readonly release: string;
  readonly build: string;
}

/**  */
export interface TransitionOperationEvent {
  readonly state: TransitionOperationState;
  readonly occurred_at: string;
  readonly detail?: string | null;
}

/**  */
export interface TransitionOperationFailure {
  readonly code: string;
  readonly message: string;
  readonly cause_type?: string | null;
}

/**  */
export interface TransitionRuntimeResult {
  readonly session_id: string;
  readonly generation: number;
}

/** Public, path-free representation returned for an opaque operation ID. */
export interface TransitionOperation {
  readonly operation_id: string;
  readonly state: TransitionOperationState;
  readonly source: TransitionOperationSource;
  readonly facts: TransitionOperationFacts;
  readonly created_at: string;
  readonly updated_at: string;
  readonly events: ReadonlyArray<TransitionOperationEvent>;
  readonly failure?: TransitionOperationFailure | null;
  readonly runtime?: TransitionRuntimeResult | null;
}

/** Backend-owned authoring capabilities for one public catalog family. */
export interface CatalogFamilyMetadata {
  readonly family: CatalogFamily;
  readonly wrapper?: string | null;
  readonly direct_user_write: boolean;
  readonly component_fork: boolean;
  readonly session_draft_save: boolean;
  readonly suggested_object_id?: string | null;
}

/** Factual backend capabilities required by the repaired Builder. */
export interface BuilderCatalogCapabilities {
  readonly user_catalog_write?: true;
  readonly deploy_yaml_closure?: true;
}

/** Typed presentation metadata for one backend-owned scheduling preset. */
export interface BuilderVisualSchedulingPresetMetadata {
  readonly id: BuilderVisualSchedulingPreset;
  readonly label: string;
}

/** Presentation facts for one canonical terminal-mount role. */
export interface BuilderVisualMountRoleMetadata {
  readonly id: WizardTerminalRole;
  readonly label: string;
  readonly description: string;
}

/** Presentation facts for one canonical link-terminal medium. */
export interface BuilderVisualLinkMediumMetadata {
  readonly id: "rf" | "optical";
  readonly label: string;
  readonly signal_seed: Readonly<Record<string, JsonValue>>;
}

/** Presentation facts for one canonical node forwarding class. */
export interface BuilderVisualForwardingClassMetadata {
  readonly id: "routed" | "host" | "bridge" | "control_only";
  readonly label: string;
}

/** Presentation and runtime facts for one routing protocol. */
export interface BuilderVisualRoutingProtocolMetadata {
  readonly runtime_supported: boolean;
  readonly support_note?: string | null;
  readonly id: "isis" | "ospf" | "bgp" | "static";
  readonly label: string;
  readonly timer_fields: boolean;
}

/** Presentation and runtime facts for one routing-boundary adapter. */
export interface BuilderVisualBoundaryAdapterMetadata {
  readonly runtime_supported: boolean;
  readonly support_note?: string | null;
  readonly id: "static_ip" | "bgp" | "dtn_bundle";
  readonly label: string;
}

/** Presentation facts for one canonical constellation phasing mode. */
export interface BuilderVisualPhasingModeMetadata {
  readonly id: BuilderVisualPhasingMode;
  readonly label: string;
}

/** Presentation facts for one visual orbit-shape form. */
export interface BuilderVisualOrbitShapeMetadata {
  readonly id: BuilderVisualOrbitShape;
  readonly label: string;
}

/** Presentation and runtime facts for one visual orbit propagator. */
export interface BuilderVisualOrbitPropagatorMetadata {
  readonly runtime_supported: boolean;
  readonly support_note?: string | null;
  readonly id: BuilderVisualOrbitPropagator;
  readonly label: string;
}

/** Presentation and runtime facts for one visual link topology mode. */
export interface BuilderVisualTopologyModeMetadata {
  readonly runtime_supported: boolean;
  readonly support_note?: string | null;
  readonly id: BuilderVisualTopologyMode;
  readonly label: string;
  readonly requires_n: boolean;
}

/** Backend-owned choices and seeds used by the visual authoring surface. */
export interface BuilderVisualAuthoringFacts {
  readonly default_phasing_mode: BuilderVisualPhasingMode;
  readonly single_plane_phasing_mode: BuilderVisualPhasingMode;
  readonly default_scheduling_preset: BuilderVisualSchedulingPreset;
  readonly default_mount_role: WizardTerminalRole;
  readonly default_terminal_mount_count: number;
  readonly default_body_ref: string;
  readonly default_node: BuilderVisualNode;
  readonly space_access_boresight: BuilderVisualSpaceBoresight;
  readonly ground_access_boresight: BuilderVisualGroundBoresight;
  readonly mount_roles: ReadonlyArray<BuilderVisualMountRoleMetadata>;
  readonly link_media: ReadonlyArray<BuilderVisualLinkMediumMetadata>;
  readonly forwarding_classes: ReadonlyArray<BuilderVisualForwardingClassMetadata>;
  readonly routing_protocols: ReadonlyArray<BuilderVisualRoutingProtocolMetadata>;
  readonly boundary_adapters: ReadonlyArray<BuilderVisualBoundaryAdapterMetadata>;
  readonly phasing_modes: ReadonlyArray<BuilderVisualPhasingModeMetadata>;
  readonly orbit_shapes: ReadonlyArray<BuilderVisualOrbitShapeMetadata>;
  readonly orbit_propagators: ReadonlyArray<BuilderVisualOrbitPropagatorMetadata>;
  readonly topology_modes: ReadonlyArray<BuilderVisualTopologyModeMetadata>;
}

/** Public documentation and catalog metadata needed to start Builder authoring. */
export interface BuilderCatalogBootstrap {
  readonly contract_version?: 1;
  readonly public_grammar_href: string;
  readonly capabilities: BuilderCatalogCapabilities;
  readonly families: ReadonlyArray<CatalogFamilyMetadata>;
  readonly scheduling_presets: ReadonlyArray<BuilderVisualSchedulingPresetMetadata>;
  readonly authoring: BuilderVisualAuthoringFacts;
}

/** Bounded catalog-list request using an opaque server cursor. */
export interface CatalogListRequest {
  readonly family?: CatalogFamily | null;
  readonly namespace?: "nodalarc" | "user" | null;
  readonly page_size?: number;
  readonly page_token?: string | null;
}

/** Revisioned metadata for one catalog library row. */
export interface CatalogDocumentSummary {
  readonly ref: CatalogRef;
  readonly namespace: "nodalarc" | "user";
  readonly family: CatalogFamily;
  readonly revision: string;
  readonly size_bytes: number;
  readonly display_name: string;
  readonly summary?: string | null;
}

/** One deterministic page pinned to an immutable catalog generation. */
export interface CatalogListPage {
  readonly generation: string;
  readonly items: ReadonlyArray<CatalogDocumentSummary>;
  readonly next_page_token?: string | null;
}

/** Request one catalog document by namespace-qualified identity. */
export interface CatalogGetRequest {
  readonly ref: CatalogRef;
}

/** Create or compare-and-swap replace one complete user component. */
export interface CatalogDocumentWriteRequest {
  readonly ref: CatalogRef;
  readonly document: Readonly<Record<string, JsonValue>>;
  readonly expected_revision?: string | null;
}

/** Add one node terminal mount with backend-generated persisted fields. */
export interface CatalogDraftAddNodeTerminalMountRequest {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly terminal_ref: string;
  readonly role: WizardTerminalRole;
}

/** Add one node Ethernet port with a backend-generated unique identifier. */
export interface CatalogDraftAddNodeEthernetPortRequest {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly expected_draft_revision: number;
}

/** Add one explicitly identified node using backend-derived persisted fields. */
export interface CatalogDraftAddSiteNodeRequest {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly node_id: string;
  readonly node_ref: string;
}

/** One backend-produced component-draft finding at an exact JSON pointer. */
export interface CatalogDraftIssue {
  readonly code: string;
  readonly stage: CatalogDraftIssueStage;
  readonly message: string;
  readonly pointer: string;
  readonly blocks: ReadonlyArray<BuilderBlockedOperation>;
}

/** Versioned full-document component draft owned by backend authoring APIs. */
export interface CatalogComponentDraftEnvelope {
  readonly contract_version?: 1;
  readonly draft_revision: number;
  readonly family: CatalogComponentFamily;
  readonly target_ref: CatalogRef;
  readonly source_ref?: CatalogRef | null;
  readonly expected_source_revision?: string | null;
  readonly expected_target_revision?: string | null;
  readonly document: Readonly<Record<string, JsonValue>>;
  readonly issues: ReadonlyArray<CatalogDraftIssue>;
}

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

/** One bounded JSON-pointer mutation applied to a full backend draft document. */
export interface CatalogDraftPatchCommand {
  readonly operation: CatalogDraftPatchOperation;
  readonly pointer: string;
  readonly value?: JsonValue | null;
}

/** Apply an ordered, revision-fenced mutation set to one component draft. */
export interface CatalogDraftPatchRequest {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly commands: ReadonlyArray<CatalogDraftPatchCommand>;
}

/** Parse and replace one advanced component object under backend authority. */
export interface CatalogDraftReplaceObjectRequest {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly expected_draft_revision: number;
  readonly raw_object_json: string;
}

/** Validate one exact draft without mutating catalog persistence. */
export interface CatalogDraftCompileRequest {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly expected_draft_revision: number;
}

/** Canonical component bytes plus structural, reference, and support findings. */
export interface CatalogDraftCompileResult {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly save_allowed: boolean;
  readonly runtime_supported: boolean;
  readonly canonical_yaml?: string | null;
  readonly canonical_json?: Readonly<Record<string, JsonValue>> | null;
  readonly content_digest?: Sha256Digest | null;
  readonly issues: ReadonlyArray<CatalogDraftIssue>;
}

/** Persist one structurally valid component draft through scoped CAS storage. */
export interface CatalogDraftSaveRequest {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly expected_draft_revision: number;
}

/** Saved canonical component, updated draft revision fence, and graph impact. */
export interface CatalogDraftSaveResult {
  readonly draft: CatalogComponentDraftEnvelope;
  readonly result: CatalogMutationResult;
  readonly compile_result: CatalogDraftCompileResult;
}

/** Fork one complete component document to a new user-owned identity. */
export interface CatalogForkRequest {
  readonly source_ref: CatalogRef;
  readonly target_ref: CatalogRef;
  readonly expected_source_revision?: string | null;
}

/** Request current overwrite and delete impact for one exact catalog ref. */
export interface CatalogDependentsRequest {
  readonly ref: CatalogRef;
}

/** One typed reverse dependency and its shortest distance from the target. */
export interface CatalogDependent {
  readonly ref: CatalogRef;
  readonly family: CatalogFamily;
  readonly revision: string;
  readonly minimum_depth: number;
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

/** Canonical saved component plus its current downstream impact. */
export interface CatalogMutationResult {
  readonly document: BuilderCatalogDocument;
  readonly impact: CatalogDependencyImpact;
}

/** Fork provenance and the complete newly persisted component. */
export interface CatalogForkResult {
  readonly source_ref: CatalogRef;
  readonly result: CatalogMutationResult;
}

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

/** Request an exact portable session closure, optionally revision-fenced. */
export interface CatalogSessionExportRequest {
  readonly session_ref: SessionRef;
  readonly expected_session_revision?: string | null;
}

/** One exact YAML file with its namespace-preserving portable path. */
export interface PortableCatalogYaml {
  readonly ref: CatalogRef;
  readonly family: CatalogFamily;
  readonly preserved_path: string;
  readonly exact_yaml: string;
  readonly document_digest: Sha256Digest;
  readonly revision: string;
}

/** Exact root session and complete dependency closure for portable export. */
export interface CatalogSessionExport {
  readonly contract_version: 1;
  readonly session_ref: SessionRef;
  readonly session_revision: string;
  readonly generation: string;
  readonly root: PortableCatalogYaml;
  readonly entries: ReadonlyArray<PortableCatalogYaml>;
  readonly document_digest: Sha256Digest;
  readonly closure_digest: Sha256Digest;
  readonly file_count: number;
  readonly total_bytes: number;
}

/** One exact closure dependency supplied without a client-selected path. */
export interface CatalogImportEntry {
  readonly ref: CatalogRef;
  readonly exact_yaml: string;
  readonly document_digest: Sha256Digest;
}

/** Bounded exact transport closure proposed for canonical atomic import. */
export interface CatalogClosureImportRequest {
  readonly contract_version: 1;
  readonly root_ref: SessionRef;
  readonly root_yaml: string;
  readonly document_digest: Sha256Digest;
  readonly closure_digest: Sha256Digest;
  readonly entries: ReadonlyArray<CatalogImportEntry>;
  readonly commit?: boolean;
}

/** One canonical user document proposed for transactional creation. */
export interface CatalogImportWrite {
  readonly ref: CatalogRef;
  readonly family: CatalogFamily;
  readonly exact_yaml: string;
  readonly document_digest: Sha256Digest;
}

/** Exact identity collision that import will never rename or deep-match. */
export interface CatalogImportCollision {
  readonly ref: CatalogRef;
  readonly reason: "shipped_missing" | "shipped_content_mismatch" | "user_content_mismatch";
  readonly incoming_digest: Sha256Digest;
  readonly existing_digest?: Sha256Digest | null;
  readonly existing_revision?: string | null;
}

/** Proposed, committed, unchanged, or blocked exact-closure import result. */
export interface CatalogImportResult {
  readonly outcome: "proposed" | "committed" | "unchanged" | "blocked";
  readonly generation: string;
  readonly document_digest: Sha256Digest;
  readonly closure_digest: Sha256Digest;
  readonly proposed_writes: ReadonlyArray<CatalogImportWrite>;
  readonly identical_refs: ReadonlyArray<CatalogRef>;
  readonly collisions: ReadonlyArray<CatalogImportCollision>;
}

/** Stable typed evidence for an authoring operation refused by the backend. */
export interface CatalogOperationRefusal {
  readonly code: "catalog_authoring.not_found" | "catalog_authoring.read_only" | "catalog_authoring.invalid_document" | "catalog_authoring.invalid_patch" | "catalog_authoring.invalid_graph" | "catalog_authoring.conflict" | "catalog_authoring.stale_revision" | "catalog_authoring.invalid_page_token" | "catalog_authoring.stale_page_token" | "catalog_authoring.impact_mismatch" | "catalog_authoring.dependents_exist" | "catalog_authoring.import_limit" | "catalog_authoring.import_digest_mismatch" | "catalog_authoring.import_incomplete" | "catalog_authoring.import_collision" | "catalog_authoring.persistence_failed";
  readonly message: string;
  readonly ref?: string | null;
  readonly expected_revision?: string | null;
  readonly current_revision?: string | null;
  readonly impact?: CatalogDependencyImpact | null;
  readonly collisions?: ReadonlyArray<CatalogImportCollision>;
  readonly cause_type?: string | null;
}

/** One resolved link-rule endpoint, flattened for builder display. */
export interface BuilderLinkEndpoint {
  readonly segment_id: string;
  readonly terminal_role: WizardTerminalRole;
  readonly terminal_medium: string | null;
  readonly min_elevation_deg: number | null;
  readonly node_ids: ReadonlyArray<string>;
}

/** Display projection of one ``ResolvedLinkRule``. The resolved rule's topology is a discriminated union; the builder needs the flat facts (mode, n, explicit pairs, range cap) to preview candidate geometry. This is a projection for display — candidate truth at runtime stays with OME. */
export interface BuilderLinkRule {
  readonly rule_id: string;
  readonly kind: "access" | "isl" | "relay" | "backbone" | "inter_body";
  readonly enabled: boolean;
  readonly endpoints: readonly [BuilderLinkEndpoint, BuilderLinkEndpoint];
  readonly topology_mode: string;
  readonly topology_n: number | null;
  readonly explicit_pairs: ReadonlyArray<readonly [string, string]>;
  readonly max_range_km: number | null;
}

/** One node's fixed-interface capacity for one rule, as allocated. */
export interface BuilderNodeInterfaceFacts {
  readonly node_id: string;
  readonly segment_id: string;
  readonly matching: number;
  readonly free: number;
}

/** One drawn preview pair: a runtime node pair whose frozen-epoch geometry passed every armed gate, oriented to the rule's endpoints server-side. canvas draws these directly and never re-derives pair identities. */
export interface BuilderPreviewPair {
  readonly rule_id: string;
  readonly kind: string;
  readonly node_a: string;
  readonly node_b: string;
}

/** How many TESTED pairs one reject reason accounts for. ``reason`` is the runtime's reject_reason verbatim (or ``no_geometry``); counts sum over ``pairs_tested`` — never an untested remainder. */
export interface BuilderPreviewReasonCount {
  readonly reason: "los_blocked" | "range_exceeded" | "elevation_below_min" | "field_of_regard" | "terminal_type_mismatch" | "no_geometry";
  readonly count: number;
}

/** The allocator's own outcome for one rule — the single capacity truth every display reports instead of re-deriving. For access rules nothing is consumed at resolve time (the runtime schedules access within terminal capacity): ``allocated_pairs`` counts the declared candidate universe and ``free`` always mirrors ``matching``. Displays must not present access facts as fixed allocation. */
export interface BuilderRuleAllocation {
  readonly rule_id: string;
  readonly kind: string;
  readonly allocated_pairs: number;
  readonly per_node: ReadonlyArray<BuilderNodeInterfaceFacts>;
}

/** The server's frozen-epoch visibility verdict for one link rule. NodalArc computes preview geometry through the same OME visibility composites the runtime uses; the builder renders these facts and never runs a second physics engine. ``preview_scope`` says whether geometry ran and, if not, why (``inter_body_pending``/``terrestrial_pending``/``disabled`` are typed walls). Only ``computed`` carries reason counts and drawn pairs. The preview is BOUNDED, not a simulation. ``pairs_total`` is the candidate universe size (a closed-form count, never a materialized pair set); ``pairs_tested`` is the deterministic subset geometry actually ran on (``min(pairs_total, budget)``, first pairs in authored/node-id order — never distance-ranked); ``pairs_drawn`` is how many tested pairs passed (the first such, capped to the draw cap). ``capped`` is true when the preview is partial on either axis (``pairs_tested < pairs_total`` or more passed than were drawn). Reason counts and drawn pairs describe the TESTED subset only. */
export interface BuilderRulePreview {
  readonly rule_id: string;
  readonly kind: string;
  readonly preview_scope: "computed" | "inter_body_pending" | "terrestrial_pending" | "disabled";
  readonly pairs_total: number;
  readonly pairs_tested: number;
  readonly pairs_drawn: number;
  readonly capped: boolean;
  readonly reason_counts: ReadonlyArray<BuilderPreviewReasonCount>;
  readonly drawable_pairs: ReadonlyArray<BuilderPreviewPair>;
}

/** One resolved node's facts for builder display, mirrored from ``ResolvedNode``. ``kind`` is carried explicitly so no consumer infers it from the ephemeris variant shape. Satellite placement (orbital elements, frames) lives in the ephemeris; ``epoch_position`` is the OME-propagated state used to seed renderers that cannot propagate that ephemeris variant locally. Ground placement is the resolver's ``surface_position``: the ephemeris only carries ground nodes that participate in space-link physics, while the world contains every resolved node — a gateway with no space links still exists. Hardware and network facts reuse the resolved models verbatim — they are the wire truth; the builder never re-shapes them. */
export interface BuilderWorldNode {
  readonly node_id: string;
  readonly local_node_id: string;
  readonly segment_id: string;
  readonly namespace: string | null;
  readonly kind: "satellite" | "ground_station" | "relay";
  readonly plane: number | null;
  readonly slot: number | null;
  readonly tags: ReadonlyArray<string>;
  readonly surface_position: ResolvedSurfacePosition | null;
  readonly epoch_position: NodePosition | null;
  readonly forwarding: "routed" | "host" | "bridge" | "control_only" | null;
  readonly terminal_inventory: ReadonlyArray<ResolvedTerminalBlock>;
  readonly interfaces: ResolvedNodeInterfaces | null;
  readonly originated_prefixes: OriginatedPrefixes | null;
}

/** One segment as the user named it — the world tree speaks their words, never bare runtime ids. */
export interface BuilderWorldSegment {
  readonly segment_id: string;
  readonly display_name: string;
}

/** Body origin in the session common frame at ``epoch_unix``. Positions and velocities are Earth-relative GCRS-like km vectors supplied by the backend ephemeris provider. The renderer may apply a visual scale or camera-relative transform, but these numbers remain the authoritative physical frame facts used to place bodies relative to one another. */
export interface EphemerisBodyFrame {
  readonly body_id: string;
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
  readonly segment_id: string | null;
  readonly local_node_id: string | null;
  readonly namespace: string | null;
  readonly tags: ReadonlyArray<string>;
  readonly reference_body: string;
  readonly frame_id: string;
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
  readonly segment_id: string | null;
  readonly local_node_id: string | null;
  readonly namespace: string | null;
  readonly tags: ReadonlyArray<string>;
  readonly reference_body: string;
  readonly frame_id: string;
}

/** TLE-backed satellite ephemeris for SGP4 propagation. */
export interface EphemerisNodeTLE {
  readonly type: "tle";
  readonly tle_line_1: string;
  readonly tle_line_2: string;
  readonly plane: number;
  readonly slot: number;
  readonly norad_id: number | null;
  readonly segment_id: string | null;
  readonly local_node_id: string | null;
  readonly namespace: string | null;
  readonly tags: ReadonlyArray<string>;
  readonly reference_body: string;
  readonly frame_id: string;
}

/** Position and velocity of a single node. Position is geodetic (WGS84). Velocity is ECEF (Earth-Centered Earth-Fixed) in km/s — includes Earth rotation subtraction, so it represents motion relative to the rotating Earth. Ground stations have zero velocity. The frontend's worldVelocity() function in astronomy.ts expects ECEF velocity and applies the view-frame rotation to produce world-frame velocity. */
export interface NodePosition {
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly alt_km: number;
  readonly vel_x_km_s: number;
  readonly vel_y_km_s: number;
  readonly vel_z_km_s: number;
}

/** Routing injection intent for a placed node. */
export interface OriginatedPrefixes {
  readonly ipv4: ReadonlyArray<string> | null;
  readonly ipv6: ReadonlyArray<string> | null;
}

/** A numbered interface address set. */
export interface ResolvedInterfaceAddress {
  readonly ipv4: string | null;
  readonly ipv6: string | null;
}

/** Numbered interfaces authored by placement or allocated by the resolver. */
export interface ResolvedNodeInterfaces {
  readonly lo0: ResolvedInterfaceAddress;
  readonly terr0: ResolvedInterfaceAddress | null;
}

/** Fixed body-surface position for one placed node. */
export interface ResolvedSurfacePosition {
  readonly body: "earth" | "luna";
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly alt_m: number;
}

/** Materialized terminal truth for one terminal block on one node. Built from the resolved satellite_type (satellites) or station/ground-set terminal config (ground stations). Consumers read this; they do not reload the source file. ``tracking_capacity`` is a ground-station-terminal concept (simultaneous links per terminal) and is ``None`` for satellite terminals. Optional fields are ``None`` only when the source legitimately omits them; the resolver fails (never invents a default) when a value is required for a supported runtime feature. */
export interface ResolvedTerminalBlock {
  readonly terminal_id: string;
  readonly owner_node_id: string;
  readonly endpoint_role: WizardTerminalRole;
  readonly medium: "rf" | "optical";
  readonly source_terminal_id: string | null;
  readonly link_role: string | null;
  readonly count: number;
  readonly tracking_capacity: number | null;
  readonly max_range_km: number | null;
  readonly min_elevation_deg: number | null;
  readonly field_of_regard_deg: number | null;
  readonly tracking_rate_deg_s: number | null;
  readonly bandwidth_mbps: number | null;
  readonly boresight: TerminalBoresight | SatGroundTerminalBoresight | null;
  readonly source_ref: string;
}

/** Boresight reference for a satellite ground-terminal FoR cone. ``target_body`` identifies which body the nadir vector points toward. The cone width itself lives on the terminal's ``field_of_regard_deg`` field as the full apex angle. */
export interface SatGroundTerminalBoresight {
  readonly target_body: "earth" | "luna";
  readonly mode: "nadir";
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

/** Boresight reference for a ground terminal field-of-regard cone. The cone width itself lives on the terminal's ``field_of_regard_deg`` field as the full apex angle. This model only states what direction that cone is centered on. */
export interface TerminalBoresight {
  readonly mode: "local_vertical" | "configured_topocentric" | "steerable_envelope";
  readonly configured_az_deg: number | null;
  readonly configured_el_deg: number | null;
  readonly min_az_deg: number | null;
  readonly max_az_deg: number | null;
  readonly min_el_deg: number | null;
  readonly max_el_deg: number | null;
}

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

/** A single insight about the constellation configuration. Severity levels: - "info": expected physics behavior, normal operation (e.g., Earth occlusion, range limits) - "note": topology characteristic worth knowing (e.g., full mesh, terminal allocation) - "warning": potential issue that may affect routing (e.g., tracking rate dropouts, coverage gaps) - "error": configuration problem that will prevent connectivity (e.g., no cross-plane links, station beyond visibility) */
export interface CoverageInsight {
  readonly severity: "info" | "note" | "warning" | "error";
  readonly message: string;
}

/** Ground station coverage statistics for one orbital period. */
export interface GsPreview {
  readonly per_station: Readonly<Record<string, GsStationPreview>>;
  readonly simultaneous_min: number;
  readonly simultaneous_max: number;
  readonly simultaneous_mean: number;
  readonly max_gap_s: number;
}

/** Per-ground-station coverage statistics. */
export interface GsStationPreview {
  readonly coverage_pct: number;
  readonly longest_gap_s: number;
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

/** Complete coverage preview result. */
export interface CoveragePreviewResult {
  readonly orbital_period_s: number;
  readonly preview_step_s: number;
  readonly isl: IslPreview;
  readonly ground_stations: GsPreview;
  readonly warnings: ReadonlyArray<CoverageInsight>;
}

/** Runtime validator descriptor generated from this backend application DTO. */
export const BUILDER_VISUAL_DRAFT_ENVELOPE_RUNTIME_DESCRIPTOR = {
  "kind": "object",
  "fields": {
    "contract_version": {
      "kind": "literal",
      "value": 1
    },
    "draft_revision": {
      "kind": "integer",
      "minimum": 0
    },
    "mode": {
      "kind": "enum",
      "values": [
        "structured",
        "opaque_yaml"
      ]
    },
    "target_ref": {
      "kind": "string",
      "pattern": "^(?:nodalarc|user):(?:sessions)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
    },
    "source_ref": {
      "kind": "union",
      "options": [
        {
          "kind": "string",
          "pattern": "^(?:nodalarc|user):(?:sessions)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "expected_session_revision": {
      "kind": "union",
      "options": [
        {
          "kind": "string",
          "min_length": 1
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "expected_catalog_revisions": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "ref": {
            "kind": "string",
            "pattern": "^(?:nodalarc|user):(?:bodies|constellations|nodes|orbits|payloads|sessions|site\\-sets|sites|space\\-node\\-sets|terminals)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
          },
          "expected_revision": {
            "kind": "string",
            "min_length": 1
          }
        },
        "additional": false
      }
    },
    "catalog_documents": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "ref": {
            "kind": "string",
            "pattern": "^(?:nodalarc|user):(?:bodies|constellations|nodes|orbits|payloads|sessions|site\\-sets|sites|space\\-node\\-sets|terminals)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
          },
          "document": {
            "kind": "object",
            "fields": {},
            "additional": {
              "kind": "json"
            }
          },
          "expected_revision": {
            "kind": "union",
            "options": [
              {
                "kind": "string",
                "min_length": 1
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          }
        },
        "additional": false
      }
    },
    "session_name_is_placeholder": {
      "kind": "boolean"
    },
    "reserved_authoring_ids": {
      "kind": "array",
      "items": {
        "kind": "string"
      }
    },
    "workspace": {
      "kind": "union",
      "options": [
        {
          "kind": "object",
          "fields": {
            "session_name": {
              "kind": "string"
            },
            "display_name": {
              "kind": "union",
              "options": [
                {
                  "kind": "string"
                },
                {
                  "kind": "null"
                }
              ],
              "exclusive": false
            },
            "description": {
              "kind": "union",
              "options": [
                {
                  "kind": "string"
                },
                {
                  "kind": "null"
                }
              ],
              "exclusive": false
            },
            "space": {
              "kind": "array",
              "items": {
                "kind": "object",
                "fields": {
                  "segment_id": {
                    "kind": "string"
                  },
                  "display_name": {
                    "kind": "string"
                  },
                  "node_ref": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "string",
                        "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "node_draft": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "object",
                        "fields": {
                          "id": {
                            "kind": "string"
                          },
                          "display_name": {
                            "kind": "string"
                          },
                          "forwarding": {
                            "kind": "union",
                            "options": [
                              {
                                "kind": "enum",
                                "values": [
                                  "routed",
                                  "host",
                                  "bridge",
                                  "control_only"
                                ]
                              },
                              {
                                "kind": "null"
                              }
                            ],
                            "exclusive": false
                          },
                          "ethernet": {
                            "kind": "array",
                            "items": {
                              "kind": "string"
                            }
                          },
                          "terminals": {
                            "kind": "array",
                            "items": {
                              "kind": "object",
                              "fields": {
                                "mount_id": {
                                  "kind": "string"
                                },
                                "role": {
                                  "kind": "union",
                                  "options": [
                                    {
                                      "kind": "enum",
                                      "values": [
                                        "access",
                                        "isl",
                                        "crosslink",
                                        "backbone"
                                      ]
                                    },
                                    {
                                      "kind": "null"
                                    }
                                  ],
                                  "exclusive": false
                                },
                                "terminal_ref": {
                                  "kind": "union",
                                  "options": [
                                    {
                                      "kind": "string",
                                      "pattern": "^(?:nodalarc|user):(?:terminals)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                                    },
                                    {
                                      "kind": "null"
                                    }
                                  ],
                                  "exclusive": false
                                },
                                "count": {
                                  "kind": "union",
                                  "options": [
                                    {
                                      "kind": "integer"
                                    },
                                    {
                                      "kind": "null"
                                    }
                                  ],
                                  "exclusive": false
                                },
                                "boresight": {
                                  "kind": "union",
                                  "options": [
                                    {
                                      "kind": "object",
                                      "fields": {
                                        "mode": {
                                          "kind": "literal",
                                          "value": "nadir"
                                        }
                                      },
                                      "additional": false
                                    },
                                    {
                                      "kind": "null"
                                    }
                                  ],
                                  "exclusive": false
                                }
                              },
                              "additional": false
                            }
                          }
                        },
                        "additional": false
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "orbit": {
                    "kind": "object",
                    "fields": {
                      "central_body": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "string",
                            "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "shape_kind": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "enum",
                            "values": [
                              "circular",
                              "elliptical"
                            ]
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "altitude_km": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "perigee_altitude_km": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "apogee_altitude_km": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "inclination_deg": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "raan_deg": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "argument_of_perigee_deg": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "mean_anomaly_deg": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "propagator": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "enum",
                            "values": [
                              "two_body",
                              "j2_mean_elements"
                            ]
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      }
                    },
                    "additional": false
                  },
                  "planes": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "integer"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "raan_spacing_deg": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "number"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "slots_per_plane": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "integer"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "phasing_mode": {
                    "kind": "enum",
                    "values": [
                      "walker_delta",
                      "walker_star",
                      "evenly_spaced_mean_anomaly"
                    ]
                  },
                  "phase_offset_deg": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "number"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  }
                },
                "additional": false
              }
            },
            "space_refs": {
              "kind": "array",
              "items": {
                "kind": "object",
                "fields": {
                  "segment_id": {
                    "kind": "string"
                  },
                  "source_ref": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "string",
                        "pattern": "^(?:nodalarc|user):(?:constellations|space\\-node\\-sets)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "label": {
                    "kind": "string"
                  }
                },
                "additional": false
              }
            },
            "ground": {
              "kind": "array",
              "items": {
                "kind": "object",
                "fields": {
                  "segment_id": {
                    "kind": "string"
                  },
                  "display_name": {
                    "kind": "string"
                  },
                  "members": {
                    "kind": "array",
                    "items": {
                      "kind": "object",
                      "fields": {
                        "member_id": {
                          "kind": "string"
                        },
                        "kind": {
                          "kind": "enum",
                          "values": [
                            "ref",
                            "draft"
                          ]
                        },
                        "ref": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "string",
                              "pattern": "^(?:nodalarc|user):(?:sites)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "site_id": {
                          "kind": "string"
                        },
                        "label": {
                          "kind": "string"
                        },
                        "summary": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "string"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "site": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "object",
                              "fields": {
                                "site_id": {
                                  "kind": "string"
                                },
                                "display_name": {
                                  "kind": "string"
                                },
                                "body": {
                                  "kind": "union",
                                  "options": [
                                    {
                                      "kind": "string",
                                      "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                                    },
                                    {
                                      "kind": "null"
                                    }
                                  ],
                                  "exclusive": false
                                },
                                "lat_deg": {
                                  "kind": "union",
                                  "options": [
                                    {
                                      "kind": "number"
                                    },
                                    {
                                      "kind": "null"
                                    }
                                  ],
                                  "exclusive": false
                                },
                                "lon_deg": {
                                  "kind": "union",
                                  "options": [
                                    {
                                      "kind": "number"
                                    },
                                    {
                                      "kind": "null"
                                    }
                                  ],
                                  "exclusive": false
                                },
                                "alt_m": {
                                  "kind": "union",
                                  "options": [
                                    {
                                      "kind": "number"
                                    },
                                    {
                                      "kind": "null"
                                    }
                                  ],
                                  "exclusive": false
                                },
                                "lan_ipv4": {
                                  "kind": "string"
                                },
                                "tags": {
                                  "kind": "array",
                                  "items": {
                                    "kind": "string"
                                  }
                                },
                                "nodes": {
                                  "kind": "array",
                                  "items": {
                                    "kind": "object",
                                    "fields": {
                                      "node_id": {
                                        "kind": "string"
                                      },
                                      "model_ref": {
                                        "kind": "union",
                                        "options": [
                                          {
                                            "kind": "string",
                                            "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                                          },
                                          {
                                            "kind": "null"
                                          }
                                        ],
                                        "exclusive": false
                                      },
                                      "installed": {
                                        "kind": "object",
                                        "fields": {},
                                        "additional": {
                                          "kind": "integer"
                                        }
                                      },
                                      "boresights": {
                                        "kind": "object",
                                        "fields": {},
                                        "additional": {
                                          "kind": "object",
                                          "fields": {
                                            "mode": {
                                              "kind": "literal",
                                              "value": "local_vertical"
                                            }
                                          },
                                          "additional": false
                                        }
                                      },
                                      "lo0_ipv4": {
                                        "kind": "string"
                                      },
                                      "terr0_ipv4": {
                                        "kind": "string"
                                      }
                                    },
                                    "additional": false
                                  }
                                }
                              },
                              "additional": false
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "scheduling_override": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "object",
                              "fields": {},
                              "additional": {
                                "kind": "json"
                              }
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        }
                      },
                      "additional": false
                    }
                  },
                  "stamp": {
                    "kind": "object",
                    "fields": {
                      "node_ref": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "string",
                            "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "installed": {
                        "kind": "object",
                        "fields": {},
                        "additional": {
                          "kind": "integer"
                        }
                      },
                      "boresights": {
                        "kind": "object",
                        "fields": {},
                        "additional": {
                          "kind": "object",
                          "fields": {
                            "mode": {
                              "kind": "literal",
                              "value": "local_vertical"
                            }
                          },
                          "additional": false
                        }
                      },
                      "body": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "string",
                            "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "lan_base": {
                        "kind": "string"
                      },
                      "loopback_base": {
                        "kind": "string"
                      }
                    },
                    "additional": false
                  },
                  "scheduling": {
                    "kind": "object",
                    "fields": {},
                    "additional": {
                      "kind": "json"
                    }
                  },
                  "originated_ipv4": {
                    "kind": "array",
                    "items": {
                      "kind": "string"
                    }
                  },
                  "tags": {
                    "kind": "array",
                    "items": {
                      "kind": "string"
                    }
                  }
                },
                "additional": false
              }
            },
            "ground_refs": {
              "kind": "array",
              "items": {
                "kind": "object",
                "fields": {
                  "segment_id": {
                    "kind": "string"
                  },
                  "site_set_ref": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "string",
                        "pattern": "^(?:nodalarc|user):(?:site\\-sets)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "label": {
                    "kind": "string"
                  },
                  "scheduling": {
                    "kind": "object",
                    "fields": {},
                    "additional": {
                      "kind": "json"
                    }
                  }
                },
                "additional": false
              }
            },
            "links": {
              "kind": "array",
              "items": {
                "kind": "object",
                "fields": {
                  "rule_id": {
                    "kind": "string"
                  },
                  "label": {
                    "kind": "string"
                  },
                  "enabled": {
                    "kind": "boolean"
                  },
                  "a": {
                    "kind": "object",
                    "fields": {
                      "segment_id": {
                        "kind": "string"
                      },
                      "tag": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "string"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "role": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "enum",
                            "values": [
                              "access",
                              "isl",
                              "crosslink",
                              "backbone"
                            ]
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "medium": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "enum",
                            "values": [
                              "rf",
                              "optical"
                            ]
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "min_elevation_deg": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      }
                    },
                    "additional": false
                  },
                  "b": {
                    "kind": "object",
                    "fields": {
                      "segment_id": {
                        "kind": "string"
                      },
                      "tag": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "string"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "role": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "enum",
                            "values": [
                              "access",
                              "isl",
                              "crosslink",
                              "backbone"
                            ]
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "medium": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "enum",
                            "values": [
                              "rf",
                              "optical"
                            ]
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      },
                      "min_elevation_deg": {
                        "kind": "union",
                        "options": [
                          {
                            "kind": "number"
                          },
                          {
                            "kind": "null"
                          }
                        ],
                        "exclusive": false
                      }
                    },
                    "additional": false
                  },
                  "topology_mode": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "enum",
                        "values": [
                          "visible_candidates",
                          "nearest_n"
                        ]
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "topology_n": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "integer"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "max_range_km": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "number"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  }
                },
                "additional": false
              }
            },
            "routing_domains": {
              "kind": "array",
              "items": {
                "kind": "object",
                "fields": {
                  "domain_id": {
                    "kind": "string"
                  },
                  "label": {
                    "kind": "string"
                  },
                  "protocol": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "enum",
                        "values": [
                          "isis",
                          "ospf",
                          "bgp",
                          "static"
                        ]
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "member_segment_ids": {
                    "kind": "array",
                    "items": {
                      "kind": "string"
                    }
                  },
                  "hello_interval_s": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "number"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "hold_interval_s": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "number"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  }
                },
                "additional": false
              }
            },
            "boundaries": {
              "kind": "array",
              "items": {
                "kind": "object",
                "fields": {
                  "boundary_id": {
                    "kind": "string"
                  },
                  "over_rule_id": {
                    "kind": "string"
                  },
                  "adapter": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "enum",
                        "values": [
                          "static_ip",
                          "bgp",
                          "dtn_bundle"
                        ]
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "from_domain_id": {
                    "kind": "string"
                  },
                  "to_domain_id": {
                    "kind": "string"
                  },
                  "export_node_loopbacks": {
                    "kind": "boolean"
                  }
                },
                "additional": false
              }
            },
            "max_pairs_per_rule": {
              "kind": "union",
              "options": [
                {
                  "kind": "integer"
                },
                {
                  "kind": "null"
                }
              ],
              "exclusive": false
            },
            "max_pairs_per_tick": {
              "kind": "union",
              "options": [
                {
                  "kind": "integer"
                },
                {
                  "kind": "null"
                }
              ],
              "exclusive": false
            },
            "start_time": {
              "kind": "string"
            },
            "step_seconds": {
              "kind": "union",
              "options": [
                {
                  "kind": "number"
                },
                {
                  "kind": "null"
                }
              ],
              "exclusive": false
            },
            "compression": {
              "kind": "union",
              "options": [
                {
                  "kind": "number"
                },
                {
                  "kind": "null"
                }
              ],
              "exclusive": false
            }
          },
          "additional": false
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "session_yaml": {
      "kind": "union",
      "options": [
        {
          "kind": "string"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    }
  },
  "additional": false
} as const satisfies BuilderVisualRuntimeDescriptor;

/** Runtime validator descriptor generated from this backend application DTO. */
export const CATALOG_COMPONENT_DRAFT_ENVELOPE_RUNTIME_DESCRIPTOR = {
  "kind": "object",
  "fields": {
    "contract_version": {
      "kind": "literal",
      "value": 1
    },
    "draft_revision": {
      "kind": "integer",
      "minimum": 0
    },
    "family": {
      "kind": "enum",
      "values": [
        "bodies",
        "terminals",
        "payloads",
        "orbits",
        "nodes",
        "sites",
        "site-sets",
        "constellations",
        "space-node-sets"
      ]
    },
    "target_ref": {
      "kind": "string",
      "pattern": "^(?:nodalarc|user):(?:bodies|constellations|nodes|orbits|payloads|sessions|site\\-sets|sites|space\\-node\\-sets|terminals)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
    },
    "source_ref": {
      "kind": "union",
      "options": [
        {
          "kind": "string",
          "pattern": "^(?:nodalarc|user):(?:bodies|constellations|nodes|orbits|payloads|sessions|site\\-sets|sites|space\\-node\\-sets|terminals)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "expected_source_revision": {
      "kind": "union",
      "options": [
        {
          "kind": "string",
          "min_length": 1
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "expected_target_revision": {
      "kind": "union",
      "options": [
        {
          "kind": "string",
          "min_length": 1
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "document": {
      "kind": "object",
      "fields": {},
      "additional": {
        "kind": "json"
      }
    },
    "issues": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "code": {
            "kind": "string",
            "min_length": 1
          },
          "stage": {
            "kind": "enum",
            "values": [
              "structural",
              "reference",
              "runtime_support"
            ]
          },
          "message": {
            "kind": "string",
            "min_length": 1
          },
          "pointer": {
            "kind": "string",
            "min_length": 1,
            "max_length": 2048
          },
          "blocks": {
            "kind": "array",
            "items": {
              "kind": "enum",
              "values": [
                "save",
                "deploy"
              ]
            }
          }
        },
        "additional": false
      }
    }
  },
  "additional": false
} as const satisfies BuilderVisualRuntimeDescriptor;

/** Runtime validator descriptor generated from this backend application DTO. */
export const BUILDER_VISUAL_WORKSPACE_RUNTIME_DESCRIPTOR = {
  "kind": "object",
  "fields": {
    "session_name": {
      "kind": "string"
    },
    "display_name": {
      "kind": "union",
      "options": [
        {
          "kind": "string"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "description": {
      "kind": "union",
      "options": [
        {
          "kind": "string"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "space": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "segment_id": {
            "kind": "string"
          },
          "display_name": {
            "kind": "string"
          },
          "node_ref": {
            "kind": "union",
            "options": [
              {
                "kind": "string",
                "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "node_draft": {
            "kind": "union",
            "options": [
              {
                "kind": "object",
                "fields": {
                  "id": {
                    "kind": "string"
                  },
                  "display_name": {
                    "kind": "string"
                  },
                  "forwarding": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "enum",
                        "values": [
                          "routed",
                          "host",
                          "bridge",
                          "control_only"
                        ]
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "ethernet": {
                    "kind": "array",
                    "items": {
                      "kind": "string"
                    }
                  },
                  "terminals": {
                    "kind": "array",
                    "items": {
                      "kind": "object",
                      "fields": {
                        "mount_id": {
                          "kind": "string"
                        },
                        "role": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "enum",
                              "values": [
                                "access",
                                "isl",
                                "crosslink",
                                "backbone"
                              ]
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "terminal_ref": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "string",
                              "pattern": "^(?:nodalarc|user):(?:terminals)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "count": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "integer"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "boresight": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "object",
                              "fields": {
                                "mode": {
                                  "kind": "literal",
                                  "value": "nadir"
                                }
                              },
                              "additional": false
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        }
                      },
                      "additional": false
                    }
                  }
                },
                "additional": false
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "orbit": {
            "kind": "object",
            "fields": {
              "central_body": {
                "kind": "union",
                "options": [
                  {
                    "kind": "string",
                    "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "shape_kind": {
                "kind": "union",
                "options": [
                  {
                    "kind": "enum",
                    "values": [
                      "circular",
                      "elliptical"
                    ]
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "altitude_km": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "perigee_altitude_km": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "apogee_altitude_km": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "inclination_deg": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "raan_deg": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "argument_of_perigee_deg": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "mean_anomaly_deg": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "propagator": {
                "kind": "union",
                "options": [
                  {
                    "kind": "enum",
                    "values": [
                      "two_body",
                      "j2_mean_elements"
                    ]
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              }
            },
            "additional": false
          },
          "planes": {
            "kind": "union",
            "options": [
              {
                "kind": "integer"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "raan_spacing_deg": {
            "kind": "union",
            "options": [
              {
                "kind": "number"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "slots_per_plane": {
            "kind": "union",
            "options": [
              {
                "kind": "integer"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "phasing_mode": {
            "kind": "enum",
            "values": [
              "walker_delta",
              "walker_star",
              "evenly_spaced_mean_anomaly"
            ]
          },
          "phase_offset_deg": {
            "kind": "union",
            "options": [
              {
                "kind": "number"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          }
        },
        "additional": false
      }
    },
    "space_refs": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "segment_id": {
            "kind": "string"
          },
          "source_ref": {
            "kind": "union",
            "options": [
              {
                "kind": "string",
                "pattern": "^(?:nodalarc|user):(?:constellations|space\\-node\\-sets)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "label": {
            "kind": "string"
          }
        },
        "additional": false
      }
    },
    "ground": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "segment_id": {
            "kind": "string"
          },
          "display_name": {
            "kind": "string"
          },
          "members": {
            "kind": "array",
            "items": {
              "kind": "object",
              "fields": {
                "member_id": {
                  "kind": "string"
                },
                "kind": {
                  "kind": "enum",
                  "values": [
                    "ref",
                    "draft"
                  ]
                },
                "ref": {
                  "kind": "union",
                  "options": [
                    {
                      "kind": "string",
                      "pattern": "^(?:nodalarc|user):(?:sites)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                    },
                    {
                      "kind": "null"
                    }
                  ],
                  "exclusive": false
                },
                "site_id": {
                  "kind": "string"
                },
                "label": {
                  "kind": "string"
                },
                "summary": {
                  "kind": "union",
                  "options": [
                    {
                      "kind": "string"
                    },
                    {
                      "kind": "null"
                    }
                  ],
                  "exclusive": false
                },
                "site": {
                  "kind": "union",
                  "options": [
                    {
                      "kind": "object",
                      "fields": {
                        "site_id": {
                          "kind": "string"
                        },
                        "display_name": {
                          "kind": "string"
                        },
                        "body": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "string",
                              "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "lat_deg": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "number"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "lon_deg": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "number"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "alt_m": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "number"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "lan_ipv4": {
                          "kind": "string"
                        },
                        "tags": {
                          "kind": "array",
                          "items": {
                            "kind": "string"
                          }
                        },
                        "nodes": {
                          "kind": "array",
                          "items": {
                            "kind": "object",
                            "fields": {
                              "node_id": {
                                "kind": "string"
                              },
                              "model_ref": {
                                "kind": "union",
                                "options": [
                                  {
                                    "kind": "string",
                                    "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                                  },
                                  {
                                    "kind": "null"
                                  }
                                ],
                                "exclusive": false
                              },
                              "installed": {
                                "kind": "object",
                                "fields": {},
                                "additional": {
                                  "kind": "integer"
                                }
                              },
                              "boresights": {
                                "kind": "object",
                                "fields": {},
                                "additional": {
                                  "kind": "object",
                                  "fields": {
                                    "mode": {
                                      "kind": "literal",
                                      "value": "local_vertical"
                                    }
                                  },
                                  "additional": false
                                }
                              },
                              "lo0_ipv4": {
                                "kind": "string"
                              },
                              "terr0_ipv4": {
                                "kind": "string"
                              }
                            },
                            "additional": false
                          }
                        }
                      },
                      "additional": false
                    },
                    {
                      "kind": "null"
                    }
                  ],
                  "exclusive": false
                },
                "scheduling_override": {
                  "kind": "union",
                  "options": [
                    {
                      "kind": "object",
                      "fields": {},
                      "additional": {
                        "kind": "json"
                      }
                    },
                    {
                      "kind": "null"
                    }
                  ],
                  "exclusive": false
                }
              },
              "additional": false
            }
          },
          "stamp": {
            "kind": "object",
            "fields": {
              "node_ref": {
                "kind": "union",
                "options": [
                  {
                    "kind": "string",
                    "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "installed": {
                "kind": "object",
                "fields": {},
                "additional": {
                  "kind": "integer"
                }
              },
              "boresights": {
                "kind": "object",
                "fields": {},
                "additional": {
                  "kind": "object",
                  "fields": {
                    "mode": {
                      "kind": "literal",
                      "value": "local_vertical"
                    }
                  },
                  "additional": false
                }
              },
              "body": {
                "kind": "union",
                "options": [
                  {
                    "kind": "string",
                    "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "lan_base": {
                "kind": "string"
              },
              "loopback_base": {
                "kind": "string"
              }
            },
            "additional": false
          },
          "scheduling": {
            "kind": "object",
            "fields": {},
            "additional": {
              "kind": "json"
            }
          },
          "originated_ipv4": {
            "kind": "array",
            "items": {
              "kind": "string"
            }
          },
          "tags": {
            "kind": "array",
            "items": {
              "kind": "string"
            }
          }
        },
        "additional": false
      }
    },
    "ground_refs": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "segment_id": {
            "kind": "string"
          },
          "site_set_ref": {
            "kind": "union",
            "options": [
              {
                "kind": "string",
                "pattern": "^(?:nodalarc|user):(?:site\\-sets)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "label": {
            "kind": "string"
          },
          "scheduling": {
            "kind": "object",
            "fields": {},
            "additional": {
              "kind": "json"
            }
          }
        },
        "additional": false
      }
    },
    "links": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "rule_id": {
            "kind": "string"
          },
          "label": {
            "kind": "string"
          },
          "enabled": {
            "kind": "boolean"
          },
          "a": {
            "kind": "object",
            "fields": {
              "segment_id": {
                "kind": "string"
              },
              "tag": {
                "kind": "union",
                "options": [
                  {
                    "kind": "string"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "role": {
                "kind": "union",
                "options": [
                  {
                    "kind": "enum",
                    "values": [
                      "access",
                      "isl",
                      "crosslink",
                      "backbone"
                    ]
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "medium": {
                "kind": "union",
                "options": [
                  {
                    "kind": "enum",
                    "values": [
                      "rf",
                      "optical"
                    ]
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "min_elevation_deg": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              }
            },
            "additional": false
          },
          "b": {
            "kind": "object",
            "fields": {
              "segment_id": {
                "kind": "string"
              },
              "tag": {
                "kind": "union",
                "options": [
                  {
                    "kind": "string"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "role": {
                "kind": "union",
                "options": [
                  {
                    "kind": "enum",
                    "values": [
                      "access",
                      "isl",
                      "crosslink",
                      "backbone"
                    ]
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "medium": {
                "kind": "union",
                "options": [
                  {
                    "kind": "enum",
                    "values": [
                      "rf",
                      "optical"
                    ]
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              },
              "min_elevation_deg": {
                "kind": "union",
                "options": [
                  {
                    "kind": "number"
                  },
                  {
                    "kind": "null"
                  }
                ],
                "exclusive": false
              }
            },
            "additional": false
          },
          "topology_mode": {
            "kind": "union",
            "options": [
              {
                "kind": "enum",
                "values": [
                  "visible_candidates",
                  "nearest_n"
                ]
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "topology_n": {
            "kind": "union",
            "options": [
              {
                "kind": "integer"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "max_range_km": {
            "kind": "union",
            "options": [
              {
                "kind": "number"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          }
        },
        "additional": false
      }
    },
    "routing_domains": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "domain_id": {
            "kind": "string"
          },
          "label": {
            "kind": "string"
          },
          "protocol": {
            "kind": "union",
            "options": [
              {
                "kind": "enum",
                "values": [
                  "isis",
                  "ospf",
                  "bgp",
                  "static"
                ]
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "member_segment_ids": {
            "kind": "array",
            "items": {
              "kind": "string"
            }
          },
          "hello_interval_s": {
            "kind": "union",
            "options": [
              {
                "kind": "number"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "hold_interval_s": {
            "kind": "union",
            "options": [
              {
                "kind": "number"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          }
        },
        "additional": false
      }
    },
    "boundaries": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "boundary_id": {
            "kind": "string"
          },
          "over_rule_id": {
            "kind": "string"
          },
          "adapter": {
            "kind": "union",
            "options": [
              {
                "kind": "enum",
                "values": [
                  "static_ip",
                  "bgp",
                  "dtn_bundle"
                ]
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "from_domain_id": {
            "kind": "string"
          },
          "to_domain_id": {
            "kind": "string"
          },
          "export_node_loopbacks": {
            "kind": "boolean"
          }
        },
        "additional": false
      }
    },
    "max_pairs_per_rule": {
      "kind": "union",
      "options": [
        {
          "kind": "integer"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "max_pairs_per_tick": {
      "kind": "union",
      "options": [
        {
          "kind": "integer"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "start_time": {
      "kind": "string"
    },
    "step_seconds": {
      "kind": "union",
      "options": [
        {
          "kind": "number"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "compression": {
      "kind": "union",
      "options": [
        {
          "kind": "number"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    }
  },
  "additional": false
} as const satisfies BuilderVisualRuntimeDescriptor;

/** Runtime validator descriptor generated from this backend application DTO. */
export const BUILDER_VISUAL_SPACE_DRAFT_RUNTIME_DESCRIPTOR = {
  "kind": "object",
  "fields": {
    "segment_id": {
      "kind": "string"
    },
    "display_name": {
      "kind": "string"
    },
    "node_ref": {
      "kind": "union",
      "options": [
        {
          "kind": "string",
          "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "node_draft": {
      "kind": "union",
      "options": [
        {
          "kind": "object",
          "fields": {
            "id": {
              "kind": "string"
            },
            "display_name": {
              "kind": "string"
            },
            "forwarding": {
              "kind": "union",
              "options": [
                {
                  "kind": "enum",
                  "values": [
                    "routed",
                    "host",
                    "bridge",
                    "control_only"
                  ]
                },
                {
                  "kind": "null"
                }
              ],
              "exclusive": false
            },
            "ethernet": {
              "kind": "array",
              "items": {
                "kind": "string"
              }
            },
            "terminals": {
              "kind": "array",
              "items": {
                "kind": "object",
                "fields": {
                  "mount_id": {
                    "kind": "string"
                  },
                  "role": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "enum",
                        "values": [
                          "access",
                          "isl",
                          "crosslink",
                          "backbone"
                        ]
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "terminal_ref": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "string",
                        "pattern": "^(?:nodalarc|user):(?:terminals)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "count": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "integer"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "boresight": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "object",
                        "fields": {
                          "mode": {
                            "kind": "literal",
                            "value": "nadir"
                          }
                        },
                        "additional": false
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  }
                },
                "additional": false
              }
            }
          },
          "additional": false
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "orbit": {
      "kind": "object",
      "fields": {
        "central_body": {
          "kind": "union",
          "options": [
            {
              "kind": "string",
              "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "shape_kind": {
          "kind": "union",
          "options": [
            {
              "kind": "enum",
              "values": [
                "circular",
                "elliptical"
              ]
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "altitude_km": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "perigee_altitude_km": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "apogee_altitude_km": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "inclination_deg": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "raan_deg": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "argument_of_perigee_deg": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "mean_anomaly_deg": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "propagator": {
          "kind": "union",
          "options": [
            {
              "kind": "enum",
              "values": [
                "two_body",
                "j2_mean_elements"
              ]
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        }
      },
      "additional": false
    },
    "planes": {
      "kind": "union",
      "options": [
        {
          "kind": "integer"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "raan_spacing_deg": {
      "kind": "union",
      "options": [
        {
          "kind": "number"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "slots_per_plane": {
      "kind": "union",
      "options": [
        {
          "kind": "integer"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "phasing_mode": {
      "kind": "enum",
      "values": [
        "walker_delta",
        "walker_star",
        "evenly_spaced_mean_anomaly"
      ]
    },
    "phase_offset_deg": {
      "kind": "union",
      "options": [
        {
          "kind": "number"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    }
  },
  "additional": false
} as const satisfies BuilderVisualRuntimeDescriptor;

/** Runtime validator descriptor generated from this backend application DTO. */
export const BUILDER_VISUAL_GROUND_DRAFT_RUNTIME_DESCRIPTOR = {
  "kind": "object",
  "fields": {
    "segment_id": {
      "kind": "string"
    },
    "display_name": {
      "kind": "string"
    },
    "members": {
      "kind": "array",
      "items": {
        "kind": "object",
        "fields": {
          "member_id": {
            "kind": "string"
          },
          "kind": {
            "kind": "enum",
            "values": [
              "ref",
              "draft"
            ]
          },
          "ref": {
            "kind": "union",
            "options": [
              {
                "kind": "string",
                "pattern": "^(?:nodalarc|user):(?:sites)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "site_id": {
            "kind": "string"
          },
          "label": {
            "kind": "string"
          },
          "summary": {
            "kind": "union",
            "options": [
              {
                "kind": "string"
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "site": {
            "kind": "union",
            "options": [
              {
                "kind": "object",
                "fields": {
                  "site_id": {
                    "kind": "string"
                  },
                  "display_name": {
                    "kind": "string"
                  },
                  "body": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "string",
                        "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "lat_deg": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "number"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "lon_deg": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "number"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "alt_m": {
                    "kind": "union",
                    "options": [
                      {
                        "kind": "number"
                      },
                      {
                        "kind": "null"
                      }
                    ],
                    "exclusive": false
                  },
                  "lan_ipv4": {
                    "kind": "string"
                  },
                  "tags": {
                    "kind": "array",
                    "items": {
                      "kind": "string"
                    }
                  },
                  "nodes": {
                    "kind": "array",
                    "items": {
                      "kind": "object",
                      "fields": {
                        "node_id": {
                          "kind": "string"
                        },
                        "model_ref": {
                          "kind": "union",
                          "options": [
                            {
                              "kind": "string",
                              "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
                            },
                            {
                              "kind": "null"
                            }
                          ],
                          "exclusive": false
                        },
                        "installed": {
                          "kind": "object",
                          "fields": {},
                          "additional": {
                            "kind": "integer"
                          }
                        },
                        "boresights": {
                          "kind": "object",
                          "fields": {},
                          "additional": {
                            "kind": "object",
                            "fields": {
                              "mode": {
                                "kind": "literal",
                                "value": "local_vertical"
                              }
                            },
                            "additional": false
                          }
                        },
                        "lo0_ipv4": {
                          "kind": "string"
                        },
                        "terr0_ipv4": {
                          "kind": "string"
                        }
                      },
                      "additional": false
                    }
                  }
                },
                "additional": false
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          },
          "scheduling_override": {
            "kind": "union",
            "options": [
              {
                "kind": "object",
                "fields": {},
                "additional": {
                  "kind": "json"
                }
              },
              {
                "kind": "null"
              }
            ],
            "exclusive": false
          }
        },
        "additional": false
      }
    },
    "stamp": {
      "kind": "object",
      "fields": {
        "node_ref": {
          "kind": "union",
          "options": [
            {
              "kind": "string",
              "pattern": "^(?:nodalarc|user):(?:nodes)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "installed": {
          "kind": "object",
          "fields": {},
          "additional": {
            "kind": "integer"
          }
        },
        "boresights": {
          "kind": "object",
          "fields": {},
          "additional": {
            "kind": "object",
            "fields": {
              "mode": {
                "kind": "literal",
                "value": "local_vertical"
              }
            },
            "additional": false
          }
        },
        "body": {
          "kind": "union",
          "options": [
            {
              "kind": "string",
              "pattern": "^(?:nodalarc|user):(?:bodies)/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\\.(?:yaml|yml)$"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "lan_base": {
          "kind": "string"
        },
        "loopback_base": {
          "kind": "string"
        }
      },
      "additional": false
    },
    "scheduling": {
      "kind": "object",
      "fields": {},
      "additional": {
        "kind": "json"
      }
    },
    "originated_ipv4": {
      "kind": "array",
      "items": {
        "kind": "string"
      }
    },
    "tags": {
      "kind": "array",
      "items": {
        "kind": "string"
      }
    }
  },
  "additional": false
} as const satisfies BuilderVisualRuntimeDescriptor;

/** Runtime validator descriptor generated from this backend application DTO. */
export const BUILDER_VISUAL_LINK_RULE_RUNTIME_DESCRIPTOR = {
  "kind": "object",
  "fields": {
    "rule_id": {
      "kind": "string"
    },
    "label": {
      "kind": "string"
    },
    "enabled": {
      "kind": "boolean"
    },
    "a": {
      "kind": "object",
      "fields": {
        "segment_id": {
          "kind": "string"
        },
        "tag": {
          "kind": "union",
          "options": [
            {
              "kind": "string"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "role": {
          "kind": "union",
          "options": [
            {
              "kind": "enum",
              "values": [
                "access",
                "isl",
                "crosslink",
                "backbone"
              ]
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "medium": {
          "kind": "union",
          "options": [
            {
              "kind": "enum",
              "values": [
                "rf",
                "optical"
              ]
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "min_elevation_deg": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        }
      },
      "additional": false
    },
    "b": {
      "kind": "object",
      "fields": {
        "segment_id": {
          "kind": "string"
        },
        "tag": {
          "kind": "union",
          "options": [
            {
              "kind": "string"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "role": {
          "kind": "union",
          "options": [
            {
              "kind": "enum",
              "values": [
                "access",
                "isl",
                "crosslink",
                "backbone"
              ]
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "medium": {
          "kind": "union",
          "options": [
            {
              "kind": "enum",
              "values": [
                "rf",
                "optical"
              ]
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        },
        "min_elevation_deg": {
          "kind": "union",
          "options": [
            {
              "kind": "number"
            },
            {
              "kind": "null"
            }
          ],
          "exclusive": false
        }
      },
      "additional": false
    },
    "topology_mode": {
      "kind": "union",
      "options": [
        {
          "kind": "enum",
          "values": [
            "visible_candidates",
            "nearest_n"
          ]
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "topology_n": {
      "kind": "union",
      "options": [
        {
          "kind": "integer"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "max_range_km": {
      "kind": "union",
      "options": [
        {
          "kind": "number"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    }
  },
  "additional": false
} as const satisfies BuilderVisualRuntimeDescriptor;

/** Runtime validator descriptor generated from this backend application DTO. */
export const BUILDER_VISUAL_ROUTING_DOMAIN_RUNTIME_DESCRIPTOR = {
  "kind": "object",
  "fields": {
    "domain_id": {
      "kind": "string"
    },
    "label": {
      "kind": "string"
    },
    "protocol": {
      "kind": "union",
      "options": [
        {
          "kind": "enum",
          "values": [
            "isis",
            "ospf",
            "bgp",
            "static"
          ]
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "member_segment_ids": {
      "kind": "array",
      "items": {
        "kind": "string"
      }
    },
    "hello_interval_s": {
      "kind": "union",
      "options": [
        {
          "kind": "number"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "hold_interval_s": {
      "kind": "union",
      "options": [
        {
          "kind": "number"
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    }
  },
  "additional": false
} as const satisfies BuilderVisualRuntimeDescriptor;

/** Runtime validator descriptor generated from this backend application DTO. */
export const BUILDER_VISUAL_ROUTING_BOUNDARY_RUNTIME_DESCRIPTOR = {
  "kind": "object",
  "fields": {
    "boundary_id": {
      "kind": "string"
    },
    "over_rule_id": {
      "kind": "string"
    },
    "adapter": {
      "kind": "union",
      "options": [
        {
          "kind": "enum",
          "values": [
            "static_ip",
            "bgp",
            "dtn_bundle"
          ]
        },
        {
          "kind": "null"
        }
      ],
      "exclusive": false
    },
    "from_domain_id": {
      "kind": "string"
    },
    "to_domain_id": {
      "kind": "string"
    },
    "export_node_loopbacks": {
      "kind": "boolean"
    }
  },
  "additional": false
} as const satisfies BuilderVisualRuntimeDescriptor;
