// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Link-explainability foundation: the canonical semantic axes.
 *
 * "No link" is never a standalone status. Every decision resolves to exactly
 * one canonical `Family`, and the family drives the visual treatment (the color
 * law). These axes are the single vocabulary used by every surface — globe,
 * cards, inspector, timeline, logs. The actual color VALUES live in the one
 * look-and-feel source (`styles/tokens.ts`); this module only maps families to
 * those tokens so the palette is set in one spot and changed in one spot.
 */

import { tokens } from "../styles/tokens";

/** The only family vocabulary the UI uses. Set elsewhere, this would drift. */
export type Family =
  | "connected" // OME wanted the link and the forwarding plane proves it is up.
  | "expected_no_link" // model says no; a correct no-link.
  | "eligible_unselected" // the pair could work, but policy/capacity withheld it.
  | "in_flight" // converging inside the bounded actuation window; never red.
  | "faulted" // the system failed to realize or prove what authority requires.
  | "unknown"; // missing source-of-truth data to classify; never render as clean.

export const FAMILIES: readonly Family[] = [
  "connected",
  "expected_no_link",
  "eligible_unselected",
  "in_flight",
  "faulted",
  "unknown",
];

/** Per-gate/per-reason severity. Escalates with stability (see ReasonRecord). */
export type Severity = "info" | "warning" | "alarm";

/** Temporal axis: a per-tick-explainable reason can still be a problem in motion. */
export type Stability = "stable" | "churning" | "settling" | "stale";

/** What lever changes the outcome — groups reasons by remediation, not component. */
export type RemediationLayer =
  | "geometry" // orbit/position: more planes/sats, different orbit, move GS.
  | "terminal_capability" // FoR, tracking rate, max range, terminal capacity.
  | "policy" // selection, hysteresis, handover mode, ranking.
  | "actuation"; // not a model refusal — a realization/proof state.

/** Which source-of-truth component owns a gate's verdict. */
export type Producer = "ome_visibility" | "ome_allocator" | "scheduler" | "node_agent";

/** The canonical decision funnel. A pair stops at exactly one binding gate. */
export const FUNNEL_GATES = [
  "line_of_sight",
  "range",
  "elevation_mask",
  "field_of_regard",
  "tracking_rate",
  "selection_policy",
  "capacity",
  "handover_policy",
  "actuation_proof",
] as const;
export type FunnelGate = (typeof FUNNEL_GATES)[number];

/** A gate's evaluation result. `not_applicable` matters for non-Earth/space nodes. */
export type GateState = "pass" | "fail" | "not_evaluated" | "not_applicable";

/** Which ground-link endpoint imposed a terminal-bound rejection. */
export type RejectingEndpoint = "none" | "ground" | "satellite" | "both";

function cssToHex(css: string): number {
  return parseInt(css.replace("#", ""), 16);
}

export interface FamilyTone {
  /** CSS color string for DOM components (also injected as a CSS var by tokens). */
  css: string;
  /** three.js material color for scene glyphs/beams/envelopes. */
  hex: number;
  /** Operator-facing family label (the only place it is spelled). */
  label: string;
}

/**
 * The Expected/Faulted color law, derived from the single token source.
 * Invariant (enforced by test): `faulted` is the only red; `faulted` and
 * `expected_no_link` never share a tone — a restrictive model must not look broken.
 */
export const FAMILY_TONE: Record<Family, FamilyTone> = {
  connected: { css: tokens.familyConnected, hex: cssToHex(tokens.familyConnected), label: "Connected" },
  expected_no_link: {
    css: tokens.familyExpectedNoLink,
    hex: cssToHex(tokens.familyExpectedNoLink),
    label: "Expected no-link",
  },
  eligible_unselected: {
    css: tokens.familyEligibleUnselected,
    hex: cssToHex(tokens.familyEligibleUnselected),
    label: "Eligible (not selected)",
  },
  in_flight: { css: tokens.familyInFlight, hex: cssToHex(tokens.familyInFlight), label: "In flight" },
  faulted: { css: tokens.familyFaulted, hex: cssToHex(tokens.familyFaulted), label: "Faulted" },
  unknown: { css: tokens.familyUnknown, hex: cssToHex(tokens.familyUnknown), label: "Unknown" },
};
