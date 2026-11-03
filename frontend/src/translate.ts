// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Display formatters for non-decision values (link type, durations, timestamps).
 *
 * Link-event reason text is NOT here: it moved to the single family-classified registry
 * src/explain/linkEvents.ts (linkEventLabel). The old REASON_MAP was a parallel, untaxonomized
 * vocabulary that conflicted with the registry — removed so there is one source. */

import type { LinkState, PublicLinkType } from "./generated/vsApi";

/** VF spec Section 8.4 — link type translation */
const LINK_TYPE_MAP: Record<PublicLinkType, string> = {
  isl: "ISL",
  inter_constellation: "Inter-constellation ISL",
  inter_body_relay: "Inter-body relay",
  ground: "Ground Link",
};

export function translateLinkType(linkType: PublicLinkType): string {
  return LINK_TYPE_MAP[linkType];
}

/** Format milliseconds as a readable duration: milliseconds to 0.1 ms below a second, seconds
 *  to the millisecond below a minute, minutes beyond (a Mars link is about 20 minutes). */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(3)} s`;
  return `${(ms / 60000).toFixed(2)} min`;
}

/** "out X · in Y": a link's latency out of and into one of its ends. */
export function formatLinkEndLatency(
  link: Pick<LinkState, "node_a" | "latency_a_to_b_ms" | "latency_b_to_a_ms">,
  nodeId: string,
): string {
  const { outbound, inbound } = linkLatencies(link, nodeId);
  return `out ${formatDuration(outbound)} · in ${formatDuration(inbound)}`;
}

/** A link's one-way latency out of and into one of its ends. Each direction has its own light
 *  time: a_to_b is the signal node_a sends to node_b. */
export function linkLatencies(
  link: Pick<LinkState, "node_a" | "latency_a_to_b_ms" | "latency_b_to_a_ms">,
  nodeId: string,
): { outbound: number; inbound: number } {
  return nodeId === link.node_a
    ? { outbound: link.latency_a_to_b_ms, inbound: link.latency_b_to_a_ms }
    : { outbound: link.latency_b_to_a_ms, inbound: link.latency_a_to_b_ms };
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
