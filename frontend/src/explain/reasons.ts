// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Reason taxonomy registry — the single client-side source of explanation text.
 *
 * Every reason a backend decision can carry maps to exactly one record here.
 * No UI surface writes its own explanation string; globe tooltip, node card,
 * pair inspector, timeline, and logs all render from this registry. This is the
 * frontend twin of the backend reason vocabulary, mirrored exactly:
 *   - GroundVisibilityRejectReason  (lib/nodalarc/models/link_decisions.py)
 *   - GroundUnscheduledReason        (same)
 *   - GroundAllocationEventCategory  (same)
 *   - ActuationState / ActuationFailureClass (lib/nodalarc/models/scheduler_ops.py)
 *
 * A completeness test asserts every union member below has a record. Codes that
 * appear in more than one backend domain (e.g. successor_aborted) get ONE record.
 */

import type { Family, FunnelGate, Producer, RemediationLayer, Severity } from "./families";

// --- Backend-mirrored reason unions (keep in lockstep with the Python enums) ---

export type GroundVisibilityRejectReason =
  | "ok"
  | "los_blocked"
  | "elevation_below_min"
  | "range_exceeded"
  | "field_of_regard"
  | "tracking_exceeded";
export const GROUND_VISIBILITY_REJECT_REASONS: readonly GroundVisibilityRejectReason[] = [
  "ok",
  "los_blocked",
  "elevation_below_min",
  "range_exceeded",
  "field_of_regard",
  "tracking_exceeded",
];

export type GroundUnscheduledReason =
  | "gs_capacity"
  | "sat_capacity"
  | "hysteresis_hold"
  | "incumbent_held"
  | "bbm_no_spare"
  | "mbb_overlap_locked"
  | "replaced_by_successor"
  | "successor_aborted"
  | "failed_successor"
  | "failed_acquire";
export const GROUND_UNSCHEDULED_REASONS: readonly GroundUnscheduledReason[] = [
  "gs_capacity",
  "sat_capacity",
  "hysteresis_hold",
  "incumbent_held",
  "bbm_no_spare",
  "mbb_overlap_locked",
  "replaced_by_successor",
  "successor_aborted",
  "failed_successor",
  "failed_acquire",
];

export type GroundAllocationEventCategory =
  | "mbb_overlap_started"
  | "teardown_completed"
  | "teardown_invalidated_by_epoch"
  | "successor_aborted"
  | "failed_successor"
  | "failed_acquire"
  | "incumbent_lost"
  | "bbm_gap";
export const GROUND_ALLOCATION_EVENT_CATEGORIES: readonly GroundAllocationEventCategory[] = [
  "mbb_overlap_started",
  "teardown_completed",
  "teardown_invalidated_by_epoch",
  "successor_aborted",
  "failed_successor",
  "failed_acquire",
  "incumbent_lost",
  "bbm_gap",
];

export type ActuationState = "clean" | "actuation_blocked" | "kernel_dirty" | "unknown";
export const ACTUATION_STATES: readonly ActuationState[] = [
  "clean",
  "actuation_blocked",
  "kernel_dirty",
  "unknown",
];

export type ActuationFailureClass =
  | "none"
  | "authority_invariant"
  | "ome_contract"
  | "fence"
  | "ground_clean_failure"
  | "ground_kernel_dirty"
  | "ground_unknown"
  | "isl_failure"
  | "ops_publish_failure";
export const ACTUATION_FAILURE_CLASSES: readonly ActuationFailureClass[] = [
  "none",
  "authority_invariant",
  "ome_contract",
  "fence",
  "ground_clean_failure",
  "ground_kernel_dirty",
  "ground_unknown",
  "isl_failure",
  "ops_publish_failure",
];

export type ReasonDomain = "visibility" | "unscheduled" | "allocation_event" | "actuation";

export interface ReasonRecord {
  /** Stable code string, matching the backend enum value verbatim. */
  code: string;
  /** Which backend domain(s) emit this code (shared codes list more than one). */
  domains: readonly ReasonDomain[];
  /** Funnel gate this reason belongs to; null only for pass markers (`ok`, `none`). */
  gate: FunnelGate | null;
  /** What lever changes the outcome. */
  layer: RemediationLayer;
  /** Canonical family this reason implies when it is the binding cause. */
  family: Family;
  /** Baseline severity. May escalate per `escalateWhenChurning`. */
  severity: Severity;
  /** Short operator-facing label (the only place it is spelled). */
  label: string;
  /** One-sentence human explanation. {actual}/{threshold}/{margin} are filled by the renderer. */
  sentence: string;
  /** Candidate model levers for this gate; the effective-envelope computation filters to the binding one. */
  levers: readonly string[];
  /** Authoritative producer of this verdict. */
  producer: Producer;
  /** If the pair is churning at this gate, escalate to this severity (stability axis). */
  escalateWhenChurning?: Severity;
}

function rec(r: ReasonRecord): ReasonRecord {
  return r;
}

export const REASON_REGISTRY: Record<string, ReasonRecord> = {
  // --- Visibility (OME visibility engine) ---
  ok: rec({
    code: "ok",
    domains: ["visibility"],
    gate: null,
    layer: "geometry",
    family: "connected",
    severity: "info",
    label: "Visible",
    sentence: "Geometrically visible and within terminal capability.",
    levers: [],
    producer: "ome_visibility",
  }),
  los_blocked: rec({
    code: "los_blocked",
    domains: ["visibility"],
    gate: "line_of_sight",
    layer: "geometry",
    family: "expected_no_link",
    severity: "info",
    label: "Below horizon",
    sentence: "The satellite is below the local horizon — no line of sight.",
    levers: ["add_planes_or_sats", "move_or_add_ground_station"],
    producer: "ome_visibility",
  }),
  elevation_below_min: rec({
    code: "elevation_below_min",
    domains: ["visibility"],
    gate: "elevation_mask",
    layer: "geometry",
    family: "expected_no_link",
    severity: "info",
    label: "Below elevation mask",
    sentence: "Elevation {actual} is under the {threshold} mask.",
    levers: ["lower_min_elevation", "add_planes_or_sats", "wait_for_higher_pass"],
    producer: "ome_visibility",
    escalateWhenChurning: "warning",
  }),
  range_exceeded: rec({
    code: "range_exceeded",
    domains: ["visibility"],
    gate: "range",
    layer: "terminal_capability",
    family: "expected_no_link",
    severity: "info",
    label: "Out of range",
    sentence: "Range {actual} exceeds the terminal max range {threshold}.",
    levers: ["increase_terminal_max_range", "add_planes_or_sats"],
    producer: "ome_visibility",
  }),
  field_of_regard: rec({
    code: "field_of_regard",
    domains: ["visibility"],
    gate: "field_of_regard",
    layer: "terminal_capability",
    family: "expected_no_link",
    severity: "info",
    label: "Outside field of regard",
    sentence: "Off-boresight angle {actual} exceeds the terminal field of regard {threshold}.",
    levers: ["widen_or_steer_for", "add_planes_or_sats"],
    producer: "ome_visibility",
  }),
  tracking_exceeded: rec({
    code: "tracking_exceeded",
    domains: ["visibility"],
    gate: "tracking_rate",
    layer: "terminal_capability",
    family: "expected_no_link",
    severity: "info",
    label: "Too fast to track",
    sentence: "Required slew rate {actual} exceeds the terminal tracking rate {threshold}.",
    levers: ["increase_tracking_rate", "use_different_terminal_model"],
    producer: "ome_visibility",
  }),

  // --- Unscheduled (OME allocator) — visible-but-not-scheduled, i.e. eligible_unselected ---
  gs_capacity: rec({
    code: "gs_capacity",
    domains: ["unscheduled"],
    gate: "capacity",
    layer: "terminal_capability",
    family: "eligible_unselected",
    severity: "info",
    label: "Ground capacity full",
    sentence: "All ground-station terminals are in use ({margin}).",
    levers: ["add_terminal_count", "change_selection_priority"],
    producer: "ome_allocator",
    escalateWhenChurning: "warning",
  }),
  sat_capacity: rec({
    code: "sat_capacity",
    domains: ["unscheduled"],
    gate: "capacity",
    layer: "terminal_capability",
    family: "eligible_unselected",
    severity: "info",
    label: "Satellite capacity full",
    sentence: "The satellite's ground terminals are all in use ({margin}).",
    levers: ["add_sat_terminal_count", "change_selection_priority"],
    producer: "ome_allocator",
    escalateWhenChurning: "warning",
  }),
  hysteresis_hold: rec({
    code: "hysteresis_hold",
    domains: ["unscheduled"],
    gate: "handover_policy",
    layer: "policy",
    family: "eligible_unselected",
    severity: "info",
    label: "Held by hysteresis",
    sentence: "The incumbent is retained; this challenger did not clear the hysteresis margin ({margin}).",
    levers: ["change_handover_policy_params", "change_selection_policy"],
    producer: "ome_allocator",
  }),
  incumbent_held: rec({
    code: "incumbent_held",
    domains: ["unscheduled"],
    gate: "handover_policy",
    layer: "policy",
    family: "eligible_unselected",
    severity: "info",
    label: "Incumbent held",
    sentence: "Policy keeps the current incumbent over this candidate.",
    levers: ["change_handover_policy_params", "change_selection_policy"],
    producer: "ome_allocator",
  }),
  bbm_no_spare: rec({
    code: "bbm_no_spare",
    domains: ["unscheduled"],
    gate: "capacity",
    layer: "terminal_capability",
    family: "eligible_unselected",
    severity: "warning",
    label: "No spare for handover",
    sentence: "BBM has no spare terminal to acquire this candidate before releasing the incumbent.",
    levers: ["add_terminal_count", "enable_mbb_if_capacity_supports"],
    producer: "ome_allocator",
  }),
  mbb_overlap_locked: rec({
    code: "mbb_overlap_locked",
    domains: ["unscheduled"],
    gate: "handover_policy",
    layer: "policy",
    family: "eligible_unselected",
    severity: "info",
    label: "Locked by MBB overlap",
    sentence: "An active make-before-break overlap holds the terminals; preemption is off.",
    levers: ["change_handover_policy_params"],
    producer: "ome_allocator",
  }),
  replaced_by_successor: rec({
    code: "replaced_by_successor",
    domains: ["unscheduled"],
    gate: "handover_policy",
    layer: "policy",
    family: "eligible_unselected",
    severity: "info",
    label: "Replaced by successor",
    sentence: "This pair was released after a completed handover to its successor.",
    levers: [],
    producer: "ome_allocator",
  }),
  successor_aborted: rec({
    code: "successor_aborted",
    domains: ["unscheduled", "allocation_event"],
    gate: "handover_policy",
    layer: "policy",
    family: "eligible_unselected",
    severity: "warning",
    label: "Successor aborted",
    sentence: "The chosen successor lost visibility/schedule before teardown; the handover did not complete.",
    levers: ["change_handover_policy_params"],
    producer: "ome_allocator",
  }),
  failed_successor: rec({
    code: "failed_successor",
    domains: ["unscheduled", "allocation_event"],
    gate: "handover_policy",
    layer: "policy",
    family: "eligible_unselected",
    severity: "warning",
    label: "Successor no longer current",
    sentence: "The successor is no longer the current allocation; the handover was not realized.",
    levers: ["change_handover_policy_params"],
    producer: "ome_allocator",
  }),
  failed_acquire: rec({
    code: "failed_acquire",
    domains: ["unscheduled", "allocation_event"],
    gate: "handover_policy",
    layer: "policy",
    family: "eligible_unselected",
    severity: "warning",
    label: "Acquire failed",
    sentence: "The allocator could not acquire this candidate within the handover window.",
    levers: ["change_handover_policy_params", "add_terminal_count"],
    producer: "ome_allocator",
  }),

  // --- Allocation events (OME allocator) — transitions / lifecycle ---
  mbb_overlap_started: rec({
    code: "mbb_overlap_started",
    domains: ["allocation_event"],
    gate: "handover_policy",
    layer: "policy",
    family: "in_flight",
    severity: "info",
    label: "MBB overlap started",
    sentence: "Make-before-break overlap began; incumbent and successor are both scheduled.",
    levers: [],
    producer: "ome_allocator",
  }),
  teardown_completed: rec({
    code: "teardown_completed",
    domains: ["allocation_event"],
    gate: "handover_policy",
    layer: "policy",
    family: "connected",
    severity: "info",
    label: "Handover completed",
    sentence: "Overlap elapsed and the incumbent was released with the successor scheduled.",
    levers: [],
    producer: "ome_allocator",
  }),
  teardown_invalidated_by_epoch: rec({
    code: "teardown_invalidated_by_epoch",
    domains: ["allocation_event"],
    gate: "handover_policy",
    layer: "policy",
    family: "expected_no_link",
    severity: "info",
    label: "Teardown invalidated by seek",
    sentence: "A seek/new epoch invalidated the pending teardown; the new epoch rebuilds from committed state.",
    levers: [],
    producer: "ome_allocator",
  }),
  incumbent_lost: rec({
    code: "incumbent_lost",
    domains: ["allocation_event"],
    gate: "elevation_mask",
    layer: "geometry",
    family: "expected_no_link",
    severity: "info",
    label: "Incumbent lost visibility",
    sentence: "The incumbent satellite left the visibility envelope and was dropped.",
    levers: ["add_planes_or_sats"],
    producer: "ome_allocator",
  }),
  bbm_gap: rec({
    code: "bbm_gap",
    domains: ["allocation_event"],
    gate: "handover_policy",
    layer: "policy",
    family: "expected_no_link",
    severity: "warning",
    label: "BBM handover gap",
    sentence: "Break-before-make: the incumbent was released before the successor was available — a coverage gap.",
    levers: ["enable_mbb_if_capacity_supports", "add_terminal_count"],
    producer: "ome_allocator",
  }),

  // --- Actuation states (Scheduler / Node Agent) ---
  clean: rec({
    code: "clean",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "connected",
    severity: "info",
    label: "Kernel clean",
    sentence: "Desired and actual kernel state agree; the link is proven.",
    levers: [],
    producer: "scheduler",
  }),
  actuation_blocked: rec({
    code: "actuation_blocked",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "warning",
    label: "Actuation blocked",
    sentence: "A clean actuation failure blocks new ground link-up for this station; the kernel was not mutated.",
    levers: [],
    producer: "scheduler",
  }),
  kernel_dirty: rec({
    code: "kernel_dirty",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "alarm",
    label: "Kernel dirty",
    sentence: "Kernel state could not be proven after actuation; manual or proof-based recovery is required.",
    levers: [],
    producer: "node_agent",
  }),
  unknown: rec({
    code: "unknown",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "unknown",
    severity: "warning",
    label: "Actuation unknown",
    sentence: "The actuation state for this station is not yet known to the UI; not assumed clean.",
    levers: [],
    producer: "scheduler",
  }),

  // --- Actuation failure classes (Scheduler / Node Agent) ---
  none: rec({
    code: "none",
    domains: ["actuation"],
    gate: null,
    layer: "actuation",
    family: "connected",
    severity: "info",
    label: "No failure",
    sentence: "No actuation failure.",
    levers: [],
    producer: "scheduler",
  }),
  authority_invariant: rec({
    code: "authority_invariant",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "alarm",
    label: "Authority violation",
    sentence: "Desired state diverged from OME authority (authority-subset violation); dispatch halted.",
    levers: [],
    producer: "scheduler",
  }),
  ome_contract: rec({
    code: "ome_contract",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "alarm",
    label: "OME contract violation",
    sentence: "An OME contract was violated during dispatch; dispatch halted.",
    levers: [],
    producer: "scheduler",
  }),
  fence: rec({
    code: "fence",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "alarm",
    label: "Fenced",
    sentence: "A session/generation fence rejected this actuation; dispatch halted.",
    levers: [],
    producer: "scheduler",
  }),
  ground_clean_failure: rec({
    code: "ground_clean_failure",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "warning",
    label: "Clean ground failure",
    sentence: "Ground actuation failed cleanly; the kernel was not mutated and the station is blocked.",
    levers: [],
    producer: "node_agent",
  }),
  ground_kernel_dirty: rec({
    code: "ground_kernel_dirty",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "alarm",
    label: "Ground kernel dirty",
    sentence: "Ground kernel state could not be proven after actuation; recovery required.",
    levers: [],
    producer: "node_agent",
  }),
  ground_unknown: rec({
    code: "ground_unknown",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "unknown",
    severity: "warning",
    label: "Ground state unknown",
    sentence: "Ground actuation state is inconclusive; not assumed clean.",
    levers: [],
    producer: "node_agent",
  }),
  isl_failure: rec({
    code: "isl_failure",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "alarm",
    label: "ISL actuation failure",
    sentence: "An inter-satellite link actuation failed; ISL failures halt the session (no per-link degraded state).",
    levers: [],
    producer: "scheduler",
  }),
  ops_publish_failure: rec({
    code: "ops_publish_failure",
    domains: ["actuation"],
    gate: "actuation_proof",
    layer: "actuation",
    family: "faulted",
    severity: "warning",
    label: "Ops publish failure",
    sentence: "An operational event failed to publish; observability for this action is degraded.",
    levers: [],
    producer: "scheduler",
  }),
};
