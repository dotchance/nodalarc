// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
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
    // Ratio 0.05, below 0.2 → clamp on first occurrence.
    onSnapshot(ISO(simStart + 2100), 4200);
    expect(wallMsPerSimMs()).toBe(2.0); // still unchanged after 1 outlier
  });

  it("re-seeds EMA after 3 consecutive outliers (persistent rate change)", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // seed EMA = 2.0
    // Simulate speed change from 1x to 30x: each snapshot now arrives
    // much faster (wallDelta=33ms for simDelta=1000ms → rate=0.033).
    // This ratio (0.033/2.0 = 0.017) is below the 0.2 clamp threshold.
    // After 3 consecutive outliers, the EMA should re-seed.
    onSnapshot(ISO(simStart + 2000), 2133); // outlier 1 → still 2.0
    expect(wallMsPerSimMs()).toBe(2.0);
    onSnapshot(ISO(simStart + 3000), 2166); // outlier 2 → still 2.0
    expect(wallMsPerSimMs()).toBe(2.0);
    onSnapshot(ISO(simStart + 4000), 2199); // outlier 3 → RE-SEED
    expect(wallMsPerSimMs()).toBeCloseTo(0.033, 1);
  });

  it("resets consecutive outlier counter on normal measurement", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // seed EMA = 2.0
    // 2 outliers (fast delivery: rate ≈ 0.033)
    onSnapshot(ISO(simStart + 2000), 2133); // outlier 1
    onSnapshot(ISO(simStart + 3000), 2166); // outlier 2
    // Then a normal measurement at a DIFFERENT rate (1.5) to move EMA
    onSnapshot(ISO(simStart + 4000), 3666); // rate=1500/1000=1.5, ratio=0.75 → normal
    const rate = wallMsPerSimMs();
    // EMA: 2.0*0.85 + 1.5*0.15 = 1.925 — moved from 2.0, still above 1.5
    expect(rate).toBeCloseTo(1.925, 2);
    // Counter was reset by the normal measurement. One more outlier
    // should NOT trigger re-seed (only 1 consecutive, need 3).
    onSnapshot(ISO(simStart + 5000), 3699); // outlier again → count=1
    expect(wallMsPerSimMs()).toBeCloseTo(1.925, 1); // unchanged
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

  it("re-seeds immediately on large backward sim_time jump (seek backward)", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // seed EMA = 2.0
    // Advance sim_time far ahead (as if running at 60x)
    onSnapshot(ISO(simStart + 60000), 3100); // 60s sim in 1s wall
    // Now seek backward to "now" — sim_time jumps back by 59 seconds
    const seekTarget = simStart + 1000;
    onSnapshot(ISO(seekTarget), 4100);
    // Should have re-seeded: interpolation should return ~seekTarget
    const interp = interpolatedSimTimeMs(4200);
    // With default rate 1.0: seekTarget + (4200-4100)/1.0 = seekTarget + 100
    expect(interp).toBeCloseTo(seekTarget + 100, -1);
    // Rate should be reset to default
    expect(wallMsPerSimMs()).toBe(1.0);
  });

  it("re-seeds immediately on large forward sim_time jump (seek forward)", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // seed EMA = 2.0
    // Seek forward by 60 seconds in 1 wall-second
    const seekTarget = simStart + 61000;
    onSnapshot(ISO(seekTarget), 3100);
    // Should have re-seeded: interpolation from new anchor
    const interp = interpolatedSimTimeMs(3600);
    // Default rate 1.0: seekTarget + (3600-3100)/1.0 = seekTarget + 500
    expect(interp).toBeCloseTo(seekTarget + 500, -1);
    expect(wallMsPerSimMs()).toBe(1.0);
  });

  it("does NOT re-seed on small backward jump (jitter)", () => {
    const simStart = 1775260800000;
    onSnapshot(ISO(simStart), 100);
    onSnapshot(ISO(simStart + 1000), 2100); // seed EMA = 2.0
    // Small backward jump of 500ms — jitter, not seek
    const result = onSnapshot(ISO(simStart + 500), 2600);
    expect(result).toBeNull();
    // Rate should be unchanged
    expect(wallMsPerSimMs()).toBeCloseTo(2.0, 10);
  });
});
