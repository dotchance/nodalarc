// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Link-lifecycle event registry — the single source for the human label + canonical {@link Family}
 * of a LinkUp/LinkDown event reason (LinkState.link_reason + the link event history). This is a
 * DIFFERENT concern from the ground-DECISION funnel (reasons.ts / REASON_REGISTRY, which is
 * organised by decision gate): a link event explains why a link came up or went down, not why a
 * GS<->sat pair was scheduled. But it shares the SAME 6-family vocabulary (the only family law),
 * so the panels render link reasons through this registry — never the old ad-hoc translate.ts map.
 *
 * Codes mirror lib/nodalarc/models/link_events.py (LinkUp/LinkDown `reason`); the cross-language
 * contract test (tests/unit/test_explain_contract.py) asserts LINK_EVENT_REASONS matches the
 * backend LINK_EVENT_REASONS constant so the two cannot drift.
 */
import type { Family } from "./families";

export interface LinkEventReasonRecord {
  code: string;
  /** Canonical family this link state implies (a down due to geometry is calm, not a fault). */
  family: Family;
  /** Short operator-facing label (the only place it is spelled). */
  label: string;
  /** One-sentence human explanation. */
  sentence: string;
}

function rec(r: LinkEventReasonRecord): LinkEventReasonRecord {
  return r;
}

export const LINK_EVENT_REGISTRY: Record<string, LinkEventReasonRecord> = {
  // --- Link up ---
  vis_gained: rec({
    code: "vis_gained",
    family: "connected",
    label: "In range",
    sentence: "Came into range — link up.",
  }),
  gs_above_horizon: rec({
    code: "gs_above_horizon",
    family: "connected",
    label: "In view",
    sentence: "The satellite rose above the horizon — link up.",
  }),
  scenario_inject_up: rec({
    code: "scenario_inject_up",
    family: "connected",
    label: "Injected up",
    sentence: "A scenario injected this link up.",
  }),
  scenario_reconciliation: rec({
    code: "scenario_reconciliation",
    family: "unknown",
    label: "Reconciled",
    sentence: "A scenario ended — link state reconciled.",
  }),
  // --- Link down (calm / geometric) ---
  vis_lost: rec({
    code: "vis_lost",
    family: "expected_no_link",
    label: "Out of range",
    sentence: "Went out of range — link down.",
  }),
  gs_below_horizon: rec({
    code: "gs_below_horizon",
    family: "expected_no_link",
    label: "Below horizon",
    sentence: "The satellite set below the horizon — link down.",
  }),
  tracking_exceeded: rec({
    code: "tracking_exceeded",
    family: "expected_no_link",
    label: "Too fast to track",
    sentence: "Relative motion exceeded the terminal's tracking rate — link down.",
  }),
  terminal_exhausted: rec({
    code: "terminal_exhausted",
    family: "eligible_unselected",
    label: "No free terminal",
    sentence: "No free terminal remained — the link could not be sustained.",
  }),
  // --- Link down (scenario / loss) ---
  scenario_inject_down: rec({
    code: "scenario_inject_down",
    family: "expected_no_link",
    label: "Injected failure",
    sentence: "A scenario injected this link down.",
  }),
  satellite_loss: rec({
    code: "satellite_loss",
    family: "expected_no_link",
    label: "Satellite lost",
    sentence: "The satellite was lost — link down.",
  }),
};

/** The authoritative link-event reason codes (mirrors backend link_events.LINK_EVENT_REASONS). */
export const LINK_EVENT_REASONS: readonly string[] = Object.keys(LINK_EVENT_REGISTRY);

/** Human label for a link-event reason code, from the single registry. Never invents text;
 *  falls back to the raw code (honest) rather than a fabricated phrase. */
export function linkEventLabel(code: string | null | undefined): string {
  if (!code) return "";
  return LINK_EVENT_REGISTRY[code]?.label ?? code;
}
