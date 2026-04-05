// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Tests for the shared sim-time clock.
 *
 *  Verifies EMA semantics were preserved during the extraction from
 *  satellites.ts. Also verifies interpolation math and reset behavior.
 */

import { describe, it, expect, beforeEach } from "vitest";
import {
  onSnapshot,
  wallMsPerSimMs,
  interpolatedSimTimeMs,
  resetSimClock,
} from "../simClock";

const ISO = (unixMs: number) => new Date(unixMs).toISOString();

describe("simClock", () => {
  beforeEach(() => {
    resetSimClock();
  });

  it("returns null before any snapshot", () => {
    expect(interpolatedSimTimeMs(1000)).toBeNull();
  });

  it("seeds on first snapshot, no EMA update, no simDelta", () => {
    const simStart = 1775260800000;
    const result = onSnapshot(ISO(simStart), 100);
    expect(result).toBeNull();
    // Default rate is 1.0 until a real measurement arrives.
    expect(wallMsPerSimMs()).toBe(1.0);
  });

  it("returns simDelta on second snapshot and updates EMA on first real measurement", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    // 1 second of sim advanced in 2 seconds of wall time → rate 2.0
    const result = onSnapshot(ISO(simStart + 1000), 2100);
    expect(result).not.toBeNull();
    expect(result!.simDeltaMs).toBe(1000);
    // First real measurement seeds EMA directly to instantRate (2.0).
    expect(wallMsPerSimMs()).toBeCloseTo(2.0, 10);
  });

  it("applies EMA smoothing on subsequent measurements with alpha 0.15", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // seed EMA to 2.0
    // Second real measurement: wallDelta 1000, simDelta 1000 → instantRate 1.0
    onSnapshot(ISO(simStart + 2000), 3100);
    // Expected: 2.0 * (1 - 0.15) + 1.0 * 0.15 = 1.85
    expect(wallMsPerSimMs()).toBeCloseTo(1.85, 10);
  });

  it("rejects duplicate or regressed sim_time", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    const dup = onSnapshot(ISO(simStart), 500);
    const back = onSnapshot(ISO(simStart - 1000), 600);
    expect(dup).toBeNull();
    expect(back).toBeNull();
    expect(wallMsPerSimMs()).toBe(1.0); // never left default
  });

  it("ignores measurements with wallDelta <= 10ms (noise floor)", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    // wallDelta = 5ms, below floor
    onSnapshot(ISO(simStart + 100), 105);
    expect(wallMsPerSimMs()).toBe(1.0); // unchanged
  });

  it("clamps outlier ratios (>5x or <0.2x) from affecting EMA after seed", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // seed EMA = 2.0
    // Send an outlier: 100ms sim in 2000ms wall → instantRate 20.0
    // Ratio to current EMA (2.0) = 10.0, above 5.0 → clamp, don't update.
    onSnapshot(ISO(simStart + 1100), 4100);
    expect(wallMsPerSimMs()).toBe(2.0); // unchanged

    // Send slow outlier: 1000ms sim in 100ms wall → instantRate 0.1
    // Ratio 0.05, below 0.2 → clamp, don't update.
    onSnapshot(ISO(simStart + 2100), 4200);
    expect(wallMsPerSimMs()).toBe(2.0); // still unchanged
  });

  it("interpolates sim_time from last snapshot + rate", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // EMA = 2.0 wall-ms per sim-ms
    // 500ms wall elapsed since last snapshot at wall=2100.
    // Expected sim delta: 500ms / 2.0 = 250ms.
    // Expected: simStart + 1000 + 250 = simStart + 1250
    const interp = interpolatedSimTimeMs(2600);
    expect(interp).toBe(simStart + 1250);
  });

  it("interpolation returns the last snapshot value at exact snapshot wall time", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    // At the wall time of the only snapshot, interp should equal sim value.
    expect(interpolatedSimTimeMs(100)).toBe(simStart);
  });

  it("resetSimClock restores defaults", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100);
    expect(wallMsPerSimMs()).toBeCloseTo(2.0, 10);
    resetSimClock();
    expect(wallMsPerSimMs()).toBe(1.0);
    expect(interpolatedSimTimeMs(100)).toBeNull();
  });

  it("handles extrapolation past last snapshot (unbounded)", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // rate = 2.0
    // 10 seconds past last wall time → 5 seconds of sim extrapolated
    const interp = interpolatedSimTimeMs(12100);
    expect(interp).toBe(simStart + 1000 + 5000);
  });
});
