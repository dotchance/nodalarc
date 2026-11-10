// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Client-side MEANING layer: turn backend DecisionExplanationFacts into the
 * canonical family, severity, and human strings — all sourced from the single
 * reason registry. The backend never assigns a family; this is the one place
 * the Expected/Faulted classification happens.
 */

import type { Family, Severity } from "./families";
import { REASON_REGISTRY } from "./reasons";
import type {
  DecisionExplanationFacts,
  ExplanationProducer,
  FunnelGate,
  GroundUnscheduledReason,
  GroundVisibilityRejectReason,
  LadderGate,
} from "../generated/vsApi";

export const GATE_LABELS: Record<FunnelGate, string> = {
  line_of_sight: "Line of sight",
  range: "Range",
  elevation_mask: "Elevation mask",
  field_of_regard: "Field of regard",
  tracking_rate: "Tracking rate",
  selection_policy: "Selection policy",
  capacity: "Capacity",
  handover_policy: "Handover policy",
  actuation_proof: "Actuation proof",
};

/** Operator-facing label for the authoritative producer of a gate's verdict (the spec's
 *  "which component owns this" — single source for the Per-Pair Inspector's provenance). */
export const PRODUCER_LABELS: Record<ExplanationProducer, string> = {
  ome_visibility: "OME visibility",
  ome_allocator: "OME allocator",
  scheduler: "Scheduler",
  node_agent: "Node Agent",
};

/**
 * Resolve the canonical family from facts. Actuation outcome dominates when the
 * pair is OME-desired; otherwise the binding reason's registry family applies.
 *
 * in_flight vs faulted: a clean-but-diverged pair is converging (in_flight) until its
 * divergence age reaches the wall-clock actuation bound (fault_after_ms), at which point
 * it escalates to faulted. The age and bound are carried on the facts (server-computed
 * elapsed + the simulation.actuation contract) — never a frontend constant or the
 * client's clock. A divergence with no age yet (e.g. just after a VS-API restart) stays
 * the calm, non-green in_flight, never red, until the age is known.
 */
export function deriveFamily(facts: DecisionExplanationFacts): Family {
  const act = facts.actuation;

  if (facts.binding_gate === "actuation_proof") {
    if (act.state === "kernel_dirty" || act.state === "actuation_blocked") return "faulted";
    if (act.state === "unknown") return "unknown";
    if (act.diverged) {
      const elapsed = act.actuation_elapsed_ms;
      const bound = act.fault_after_ms;
      if (elapsed !== null && bound !== null && elapsed >= bound) return "faulted";
      return "in_flight";
    }
    return "faulted";
  }

  // No rejection at all: the composer leaves binding_gate null for a scheduled pair
  // that is kernel-up and not failing. Connected only when the roster confirms clean;
  // kernel-up but roster-unknown is "unknown" (proven up, health unconfirmed) — never
  // silently green, per the family contract.
  if (facts.binding_gate === null) {
    if (act.state === "unknown") return "unknown";
    return "connected";
  }

  // A binding gate with no reason code cannot be classified.
  if (facts.binding_reason_code === null) return "unknown";
  return REASON_REGISTRY[facts.binding_reason_code].family;
}

export function deriveSeverity(facts: DecisionExplanationFacts): Severity {
  if (facts.binding_gate === "actuation_proof") {
    const fam = deriveFamily(facts);
    if (fam === "faulted") return "alarm";
    if (fam === "unknown") return "warning";
    return "info";
  }
  if (facts.binding_reason_code === null) return "info";
  return REASON_REGISTRY[facts.binding_reason_code].severity;
}

function round1(n: number): string {
  return (Math.round(n * 10) / 10).toString();
}

function gateUnit(gate: FunnelGate): string {
  if (gate === "range") return " km";
  if (gate === "tracking_rate") return " deg/s";
  if (gate === "line_of_sight" || gate === "elevation_mask" || gate === "field_of_regard") {
    return " deg";
  }
  return "";
}

/**
 * Margin text for a numeric ladder gate: "14 deg / min 25 deg (-11 deg)".
 * `max` comparison for terminal-capability ceilings (range/FoR/tracking),
 * `min` for the elevation floor. Returns null when the gate carries no numbers.
 */
export function formatMargin(g: LadderGate): string | null {
  if (g.actual == null && g.threshold == null) return null;
  const u = gateUnit(g.gate);
  const ceiling = g.gate === "range" || g.gate === "field_of_regard" || g.gate === "tracking_rate";
  const cmp = ceiling ? "max" : "min";
  const a = g.actual != null ? `${round1(g.actual)}${u}` : "—";
  const t = g.threshold != null ? `${cmp} ${round1(g.threshold)}${u}` : "—";
  let delta = "";
  if (g.actual != null && g.threshold != null) {
    const d = g.actual - g.threshold;
    delta = ` (${d >= 0 ? "+" : ""}${round1(d)}${u})`;
  }
  return `${a} / ${t}${delta}`;
}

/** Fill a registry sentence template's {actual}/{threshold}/{margin} tokens. */
export function fillSentence(
  sentence: string,
  vals: { actual?: string; threshold?: string; margin?: string },
): string {
  return sentence
    .replace("{actual}", vals.actual ?? "—")
    .replace("{threshold}", vals.threshold ?? "—")
    .replace("{margin}", vals.margin ?? "");
}

/** The binding ladder row (the gate the pair stopped at), if any. */
export function bindingRow(facts: DecisionExplanationFacts): LadderGate | null {
  return facts.ladder.find((g) => g.is_binding) ?? null;
}

/** A candidate as the raw OME decision states it: rejected with a reason, or
 *  visible and either withheld with a reason or scheduled (no reason). */
export type CandidateDecision =
  | { visible: false; rejectReason: GroundVisibilityRejectReason }
  | { visible: true; unscheduledReason: GroundUnscheduledReason | null };

/**
 * Lightweight family + label for a candidate row in the GS candidate list, derived
 * from the raw OME decision (the precise family + funnel come from the inspector).
 * A scheduled pair's actuation is not in the raw decision, so it reads neutral
 * ("unknown") in the list rather than a possibly-false green. Family for rejected /
 * withheld rows comes from the reason registry — single source, no drift.
 */
export function candidateStatus(o: CandidateDecision): { family: Family; label: string } {
  if (!o.visible) {
    const rec = REASON_REGISTRY[o.rejectReason];
    return { family: rec.family, label: rec.label };
  }
  if (o.unscheduledReason === null) return { family: "unknown", label: "scheduled" };
  const rec = REASON_REGISTRY[o.unscheduledReason];
  return { family: rec.family, label: rec.label };
}

/** A one-line headline for the focal pair, built from the binding reason + facts. */
export function headline(facts: DecisionExplanationFacts): string {
  const fam = deriveFamily(facts);
  const sat = facts.pair.find((n) => n !== facts.gs_id) ?? facts.pair[1];
  if (fam === "connected") return `Connected to ${sat}.`;
  if (fam === "in_flight") return `Converging on ${sat} (handover in flight).`;

  const code = facts.binding_reason_code;
  if (code === null) return "State unknown.";
  const rec = REASON_REGISTRY[code];

  const row = bindingRow(facts);
  const margin = row ? formatMargin(row) : null;
  const actual = row?.actual != null ? round1(row.actual) : undefined;
  const threshold = row?.threshold != null ? round1(row.threshold) : undefined;
  const filled = fillSentence(rec.sentence, { actual, threshold, margin: margin ?? undefined });

  if (fam === "eligible_unselected") {
    return `${sat} is eligible but not selected — ${filled}`;
  }
  return filled;
}

export interface DisplayRow {
  row: LadderGate;
  label?: string;
}

/**
 * Ladder rows for display, collapsing co-linear gates. Under a local_vertical
 * boresight, elevation mask and field-of-regard are the SAME axis — FoR imposes
 * an effective elevation floor. Showing them as independent rows produces the
 * "elevation passed / FoR failed" contradiction for a 25-30 deg satellite. Here
 * they fold into one row measured against the effective floor, so the ladder
 * agrees with the effective-envelope panel. Non-vertical (steerable) boresights
 * keep the rows separate — there they genuinely decouple.
 */
export function displayLadder(facts: DecisionExplanationFacts): DisplayRow[] {
  const env = facts.envelope;
  const hasElev = facts.ladder.some((g) => g.gate === "elevation_mask");
  const hasFor = facts.ladder.some((g) => g.gate === "field_of_regard");
  // Collapse elevation + FoR into one ground-floor row only when the binding constraint
  // is ground-side (or nothing binds): the ground local-vertical FoR is co-linear with
  // the displayed elevation floor. If the SATELLITE terminal's FoR binds, its nadir
  // geometry is NOT co-linear with the ground floor, so keep the rows separate — folding
  // would hide a satellite-side FoR failure behind a passing ground floor.
  const collapse =
    env.ground.boresight_mode === "local_vertical" &&
    (env.binding_endpoint === "none" || env.binding_endpoint === "ground") &&
    hasElev &&
    hasFor;

  if (!collapse) return facts.ladder.map((row) => ({ row }));

  const floor = env.effective_min_elevation_deg;
  const elevRow = facts.ladder.find((g) => g.gate === "elevation_mask")!;
  const forRow = facts.ladder.find((g) => g.gate === "field_of_regard")!;
  const elevation = elevRow.actual;
  const forLimited = env!.binding_source === "field_of_regard";

  const merged: LadderGate = {
    gate: "elevation_mask",
    state: elevation == null ? "not_evaluated" : elevation >= floor ? "pass" : "fail",
    actual: elevation,
    threshold: floor,
    rejecting_endpoint: forLimited ? forRow.rejecting_endpoint : elevRow.rejecting_endpoint,
    reason_code:
      elevation != null && elevation < floor
        ? forLimited
          ? "field_of_regard"
          : "elevation_below_min"
        : null,
    producer: "ome_visibility",
    is_binding: elevRow.is_binding || forRow.is_binding,
  };
  const label = forLimited ? "Elevation (FoR-limited)" : "Elevation mask";

  const out: DisplayRow[] = [];
  for (const row of facts.ladder) {
    if (row.gate === "field_of_regard") continue;
    out.push(row.gate === "elevation_mask" ? { row: merged, label } : { row });
  }
  return out;
}
