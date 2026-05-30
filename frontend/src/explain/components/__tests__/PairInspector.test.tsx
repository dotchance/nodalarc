// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Render contract for the Per-Pair Inspector. */

import { afterEach, describe, it, expect } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

afterEach(cleanup);
import type { DecisionFacts } from "../../types";
import { PairInspector } from "../PairInspector";

const connected: DecisionFacts = {
  gs_id: "gs-denver",
  pair: ["gs-denver", "sat-P00S03"],
  node_focus: "pair",
  reference_body: "earth",
  tenant_id: "default",
  binding_gate: null,
  binding_reason_code: null,
  rejecting_endpoint: null,
  ladder: [],
  envelope: null,
  best_candidate: null,
  actuation: {
    state: "clean",
    ome_desired: true,
    kernel_up: true,
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

describe("PairInspector", () => {
  it("renders the pair, the raw realization truths, and snapshot provenance", () => {
    render(
      <PairInspector gsId="gs-denver" satId="sat-P00S03" facts={connected} onBack={() => {}} />,
    );
    expect(screen.getByText(/gs-denver.*sat-P00S03/)).toBeTruthy();
    expect(screen.getByText("OME desired")).toBeTruthy();
    expect(screen.getByText("Kernel actual")).toBeTruthy();
    expect(screen.getByText(/seq 516/)).toBeTruthy();
  });

  it("shows an empty state when no decision covers the pair", () => {
    render(<PairInspector gsId="gs-denver" satId="sat-x" facts={null} onBack={() => {}} />);
    expect(screen.getByText(/No decision for this pair/i)).toBeTruthy();
  });

  it("calls onBack when Back is clicked", () => {
    let backed = false;
    render(
      <PairInspector
        gsId="gs-denver"
        satId="sat-x"
        facts={null}
        onBack={() => {
          backed = true;
        }}
      />,
    );
    fireEvent.click(screen.getByText(/Back/));
    expect(backed).toBe(true);
  });
});
