// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Candidate preview — the client renders the server's frozen-epoch verdicts.
 *
 *  There is no client geometry left to pin (it moved server-side; the numeric
 *  parity fixtures live in tests/unit/test_builder_world.py). What remains is
 *  the ADAPTER: BuilderRulePreview facts -> canvas candidate lines + editor rule
 *  notes. These pins cover the decided note-mapping table row by row, the
 *  drawable-pairs passthrough, and the "enabled means computed" mapping the
 *  status bar's dark-rule count relies on.
 */

import { describe, expect, it } from "vitest";
import { computeCandidates } from "../candidates";
import type {
  BuilderLinkRule,
  BuilderRuleAllocation,
  BuilderRulePreview,
  BuilderWorld,
} from "../builderTypes";

function preview(over: Partial<BuilderRulePreview>): BuilderRulePreview {
  return {
    rule_id: "r",
    kind: "access",
    preview_scope: "computed",
    pairs_total: 0,
    pairs_tested: 0,
    pairs_drawn: 0,
    capped: false,
    reason_counts: [],
    drawable_pairs: [],
    ...over,
  };
}

function rule(rule_id: string, topology_mode: string): BuilderLinkRule {
  return {
    rule_id,
    kind: "access",
    enabled: true,
    endpoints: [
      { segment_id: "g", terminal_role: "access", terminal_medium: "rf", min_elevation_deg: null, node_ids: ["g1"] },
      { segment_id: "s", terminal_role: "access", terminal_medium: "rf", min_elevation_deg: null, node_ids: ["s1"] },
    ],
    topology_mode,
    topology_n: null,
    explicit_pairs: [],
    max_range_km: null,
  };
}

function alloc(rule_id: string, allocated_pairs: number): BuilderRuleAllocation {
  return { rule_id, kind: "access", allocated_pairs, per_node: [] };
}

function world(
  previews: BuilderRulePreview[],
  rules: BuilderLinkRule[] = [],
  allocations: BuilderRuleAllocation[] = [],
): BuilderWorld {
  return {
    session: { name: "t", display_name: null, description: null },
    epoch_unix: 0,
    ephemeris: { epoch_id: 0, sim_time: "", epoch_unix: 0, nodes: {}, body_frames: {} },
    nodes: [],
    link_rules: rules,
    segments: [],
    allocations,
    link_candidates: [],
    rule_previews: previews,
  };
}

const only = (w: BuilderWorld) => computeCandidates(w).previews[0]!;

describe("adapter: drawable pairs and the dark-rule mapping", () => {
  it("passes the server's drawn pairs to the canvas verbatim, oriented as sent", () => {
    const { pairs, previews } = computeCandidates(
      world([
        preview({
          rule_id: "r",
          pairs_total: 2,
          pairs_tested: 2,
          pairs_drawn: 1,
          drawable_pairs: [{ rule_id: "r", kind: "access", node_a: "g1", node_b: "s1" }],
          reason_counts: [{ reason: "los_blocked", count: 1 }],
        }),
      ]),
    );
    expect(pairs).toEqual([{ rule_id: "r", kind: "access", a: "g1", b: "s1" }]);
    expect(previews[0]!.candidates).toBe(1); // == pairs_drawn
  });

  it("only a computed rule reads as enabled/dark; a pending or disabled rule never does", () => {
    // The status bar counts dark = enabled && candidates===0; that must be
    // exactly "computed with nothing drawn", never a pending or off rule.
    expect(only(world([preview({ preview_scope: "computed", pairs_drawn: 0 })])).enabled).toBe(true);
    expect(only(world([preview({ preview_scope: "terrestrial_pending" })])).enabled).toBe(false);
    expect(only(world([preview({ preview_scope: "disabled" })])).enabled).toBe(false);
  });
});

describe("note-mapping table — one row at a time", () => {
  it("(a) a non-computed scope renders the typed wall, superseding everything", () => {
    expect(only(world([preview({ preview_scope: "inter_body_pending" })])).note).toBe(
      "inter-body span — preview pending, runtime computes contacts",
    );
    expect(only(world([preview({ preview_scope: "terrestrial_pending" })])).note).toBe(
      "terrestrial run — surface routing preview pending",
    );
    const disabled = computeCandidates(world([preview({ preview_scope: "disabled" })]));
    expect(disabled.previews[0]!.note).toBe("rule disabled");
    expect(disabled.previews[0]!.candidates).toBe(0);
    expect(disabled.pairs).toEqual([]); // ZERO lines, no counts, no pairs
  });

  it("(b) a fixed rule the allocator granted nothing says so — never 'geometry forbids'", () => {
    const note = only(
      world([preview({ rule_id: "x", preview_scope: "computed" })], [rule("x", "explicit_pairs")], [alloc("x", 0)]),
    ).note;
    expect(note).toBe("the allocator granted no pairs for this rule");
    expect(note).not.toMatch(/geometry currently forbids/);
  });

  it("(c) reason counts render per reason; when capped every reason carries the denominator", () => {
    const uncapped = only(
      world([
        preview({
          pairs_total: 100,
          pairs_tested: 100,
          pairs_drawn: 63,
          reason_counts: [
            { reason: "elevation_below_min", count: 30 },
            { reason: "los_blocked", count: 7 },
          ],
        }),
      ]),
    ).note;
    expect(uncapped).toBe("30 pairs below the elevation mask; 7 pairs with no line of sight");

    const capped = only(
      world([
        preview({
          pairs_total: 1000,
          pairs_tested: 100,
          pairs_drawn: 60,
          capped: true,
          reason_counts: [{ reason: "range_exceeded", count: 40 }],
        }),
      ]),
    ).note;
    // Each reason carries the tested/total denominator AND the (e) summary rides along.
    expect(capped).toContain("40 pairs beyond terminal range among 100 tested of 1000 possible");
    expect(capped).toContain("showing 60 drawn from 100 tested of 1000 possible");
  });

  it("(d) a computed rule that drew nothing and rejected nothing shows the geometry-forbids note", () => {
    expect(only(world([preview({ preview_scope: "computed", pairs_drawn: 0 })])).note).toBe(
      "rule permits, geometry currently forbids — runtime computes contacts over time",
    );
  });

  it("(e) a capped preview with everything drawn still reports the truncation summary", () => {
    const note = only(
      world([
        preview({ pairs_total: 5000, pairs_tested: 800, pairs_drawn: 800, capped: true }),
      ]),
    ).note;
    expect(note).toBe("showing 800 drawn from 800 tested of 5000 possible");
  });

  it("terminal_type_mismatch renders human copy over the unchanged machine token", () => {
    expect(
      only(
        world([
          preview({
            pairs_total: 3,
            pairs_tested: 3,
            pairs_drawn: 1,
            reason_counts: [{ reason: "terminal_type_mismatch", count: 2 }],
          }),
        ]),
      ).note,
    ).toBe("2 pairs with incompatible terminal types");
  });

  it("a complete computed rule with everything drawn carries no note", () => {
    expect(
      only(world([preview({ pairs_total: 4, pairs_tested: 4, pairs_drawn: 4 })])).note,
    ).toBeNull();
  });
});
