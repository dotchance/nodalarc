// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * TypeScript mirror of the backend DecisionExplanationFacts wire shape
 * (lib/nodalarc/models/decision_explanation.py), in snake_case to match the
 * JSON exactly — consistent with the rest of this codebase's API types
 * (`node_id`, `node_a`, ...). No mapping layer, nothing to drift.
 *
 * The backend carries FACTS only. Family/severity/human text are added on the
 * client from the reason registry (see derive.ts) — the meaning lives in one
 * place, the TS registry.
 */

import type { FunnelGate, GateState, Producer, RejectingEndpoint } from "./families";
import type { ActuationState } from "./reasons";

export type NodeFocus = "gs" | "sat" | "pair";

export interface LadderGate {
  gate: FunnelGate;
  state: GateState;
  actual: number | null;
  threshold: number | null;
  rejecting_endpoint: RejectingEndpoint | null;
  reason_code: string | null;
  producer: Producer;
  is_binding: boolean;
}

export interface EffectiveEnvelopeFacts {
  reference_body: string;
  configured_min_elevation_deg: number | null;
  effective_min_elevation_deg: number | null;
  binding_source: string | null;
  dead_knobs: string[];
  max_range_km: number | null;
  field_of_regard_deg: number | null;
  boresight_mode: string | null;
  tracking_rate_deg_s: number | null;
}

export interface CandidateFacts {
  pair: [string, string];
  binding_gate: FunnelGate | null;
  binding_reason_code: string | null;
  rejecting_endpoint: RejectingEndpoint | null;
  range_km: number | null;
  elevation_deg: number | null;
  viable_withheld: boolean;
}

export interface ActuationFacts {
  state: ActuationState;
  ome_desired: boolean | null;
  kernel_up: boolean | null;
  diverged: boolean | null;
  /** Wall-clock UTC instant the divergence was first observed; null if not diverged. */
  diverged_since: string | null;
  /** Server-computed age (ms) of the divergence at compose time; null if not diverged. */
  actuation_elapsed_ms: number | null;
  /** simulation.actuation contract (ms): the in_flight target. */
  expected_latency_ms: number | null;
  /** simulation.actuation contract (ms): escalate in_flight -> faulted at/after this age. */
  fault_after_ms: number | null;
}

export interface DecisionFacts {
  gs_id: string;
  pair: [string, string] | null;
  node_focus: NodeFocus;
  reference_body: string;
  tenant_id: string;
  binding_gate: FunnelGate | null;
  binding_reason_code: string | null;
  rejecting_endpoint: RejectingEndpoint | null;
  ladder: LadderGate[];
  envelope: EffectiveEnvelopeFacts | null;
  best_candidate: CandidateFacts | null;
  actuation: ActuationFacts | null;
  sim_time: string;
  snapshot_seq: number;
  epoch_id: number;
}
