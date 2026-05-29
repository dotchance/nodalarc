// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Render contract for the GroundStationCard — families must look different. */

import { afterEach, describe, it, expect } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

afterEach(cleanup);
import { FUNNEL_GATES } from "../../families";
import type { DecisionFacts, LadderGate } from "../../types";
import { GroundStationCard } from "../GroundStationCard";

function ladder(rows: (Partial<LadderGate> & { gate: LadderGate["gate"] })[]): LadderGate[] {
  const byGate = new Map(rows.map((r) => [r.gate, r]));
  return FUNNEL_GATES.map((g) => {
    const o = byGate.get(g);
    return {
      gate: g,
      state: o?.state ?? "not_evaluated",
      actual: o?.actual ?? null,
      threshold: o?.threshold ?? null,
      rejecting_endpoint: o?.rejecting_endpoint ?? null,
      reason_code: o?.reason_code ?? null,
      producer: o?.producer ?? "ome_visibility",
      is_binding: o?.is_binding ?? false,
    };
  });
}

const denverGap: DecisionFacts = {
  gs_id: "gs-denver",
  pair: ["gs-denver", "sat-P00S02"],
  node_focus: "gs",
  reference_body: "earth",
  tenant_id: "default",
  binding_gate: "elevation_mask",
  binding_reason_code: "elevation_below_min",
  rejecting_endpoint: "none",
  ladder: ladder([
    { gate: "line_of_sight", state: "pass", actual: 14, threshold: 0 },
    { gate: "range", state: "pass", actual: 1567, threshold: 2000 },
    {
      gate: "elevation_mask",
      state: "fail",
      actual: 14,
      threshold: 25,
      reason_code: "elevation_below_min",
      is_binding: true,
    },
  ]),
  envelope: {
    reference_body: "earth",
    configured_min_elevation_deg: 25,
    effective_min_elevation_deg: 30,
    binding_source: "field_of_regard",
    dead_knobs: ["min_elevation_deg"],
    max_range_km: 2000,
    field_of_regard_deg: 120,
    boresight_mode: "local_vertical",
    tracking_rate_deg_s: 1.5,
  },
  best_candidate: {
    pair: ["gs-denver", "sat-P00S02"],
    binding_gate: "elevation_mask",
    binding_reason_code: "elevation_below_min",
    rejecting_endpoint: "none",
    range_km: 1567,
    elevation_deg: 14,
    viable_withheld: false,
  },
  actuation: { state: "unknown", ome_desired: false, kernel_up: false, diverged: false },
  sim_time: "2026-05-29T18:08:20Z",
  snapshot_seq: 516,
  epoch_id: 0,
};

const faulted: DecisionFacts = {
  ...denverGap,
  binding_gate: "actuation_proof",
  binding_reason_code: "kernel_dirty",
  rejecting_endpoint: null,
  ladder: ladder([
    { gate: "line_of_sight", state: "pass", actual: 31.6, threshold: 0 },
    { gate: "range", state: "pass", actual: 960, threshold: 2000 },
    { gate: "elevation_mask", state: "pass", actual: 31.6, threshold: 25 },
    {
      gate: "actuation_proof",
      state: "fail",
      reason_code: "kernel_dirty",
      producer: "node_agent",
      is_binding: true,
    },
  ]),
  actuation: { state: "kernel_dirty", ome_desired: true, kernel_up: false, diverged: true },
};

describe("GroundStationCard", () => {
  it("renders the Denver gap as a calm Expected no-link, not a fault", () => {
    render(<GroundStationCard facts={denverGap} />);
    expect(screen.getByText("Expected no-link")).toBeTruthy();
    expect(screen.queryByText("Faulted")).toBeNull();
    // Co-linear collapse: under vertical boresight the ladder folds elevation + FoR
    // into one FoR-limited row against the 30 deg effective floor — no contradictory
    // "elevation pass / FoR fail" ladder rows. (FoR still appears once in the envelope
    // panel; the ladder-row absence is covered precisely by displayLadder unit tests.)
    expect(screen.getByText("Elevation (FoR-limited)")).toBeTruthy();
    // The Denver insight: the configured mask is called out as non-binding.
    expect(screen.getByText(/has no effect/i)).toBeTruthy();
  });

  it("renders a dirty-kernel divergence as Faulted, not as expected", () => {
    render(<GroundStationCard facts={faulted} />);
    expect(screen.getByText("Faulted")).toBeTruthy();
    expect(screen.queryByText("Expected no-link")).toBeNull();
    expect(screen.getByText(/OME desired, kernel not up/i)).toBeTruthy();
  });
});
