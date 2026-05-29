// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Client-side MEANING layer: turn backend DecisionExplanationFacts into the
 * canonical family, severity, and human strings — all sourced from the single
 * reason registry. The backend never assigns a family; this is the one place
 * the Expected/Faulted classification happens.
 */

import type { Family, FunnelGate, Severity } from "./families";
import { REASON_REGISTRY } from "./reasons";
import type { DecisionFacts, LadderGate } from "./types";

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

/**
 * Resolve the canonical family from facts. Actuation outcome dominates when the
 * pair is OME-desired; otherwise the binding reason's registry family applies.
 *
 * in_flight vs faulted: a clean-but-diverged pair is converging (in_flight).
 * The C-D latency-bound escalation to faulted needs a convergence deadline the
 * facts do not yet carry — tracked as a follow-up; until then a clean divergence
 * reads as in_flight, never red.
 */
export function deriveFamily(facts: DecisionFacts): Family {
  const act = facts.actuation;

  if (facts.binding_gate === "actuation_proof") {
    if (act?.state === "kernel_dirty" || act?.state === "actuation_blocked") return "faulted";
    if (act?.state === "unknown") return "unknown";
    if (act?.diverged) return "in_flight";
    return "faulted";
  }

  // No rejection at all: the composer only leaves binding_gate null for a
  // scheduled pair proven up.
  if (facts.binding_gate === null) return "connected";

  const rec = facts.binding_reason_code ? REASON_REGISTRY[facts.binding_reason_code] : undefined;
  return rec?.family ?? "unknown";
}

export function deriveSeverity(facts: DecisionFacts): Severity {
  if (facts.binding_gate === "actuation_proof") {
    const fam = deriveFamily(facts);
    if (fam === "faulted") return "alarm";
    if (fam === "unknown") return "warning";
    return "info";
  }
  const rec = facts.binding_reason_code ? REASON_REGISTRY[facts.binding_reason_code] : undefined;
  return rec?.severity ?? "info";
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
export function bindingRow(facts: DecisionFacts): LadderGate | null {
  return facts.ladder.find((g) => g.is_binding) ?? null;
}

/** A one-line headline for the focal pair, built from the binding reason + facts. */
export function headline(facts: DecisionFacts): string {
  const fam = deriveFamily(facts);
  const sat = facts.pair ? facts.pair.find((n) => n !== facts.gs_id) ?? facts.pair[1] : null;
  if (fam === "connected") return sat ? `Connected to ${sat}.` : "Connected.";
  if (fam === "in_flight") return sat ? `Converging on ${sat} (handover in flight).` : "Converging.";

  const code = facts.binding_reason_code;
  const rec = code ? REASON_REGISTRY[code] : undefined;
  if (!rec) return "State unknown.";

  const row = bindingRow(facts);
  const margin = row ? formatMargin(row) : null;
  const actual = row?.actual != null ? round1(row.actual) : undefined;
  const threshold = row?.threshold != null ? round1(row.threshold) : undefined;
  const filled = fillSentence(rec.sentence, { actual, threshold, margin: margin ?? undefined });

  if (fam === "eligible_unselected" && sat) {
    return `${sat} is eligible but not selected — ${filled}`;
  }
  return filled;
}
