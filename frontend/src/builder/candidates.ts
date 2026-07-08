// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Candidate-pair preview for resolved link rules — a RENDERER of the server's
 *  frozen-epoch visibility facts, never a second physics engine.
 *
 *  NodalArc computes each rule's preview geometry server-side (BuilderRulePreview
 *  on `world.rule_previews`) through the same OME visibility composites the
 *  runtime uses. This module ADAPTS those facts into the canvas's candidate
 *  lines and the editor's rule notes; it decides nothing about geometry. A rule
 *  renders the server's drawn pairs; a rule the server could not compute
 *  (inter-body span, terrestrial run, disabled) carries the server's typed scope
 *  note instead of fake lines; every reject reason and every cap is the server's,
 *  reported verbatim. The one thing left client-side is phrasing — the machine
 *  reason token stays on the wire, the human copy is display only.
 */

import type { BuilderRulePreview, BuilderWorld } from "./builderTypes";

export interface CandidatePair {
  rule_id: string;
  kind: string;
  a: string;
  b: string;
}

export interface RulePreview {
  rule_id: string;
  kind: string;
  mode: string;
  /** "enabled" here means the rule has COMPUTED geometry — the only rules that
   *  can read as "dark" (on, yet zero lines). A disabled or preview-pending rule
   *  is never dark; it shows its scope note. */
  enabled: boolean;
  candidates: number;
  note: string | null;
}

export interface CandidateComputation {
  pairs: CandidatePair[];
  previews: RulePreview[];
}

/** The human phrasing of each runtime reject reason, read after a pair count.
 *  The machine token stays on the wire (BuilderRulePreview.reason_counts) — this
 *  is display only, so it never renames the vocabulary. */
const REASON_COPY: Record<string, string> = {
  los_blocked: "with no line of sight",
  range_exceeded: "beyond terminal range",
  elevation_below_min: "below the elevation mask",
  field_of_regard: "outside the field of regard",
  terminal_type_mismatch: "with incompatible terminal types",
  no_geometry: "with no computable geometry",
};

/** The typed scope walls, rendered verbatim from the server's preview_scope. */
const SCOPE_NOTE: Record<string, string> = {
  inter_body_pending: "inter-body span — preview pending, runtime computes contacts",
  terrestrial_pending: "terrestrial run — surface routing preview pending",
  disabled: "rule disabled",
};

/** The rule note, composed from the server's verdict (the decided mapping):
 *  (a) a non-computed scope is a typed wall superseding everything; (b) a fixed
 *  rule the allocator granted nothing states that (the allocator's word, beside
 *  the editor's own allocation facts, not a geometry claim); (c) each reject
 *  reason with its count — carrying the tested/total denominator whenever the
 *  preview is capped, since the counts are over the tested subset; (d) a
 *  computed rule that drew nothing and rejected nothing permits pairs the
 *  geometry currently forbids; (e) a capped preview says how much it drew of
 *  how much it tested of how much is possible — deterministic-order truncation,
 *  never a distance rank, never silent. */
function previewNote(
  preview: BuilderRulePreview,
  allocatedPairs: number,
  fixed: boolean,
): string | null {
  if (preview.preview_scope !== "computed") {
    return SCOPE_NOTE[preview.preview_scope] ?? null; // (a)
  }
  if (fixed && allocatedPairs === 0) {
    return "the allocator granted no pairs for this rule"; // (b)
  }
  const notes: string[] = [];
  const denom = preview.capped
    ? ` among ${preview.pairs_tested} tested of ${preview.pairs_total} possible`
    : "";
  for (const rc of preview.reason_counts) {
    const copy = REASON_COPY[rc.reason] ?? rc.reason;
    notes.push(`${rc.count} ${rc.count === 1 ? "pair" : "pairs"} ${copy}${denom}`); // (c)
  }
  if (preview.pairs_drawn === 0 && preview.reason_counts.length === 0) {
    notes.push("rule permits, geometry currently forbids — runtime computes contacts over time"); // (d)
  }
  if (preview.capped) {
    notes.push(
      `showing ${preview.pairs_drawn} drawn from ${preview.pairs_tested} tested of ${preview.pairs_total} possible`,
    ); // (e)
  }
  return notes.length ? notes.join("; ") : null;
}

export function computeCandidates(world: BuilderWorld): CandidateComputation {
  const modeByRule = new Map(world.link_rules.map((rule) => [rule.rule_id, rule.topology_mode]));
  const allocByRule = new Map(
    world.allocations.map((alloc) => [alloc.rule_id, alloc.allocated_pairs]),
  );

  const pairs: CandidatePair[] = [];
  const previews: RulePreview[] = [];
  for (const preview of world.rule_previews) {
    const mode = modeByRule.get(preview.rule_id) ?? "";
    const fixed = mode === "explicit_pairs" || mode === "nearest_n";
    previews.push({
      rule_id: preview.rule_id,
      kind: preview.kind,
      mode,
      enabled: preview.preview_scope === "computed",
      candidates: preview.pairs_drawn,
      note: previewNote(preview, allocByRule.get(preview.rule_id) ?? 0, fixed),
    });
    for (const pair of preview.drawable_pairs) {
      pairs.push({ rule_id: pair.rule_id, kind: pair.kind, a: pair.node_a, b: pair.node_b });
    }
  }
  return { pairs, previews };
}
