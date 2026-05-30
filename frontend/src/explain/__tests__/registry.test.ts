// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Foundation guards for the link-explainability taxonomy:
 *  - every backend reason code maps to exactly one registry record (no drift);
 *  - every record is complete and well-typed;
 *  - the color law holds: faulted is the only red, and a correct (expected)
 *    no-link never shares a tone with a fault.
 */

import { describe, it, expect } from "vitest";
import {
  FAMILIES,
  FAMILY_TONE,
  FUNNEL_GATES,
  type Family,
  type Producer,
  type RemediationLayer,
  type Severity,
} from "../families";
import {
  ACTUATION_EXPLANATION_REASONS,
  ACTUATION_FAILURE_CLASSES,
  ACTUATION_STATES,
  GROUND_ALLOCATION_EVENT_CATEGORIES,
  GROUND_UNSCHEDULED_REASONS,
  GROUND_VISIBILITY_REJECT_REASONS,
  REASON_REGISTRY,
} from "../reasons";

const ALL_BACKEND_CODES: readonly string[] = [
  ...GROUND_VISIBILITY_REJECT_REASONS,
  ...GROUND_UNSCHEDULED_REASONS,
  ...GROUND_ALLOCATION_EVENT_CATEGORIES,
  ...ACTUATION_STATES,
  ...ACTUATION_FAILURE_CLASSES,
  ...ACTUATION_EXPLANATION_REASONS,
];

const VALID_FAMILIES = new Set<Family>(FAMILIES);
const VALID_SEVERITIES = new Set<Severity>(["info", "warning", "alarm"]);
const VALID_LAYERS = new Set<RemediationLayer>([
  "geometry",
  "terminal_capability",
  "policy",
  "actuation",
]);
const VALID_PRODUCERS = new Set<Producer>([
  "ome_visibility",
  "ome_allocator",
  "scheduler",
  "node_agent",
]);
const VALID_GATES = new Set<string>(FUNNEL_GATES);

describe("reason taxonomy registry — completeness", () => {
  it("maps every emitted backend reason code to a registry record", () => {
    const missing = ALL_BACKEND_CODES.filter((code) => !(code in REASON_REGISTRY));
    expect(missing, `unmapped reason codes: ${missing.join(", ")}`).toEqual([]);
  });

  it("has no orphan records that no backend code emits", () => {
    const known = new Set(ALL_BACKEND_CODES);
    const orphans = Object.keys(REASON_REGISTRY).filter((code) => !known.has(code));
    expect(orphans, `orphan registry records: ${orphans.join(", ")}`).toEqual([]);
  });
});

describe("reason taxonomy registry — record shape", () => {
  for (const [code, r] of Object.entries(REASON_REGISTRY)) {
    it(`'${code}' is complete and well-typed`, () => {
      expect(r.code).toBe(code);
      expect(r.label.length).toBeGreaterThan(0);
      expect(r.sentence.length).toBeGreaterThan(0);
      expect(r.domains.length).toBeGreaterThan(0);
      expect(VALID_FAMILIES.has(r.family)).toBe(true);
      expect(VALID_SEVERITIES.has(r.severity)).toBe(true);
      expect(VALID_LAYERS.has(r.layer)).toBe(true);
      expect(VALID_PRODUCERS.has(r.producer)).toBe(true);
      // gate is one of the canonical funnel gates, or null only for pass markers.
      if (r.gate === null) {
        expect(["ok", "none"]).toContain(code);
      } else {
        expect(VALID_GATES.has(r.gate)).toBe(true);
      }
      if (r.escalateWhenChurning !== undefined) {
        expect(VALID_SEVERITIES.has(r.escalateWhenChurning)).toBe(true);
      }
    });
  }
});

describe("family color law", () => {
  it("defines a tone for every family", () => {
    for (const fam of FAMILIES) {
      expect(FAMILY_TONE[fam]).toBeDefined();
      expect(FAMILY_TONE[fam].css).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });

  it("derives the three.js hex from the css string consistently", () => {
    for (const fam of FAMILIES) {
      const { css, hex } = FAMILY_TONE[fam];
      expect(hex).toBe(parseInt(css.replace("#", ""), 16));
    }
  });

  it("reserves red for faulted only — a correct no-link must not look broken", () => {
    const faulted = FAMILY_TONE.faulted.hex;
    expect(faulted).toBe(0xff3333);
    for (const fam of FAMILIES) {
      if (fam === "faulted") continue;
      expect(
        FAMILY_TONE[fam].hex,
        `family '${fam}' must not share the faulted tone`,
      ).not.toBe(faulted);
    }
  });

  it("keeps expected_no_link, in_flight, and connected visually distinct from each other", () => {
    const calm = [
      FAMILY_TONE.connected.hex,
      FAMILY_TONE.expected_no_link.hex,
      FAMILY_TONE.eligible_unselected.hex,
      FAMILY_TONE.in_flight.hex,
    ];
    expect(new Set(calm).size).toBe(calm.length);
  });
});

describe("registry families vs funnel", () => {
  it("only uses families and gates from the canonical vocabularies", () => {
    for (const r of Object.values(REASON_REGISTRY)) {
      expect(VALID_FAMILIES.has(r.family)).toBe(true);
      if (r.gate !== null) expect(VALID_GATES.has(r.gate)).toBe(true);
    }
  });
});
