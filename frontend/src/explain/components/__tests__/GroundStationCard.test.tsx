// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Render contract for the GroundStationCard — families must look different. */

import { afterEach, describe, it, expect } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

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
    ground: {
      node_role: "ground",
      terminal_profile: "gs-denver.terminals",
      boresight_mode: "local_vertical",
      field_of_regard_deg: 120,
      max_tracking_rate_deg_s: 1.5,
      max_range_km: 2000,
    },
    satellite: {
      node_role: "satellite",
      terminal_profile: "sat-P00S02.ground_terminals",
      boresight_mode: "nadir",
      field_of_regard_deg: 120,
      max_tracking_rate_deg_s: 1.5,
      max_range_km: 2000,
    },
    binding_endpoint: "none",
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
  actuation: {
    state: "unknown",
    ome_desired: false,
    kernel_up: false,
    diverged: false,
    diverged_since: null,
    actuation_elapsed_ms: null,
    expected_latency_ms: 250,
    fault_after_ms: 1200,
  },
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
  actuation: {
    state: "kernel_dirty",
    ome_desired: true,
    kernel_up: false,
    diverged: true,
    diverged_since: "2026-05-29T18:08:18Z",
    actuation_elapsed_ms: 2000,
    expected_latency_ms: 250,
    fault_after_ms: 1200,
  },
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
    // The convergence deadline: 2.0 s elapsed past the 1.2 s fault threshold.
    expect(screen.getByText(/2\.0s elapsed \/ fault at 1\.2s/)).toBeTruthy();
  });
  it("renders observed diagnosis from the bounded timeline", () => {
    let inspected: string | null = null;
    render(
      <GroundStationCard
        facts={denverGap}
        timeline={{
          gs_id: "gs-denver",
          sample_count: 2,
          window_started_sim_time: "2026-05-29T18:08:20Z",
          window_ended_sim_time: "2026-05-29T18:08:25Z",
          reason_counts: [
            { state: "expected_no_link", reason_code: "elevation_below_min", count: 2 },
          ],
          samples: [
            {
              gs_id: "gs-denver",
              sim_time: "2026-05-29T18:08:20Z",
              snapshot_seq: 516,
              epoch_id: 0,
              state: "expected_no_link",
              pair: ["gs-denver", "sat-P00S02"],
              binding_gate: "elevation_mask",
              reason_code: "elevation_below_min",
              rejecting_endpoint: "none",
              range_km: 1567,
              elevation_deg: 14,
            },
            {
              gs_id: "gs-denver",
              sim_time: "2026-05-29T18:08:25Z",
              snapshot_seq: 517,
              epoch_id: 0,
              state: "expected_no_link",
              pair: ["gs-denver", "sat-P00S02"],
              binding_gate: "elevation_mask",
              reason_code: "elevation_below_min",
              rejecting_endpoint: "none",
              range_km: 1550,
              elevation_deg: 15,
            },
          ],
        }}
        timelineLimit={30}
        onTimelineLimitChange={() => {}}
        onInspectSat={(sat) => {
          inspected = sat;
        }}
      />,
    );

    expect(screen.getByText("Observed diagnosis")).toBeTruthy();
    expect(screen.getByText(/2 samples/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "30" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getAllByText("Below elevation mask").length).toBeGreaterThan(0);
    const sampleButton = screen.getAllByTitle(/sat-P00S02/)[0];
    if (!sampleButton) throw new Error("sample button not found");
    fireEvent.click(sampleButton);
    expect(inspected).toBe("sat-P00S02");
  });

});
