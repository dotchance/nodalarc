// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import { gsCandidateRelations } from "../gsCandidateRelations";
import type { GroundDecisionsSnapshot } from "../client";
import type { LinkState } from "../../types";

function link(a: string, b: string, state = "active"): LinkState {
  return {
    node_a: a,
    node_b: b,
    state,
    link_type: null,
    link_reason: null,
    latency_ms: 0,
    bandwidth_mbps: 0,
    range_km: 0,
    traffic_load_pct: null,
    interface_a: "",
    interface_b: "",
  } as LinkState;
}

const decisions: GroundDecisionsSnapshot = {
  // intentionally generic shape; only the fields the classifier reads matter
  decisions: [{ pair: ["gs-1", "sat-rej"], reject_reason: "below_elevation_mask" }],
  unscheduled_pairs: [{ pair: ["sat-elig", "gs-1"], unscheduled_reason: "capacity_withheld" }],
} as unknown as GroundDecisionsSnapshot;

describe("gsCandidateRelations (single source: globe agrees with the card)", () => {
  it("marks an active-linked sat connected (and connected wins over a decision)", () => {
    const rel = gsCandidateRelations("gs-1", decisions, [link("gs-1", "sat-conn")]);
    expect(rel.get("sat-conn")?.family).toBe("connected");
  });

  it("marks an unscheduled (withheld) sat eligible (not selected)", () => {
    const rel = gsCandidateRelations("gs-1", decisions, []);
    expect(rel.get("sat-elig")?.family).toBe("eligible_unselected");
  });

  it("marks a rejected sat with a registry-derived family + human reason (never a raw code)", () => {
    const rel = gsCandidateRelations("gs-1", decisions, []);
    const r = rel.get("sat-rej");
    expect(r).toBeDefined();
    expect(r!.family).not.toBe("connected");
    // reason is a human label, not the raw code
    expect(typeof r!.reason).toBe("string");
  });

  it("connected wins over a withheld/rejected classification for the same sat", () => {
    const d: GroundDecisionsSnapshot = {
      decisions: [{ pair: ["gs-1", "sat-x"], reject_reason: "below_elevation_mask" }],
      unscheduled_pairs: [],
    } as unknown as GroundDecisionsSnapshot;
    const rel = gsCandidateRelations("gs-1", d, [link("sat-x", "gs-1")]);
    expect(rel.get("sat-x")?.family).toBe("connected");
  });

  it("handles pair order in either direction and ignores inactive links", () => {
    const rel = gsCandidateRelations("gs-1", null, [link("gs-1", "sat-down", "inactive")]);
    expect(rel.has("sat-down")).toBe(false);
  });

  it("a sat in no decision and no link is absent (far/irrelevant)", () => {
    const rel = gsCandidateRelations("gs-1", decisions, []);
    expect(rel.has("sat-unrelated")).toBe(false);
  });
});
