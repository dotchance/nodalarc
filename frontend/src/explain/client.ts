// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Typed VS-API client for the link-explainability endpoints. */

import { REST_URL, authHeaders } from "../config";
import type { DecisionFacts, GsDecisionTimelineFacts } from "./types";

/**
 * Fetch composed decision-explanation facts for one ground station, or — when
 * `satId` is given — for that exact GS<->sat pair (the Per-Pair Inspector).
 * Returns null on 404 (no snapshot yet, or no decision covers the GS/pair) so the
 * caller can render a neutral "no data" state rather than an error.
 */
export async function fetchDecisionExplanation(
  gsId: string,
  satId?: string | null,
  signal?: AbortSignal,
): Promise<DecisionFacts | null> {
  const params = new URLSearchParams({ gs: gsId });
  if (satId) params.set("sat", satId);
  const url = `${REST_URL}/api/v1/decision-explanation?${params.toString()}`;
  const resp = await fetch(url, { headers: authHeaders(), signal });
  if (resp.status === 404) return null;
  if (!resp.ok) {
    throw new Error(`decision-explanation ${resp.status}: ${await resp.text()}`);
  }
  return (await resp.json()) as DecisionFacts;
}

/** One row of the OME ground-decision snapshot (only the fields the candidate list reads). */
export interface GroundDecisionRow {
  pair: [string, string];
  visible: boolean;
  reject_reason: string;
  elevation_deg: number | null;
  range_km: number | null;
}

export interface GroundDecisionsSnapshot {
  sim_time: string;
  snapshot_seq: number;
  epoch_id: number;
  decisions: GroundDecisionRow[];
  unscheduled_pairs: { pair: [string, string]; unscheduled_reason: string }[];
}

/**
 * Fetch the OME ground-decision snapshot sliced to one node's candidates — the source
 * for a node card's candidate list. `node` is the selected GS or satellite id; the server
 * returns only the decisions/unscheduled pairs that node participates in, so the card
 * never polls and discards the whole GS×satellite cross-product (wrong primitive at
 * thousand-satellite scale). Omit `node` only for the full-snapshot view. Null on 404 (no
 * snapshot yet); a node with no candidates this tick is a 200 with empty arrays.
 */
export async function fetchGroundDecisions(
  node?: string | null,
  signal?: AbortSignal,
): Promise<GroundDecisionsSnapshot | null> {
  const url = node
    ? `${REST_URL}/api/v1/ground-link-decisions?node=${encodeURIComponent(node)}`
    : `${REST_URL}/api/v1/ground-link-decisions`;
  const resp = await fetch(url, { headers: authHeaders(), signal });
  if (resp.status === 404) return null;
  if (!resp.ok) {
    throw new Error(`ground-link-decisions ${resp.status}: ${await resp.text()}`);
  }
  return (await resp.json()) as GroundDecisionsSnapshot;
}


export async function fetchDecisionTimeline(
  gsId: string,
  signal?: AbortSignal,
): Promise<GsDecisionTimelineFacts | null> {
  const url = `${REST_URL}/api/v1/decision-explanation/timeline?gs=${encodeURIComponent(gsId)}`;
  const resp = await fetch(url, { headers: authHeaders(), signal });
  if (resp.status === 404) return null;
  if (!resp.ok) {
    throw new Error(`decision-explanation timeline ${resp.status}: ${await resp.text()}`);
  }
  return (await resp.json()) as GsDecisionTimelineFacts;
}
