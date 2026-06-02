// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Default-state ground-station family — the canonical {@link Family} for a GS glyph on the globe
 * when NOTHING is selected, derived from the snapshot alone (active links + actuation notices),
 * with no per-GS decision fetch. This is the snapshot APPROXIMATION the spec's "Globe Default
 * State" calls for: faulted / degraded come from the Scheduler actuation notices; connected from
 * an active ground link; everything else shows as expected_no_link (slate).
 *
 * The PRECISE family — and the expected-no-link vs eligible-unselected distinction — comes from
 * the decision-explanation on SELECT (the node card / on-select overlay via deriveFamily), not
 * from the snapshot. This function deliberately does NOT claim that finer distinction: it never
 * returns eligible_unselected, because the snapshot cannot tell "no viable candidate" from "viable
 * but unselected" without the decision. Pure; unit-tested.
 */
import type { Family } from "./families";
import { schedulerOpsLabel } from "./reasons";
import type { ActuationNotice, LinkState } from "../types";

export interface GsFamily {
  family: Family;
  /** Short cause for the tooltip caption (the actuation fault message), or null. */
  reason: string | null;
}

export function groundStationFamily(
  gsId: string,
  links: readonly LinkState[],
  actuationNotices: readonly ActuationNotice[],
): GsFamily {
  const notice = actuationNotices.find((n) => n.gs_id === gsId);
  if (notice) {
    const reason = notice.message || schedulerOpsLabel(notice.reason_code);
    if (notice.blocking_new_ground_link_up || notice.actuation_state === "kernel_dirty" || notice.actuation_state === "actuation_blocked") {
      return { family: "faulted", reason };
    }
    if (notice.actuation_state === "unknown") return { family: "unknown", reason };
    // A retained clean heartbeat is health metadata, not a visual state. Fall through to the
    // actual link snapshot so clean notices cannot manufacture an in-flight/degraded glyph.
  }
  const connected = links.some(
    (l) => l.state === "active" && (l.node_a === gsId || l.node_b === gsId),
  );
  return connected
    ? { family: "connected", reason: null }
    : { family: "expected_no_link", reason: null };
}
