// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The client-side meaning layer: family classification, margin formatting, headline. */

import { describe, it, expect } from "vitest";
import type { FunnelGate, GateState } from "../families";
import { deriveFamily, deriveSeverity, displayLadder, formatMargin, headline } from "../derive";
import type { ActuationFacts, DecisionFacts, LadderGate } from "../types";

function facts(over: Partial<DecisionFacts>): DecisionFacts {
  return {
    gs_id: "gs-denver",
    pair: ["gs-denver", "sat-P00S02"],
    node_focus: "gs",
    reference_body: "earth",
    tenant_id: "default",
    binding_gate: null,
    binding_reason_code: null,
    rejecting_endpoint: null,
    ladder: [],
    envelope: null,
    best_candidate: null,
    actuation: null,
    sim_time: "2026-05-29T18:08:20Z",
    snapshot_seq: 1,
    epoch_id: 0,
    ...over,
  };
}

const act = (over: Partial<ActuationFacts>): ActuationFacts => ({
  state: "unknown",
  ome_desired: null,
  kernel_up: null,
  diverged: null,
  ...over,
});

describe("deriveFamily", () => {
  it("connected: no binding gate, proven up", () => {
    expect(
      deriveFamily(facts({ binding_gate: null, actuation: act({ state: "clean", kernel_up: true, diverged: false }) })),
    ).toBe("connected");
  });

  it("expected_no_link: physics rejection (below mask)", () => {
    expect(
      deriveFamily(facts({ binding_gate: "elevation_mask", binding_reason_code: "elevation_below_min" })),
    ).toBe("expected_no_link");
  });

  it("eligible_unselected: visible but withheld by capacity", () => {
    expect(
      deriveFamily(facts({ binding_gate: "capacity", binding_reason_code: "gs_capacity" })),
    ).toBe("eligible_unselected");
  });

  it("faulted: actuation kernel_dirty", () => {
    expect(
      deriveFamily(facts({ binding_gate: "actuation_proof", actuation: act({ state: "kernel_dirty", ome_desired: true, kernel_up: false, diverged: true }) })),
    ).toBe("faulted");
  });

  it("in_flight: clean but diverged inside the convergence window", () => {
    expect(
      deriveFamily(facts({ binding_gate: "actuation_proof", actuation: act({ state: "clean", ome_desired: true, kernel_up: false, diverged: true }) })),
    ).toBe("in_flight");
  });

  it("unknown: actuation state not known", () => {
    expect(
      deriveFamily(facts({ binding_gate: "actuation_proof", actuation: act({ state: "unknown", ome_desired: true, kernel_up: false, diverged: true }) })),
    ).toBe("unknown");
  });
});

describe("deriveSeverity", () => {
  it("alarm for a kernel-dirty fault", () => {
    expect(
      deriveSeverity(facts({ binding_gate: "actuation_proof", actuation: act({ state: "kernel_dirty" }) })),
    ).toBe("alarm");
  });
  it("info for an expected elevation rejection", () => {
    expect(
      deriveSeverity(facts({ binding_gate: "elevation_mask", binding_reason_code: "elevation_below_min" })),
    ).toBe("info");
  });
});

describe("formatMargin", () => {
  it("elevation: actual / min threshold / signed delta in degrees", () => {
    const g: LadderGate = {
      gate: "elevation_mask",
      state: "fail",
      actual: 14,
      threshold: 25,
      rejecting_endpoint: null,
      reason_code: "elevation_below_min",
      producer: "ome_visibility",
      is_binding: true,
    };
    expect(formatMargin(g)).toBe("14 deg / min 25 deg (-11 deg)");
  });

  it("range: max comparison in km", () => {
    const g: LadderGate = {
      gate: "range",
      state: "fail",
      actual: 2407,
      threshold: 2000,
      rejecting_endpoint: "both",
      reason_code: "range_exceeded",
      producer: "ome_visibility",
      is_binding: true,
    };
    expect(formatMargin(g)).toBe("2407 deg / max 2000 deg (+407 deg)".replace(/deg/g, "km"));
  });

  it("returns null when the gate carries no numbers", () => {
    const g: LadderGate = {
      gate: "handover_policy",
      state: "fail",
      actual: null,
      threshold: null,
      rejecting_endpoint: null,
      reason_code: "gs_capacity",
      producer: "ome_allocator",
      is_binding: true,
    };
    expect(formatMargin(g)).toBeNull();
  });
});

describe("headline", () => {
  it("connected names the satellite", () => {
    const h = headline(facts({ binding_gate: null, pair: ["gs-denver", "sat-P00S03"], actuation: act({ state: "clean", kernel_up: true }) }));
    expect(h).toContain("sat-P00S03");
    expect(h.toLowerCase()).toContain("connected");
  });
});

describe("displayLadder co-linearity collapse", () => {
  const env = (over: Partial<NonNullable<DecisionFacts["envelope"]>> = {}) => ({
    reference_body: "earth",
    configured_min_elevation_deg: 25,
    effective_min_elevation_deg: 30,
    binding_source: "field_of_regard",
    dead_knobs: ["min_elevation_deg"],
    max_range_km: 2000,
    field_of_regard_deg: 120,
    boresight_mode: "local_vertical",
    tracking_rate_deg_s: 1.5,
    ...over,
  });

  const lrow = (
    gate: FunnelGate,
    state: GateState,
    actual: number | null,
    is_binding = false,
    reason: string | null = null,
  ): LadderGate => ({
    gate,
    state,
    actual,
    threshold: gate === "elevation_mask" ? 25 : gate === "field_of_regard" ? 60 : null,
    rejecting_endpoint: null,
    reason_code: reason,
    producer: "ome_visibility",
    is_binding,
  });

  it("folds elevation+FoR into one FoR-limited row at 27 deg — no pass/fail contradiction", () => {
    const rows = displayLadder(
      facts({
        envelope: env(),
        ladder: [
          lrow("line_of_sight", "pass", 27),
          lrow("elevation_mask", "pass", 27),
          lrow("field_of_regard", "fail", 63, true, "field_of_regard"),
        ],
      }),
    );
    expect(rows.some((r) => r.row.gate === "field_of_regard")).toBe(false);
    const elev = rows.find((r) => r.row.gate === "elevation_mask")!;
    expect(elev.label).toBe("Elevation (FoR-limited)");
    expect(elev.row.state).toBe("fail");
    expect(elev.row.threshold).toBe(30);
    expect(elev.row.is_binding).toBe(true);
  });

  it("passes the collapsed row when above the effective floor", () => {
    const rows = displayLadder(
      facts({
        envelope: env(),
        ladder: [lrow("elevation_mask", "pass", 31.6), lrow("field_of_regard", "pass", 58.4)],
      }),
    );
    const elev = rows.find((r) => r.row.gate === "elevation_mask")!;
    expect(elev.row.state).toBe("pass");
    expect(rows.some((r) => r.row.gate === "field_of_regard")).toBe(false);
  });

  it("does NOT collapse under a non-vertical boresight — gates genuinely decouple", () => {
    const rows = displayLadder(
      facts({
        envelope: env({ boresight_mode: "steered" }),
        ladder: [
          lrow("elevation_mask", "pass", 27),
          lrow("field_of_regard", "fail", 63, true, "field_of_regard"),
        ],
      }),
    );
    expect(rows.some((r) => r.row.gate === "elevation_mask")).toBe(true);
    expect(rows.some((r) => r.row.gate === "field_of_regard")).toBe(true);
  });
});
