// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The client-side meaning layer: family classification, margin formatting, headline. */

import { describe, it, expect } from "vitest";
import type { FunnelGate, GateState } from "../families";
import {
  candidateStatus,
  deriveFamily,
  deriveSeverity,
  displayLadder,
  formatMargin,
  headline,
} from "../derive";
import type { ActuationFacts, DecisionFacts, EnvelopeEndpoint, LadderGate } from "../types";

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
  diverged_since: null,
  actuation_elapsed_ms: null,
  expected_latency_ms: 250,
  fault_after_ms: 1200,
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
      deriveFamily(facts({ binding_gate: "actuation_proof", actuation: act({ state: "clean", ome_desired: true, kernel_up: false, diverged: true, actuation_elapsed_ms: 400, fault_after_ms: 1200 }) })),
    ).toBe("in_flight");
  });

  it("faulted: clean divergence past the wall-clock bound escalates to red", () => {
    expect(
      deriveFamily(facts({ binding_gate: "actuation_proof", actuation: act({ state: "clean", ome_desired: true, kernel_up: false, diverged: true, actuation_elapsed_ms: 1300, fault_after_ms: 1200 }) })),
    ).toBe("faulted");
  });

  it("in_flight: diverged but age not yet known stays calm, never red", () => {
    expect(
      deriveFamily(facts({ binding_gate: "actuation_proof", actuation: act({ state: "clean", ome_desired: true, kernel_up: false, diverged: true, actuation_elapsed_ms: null }) })),
    ).toBe("in_flight");
  });

  it("unknown: actuation state not known", () => {
    expect(
      deriveFamily(facts({ binding_gate: "actuation_proof", actuation: act({ state: "unknown", ome_desired: true, kernel_up: false, diverged: true }) })),
    ).toBe("unknown");
  });

  it("unknown: kernel-up but roster health unconfirmed is not silently connected", () => {
    expect(
      deriveFamily(facts({ binding_gate: null, actuation: act({ state: "unknown", kernel_up: true, diverged: false }) })),
    ).toBe("unknown");
  });

  it("connected: kernel-up with a clean roster still reads connected", () => {
    expect(
      deriveFamily(facts({ binding_gate: null, actuation: act({ state: "clean", kernel_up: true, diverged: false }) })),
    ).toBe("connected");
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
  it("alarm once a divergence escalates past the bound", () => {
    expect(
      deriveSeverity(facts({ binding_gate: "actuation_proof", actuation: act({ state: "clean", ome_desired: true, kernel_up: false, diverged: true, actuation_elapsed_ms: 1300, fault_after_ms: 1200 }) })),
    ).toBe("alarm");
  });
});

describe("candidateStatus", () => {
  it("rejected (not visible) takes the reject reason's registry family", () => {
    expect(
      candidateStatus({
        visible: false,
        isWithheld: false,
        rejectReason: "elevation_below_min",
        unscheduledReason: null,
      }),
    ).toEqual({ family: "expected_no_link", label: "elevation_below_min" });
  });
  it("withheld (visible) takes the unscheduled reason's registry family", () => {
    expect(
      candidateStatus({
        visible: true,
        isWithheld: true,
        rejectReason: "ok",
        unscheduledReason: "gs_capacity",
      }),
    ).toEqual({ family: "eligible_unselected", label: "gs_capacity" });
  });
  it("scheduled (visible, not withheld) reads neutral — actuation is not in the raw decision", () => {
    expect(
      candidateStatus({
        visible: true,
        isWithheld: false,
        rejectReason: "ok",
        unscheduledReason: null,
      }),
    ).toEqual({ family: "unknown", label: "scheduled" });
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
  const ep = (
    role: "ground" | "satellite",
    over: Partial<EnvelopeEndpoint> = {},
  ): EnvelopeEndpoint => ({
    node_role: role,
    terminal_profile: role === "ground" ? "gs.terminals" : "sat.terminals",
    boresight_mode: role === "ground" ? "local_vertical" : "nadir",
    field_of_regard_deg: 120,
    max_tracking_rate_deg_s: 1.5,
    max_range_km: 2000,
    ...over,
  });

  const env = (over: Partial<NonNullable<DecisionFacts["envelope"]>> = {}) => ({
    reference_body: "earth",
    configured_min_elevation_deg: 25,
    effective_min_elevation_deg: 30,
    binding_source: "field_of_regard",
    dead_knobs: ["min_elevation_deg"],
    max_range_km: 2000,
    ground: ep("ground"),
    satellite: ep("satellite"),
    binding_endpoint: "none" as const,
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
        envelope: env({ ground: ep("ground", { boresight_mode: "steered" }) }),
        ladder: [
          lrow("elevation_mask", "pass", 27),
          lrow("field_of_regard", "fail", 63, true, "field_of_regard"),
        ],
      }),
    );
    expect(rows.some((r) => r.row.gate === "elevation_mask")).toBe(true);
    expect(rows.some((r) => r.row.gate === "field_of_regard")).toBe(true);
  });

  it("does NOT collapse when the SATELLITE terminal's FoR binds — keeps the sat fail visible", () => {
    const rows = displayLadder(
      facts({
        envelope: env({ binding_endpoint: "satellite" }),
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
