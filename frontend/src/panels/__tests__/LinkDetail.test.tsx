// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LinkDetail } from "../LinkDetail";
import type { LinkState, StateSnapshot } from "../../types";

function link(overrides: Partial<LinkState> = {}): LinkState {
  return {
    node_a: "leo-sat-p00s00",
    node_b: "meo-sat-p00s00",
    state: "active",
    link_type: "isl",
    link_reason: "link_state_snapshot",
    latency_ms: 42,
    bandwidth_mbps: 1000,
    range_km: 12000,
    traffic_load_pct: null,
    interface_a: "isl0",
    interface_b: "isl1",
    link_rule_id: "leo-to-meo-relay-candidates",
    topology_mode: "nearest_n",
    endpoint_segments: ["leo", "meo"],
    ...overrides,
  };
}

function snapshot(): StateSnapshot {
  return {
    sim_time: "2026-01-01T00:00:00Z",
    wall_time: "2026-01-01T00:00:00Z",
    schema_version: 1,
    session_id: "test",
    nodes: [],
    links: [],
    kernel_actual_pairs: [],
    traced_paths: [],
    active_flows: [],
    recent_events: [],
    network_health: {
      status: "converged",
      converging_since_ms: null,
      unreachable_flows: 0,
      last_convergence_ms: null,
    },
    routing_stack: "isis-plain",
    constellation_name: "test",
    session_status: "ready",
    session_status_detail: null,
    playback_paused: false,
    playback_speed: 1,
    stale: false,
  };
}

describe("LinkDetail", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => [] }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("shows the declared rule, topology, and endpoint segments for a selected link", () => {
    render(<LinkDetail link={link()} snapshot={snapshot()} />);

    expect(screen.getByText("Rule")).toBeTruthy();
    expect(screen.getByText("leo-to-meo-relay-candidates")).toBeTruthy();
    expect(screen.getByText("Topology")).toBeTruthy();
    expect(screen.getByText("nearest_n")).toBeTruthy();
    expect(screen.getByText("Segments")).toBeTruthy();
    expect(screen.getByText("leo ↔ meo")).toBeTruthy();
  });
});
