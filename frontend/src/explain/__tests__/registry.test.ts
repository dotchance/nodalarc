// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/**
 * The color law of the link-explainability taxonomy: faulted is the only red,
 * and a correct (expected) no-link never shares a tone with a fault. Registry
 * completeness is a type check: each registry is a Record over the generated
 * unions.
 */

import { describe, it, expect } from "vitest";
import { THEMES } from "../../styles/tokens";
import { FAMILIES, type Family } from "../families";

describe("family color law", () => {
  it("holds the color law in EVERY theme, not just the active one", () => {
    for (const [themeName, theme] of Object.entries(THEMES)) {
      const tones: Record<Family, string> = {
        connected: theme.familyConnected,
        expected_no_link: theme.familyExpectedNoLink,
        eligible_unselected: theme.familyEligibleUnselected,
        in_flight: theme.familyInFlight,
        faulted: theme.familyFaulted,
        unknown: theme.familyUnknown,
      };
      for (const fam of FAMILIES) {
        expect(tones[fam], `${themeName}: family '${fam}' tone`).toMatch(/^#[0-9a-fA-F]{6}$/);
      }
      const all = FAMILIES.map((f) => tones[f].toLowerCase());
      expect(new Set(all).size, `${themeName}: family tones must be distinct`).toBe(all.length);
      for (const fam of FAMILIES) {
        if (fam === "faulted") continue;
        expect(
          tones[fam].toLowerCase(),
          `${themeName}: family '${fam}' must not share the faulted tone`,
        ).not.toBe(tones.faulted.toLowerCase());
      }
      // beams and cards must agree on what "faulted red" is, per theme
      expect(theme.colorLinkFail, `${themeName}: colorLinkFail must equal familyFaulted`)
        .toBe(parseInt(theme.familyFaulted.replace("#", ""), 16));
    }
  });
});
