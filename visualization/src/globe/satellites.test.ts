/** Tests for satellite animation smoothness.
 *
 *  Core invariant: satellites move at constant speed between snapshots.
 *  No stop-and-jump at snapshot boundaries. No frame-rate dependent jitter.
 */

import { describe, it, expect } from "vitest";
import { interpParam, SNAPSHOT_INTERVAL } from "./satellites";

describe("interpParam", () => {
  it("returns 0 at the moment a snapshot arrives", () => {
    expect(interpParam(0, SNAPSHOT_INTERVAL)).toBe(0);
  });

  it("returns 0.5 halfway through the interval", () => {
    expect(interpParam(SNAPSHOT_INTERVAL / 2, SNAPSHOT_INTERVAL)).toBeCloseTo(0.5);
  });

  it("returns 1.0 at the end of the interval", () => {
    expect(interpParam(SNAPSHOT_INTERVAL, SNAPSHOT_INTERVAL)).toBe(1.0);
  });

  it("clamps at 1.0 if snapshot is late", () => {
    expect(interpParam(SNAPSHOT_INTERVAL * 2, SNAPSHOT_INTERVAL)).toBe(1.0);
    expect(interpParam(SNAPSHOT_INTERVAL * 10, SNAPSHOT_INTERVAL)).toBe(1.0);
  });

  it("returns 1 when interval is zero (degenerate)", () => {
    expect(interpParam(0.5, 0)).toBe(1);
  });
});

describe("constant-speed motion (no jerk)", () => {
  /** Simulate 1D linear interpolation: prev=0, target=100,
   *  advancing by dt each frame for `duration` seconds. */
  function simulateMotion(fps: number, duration: number): number[] {
    const dt = 1 / fps;
    const frames = Math.round(duration * fps);
    const positions: number[] = [];
    let age = 0;
    for (let i = 0; i < frames; i++) {
      age += dt;
      const t = interpParam(age, SNAPSHOT_INTERVAL);
      positions.push(t * 100); // prev=0, target=100
    }
    return positions;
  }

  it("position increases monotonically (no backwards motion)", () => {
    const positions = simulateMotion(60, SNAPSHOT_INTERVAL);
    for (let i = 1; i < positions.length; i++) {
      expect(positions[i]).toBeGreaterThanOrEqual(positions[i - 1]!);
    }
  });

  it("position increments are constant (constant speed, no jerk)", () => {
    const positions = simulateMotion(60, SNAPSHOT_INTERVAL);
    // Before clamping at t=1, all increments should be equal
    const increments: number[] = [];
    for (let i = 1; i < positions.length; i++) {
      const inc = positions[i]! - positions[i - 1]!;
      if (inc > 0.001) increments.push(inc); // skip clamped-at-1 frames
    }
    // All non-zero increments should be the same (linear motion)
    const first = increments[0]!;
    for (const inc of increments) {
      expect(inc).toBeCloseTo(first, 5);
    }
  });

  it("reaches target within one snapshot interval", () => {
    const positions = simulateMotion(60, SNAPSHOT_INTERVAL);
    const last = positions[positions.length - 1]!;
    expect(last).toBeCloseTo(100, 1);
  });

  it("same final position regardless of frame rate", () => {
    const pos30 = simulateMotion(30, SNAPSHOT_INTERVAL);
    const pos60 = simulateMotion(60, SNAPSHOT_INTERVAL);
    const pos144 = simulateMotion(144, SNAPSHOT_INTERVAL);
    expect(pos30[pos30.length - 1]).toBeCloseTo(100, 1);
    expect(pos60[pos60.length - 1]).toBeCloseTo(100, 1);
    expect(pos144[pos144.length - 1]).toBeCloseTo(100, 1);
  });

  it("same position at t=0.5s regardless of frame rate", () => {
    // At halfway, position should be ~50 regardless of fps
    function posAtHalf(fps: number): number {
      const dt = 1 / fps;
      const halfFrames = Math.round(0.5 * fps);
      let age = 0;
      let pos = 0;
      for (let i = 0; i < halfFrames; i++) {
        age += dt;
        pos = interpParam(age, SNAPSHOT_INTERVAL) * 100;
      }
      return pos;
    }
    expect(posAtHalf(30)).toBeCloseTo(50, 0);
    expect(posAtHalf(60)).toBeCloseTo(50, 0);
    expect(posAtHalf(144)).toBeCloseTo(50, 0);
  });

  it("dropped frame does not cause position jump", () => {
    // 29 normal frames, 1 doubled frame, 30 normal frames
    const dt60 = 1 / 60;
    const dtDropped = 2 / 60;
    let age = 0;

    const positions: number[] = [];
    for (let i = 0; i < 29; i++) {
      age += dt60;
      positions.push(interpParam(age, SNAPSHOT_INTERVAL) * 100);
    }
    // Dropped frame — dt is doubled
    age += dtDropped;
    positions.push(interpParam(age, SNAPSHOT_INTERVAL) * 100);
    for (let i = 0; i < 30; i++) {
      age += dt60;
      positions.push(interpParam(age, SNAPSHOT_INTERVAL) * 100);
    }

    // Check no increment is more than 2× the median (allowing for the doubled frame)
    const increments: number[] = [];
    for (let i = 1; i < positions.length; i++) {
      increments.push(positions[i]! - positions[i - 1]!);
    }
    const sorted = [...increments].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)]!;
    // The dropped frame increment should be ~2× normal (smooth double-step),
    // never more than 2.5× (which would indicate a discontinuity)
    for (const inc of increments) {
      expect(inc).toBeLessThan(median * 2.5);
    }
  });
});
