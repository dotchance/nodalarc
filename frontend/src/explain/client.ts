// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Typed VS-API client for the link-explainability endpoints. */

import { REST_URL, authHeaders } from "../config";
import type { DecisionFacts } from "./types";

/**
 * Fetch composed decision-explanation facts for one ground station.
 * Returns null on 404 (no snapshot yet, or no decision covers this GS) so the
 * caller can render a neutral "no data" state rather than an error.
 */
export async function fetchDecisionExplanation(
  gsId: string,
  signal?: AbortSignal,
): Promise<DecisionFacts | null> {
  const url = `${REST_URL}/api/v1/decision-explanation?gs=${encodeURIComponent(gsId)}`;
  const resp = await fetch(url, { headers: authHeaders(), signal });
  if (resp.status === 404) return null;
  if (!resp.ok) {
    throw new Error(`decision-explanation ${resp.status}: ${await resp.text()}`);
  }
  return (await resp.json()) as DecisionFacts;
}
