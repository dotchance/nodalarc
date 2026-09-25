// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, expect, it } from "vitest";
import { FAIL_FADE_MS, FAIL_HOLD_MS } from "../../config";
import { TraceFades, stoppedTraceOpacity } from "../traceFades";
import type { TracedPath } from "../../types";

const GONE = FAIL_HOLD_MS + FAIL_FADE_MS;

function trace(flowId: string, tracing: boolean): TracedPath {
  return {
    flow_id: flowId,
    src_node: "gs-a",
    dst_node: "gs-b",
    hops: ["gs-a", "sat-1", "gs-b"],
    hop_rtts: [null, null, null],
    state: "reached",
    rtt_ms: 10,
    error: null,
    reverse_hops: [],
    reverse_hop_rtts: [],
    reverse_state: "not_reached",
    reverse_rtt_ms: null,
    reverse_error: null,
    asymmetry_detected: null,
    tracing,
    traced_at: "2026-09-24T00:00:00Z",
    sim_time: "2026-09-24T00:00:00Z",
    stop_reason: tracing ? null : "time_limit",
  };
}

function drawn(fades: TraceFades, now: number) {
  return fades.drawn(now).map(({ path, opacity, animate }) => [path.flow_id, opacity, animate]);
}

describe("stoppedTraceOpacity", () => {
  it("holds, fades like a failed link, then is gone", () => {
    expect(stoppedTraceOpacity(0)).toBe(1);
    expect(stoppedTraceOpacity(FAIL_HOLD_MS - 1)).toBe(1);
    expect(stoppedTraceOpacity(FAIL_HOLD_MS + FAIL_FADE_MS / 2)).toBeCloseTo(0.5);
    expect(stoppedTraceOpacity(GONE)).toBeNull();
  });
});

describe("TraceFades", () => {
  it("draws a live trace at full opacity with its dash flowing", () => {
    const fades = new TraceFades();
    fades.observe([trace("t", true)], 0);
    expect(drawn(fades, 100)).toEqual([["t", 1, true]]);
  });

  it("fades a trace from the moment it stops, and keeps it gone while the snapshot keeps it", () => {
    const fades = new TraceFades();
    fades.observe([trace("t", true)], 0);
    fades.observe([trace("t", false)], 1000);
    expect(drawn(fades, 1000)).toEqual([["t", 1, false]]);
    expect(drawn(fades, 1000 + FAIL_HOLD_MS + FAIL_FADE_MS / 2)[0]![1]).toBeCloseTo(0.5);
    // Later snapshots still carry the stopped result; it does not come back.
    fades.observe([trace("t", false)], 1000 + GONE);
    fades.observe([trace("t", false)], 1000 + GONE + 5000);
    expect(drawn(fades, 1000 + GONE + 5000)).toEqual([]);
  });

  it("fades a live trace that left the snapshot, then forgets it", () => {
    const fades = new TraceFades();
    fades.observe([trace("t", true)], 0);
    fades.observe([], 1000);
    expect(drawn(fades, 1000)).toEqual([["t", 1, false]]);
    fades.observe([], 1000 + GONE);
    expect(drawn(fades, 1000 + GONE)).toEqual([]);
    // Forgotten: a stopped result that reappears is not drawn.
    fades.observe([trace("t", false)], 1000 + GONE + 1);
    expect(drawn(fades, 1000 + GONE + 1)).toEqual([]);
  });

  it("does not draw a trace first seen already stopped", () => {
    const fades = new TraceFades();
    fades.observe([trace("t", false)], 0);
    expect(drawn(fades, 0)).toEqual([]);
  });

  it("draws a restarted trace live again", () => {
    const fades = new TraceFades();
    fades.observe([trace("t", true)], 0);
    fades.observe([trace("t", false)], 1000);
    fades.observe([trace("t", false)], 1000 + GONE);
    fades.observe([trace("t", true)], 1000 + GONE + 10);
    expect(drawn(fades, 1000 + GONE + 10)).toEqual([["t", 1, true]]);
  });

  it("keeps traces in the order they first appeared", () => {
    const fades = new TraceFades();
    fades.observe([trace("first", true)], 0);
    fades.observe([trace("second", true), trace("first", true)], 10);
    expect(drawn(fades, 10).map(([flowId]) => flowId)).toEqual(["first", "second"]);
  });
});
