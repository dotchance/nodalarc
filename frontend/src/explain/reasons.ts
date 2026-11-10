// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/**
 * Reason taxonomy registry — the single client-side source of explanation text.
 *
 * Every reason code a backend decision can carry maps to exactly one record here,
 * keyed by the generated unions, so a code the backend adds or drops fails the
 * type check. No UI surface writes its own explanation string; globe tooltip,
 * node card, pair inspector, timeline, and logs all render from this registry.
 * Codes that appear in more than one backend domain (e.g. successor_aborted) get
 * ONE record.
 */

import type {
  ActuationState,
  BindingReasonCode,
  EnvelopeBindingSource,
  ExplanationProducer,
  FunnelGate,
  GroundAllocationEventCategory,
  GroundUnscheduledReason,
  GroundVisibilityRejectReason,
  SchedulerOpsCode,
} from "../generated/vsApi";
import type { Family, Severity } from "./families";

/** Every reason code a ground decision, allocation event or actuation state carries. */
export type ReasonCode =
  | GroundVisibilityRejectReason
  | GroundUnscheduledReason
  | GroundAllocationEventCategory
  | ActuationState
  | BindingReasonCode;

interface SchedulerOpsRecord {
  code: SchedulerOpsCode;
  label: string;
  sentence: string;
  severity: Severity;
}

function ops(code: SchedulerOpsCode, label: string, sentence: string, severity: Severity): SchedulerOpsRecord {
  return { code, label, sentence, severity };
}

export const SCHEDULER_OPS_REGISTRY: Record<SchedulerOpsCode, SchedulerOpsRecord> = {
  REPLACEMENT_LINK_UP_FAILED: ops("REPLACEMENT_LINK_UP_FAILED", "Replacement link-up failed", "The replacement ground link did not come up.", "alarm"),
  GROUND_LINK_UP_FAILED: ops("GROUND_LINK_UP_FAILED", "Ground link-up failed", "A ground link-up command failed.", "alarm"),
  GROUND_LINK_DOWN_FAILED: ops("GROUND_LINK_DOWN_FAILED", "Ground link-down failed", "A ground link-down command failed.", "alarm"),
  GROUND_LATENCY_UPDATE_FAILED: ops("GROUND_LATENCY_UPDATE_FAILED", "Ground latency update failed", "A ground latency update failed.", "warning"),
  OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR: ops("OLD_PAIR_DROPPED_WITHOUT_SUCCESSOR", "Old pair dropped without successor", "The old ground pair was dropped before a successor was proven up.", "alarm"),
  ACTUATION_BLOCKED: ops("ACTUATION_BLOCKED", "Actuation blocked", "A clean actuation failure blocks new ground link changes for this station.", "warning"),
  ACTUATION_CLEAN: ops("ACTUATION_CLEAN", "Actuation clean", "The station's desired and kernel state match.", "info"),
  KERNEL_DIRTY: ops("KERNEL_DIRTY", "Kernel dirty", "Kernel state could not be proven clean.", "alarm"),
  KERNEL_VERIFY_ATTEMPTED: ops("KERNEL_VERIFY_ATTEMPTED", "Kernel verification attempted", "A read-only kernel verification was attempted.", "info"),
  KERNEL_VERIFY_EXHAUSTED: ops("KERNEL_VERIFY_EXHAUSTED", "Kernel verification exhausted", "Read-only kernel verification exhausted without proving the station clean.", "alarm"),
  AUTHORITY_SUBSET_VIOLATION: ops("AUTHORITY_SUBSET_VIOLATION", "Authority violation", "Scheduler desired state exceeded OME authority.", "alarm"),
  ACTUATION_HALTED: ops("ACTUATION_HALTED", "Actuation halted", "A fatal actuation condition halted dispatch.", "alarm"),
  SCHEDULER_SUPERSEDED: ops("SCHEDULER_SUPERSEDED", "Scheduler superseded", "A newer session generation replaced this scheduler instance; it shut down cleanly.", "info"),
  OPERATOR_REPAIR_REQUESTED: ops("OPERATOR_REPAIR_REQUESTED", "Operator repair requested", "An explicit operator repair was requested.", "info"),
  OPERATOR_REPAIR_STARTED: ops("OPERATOR_REPAIR_STARTED", "Operator repair started", "Operator repair started for the station.", "info"),
  OPERATOR_REPAIR_SUCCEEDED: ops("OPERATOR_REPAIR_SUCCEEDED", "Operator repair succeeded", "Operator repair proved the station clean.", "info"),
  OPERATOR_REPAIR_FAILED: ops("OPERATOR_REPAIR_FAILED", "Operator repair failed", "Operator repair did not prove the station clean.", "alarm"),
  OPERATOR_REPAIR_REJECTED: ops("OPERATOR_REPAIR_REJECTED", "Operator repair rejected", "The operator repair request was rejected.", "warning"),
  AGENT_DEGRADED: ops("AGENT_DEGRADED", "Node Agent degraded", "A Node Agent has failed most of its recent calls and appears down; check its DaemonSet pod.", "alarm"),
  AGENT_RECOVERED: ops("AGENT_RECOVERED", "Node Agent recovered", "A previously degraded Node Agent is answering again.", "info"),
};

export function schedulerOpsLabel(code: SchedulerOpsCode): string {
  return SCHEDULER_OPS_REGISTRY[code].label;
}

export function reasonLabel(code: ReasonCode): string {
  return REASON_REGISTRY[code].label;
}

const ENVELOPE_BINDING_REASON: Record<EnvelopeBindingSource, ReasonCode> = {
  field_of_regard: "field_of_regard",
  min_elevation_mask: "elevation_below_min",
};

export function effectiveEnvelopeBindingLabel(source: EnvelopeBindingSource): string {
  return reasonLabel(ENVELOPE_BINDING_REASON[source]);
}

export interface ReasonRecord {
  /** Stable code string, matching the backend enum value verbatim. */
  code: ReasonCode;
  /** Funnel gate this reason belongs to; null only for pass markers (`ok`). */
  gate: FunnelGate | null;
  /** Canonical family this reason implies when it is the binding cause. */
  family: Family;
  /** Baseline severity. */
  severity: Severity;
  /** Short operator-facing label (the only place it is spelled). */
  label: string;
  /** One-sentence human explanation. {actual}/{threshold}/{margin} are filled by the renderer. */
  sentence: string;
  /** Authoritative producer of this verdict. */
  producer: ExplanationProducer;
}

function rec(r: ReasonRecord): ReasonRecord {
  return r;
}

export const REASON_REGISTRY: Record<ReasonCode, ReasonRecord> = {
  // --- Visibility (OME visibility engine) ---
  ok: rec({
    code: "ok",
    gate: null,
    family: "connected",
    severity: "info",
    label: "Visible",
    sentence: "Geometrically visible and within terminal capability.",
    producer: "ome_visibility",
  }),
  los_blocked: rec({
    code: "los_blocked",
    gate: "line_of_sight",
    family: "expected_no_link",
    severity: "info",
    label: "Below horizon",
    sentence: "The satellite is below the local horizon — no line of sight.",
    producer: "ome_visibility",
  }),
  elevation_below_min: rec({
    code: "elevation_below_min",
    gate: "elevation_mask",
    family: "expected_no_link",
    severity: "info",
    label: "Below elevation mask",
    sentence: "Elevation {actual} is under the {threshold} mask.",
    producer: "ome_visibility",
  }),
  range_exceeded: rec({
    code: "range_exceeded",
    gate: "range",
    family: "expected_no_link",
    severity: "info",
    label: "Out of range",
    sentence: "Range {actual} exceeds the terminal max range {threshold}.",
    producer: "ome_visibility",
  }),
  field_of_regard: rec({
    code: "field_of_regard",
    gate: "field_of_regard",
    family: "expected_no_link",
    severity: "info",
    label: "Outside field of regard",
    sentence: "Off-boresight angle {actual} exceeds the terminal field of regard {threshold}.",
    producer: "ome_visibility",
  }),
  tracking_exceeded: rec({
    code: "tracking_exceeded",
    gate: "tracking_rate",
    family: "expected_no_link",
    severity: "info",
    label: "Too fast to track",
    sentence: "Required slew rate {actual} exceeds the terminal tracking rate {threshold}.",
    producer: "ome_visibility",
  }),

  // --- Unscheduled (OME allocator) — visible-but-not-scheduled, i.e. eligible_unselected ---
  gs_capacity: rec({
    code: "gs_capacity",
    gate: "capacity",
    family: "eligible_unselected",
    severity: "info",
    label: "Ground capacity full",
    sentence: "All ground-station terminals are in use ({margin}).",
    producer: "ome_allocator",
  }),
  sat_capacity: rec({
    code: "sat_capacity",
    gate: "capacity",
    family: "eligible_unselected",
    severity: "info",
    label: "Satellite capacity full",
    sentence: "The satellite's ground terminals are all in use ({margin}).",
    producer: "ome_allocator",
  }),
  hysteresis_hold: rec({
    code: "hysteresis_hold",
    gate: "handover_policy",
    family: "eligible_unselected",
    severity: "info",
    label: "Held by hysteresis",
    sentence: "The incumbent is retained; this challenger did not clear the hysteresis margin ({margin}).",
    producer: "ome_allocator",
  }),
  incumbent_held: rec({
    code: "incumbent_held",
    gate: "handover_policy",
    family: "eligible_unselected",
    severity: "info",
    label: "Incumbent held",
    sentence: "Policy keeps the current incumbent over this candidate.",
    producer: "ome_allocator",
  }),
  bbm_no_spare: rec({
    code: "bbm_no_spare",
    gate: "capacity",
    family: "eligible_unselected",
    severity: "warning",
    label: "No spare for handover",
    sentence: "BBM has no spare terminal to acquire this candidate before releasing the incumbent.",
    producer: "ome_allocator",
  }),
  mbb_overlap_locked: rec({
    code: "mbb_overlap_locked",
    gate: "handover_policy",
    family: "eligible_unselected",
    severity: "info",
    label: "Locked by MBB overlap",
    sentence: "An active make-before-break overlap holds the terminals; preemption is off.",
    producer: "ome_allocator",
  }),
  replaced_by_successor: rec({
    code: "replaced_by_successor",
    gate: "handover_policy",
    family: "eligible_unselected",
    severity: "info",
    label: "Replaced by successor",
    sentence: "This pair was released after a completed handover to its successor.",
    producer: "ome_allocator",
  }),
  successor_aborted: rec({
    code: "successor_aborted",
    gate: "handover_policy",
    family: "eligible_unselected",
    severity: "warning",
    label: "Successor aborted",
    sentence: "The chosen successor lost visibility/schedule before teardown; the handover did not complete.",
    producer: "ome_allocator",
  }),
  failed_successor: rec({
    code: "failed_successor",
    gate: "handover_policy",
    family: "eligible_unselected",
    severity: "warning",
    label: "Successor no longer current",
    sentence: "The successor is no longer the current allocation; the handover was not realized.",
    producer: "ome_allocator",
  }),
  failed_acquire: rec({
    code: "failed_acquire",
    gate: "handover_policy",
    family: "eligible_unselected",
    severity: "warning",
    label: "Acquire failed",
    sentence: "The allocator could not acquire this candidate within the handover window.",
    producer: "ome_allocator",
  }),

  // --- Allocation events (OME allocator) — transitions / lifecycle ---
  mbb_overlap_started: rec({
    code: "mbb_overlap_started",
    gate: "handover_policy",
    family: "in_flight",
    severity: "info",
    label: "MBB overlap started",
    sentence: "Make-before-break overlap began; incumbent and successor are both scheduled.",
    producer: "ome_allocator",
  }),
  teardown_completed: rec({
    code: "teardown_completed",
    gate: "handover_policy",
    family: "connected",
    severity: "info",
    label: "Handover completed",
    sentence: "Overlap elapsed and the incumbent was released with the successor scheduled.",
    producer: "ome_allocator",
  }),
  teardown_invalidated_by_epoch: rec({
    code: "teardown_invalidated_by_epoch",
    gate: "handover_policy",
    family: "expected_no_link",
    severity: "info",
    label: "Teardown invalidated by seek",
    sentence: "A seek/new epoch invalidated the pending teardown; the new epoch rebuilds from committed state.",
    producer: "ome_allocator",
  }),
  incumbent_lost: rec({
    code: "incumbent_lost",
    gate: "elevation_mask",
    family: "expected_no_link",
    severity: "info",
    label: "Incumbent lost visibility",
    sentence: "The incumbent satellite left the visibility envelope and was dropped.",
    producer: "ome_allocator",
  }),
  bbm_gap: rec({
    code: "bbm_gap",
    gate: "handover_policy",
    family: "expected_no_link",
    severity: "warning",
    label: "BBM handover gap",
    sentence: "Break-before-make: the incumbent was released before the successor was available — a coverage gap.",
    producer: "ome_allocator",
  }),
  retargeted_before_last_arrival: rec({
    code: "retargeted_before_last_arrival",
    gate: "handover_policy",
    family: "expected_no_link",
    severity: "warning",
    label: "Retargeted before last arrival",
    sentence:
      "A terminal turned to a new peer while signals from its old peer were still arriving; those signals are lost.",
    producer: "ome_allocator",
  }),

  // --- Actuation states (Scheduler / Node Agent) ---
  clean: rec({
    code: "clean",
    gate: "actuation_proof",
    family: "connected",
    severity: "info",
    label: "Kernel clean",
    sentence: "Desired and actual kernel state agree; the link is proven.",
    producer: "scheduler",
  }),
  actuation_blocked: rec({
    code: "actuation_blocked",
    gate: "actuation_proof",
    family: "faulted",
    severity: "warning",
    label: "Actuation blocked",
    sentence: "A clean actuation failure blocks new ground link-up for this station; the kernel was not mutated.",
    producer: "scheduler",
  }),
  kernel_dirty: rec({
    code: "kernel_dirty",
    gate: "actuation_proof",
    family: "faulted",
    severity: "alarm",
    label: "Kernel dirty",
    sentence: "Kernel state could not be proven after actuation; manual or proof-based recovery is required.",
    producer: "node_agent",
  }),
  actuation_diverged: rec({
    code: "actuation_diverged",
    gate: "actuation_proof",
    // Family/severity for an actuation_proof binding are decided by actuation.state +
    // the wall-clock bound in deriveFamily/deriveSeverity, not this record; these are
    // for consistency. The sentence is what the headline renders once it has faulted.
    family: "in_flight",
    severity: "info",
    label: "Actuation diverged",
    sentence: "OME desires this link, but the kernel has not brought it up.",
    producer: "scheduler",
  }),
  unknown: rec({
    code: "unknown",
    gate: "actuation_proof",
    family: "unknown",
    severity: "warning",
    label: "Actuation unknown",
    sentence: "The actuation state for this station is not yet known to the UI; not assumed clean.",
    producer: "scheduler",
  }),

  // --- Actuation failure classes (Scheduler / Node Agent) ---
};
