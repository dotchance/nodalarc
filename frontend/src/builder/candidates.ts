// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Candidate-pair preview for resolved link rules.
 *
 *  The server computes preview geometry through the OME visibility composites.
 *  This module maps those results to canvas lines and editor notes. Machine
 *  reason tokens remain unchanged; only their display text is defined here.
 */

import type {
  BuilderPreviewPair,
  BuilderPreviewRejectReason,
  BuilderRulePreview,
  BuilderWorld,
  PreviewScope,
} from "./generated/builderApi";

export interface CandidateComputation {
  /** Every rule's drawable pairs, as the server sent them. */
  pairs: readonly BuilderPreviewPair[];
  /** Each rule's note by rule id; null when the rule has nothing to say. */
  notes: ReadonlyMap<string, string | null>;
  /** Rules whose geometry the server computed and that drew no pair. A disabled
   *  or preview-pending rule is never dark; it shows its scope note. */
  darkRules: number;
}

/** The human phrasing of each runtime reject reason, read after a pair count.
 *  The machine token stays on the wire (BuilderRulePreview.reason_counts) — this
 *  is display only, so it never renames the vocabulary. */
const REASON_COPY: Record<BuilderPreviewRejectReason, string> = {
  los_blocked: "with no line of sight",
  range_exceeded: "beyond terminal range",
  elevation_below_min: "below the elevation mask",
  field_of_regard: "outside the field of regard",
  terminal_type_mismatch: "with incompatible terminal types",
  no_geometry: "with no computable geometry",
};

/** Notes for preview scopes the server did not compute. */
const SCOPE_NOTE: Record<Exclude<PreviewScope, "computed">, string> = {
  terrestrial_pending: "terrestrial run — surface routing preview pending",
  disabled: "rule disabled",
};

/** The rule note, composed from the server's result:
 *  (a) a non-computed scope supersedes other notes; (b) a fixed
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
    return SCOPE_NOTE[preview.preview_scope]; // (a)
  }
  if (fixed && allocatedPairs === 0) {
    return "the allocator granted no pairs for this rule"; // (b)
  }
  const notes: string[] = [];
  const denom = preview.capped
    ? ` among ${preview.pairs_tested} tested of ${preview.pairs_total} possible`
    : "";
  for (const rc of preview.reason_counts) {
    notes.push(`${rc.count} ${rc.count === 1 ? "pair" : "pairs"} ${REASON_COPY[rc.reason]}${denom}`); // (c)
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

  const pairs: BuilderPreviewPair[] = [];
  const notes = new Map<string, string | null>();
  let darkRules = 0;
  for (const preview of world.rule_previews) {
    const mode = modeByRule.get(preview.rule_id);
    if (mode === undefined) {
      throw new Error(`rule preview ${preview.rule_id} has no resolved link rule`);
    }
    const allocated = allocByRule.get(preview.rule_id);
    if (allocated === undefined) {
      throw new Error(`rule preview ${preview.rule_id} has no allocation`);
    }
    const fixed = mode === "explicit_pairs" || mode === "nearest_n";
    notes.set(preview.rule_id, previewNote(preview, allocated, fixed));
    if (preview.preview_scope === "computed" && preview.pairs_drawn === 0) darkRules += 1;
    pairs.push(...preview.drawable_pairs);
  }
  return { pairs, notes, darkRules };
}
