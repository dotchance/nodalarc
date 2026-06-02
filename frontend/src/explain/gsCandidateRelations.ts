// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Per-satellite relation to a SELECTED ground station, for the globe's on-select bloom (spec
 * "Globe On Ground-Station Select": sats tinted by relation — connected / eligible / rejected /
 * far). This is the SINGLE classifier the globe shares with the node card: it reuses
 * {@link candidateStatus} (registry-backed family) and the same `ground-link-decisions` data the
 * card's candidate list is built from, so a satellite can never read one family on the glyph and
 * another in the panel. Connected wins (active link), then withheld-but-viable (eligible), then
 * rejected (registry-derived calm family); satellites in none of those are simply absent from the
 * map ("far/irrelevant", dimmed by the caller). Pure; unit-tested.
 */
import type { Family } from "./families";
import { candidateStatus } from "./derive";
import { reasonLabel } from "./reasons";
import type { GroundDecisionsSnapshot } from "./client";
import type { LinkState } from "../types";

export interface SatRelation {
  family: Family;
  /** Registry-resolved human reason for the hover tooltip (never a raw code), or null. */
  reason: string | null;
}

export function gsCandidateRelations(
  gsId: string,
  decisions: GroundDecisionsSnapshot | null,
  links: readonly LinkState[],
): Map<string, SatRelation> {
  const rel = new Map<string, SatRelation>();

  // Connected wins: an active ground link with this GS is the strongest relation.
  for (const l of links) {
    if (l.state !== "active") continue;
    const sat = l.node_a === gsId ? l.node_b : l.node_b === gsId ? l.node_a : null;
    if (sat) rel.set(sat, { family: "connected", reason: null });
  }

  // Eligible but withheld (viable, not selected).
  for (const u of decisions?.unscheduled_pairs ?? []) {
    const sat = u.pair[0] === gsId ? u.pair[1] : u.pair[0];
    if (rel.has(sat)) continue;
    const { family } = candidateStatus({
      visible: true,
      isWithheld: true,
      rejectReason: "",
      unscheduledReason: u.unscheduled_reason,
    });
    rel.set(sat, { family, reason: reasonLabel(u.unscheduled_reason) });
  }

  // Rejected: outside the envelope — registry-derived calm family + reason.
  for (const d of decisions?.decisions ?? []) {
    const sat = d.pair[0] === gsId ? d.pair[1] : d.pair[0];
    if (rel.has(sat)) continue;
    const { family } = candidateStatus({
      visible: false,
      isWithheld: false,
      rejectReason: d.reject_reason,
      unscheduledReason: null,
    });
    rel.set(sat, { family, reason: reasonLabel(d.reject_reason) });
  }

  return rel;
}
