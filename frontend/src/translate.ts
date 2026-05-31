// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Display formatters for non-decision values (link type, durations, timestamps).
 *
 * Link-event reason text is NOT here: it moved to the single family-classified registry
 * src/explain/linkEvents.ts (linkEventLabel). The old REASON_MAP was a parallel, untaxonomized
 * vocabulary that conflicted with the registry — removed so there is one source. */

/** VF spec Section 8.4 — link type translation */
const LINK_TYPE_MAP: Record<string, string> = {
  intra_plane_isl: "Intra-area ISL",
  cross_plane_isl: "Cross-area ISL (ABR link)",
  ground_uplink: "Ground Uplink",
  ground_downlink: "Ground Downlink",
};

export function translateLinkType(linkType: string | null): string {
  if (!linkType) return "unknown";
  return LINK_TYPE_MAP[linkType] ?? linkType;
}

/** Format milliseconds as human-readable duration. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

/** Format ISO datetime string to full YYYY-MM-DD HH:MM:SS UTC. */
export function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toISOString().replace("T", " ").substring(0, 19) + " UTC";
  } catch {
    return iso;
  }
}

/** Format ISO datetime string to compact HH:MM:SS (for event log). */
export function formatTimeShort(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toISOString().substring(11, 19);
  } catch {
    return iso;
  }
}
