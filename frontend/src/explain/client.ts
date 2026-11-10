// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** VS-API client for the link-explainability endpoints. */

import { REST_URL, authHeaders } from "../config";
import type {
  DecisionExplanationFacts,
  GroundLinkDecisionSnapshot,
  GsDecisionTimelineFacts,
} from "../generated/vsApi";
import { apiErrorMessage } from "../ui/apiError";

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
): Promise<DecisionExplanationFacts | null> {
  const params = new URLSearchParams({ gs: gsId });
  if (satId) params.set("sat", satId);
  const url = `${REST_URL}/api/v1/decision-explanation?${params.toString()}`;
  const resp = await fetch(url, { headers: authHeaders(), signal });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(await apiErrorMessage(resp));
  return (await resp.json()) as DecisionExplanationFacts;
}

/**
 * Fetch the OME ground-decision snapshot sliced to one node's candidates — the source
 * for a node card's candidate list. `node` is the selected GS or satellite id; the server
 * returns only the decisions/unscheduled pairs that node participates in, so the card
 * never polls and discards the whole GS×satellite cross-product (wrong primitive at
 * thousand-satellite scale). Null on 404 (no snapshot yet); a node with no candidates
 * this tick is a 200 with empty arrays.
 */
export async function fetchGroundDecisions(
  node: string,
  signal?: AbortSignal,
): Promise<GroundLinkDecisionSnapshot | null> {
  const url = `${REST_URL}/api/v1/ground-link-decisions?node=${encodeURIComponent(node)}`;
  const resp = await fetch(url, { headers: authHeaders(), signal });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(await apiErrorMessage(resp));
  return (await resp.json()) as GroundLinkDecisionSnapshot;
}

export async function fetchDecisionTimeline(
  gsId: string,
  limit = 120,
  signal?: AbortSignal,
): Promise<GsDecisionTimelineFacts | null> {
  const params = new URLSearchParams({ gs: gsId, limit: String(limit) });
  const url = `${REST_URL}/api/v1/decision-explanation/timeline?${params.toString()}`;
  const resp = await fetch(url, { headers: authHeaders(), signal });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(await apiErrorMessage(resp));
  return (await resp.json()) as GsDecisionTimelineFacts;
}
