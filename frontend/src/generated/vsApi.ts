// GENERATED FILE — DO NOT EDIT BY HAND.
// Sources of truth: backend Pydantic application contracts in
// lib/nodalarc/models.
// Regenerate: make generate-contracts
//
// The runtime contracts VS-API serves and the clients it accepts.

import type {
  BodySampleInstant,
  EphemerisBodyFrame,
  EphemerisNodeFixed,
  EphemerisNodeKeplerian,
  EphemerisNodeTLE,
  GroundBoresightMode,
  JsonValue,
  NodeKind,
  NodeRole,
  NodeRoutingInstance,
  SatGroundBoresightMode,
  SupportedSurfaceBody,
  TopologyMode,
} from "../builder/generated/builderApi";

export type StateFeedFrame = StateSnapshot | SessionEphemerisFrame | BodySamplesFrame | WiringProgressFrame | SessionTransitioningFrame | SessionReadyFrame | SessionFailedFrame;

export type StateSocketCommand = DebugStreamCommand | DebugStopCommand | DebugStopAllCommand;

export type TerminalMessage = TerminalInput | TerminalResize | TerminalOutput;

export type DebugSourceControl = DebugControlAcknowledgedFact | DebugControlRejectedFact | DebugControlUnansweredFact | DebugControlFailedFact;

/**  */
export type ActuationExplanationReason = "actuation_diverged";

/** Realization facts. The client decides in_flight vs faulted by comparing ``actuation_elapsed_ms`` to ``fault_after_ms`` — the backend states the raw facts and carries the contract bounds; it never assigns the family. ``diverged_since`` is the wall-clock instant (UTC) the SCHEDULER folded this pair into its desired set without kernel proof (recovered from the retained ActualLinkSnapshot, not VS-API's observation time); ``actuation_elapsed_ms`` is the skew-free age of that divergence at compose time. Both are ``None`` when the pair is not diverged, or when the Scheduler's pending clock has not yet reached VS-API. ``expected_latency_ms``/ ``fault_after_ms`` are the ``simulation.actuation`` contract, wall-clock not sim-time: a divergence younger than ``fault_after_ms`` is calm in_flight, at/older is faulted; the deadline a UI shows is ``diverged_since + fault_after_ms``. */
export interface ActuationFacts {
  readonly state: ActuationState;
  readonly ome_desired: boolean;
  readonly kernel_up: boolean;
  readonly diverged: boolean;
  readonly diverged_since: string | null;
  readonly actuation_elapsed_ms: number | null;
  readonly expected_latency_ms: number | null;
  readonly fault_after_ms: number | null;
}

/** Session-level Scheduler actuation health. */
export interface ActuationHealth {
  readonly session_id: string;
  readonly wiring_generation: string;
  readonly scheduler_instances: ReadonlyArray<ActuationHealthInstance>;
}

/** Latest actuation state for one GS on one Scheduler instance. */
export interface ActuationHealthGroundStation {
  readonly gs_id: string;
  readonly actuation_state: ActuationState;
  readonly since: string | null;
  readonly reason_code: SchedulerOpsCode;
  readonly blocking_new_ground_link_up: boolean;
  readonly recovery_status: Readonly<Record<string, JsonValue>>;
}

/** Aggregated actuation health for one Scheduler instance. */
export interface ActuationHealthInstance {
  readonly scheduler_instance_id: string;
  readonly hostname: string;
  readonly status: "clean" | "dirty" | "degraded" | "unknown";
  readonly ground_stations: ReadonlyArray<ActuationHealthGroundStation>;
}

/** User-visible Scheduler actuation problem for one ground station. */
export interface ActuationNotice {
  readonly gs_id: string;
  readonly actuation_state: ActuationState;
  readonly reason_code: SchedulerOpsCode;
  readonly message: string;
  readonly since: string | null;
  readonly blocking_new_ground_link_up: boolean;
  readonly affected_pairs: ReadonlyArray<ReadonlyArray<string>>;
  readonly desired_pairs_for_gs: ReadonlyArray<ReadonlyArray<string>>;
  readonly actual_pairs_for_gs: ReadonlyArray<ReadonlyArray<string>>;
  readonly ome_visible_scheduled_pairs_for_gs: ReadonlyArray<ReadonlyArray<string>>;
  readonly recovery_status: Readonly<Record<string, JsonValue>>;
  readonly last_event: Readonly<Record<string, JsonValue>>;
}

/**  */
export type ActuationState = "clean" | "actuation_blocked" | "kernel_dirty" | "unknown";

/**  */
export type AddressFamily = "ipv4" | "ipv6";

/** The API key a client sends as its bearer token. */
export interface AuthToken {
  readonly token: string;
}

/**  */
export type BindingReasonCode = DecisionReasonCode | FailingActuationState | ActuationExplanationReason;

/** The latest body-sample window as the state socket sends it: on connect and whenever OME publishes a new window. */
export interface BodySamplesFrame {
  readonly epoch_id: number;
  readonly interval_s: number;
  readonly samples: ReadonlyArray<BodySampleInstant>;
  readonly msg_type: "body_samples";
}

/** The pair the card leads with. Ordering is by meaning, resolved by the composer: a viable-but-withheld pair (`viable_withheld=True` — cleared physics/terminal gates, withheld by policy/capacity) dominates a physics near-miss. Only when no viable pair exists is the closest-to-visible rejected pair chosen. */
export interface CandidateFacts {
  readonly pair: readonly [string, string];
  readonly binding_gate: FunnelGate | null;
  readonly binding_reason_code: BindingReasonCode | null;
  readonly rejecting_endpoint: GroundVisibilityRejectingEndpoint | null;
  readonly range_km: number;
  readonly elevation_deg: number;
  readonly viable_withheld: boolean;
}

/** What a link's ended contact lost, end by end, with each end's cause: a local link's in-flight packets at its close (a sample), a long-delay end's packets still queued at its drain deadline, a seek or a scenario command (counts), and packets each stopped transmitter refused while its contact drained. */
export interface ContactLoss {
  readonly node_a: string;
  readonly node_b: string;
  readonly contact_id: string | null;
  readonly reason: string;
  readonly sim_time: string;
  readonly wall_time: string;
  readonly ends: ReadonlyArray<LinkEndLoss>;
}

/** The continuous trace now runs between these two nodes. */
export interface ContinuousTraceStarted {
  readonly ok: true;
  readonly src: string;
  readonly dst: string;
}

/** The continuous trace's endpoints and latest result. ``active`` turns false when the trace stops on its own, which keeps its endpoints and last result until it is stopped or replaced; all are empty when no trace was started. */
export interface ContinuousTraceStatus {
  readonly active: boolean;
  readonly src: string | null;
  readonly dst: string | null;
  readonly result: TracedPath | null;
}

/** No continuous trace runs. */
export interface ContinuousTraceStopped {
  readonly ok: true;
}

/**  */
export type CrossTenantDisplacementPolicy = "off";

/** One instance of the service changed its log level. The request reaches every instance; this is the first one's reply, not a statement about the others. */
export interface DebugControlAcknowledgedFact {
  readonly source: ServiceSource;
  readonly request_seq: number;
  readonly request: DebugControlAction;
  readonly outcome: "acknowledged";
  readonly level: string;
}

/**  */
export type DebugControlAction = "enable" | "disable";

/** VS-API could not send the request or could not read the reply. */
export interface DebugControlFailedFact {
  readonly source: ServiceSource;
  readonly request_seq: number;
  readonly request: DebugControlAction;
  readonly outcome: "failed";
  readonly error: string;
}

/** One instance of the service refused the request. */
export interface DebugControlRejectedFact {
  readonly source: ServiceSource;
  readonly request_seq: number;
  readonly request: DebugControlAction;
  readonly outcome: "rejected";
  readonly error: string;
}

/** No instance replied in time, or no instance was listening. */
export interface DebugControlUnansweredFact {
  readonly source: ServiceSource;
  readonly request_seq: number;
  readonly request: DebugControlAction;
  readonly outcome: "timed_out" | "no_responders";
}

/** The page stops asking for any debug output. */
export interface DebugStopAllCommand {
  readonly action: "debug_stop_all";
}

/** The page stops asking for these services' debug output. */
export interface DebugStopCommand {
  readonly action: "debug_stop";
  readonly sources: ReadonlyArray<ServiceSource>;
}

/** The page asks for these services' debug output. */
export interface DebugStreamCommand {
  readonly action: "debug_stream";
  readonly sources: ReadonlyArray<ServiceSource>;
}

/** The structured facts a single explainability surface consumes. Carries no family/label/sentence — the client registry adds those. The `pair` is the focal pair (connected, else best candidate). Provenance ties the explanation to the committed snapshot it was derived from. */
export interface DecisionExplanationFacts {
  readonly gs_id: string;
  readonly pair: readonly [string, string];
  readonly node_focus: NodeFocus;
  readonly reference_body: SupportedSurfaceBody;
  readonly tenant_id: string;
  readonly binding_gate: FunnelGate | null;
  readonly binding_reason_code: BindingReasonCode | null;
  readonly rejecting_endpoint: GroundVisibilityRejectingEndpoint | null;
  readonly ladder: ReadonlyArray<LadderGate>;
  readonly envelope: EffectiveEnvelopeFacts;
  readonly best_candidate: CandidateFacts;
  readonly actuation: ActuationFacts;
  readonly sim_time: string;
  readonly snapshot_seq: number;
  readonly epoch_id: number;
}

/** The ground station to explain, and optionally one satellite paired with it. */
export interface DecisionExplanationQuery {
  readonly gs: string;
  readonly sat?: string | null;
}

/**  */
export type DecisionReasonCode = GroundVisibilityRejectReason | GroundUnscheduledReason;

/**  */
export type DecisionSampleState = "scheduled" | "eligible_unselected" | "expected_no_link";

/** The ground station whose recent decisions to return, and how many. */
export interface DecisionTimelineQuery {
  readonly gs: string;
  readonly limit?: number;
}

/** One direction of a long-delay link whose contact ended. Its transmitter stopped admitting packets at ``stopped_at``; the packets it emitted before then keep arriving at the receiver until ``last_arrival_at``. Both are Scheduler wall times: the kernel delays packets in wall time at every simulation speed. A direction absent from the list is open (its link is in ``links``) or closed. */
export interface DrainingDirection {
  readonly contact_id: string;
  readonly transmitter_node_id: string;
  readonly transmitter_interface: string;
  readonly receiver_node_id: string;
  readonly receiver_interface: string;
  readonly stopped_at: string;
  readonly last_arrival_at: string;
}

/** The combined envelope a user cannot infer by eye. Under a local-vertical GROUND boresight, elevation and field-of-regard collapse to a single floor: `effective_min_elevation_deg = max(configured_min_elevation, 90 - FoR/2)`. `binding_source` names which constraint produced the floor and `dead_knobs` lists configured values that are non-binding because another constraint dominates (e.g. a 25 deg mask under a FoR-derived 30 deg floor). The collapse is ground-specific; `ground`/`satellite` carry each terminal's raw constraints and `binding_endpoint` names which terminal the binding gate rejected at, so a hint points at the terminal that actually limits the link. */
export interface EffectiveEnvelopeFacts {
  readonly reference_body: SupportedSurfaceBody;
  readonly configured_min_elevation_deg: number;
  readonly effective_min_elevation_deg: number;
  readonly binding_source: EnvelopeBindingSource;
  readonly dead_knobs: ReadonlyArray<EnvelopeKnob>;
  readonly max_range_km: number | null;
  readonly ground: EnvelopeEndpoint;
  readonly satellite: EnvelopeEndpoint;
  readonly binding_endpoint: GroundVisibilityRejectingEndpoint;
}

/**  */
export type EnvelopeBindingSource = "field_of_regard" | "min_elevation_mask";

/** One terminal's raw pointing/range constraints. Carried for BOTH endpoints so the "what to change" lever targets the terminal that actually binds — the satellite terminal can be the limiting constraint, not always the ground station. A satellite's nadir boresight does not collapse to a ground elevation floor, so its envelope is shown as the raw pointing/range constraints rather than pretending it is one scalar (see the UX plan, Effective Envelope). */
export interface EnvelopeEndpoint {
  readonly node_role: "ground" | "satellite";
  readonly terminal_profile: string | null;
  readonly boresight_mode: GroundBoresightMode | SatGroundBoresightMode | null;
  readonly field_of_regard_deg: number | null;
  readonly max_tracking_rate_deg_s: number | null;
  readonly max_range_km: number | null;
}

/**  */
export type EnvelopeKnob = "min_elevation_deg";

/**  */
export type ExplanationProducer = "ome_visibility" | "ome_allocator" | "scheduler" | "node_agent";

/**  */
export type FailingActuationState = "kernel_dirty" | "actuation_blocked";

/**  */
export type FunnelGate = "line_of_sight" | "range" | "elevation_mask" | "field_of_regard" | "tracking_rate" | "selection_policy" | "capacity" | "handover_policy" | "actuation_proof";

/**  */
export type GateState = "pass" | "fail" | "not_evaluated" | "not_applicable";

/** Typed non-steady transition produced by the ground allocator. `policy_kind` names the taxonomy for `policy_name`. Events that are not caused by a policy decision, such as physical incumbent visibility loss, set both fields to `None`. `retargeted_before_last_arrival` reports a terminal given a new peer while signals the old peer emitted were still arriving: `pair` is the old pair, `successor_pair` the new one, `terminal_node_id` and `terminal_index` the terminal, `remaining_arrival_ms` how long the old peer's signals would still have been arriving, and `released_by` why the old pair was released. Those four fields are set on that category only. */
export interface GroundAllocationEvent {
  readonly category: GroundAllocationEventCategory;
  readonly pair: readonly [string, string];
  readonly tenant_id: string;
  readonly reference_body: SupportedSurfaceBody;
  readonly message: string;
  readonly successor_pair: readonly [string, string] | null;
  readonly challenger_pair: readonly [string, string] | null;
  readonly policy_kind: GroundAllocationPolicyKind | null;
  readonly policy_name: GroundAllocationPolicyName | null;
  readonly terminal_node_id: string | null;
  readonly terminal_index: number | null;
  readonly remaining_arrival_ms: number | null;
  readonly released_by: GroundTerminalReleaseCause | null;
}

/**  */
export type GroundAllocationEventCategory = "mbb_overlap_started" | "teardown_completed" | "teardown_invalidated_by_epoch" | "successor_aborted" | "failed_successor" | "failed_acquire" | "incumbent_lost" | "bbm_gap" | "retargeted_before_last_arrival";

/**  */
export type GroundAllocationPolicyKind = "selection_policy" | "handover_policy" | "handover_mode" | "successor_abort_policy" | "mbb_preemption" | "cross_tenant_displacement";

/**  */
export type GroundAllocationPolicyName = SelectionPolicyName | HandoverPolicyName | GroundHandoverModeName | SuccessorAbortPolicy | MbbPreemptionPolicy | CrossTenantDisplacementPolicy;

/**  */
export type GroundHandoverModeName = "bbm" | "mbb";

/** Companion to `LinkStateSnapshot`: what the OME decided about GROUND links, and why. Ground-scoped. Carries decisions only for GS↔satellite pairs. ISL pair decisions are not snapshotted today; a separate `IslLinkDecisionSnapshot` type and subject will be added when ISL decision attribution lands. Consumers must not interpret an absent pair as "the OME ignored it" — it may simply be an ISL pair this snapshot does not cover. Published on `ground_link_decision_snapshot_subject(session_id)` (same `sim_time` and `snapshot_seq` as the corresponding `LinkStateSnapshot`, for pairing). `LinkStateSnapshot` carries the actuated forwarding-plane state across both link types. `GroundLinkDecisionSnapshot` carries the OME's decision context for every GROUND pair the OME considered — visible-and-scheduled, visible-but-rejected, and visible-but-unscheduled. The two snapshots are correlatable by `sim_time` and `snapshot_seq`. Consumers asking "what is forwarding?" read the state snapshot; consumers asking "why isn't this ground link up?" read this decision snapshot. Internal consistency invariants (enforced at construction): 1. `decisions` has no duplicate pairs. Each (gs, sat) pair gets exactly one decision per snapshot. 2. Every `UnscheduledPair` references a `pair` that is present in `decisions` with `visible=True`. An unscheduled pair by definition is visible-but-not-scheduled; pointing at an invisible pair or a pair not in the decision set is a producer bug. 3. Every `UnscheduledPair`'s `tenant_id` and `reference_body` match the corresponding decision's. Divergence means the allocator and visibility engine disagree about which tenant/body owns the pair — a fatal contract break. 4. `unscheduled_pairs` has no duplicate pairs. Producers that fail any of these are wrong. Foundational types reject impossible states at construction. */
export interface GroundLinkDecisionSnapshot {
  readonly sim_time: string;
  readonly snapshot_seq: number;
  readonly epoch_id: number;
  readonly decisions: ReadonlyArray<GroundVisibilityDecisionWire>;
  readonly unscheduled_pairs: ReadonlyArray<UnscheduledPair>;
  readonly policy_audit: GroundPolicyAudit;
  readonly allocation_events: ReadonlyArray<GroundAllocationEvent>;
}

/** The node whose candidate pairs to return; the whole snapshot when absent. */
export interface GroundLinkDecisionsQuery {
  readonly node?: string | null;
}

/** Resolved policy surface applied to one ground decision snapshot. Per-policy params are keyed by GS id and interpreted through the corresponding policy name in selection_policies / handover_policies. Consumers must branch on the policy name before reading policy-specific params; the params bag is not a global schema shared by every policy. */
export interface GroundPolicyAudit {
  readonly selection_policies: Readonly<Record<string, string>>;
  readonly selection_policy_params: Readonly<Record<string, Readonly<Record<string, JsonValue>>>>;
  readonly handover_policies: Readonly<Record<string, string>>;
  readonly handover_policy_params: Readonly<Record<string, Readonly<Record<string, JsonValue>>>>;
  readonly ranking_order: ReadonlyArray<string>;
  readonly handover_mode: string;
  readonly handover_modes: Readonly<Record<string, string>>;
  readonly mbb_preemption: string;
  readonly successor_abort_policy: string;
  readonly cross_tenant_displacement: string;
  readonly mbb_overlap_ticks: number;
  readonly mbb_overlap_ticks_by_gs: Readonly<Record<string, number>>;
  readonly mbb_reserve: number;
  readonly mbb_reserve_by_gs: Readonly<Record<string, number>>;
  readonly bbm_acquire_timeout_ticks: number;
  readonly ignored_capacity_fields: ReadonlyArray<string>;
}

/**  */
export type GroundTerminalReleaseCause = "bbm_gap" | "incumbent_lost" | "teardown_completed" | "successor_aborted" | "failed_successor" | "sat_capacity";

/**  */
export type GroundUnscheduledReason = "gs_capacity" | "sat_capacity" | "hysteresis_hold" | "incumbent_held" | "bbm_no_spare" | "mbb_overlap_locked" | "replaced_by_successor" | "successor_aborted" | "failed_successor" | "failed_acquire";

/** Per-pair ground visibility decision in wire form. Published as part of `GroundLinkDecisionSnapshot` on the subject `ground_link_decision_snapshot_subject(session_id)` builds (``nodalarc.links.<session>.ground_decisions``). Every field is required at construction. `applied_*` fields use `None` to mean "this constraint was not in effect for the decision" (e.g., a `geometry_only` session does not declare `max_range_km`, so the field is `None`); they never mean "we forgot to populate this." A `terminal_physics` session must populate every applied constraint that the GS or sat terminal declares. Terminal profile fields explained: - `applied_gs_terminal_profile` and `applied_sat_terminal_profile` identify which *terminal definition / constraint profile* the visibility check evaluated against — NOT the kernel interface name (`term0`, `gnd0`) and NOT the terminal instance index. A satellite may carry multiple resolved access-terminal blocks. entries with different `max_range_km` / boresight values; the profile identifier names which definition's constraints applied to this decision. Use `None` only when the session is `geometry_only` and no terminal-level constraints were applied. The instance index (which physical terminal got the assignment) is carried separately on `VisibilityEvent` / `LinkState` as `gs_terminal_index` / `sat_terminal_index`. Invariant (enforced by the model validator below): if `reject_reason` names a terminal-bound constraint (`range_exceeded`, `field_of_regard`, `tracking_exceeded`), at least one side's profile MUST be identified — otherwise the rejection is unattributable to a specific terminal and cannot be audited. */
export interface GroundVisibilityDecisionWire {
  readonly pair: readonly [string, string];
  readonly tenant_id: string;
  readonly reference_body: SupportedSurfaceBody;
  readonly visible: boolean;
  readonly range_km: number;
  readonly latency_a_to_b_ms: number;
  readonly latency_b_to_a_ms: number;
  readonly elevation_deg: number;
  readonly azimuth_deg: number | null;
  readonly sat_off_nadir_deg: number | null;
  readonly observer_frame: ObserverFrame;
  readonly reject_reason: GroundVisibilityRejectReason;
  readonly rejecting_endpoint: GroundVisibilityRejectingEndpoint;
  readonly applied_min_elevation_deg: number;
  readonly applied_gs_max_range_km: number | null;
  readonly applied_sat_max_range_km: number | null;
  readonly applied_gs_field_of_regard_deg: number | null;
  readonly applied_sat_field_of_regard_deg: number | null;
  readonly applied_gs_max_tracking_rate_deg_s: number | null;
  readonly applied_sat_max_tracking_rate_deg_s: number | null;
  readonly applied_gs_boresight_mode: GroundBoresightMode | null;
  readonly applied_sat_boresight_mode: SatGroundBoresightMode | null;
  readonly applied_gs_terminal_profile: string | null;
  readonly applied_sat_terminal_profile: string | null;
}

/**  */
export type GroundVisibilityRejectReason = "ok" | "los_blocked" | "elevation_below_min" | "range_exceeded" | "field_of_regard" | "tracking_exceeded";

/**  */
export type GroundVisibilityRejectingEndpoint = "none" | "ground" | "satellite" | "both";

/** Aggregated count for a reason/state in the sampled window. */
export interface GsDecisionReasonCount {
  readonly state: DecisionSampleState;
  readonly reason_code: DecisionReasonCode | null;
  readonly count: number;
}

/** Bounded observed window for one GS, plus diagnosis roll-up facts. */
export interface GsDecisionTimelineFacts {
  readonly gs_id: string;
  readonly sample_count: number;
  readonly window_started_sim_time: string | null;
  readonly window_ended_sim_time: string | null;
  readonly samples: ReadonlyArray<GsDecisionTimelineSample>;
  readonly reason_counts: ReadonlyArray<GsDecisionReasonCount>;
}

/** One bounded observed decision sample for a ground station. This is not historical playback and does not try to reconstruct old full snapshots. VS-API samples the committed OME decision surface as it arrives and keeps a bounded in-memory window per GS so the UI can answer "what has this station been doing recently?" without polling the full GS×sat matrix. */
export interface GsDecisionTimelineSample {
  readonly gs_id: string;
  readonly sim_time: string;
  readonly snapshot_seq: number;
  readonly epoch_id: number;
  readonly state: DecisionSampleState;
  readonly pair: readonly [string, string];
  readonly binding_gate: FunnelGate | null;
  readonly reason_code: DecisionReasonCode | null;
  readonly rejecting_endpoint: GroundVisibilityRejectingEndpoint | null;
  readonly range_km: number;
  readonly elevation_deg: number;
}

/**  */
export type HandoverPolicyName = "hysteresis" | "none";

/** VS-API is serving, and where its session manager stands. */
export interface HealthStatus {
  readonly status: "ok";
  readonly session_status: SessionStatus;
}

/** A recorded session run's history recording: running, or stopped and why. */
export interface HistoryRecordingState {
  readonly state: "recording" | "stopped";
  readonly error: string | null;
}

/** One whitelisted vtysh command addressed to one runtime node. */
export interface IntrospectRequest {
  readonly node_id: string;
  readonly command: string;
}

/** The exact output and exit status of one executed vtysh command. */
export interface IntrospectResult {
  readonly node_id: string;
  readonly command: string;
  readonly output: string;
  readonly exit_code: number;
  readonly stderr: string;
}

/** One row of the decision funnel for a focal pair. `actual`/`threshold` are populated where the value is numeric and available in the committed decision; `None` where not applicable or not carried by the source decision (e.g. the required slew rate is not on the wire). The client renders the reason_code through the registry — this row carries no human text. */
export interface LadderGate {
  readonly gate: FunnelGate;
  readonly state: GateState;
  readonly actual: number | null;
  readonly threshold: number | null;
  readonly rejecting_endpoint: GroundVisibilityRejectingEndpoint | null;
  readonly reason_code: BindingReasonCode | null;
  readonly producer: ExplanationProducer;
  readonly is_binding: boolean;
}

/** The ``{"error": ...}`` body some older routes still refuse with. */
export interface LegacyRefusal {
  readonly error: string;
}

/** Why an active link exists and which authority produced its values. */
export interface LinkDecisionTrace {
  readonly node_a: string;
  readonly node_b: string;
  readonly link_type: PublicLinkType;
  readonly state: "active";
  readonly interface_a: string;
  readonly interface_b: string;
  readonly reason: string | null;
  readonly geometry_authority: "ome";
  readonly authority_source: string;
  readonly authority_sim_time: string;
  readonly authority_sequence: number | null;
  readonly authority_age_ms: number;
  readonly range_km: number;
  readonly orbital_a_to_b_ms: number;
  readonly orbital_b_to_a_ms: number;
  readonly substrate_rtt_ms: number | null;
  readonly substrate_one_way_ms: number | null;
  readonly netem_a_to_b_ms: number | null;
  readonly netem_b_to_a_ms: number | null;
  readonly rtt_to_one_way_policy: string | null;
  readonly link_rule_id: string | null;
  readonly topology_mode: TopologyMode | null;
  readonly endpoint_segments: readonly [string, string] | null;
}

/** Both nodes name one link's decision trace; neither returns every trace. Naming one node alone is refused. */
export interface LinkDecisionTracesQuery {
  readonly node_a?: string | null;
  readonly node_b?: string | null;
}

/**  */
export type LinkEndCloseCause = "end_of_contact" | "drain_deadline" | "seek" | "scenario" | "reopened" | "retargeted";

/** What one link end lost during its contact, from the Node Agent's counts at each stage. ``discarded_at_close`` is what the end's delay queue still held when it closed. With ``discarded_exact`` it is a count: admission had stopped at both ends, so nothing entered the queue between the read and the reset. Without it, it is a sample read just before the close of a local link, which keeps admitting until then. ``queue_dropped`` is what the end's delay queue dropped at its limit, and ``transmit_dropped`` what its transmit queue dropped at its limit (both substrate resource loss). On a long-delay end, ``refused_before_open`` is what its stopped admission refused while the contact was being prepared, ``untransmitted_at_stop`` what still waited to be transmitted when the contact ended (purged at the stop, never sent), and ``admission_taken`` what its stopped admission refused after the stop; all three are 0 on a local link. */
export interface LinkEndLoss {
  readonly node_id: string;
  readonly interface: string;
  readonly cause: LinkEndCloseCause;
  readonly refused_before_open: number;
  readonly untransmitted_at_stop: number;
  readonly admission_taken: number;
  readonly transmit_dropped: number;
  readonly queue_dropped: number;
  readonly discarded_at_close: number;
  readonly discarded_exact: boolean;
}

/** One recorded link event: a transition, a latency change, or a link active when the recording started (``LinkActive``, reason ``recording_start``). */
export interface LinkHistoryEvent {
  readonly id: number;
  readonly session_id: string;
  readonly sim_time: string;
  readonly wall_time: string;
  readonly event_type: "LinkUp" | "LinkDown" | "LatencyUpdate" | "LinkActive";
  readonly node_a: string;
  readonly node_b: string;
  readonly interface_a: string | null;
  readonly interface_b: string | null;
  readonly latency_a_to_b_ms: number | null;
  readonly latency_b_to_a_ms: number | null;
  readonly range_km: number | null;
  readonly reason: string | null;
}

/** One page of a recorded session's link events. ``returned`` of ``total`` matching events are in ``events``. A further page is requested with ``next_cursor`` and the same filters; it is None when no event follows. ``retained_from`` is when the oldest kept data was recorded, once the history size budget dropped older rows; None while the recording is complete. */
export interface LinkHistoryPage {
  readonly events: ReadonlyArray<LinkHistoryEvent>;
  readonly returned: number;
  readonly total: number;
  readonly next_cursor: string | null;
  readonly retained_from: string | null;
}

/** The filters and page of a link history request. */
export interface LinkHistoryQuery {
  readonly start?: string | null;
  readonly end?: string | null;
  readonly node?: string | null;
  readonly peer?: string | null;
  readonly order?: "oldest_first" | "newest_first";
  readonly limit?: number;
  readonly cursor?: string | null;
}

/** State of a single link between two nodes. */
export interface LinkState {
  readonly node_a: string;
  readonly node_b: string;
  readonly state: "active";
  readonly link_type: PublicLinkType;
  readonly link_reason: string | null;
  readonly latency_a_to_b_ms: number;
  readonly latency_b_to_a_ms: number;
  readonly transmit_mbps_a: number;
  readonly receive_mbps_a: number;
  readonly transmit_mbps_b: number;
  readonly receive_mbps_b: number;
  readonly range_km: number;
  readonly range_sim_time: string;
  readonly traffic_load_pct: number | null;
  readonly interface_a: string;
  readonly interface_b: string;
  readonly link_rule_id: string | null;
  readonly topology_mode: TopologyMode | null;
  readonly endpoint_segments: readonly [string, string] | null;
  readonly scheduling_state: SchedulingState;
  readonly teardown_remaining_ticks: number | null;
  readonly successor_pair: readonly [string, string] | null;
  readonly contact_id: string | null;
}

/**  */
export type MbbPreemptionPolicy = "off";

/** Configured network identity/address associated with one node. */
export interface NodeAddress {
  readonly purpose: "router_loopback" | "site_interface" | "site_prefix";
  readonly family: AddressFamily;
  readonly address: string;
  readonly interface: string | null;
  readonly metric: number | null;
}

/**  */
export type NodeFocus = "gs" | "sat" | "pair";

/** State of a single node in the constellation. */
export interface NodeState {
  readonly node_id: string;
  readonly node_type: NodeKind;
  readonly lat_deg: number;
  readonly lon_deg: number;
  readonly alt_km: number;
  readonly vel_x_km_s: number | null;
  readonly vel_y_km_s: number | null;
  readonly vel_z_km_s: number | null;
  readonly plane: number | null;
  readonly slot: number | null;
  readonly routing_instances: ReadonlyArray<NodeRoutingInstance>;
  readonly role: NodeRole;
  readonly isl_count: number;
  readonly gnd_count: number;
  readonly prefix: string | null;
  readonly addresses: ReadonlyArray<NodeAddress>;
  readonly min_elevation_deg: number | null;
  readonly beam_falloff_exponent: number | null;
  readonly reference_body: SupportedSurfaceBody;
  readonly frame_id: SupportedSurfaceBody;
  readonly tenant_id: string;
  readonly segment_id: string;
  readonly local_node_id: string;
  readonly namespace: string;
  readonly tags: ReadonlyArray<string>;
}

/**  */
export type ObserverFrame = "body_local";

/** What an operator sends to repair one station. VS-API fills the Scheduler instance and wiring generation from the station's latest actuation state when the request leaves them out, then sends an OperatorRepairCommand. */
export interface OperatorRepairRequest {
  readonly gs_id: string;
  readonly reason: string;
  readonly scheduler_instance_id?: string | null;
  readonly wiring_generation?: string | null;
  readonly intervention_id?: string | null;
}

/**  */
export interface OperatorRepairResponse {
  readonly status: "accepted" | "rejected";
  readonly intervention_id: string;
  readonly message: string;
}

/** Filters on the operator-visible ops log. */
export interface OpsEventsQuery {
  readonly source?: ServiceSource | null;
  readonly level?: OpsLevel | null;
  readonly limit?: number;
}

/**  */
export type OpsLevel = "critical" | "error" | "warning" | "info" | "debug";

/** Validated command payload for the OME playback-control subject. */
export interface PlaybackControlCommand {
  readonly action: "pause" | "resume" | "set_speed" | "seek" | "get_status";
  readonly factor?: number | null;
  readonly target_sim_time?: string | null;
}

/** The data that limits a session: the body (or the Sun) whose data falls short, and the usable interval, the span of master sim time in which the session's ticks can run on the data. */
export interface PlaybackCoverageLimit {
  readonly body_id: string;
  readonly usable_from: string;
  readonly usable_until: string;
}

/** OME playback state — seeking/playing/paused. Published to NODALARC_SESSION stream (MaxMsgsPerSubject=1) at every state transition. Late-joining subscribers immediately know current state. The 'seeking' state is a mutex — edges must suspend until all epoch dependencies are satisfied. ``reason`` and ``coverage`` are set only on a pause OME made itself: the clock stopped at the last tick the ephemeris data supports. */
export interface PlaybackState {
  readonly epoch_id: number;
  readonly state: "seeking" | "playing" | "paused";
  readonly reason: "ephemeris_coverage_ended" | null;
  readonly coverage: PlaybackCoverageLimit | null;
  readonly time_regimes: ReadonlyArray<TimeRegime>;
}

/** The project's attribution and the build serving it. */
export interface ProjectAbout {
  readonly name: string;
  readonly version: string;
  readonly revision: string;
  readonly build_date: string;
  readonly author: string;
  readonly copyright: string;
  readonly source: string;
  readonly url: string;
  readonly license: string;
  readonly notice: string;
}

/**  */
export type PublicLinkType = "ground" | "isl" | "inter_constellation" | "inter_body_relay";

/** Recent event for the VF event log. */
export interface RecentEvent {
  readonly sim_time: string;
  readonly node_id: string;
  readonly event_type: "link_up" | "link_down" | "PATH_CHANGE";
  readonly summary: string;
}

/**  */
export type SchedulerOpsCode = "REPLACEMENT_LINK_UP_FAILED" | "GROUND_LINK_UP_FAILED" | "GROUND_LINK_DOWN_FAILED" | "GROUND_LATENCY_UPDATE_FAILED" | "OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR" | "ACTUATION_BLOCKED" | "ACTUATION_CLEAN" | "KERNEL_DIRTY" | "KERNEL_VERIFY_ATTEMPTED" | "KERNEL_VERIFY_EXHAUSTED" | "AUTHORITY_SUBSET_VIOLATION" | "ACTUATION_HALTED" | "SCHEDULER_SUPERSEDED" | "OPERATOR_REPAIR_REQUESTED" | "OPERATOR_REPAIR_STARTED" | "OPERATOR_REPAIR_SUCCEEDED" | "OPERATOR_REPAIR_FAILED" | "OPERATOR_REPAIR_REJECTED" | "AGENT_DEGRADED" | "AGENT_RECOVERED";

/**  */
export type SchedulingState = "active" | "teardown";

/**  */
export type SelectionPolicyName = "highest-elevation" | "lowest-elevation" | "longest-remaining-pass";

/**  */
export type ServiceSource = "ome" | "scheduler" | "node_agent" | "operator" | "vs_api";

/** The session ephemeris as the state socket sends it: on connect and when a session becomes active. */
export interface SessionEphemerisFrame {
  readonly epoch_id: number;
  readonly sim_time: string;
  readonly epoch_unix: number;
  readonly nodes: Readonly<Record<string, EphemerisNodeKeplerian | EphemerisNodeTLE | EphemerisNodeFixed>>;
  readonly body_frames: Readonly<Record<string, EphemerisBodyFrame>>;
  readonly msg_type: "session_ephemeris";
}

/** A session switch failed. */
export interface SessionFailedFrame {
  readonly msg_type: "session_failed";
  readonly error: string;
}

/** The switched-to session is live; carries its first state snapshot. */
export interface SessionReadyFrame {
  readonly msg_type: "session_ready";
  readonly snapshot: StateSnapshot;
}

/**  */
export type SessionStatus = "idle" | "switching" | "wiring" | "ready" | "error";

/** A session switch has started; later frames carry its progress detail. */
export interface SessionTransitioningFrame {
  readonly msg_type: "session_transitioning";
  readonly detail: string | null;
}

/** The codes VS-API closes an accepted state or terminal socket with. A refusal before VS-API accepts the socket (a wrong token) reaches the browser as a failed handshake, so it has no code here. */
export type SocketCloseCode = 1000 | 4400 | 4404 | 4409 | 4410 | 4503;

/** An ops event as VS-API serves it, stamped with its arrival seq. */
export interface StampedOpsEvent {
  readonly timestamp: string;
  readonly tenant_id: string;
  readonly session_id: string;
  readonly source: ServiceSource;
  readonly hostname: string;
  readonly level: OpsLevel;
  readonly code: string;
  readonly message: string;
  readonly details: Readonly<Record<string, JsonValue>> | null;
  readonly seq: number;
}

/** Complete constellation state sent via WebSocket at ~1Hz. Full snapshots only, except the ops log: a connection's frame carries only the ops events that connection has not been sent. Drop intermediate frames if behind, never queue. */
export interface StateSnapshot {
  readonly msg_type: "state_snapshot";
  readonly sim_time: string;
  readonly wall_time: string;
  readonly session_id: string;
  readonly nodes: ReadonlyArray<NodeState>;
  readonly links: ReadonlyArray<LinkState>;
  readonly kernel_actual_pairs: ReadonlyArray<readonly [string, string]>;
  readonly draining_directions: ReadonlyArray<DrainingDirection>;
  readonly contact_losses: ReadonlyArray<ContactLoss>;
  readonly traced_paths: ReadonlyArray<TracedPath>;
  readonly recent_events: ReadonlyArray<RecentEvent>;
  readonly routing_stack: string | null;
  readonly constellation_name: string | null;
  readonly session_status: SessionStatus;
  readonly session_status_detail: string | null;
  readonly playback_paused: boolean;
  readonly playback_pause_reason: "ephemeris_coverage_ended" | null;
  readonly playback_coverage: PlaybackCoverageLimit | null;
  readonly playback_speed: number;
  readonly playback_achieved: number | null;
  readonly pacing_degraded: boolean;
  readonly stale: boolean;
  readonly actuation_notices: ReadonlyArray<ActuationNotice>;
  readonly actuation_health: ActuationHealth | null;
  readonly history_recording: HistoryRecordingState | null;
  readonly ops_events: ReadonlyArray<StampedOpsEvent>;
  readonly ops_log_token: string;
  readonly debug_events: ReadonlyArray<StampedOpsEvent>;
  readonly debug_control: ReadonlyArray<DebugControlAcknowledgedFact | DebugControlRejectedFact | DebugControlUnansweredFact | DebugControlFailedFact>;
  readonly debug_subscription_open: boolean;
}

/**  */
export type SuccessorAbortPolicy = "hard_release" | "soft_retain";

/** Keystrokes the browser sends to a node's terminal. */
export interface TerminalInput {
  readonly type: "input";
  readonly data: string;
}

/** What the node's terminal printed. */
export interface TerminalOutput {
  readonly type: "output";
  readonly data: string;
}

/** The browser's terminal window changed size. */
export interface TerminalResize {
  readonly type: "resize";
  readonly cols: number;
  readonly rows: number;
}

/** One stretch of OME's time controls, from ``started_at`` (OME's wall clock) until the next regime starts: the playback state, the commanded rate and whether OME delivered it. */
export interface TimeRegime {
  readonly started_at: string;
  readonly cause: TimeRegimeCause;
  readonly state: "seeking" | "playing" | "paused";
  readonly commanded_rate: number;
  readonly pacing_degraded: boolean;
}

/**  */
export type TimeRegimeCause = "start" | "pause" | "resume" | "speed" | "seek" | "pacing_degraded" | "pacing_recovered" | "coverage_pause";

/** The two nodes a trace runs between. */
export interface TraceRequest {
  readonly src_node: string;
  readonly dst_node: string;
}

/**  */
export type TraceState = "running" | "reached" | "not_reached" | "failed";

/**  */
export type TraceStopReason = "time_limit" | "internal_error";

/** The live measured path between two nodes: one traceroute per direction. Each direction lists what answered at every hop, in hop order, after the node the trace starts from: a node id when the resolver assigned the answering address to that node, the address itself when no node owns it, and ``*`` when nothing answered. ``state`` says how the trace ended. ``rtt_ms`` is the destination's round trip and exists only when the destination answered; ``error`` exists only when the trace could not run. ``asymmetry_detected`` exists only when both directions reached their destination and every hop between the ends answered from a node's address. ``tracing`` is false once the trace loop has stopped; the last result then stays until the trace is stopped or restarted. ``stop_reason`` says why a continuous trace stopped on its own; a one-shot trace and a running trace have none. */
export interface TracedPath {
  readonly continuous_trace: boolean;
  readonly src_node: string;
  readonly dst_node: string;
  readonly hops: ReadonlyArray<string>;
  readonly hop_rtts: ReadonlyArray<number | null>;
  readonly state: TraceState;
  readonly rtt_ms: number | null;
  readonly error: string | null;
  readonly reverse_hops: ReadonlyArray<string>;
  readonly reverse_hop_rtts: ReadonlyArray<number | null>;
  readonly reverse_state: TraceState;
  readonly reverse_rtt_ms: number | null;
  readonly reverse_error: string | null;
  readonly asymmetry_detected: boolean | null;
  readonly tracing: boolean;
  readonly traced_at: string;
  readonly sim_time: string;
  readonly stop_reason: TraceStopReason | null;
  readonly measured_from: string;
  readonly measured_until: string | null;
  readonly wall_clock: WallClockMarking;
}

/** A pair that was visible but not scheduled, with the reason. Carried in `GroundLinkDecisionSnapshot.unscheduled_pairs`. A pair appears here when `GroundVisibilityDecisionWire.visible=True` but the allocator did not schedule it. Teardown-overlap pairs do NOT appear here — they are scheduled (the old terminal is still active) and carry `scheduling_state="teardown"` on `VisibilityEvent` / `LinkState`. `incumbent_pair` is populated when this pair was rejected because a specific incumbent held the slot (`hysteresis_hold` or `incumbent_held`). `capacity_constraint` is populated when the reason names a specific resource that ran out (e.g., the sat ground-terminal id). */
export interface UnscheduledPair {
  readonly pair: readonly [string, string];
  readonly tenant_id: string;
  readonly reference_body: SupportedSurfaceBody;
  readonly unscheduled_reason: GroundUnscheduledReason;
  readonly incumbent_pair: readonly [string, string] | null;
  readonly capacity_constraint: string | null;
}

/** Whether a result NodalArc measured on wall time describes the emulated world. ``representative`` is true when OME ran the whole measurement at 1x, playing, delivering the commanded rate, with no change of time controls; false otherwise, and ``regimes`` then lists every regime the measurement ran through. It is None when OME's time-control record does not reach back to the start of the measurement. The measured values stay as measured. */
export interface WallClockMarking {
  readonly representative: boolean | null;
  readonly regimes: ReadonlyArray<TimeRegime>;
}

/** A wiring progress line from the Node Agent, relayed as it arrives. */
export interface WiringProgressFrame {
  readonly msg_type: "wiring_progress";
  readonly message: string;
}
