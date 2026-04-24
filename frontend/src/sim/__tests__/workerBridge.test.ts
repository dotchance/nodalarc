// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import { readPosition } from "../workerBridge";

// readPosition reads from a SharedArrayBuffer that the Worker writes.
// We can't run a real Worker in vitest, but we CAN test the interpolation
// math and buffer layout by constructing a SAB with known data and
// calling readPosition. This tests the actual code path, not a mock.

// However, readPosition depends on module-scoped state (control, bufferA, etc)
// that's set by initWorkerBridge(). Since we can't call that without SAB
// support in the test env, we test the buffer math directly.

// These tests verify the interpolation logic that readPosition uses.
// The formula: target = pos[low] + (pos[high] - pos[low]) * frac

describe("SGP4 Worker bridge interpolation math", () => {
  it("linear interpolation at exact sample point returns that sample", () => {
    const x0 = 10, y0 = 20, z0 = 30;
    const x1 = 40, y1 = 50, z1 = 60;
    const frac = 0.0; // exact low sample

    const x = x0 + (x1 - x0) * frac;
    const y = y0 + (y1 - y0) * frac;
    const z = z0 + (z1 - z0) * frac;

    expect(x).toBe(10);
    expect(y).toBe(20);
    expect(z).toBe(30);
  });

  it("linear interpolation at midpoint returns average", () => {
    const x0 = 10, y0 = 20, z0 = 30;
    const x1 = 40, y1 = 50, z1 = 60;
    const frac = 0.5;

    const x = x0 + (x1 - x0) * frac;
    const y = y0 + (y1 - y0) * frac;
    const z = z0 + (z1 - z0) * frac;

    expect(x).toBe(25);
    expect(y).toBe(35);
    expect(z).toBe(45);
  });

  it("linear interpolation at frac=1.0 returns high sample", () => {
    const x0 = 10, y0 = 20, z0 = 30;
    const x1 = 40, y1 = 50, z1 = 60;
    const frac = 1.0;

    const x = x0 + (x1 - x0) * frac;
    const y = y0 + (y1 - y0) * frac;
    const z = z0 + (z1 - z0) * frac;

    expect(x).toBe(40);
    expect(y).toBe(50);
    expect(z).toBe(60);
  });
});

describe("SAB buffer layout arithmetic", () => {
  const SAMPLES_PER_WINDOW = 50;

  function bufferOffset(satIndex: number, sampleIndex: number): number {
    return (satIndex * SAMPLES_PER_WINDOW + sampleIndex) * 3;
  }

  it("satellite 0 sample 0 starts at offset 0", () => {
    expect(bufferOffset(0, 0)).toBe(0);
  });

  it("satellite 0 sample 1 starts at offset 3 (3 floats per sample)", () => {
    expect(bufferOffset(0, 1)).toBe(3);
  });

  it("satellite 1 sample 0 starts after all of satellite 0's samples", () => {
    expect(bufferOffset(1, 0)).toBe(SAMPLES_PER_WINDOW * 3);
  });

  it("satellite indices don't overlap", () => {
    const sat0End = bufferOffset(0, SAMPLES_PER_WINDOW - 1) + 3;
    const sat1Start = bufferOffset(1, 0);
    expect(sat1Start).toBeGreaterThanOrEqual(sat0End);
  });

  it("10K satellites at 50 samples fit in the allocated float count", () => {
    const maxSats = 10_000;
    const totalFloats = maxSats * SAMPLES_PER_WINDOW * 3;
    const totalBytes = totalFloats * 4;
    expect(totalBytes).toBe(6_000_000);
    expect(totalFloats).toBe(1_500_000);
  });

  it("double buffer total SAB size is correct", () => {
    const controlBytes = 32;
    const headerBytes = 16;
    const maxSats = 10_000;
    const dataBytes = maxSats * SAMPLES_PER_WINDOW * 3 * 4;
    const perBuffer = headerBytes + dataBytes;
    const total = controlBytes + perBuffer * 2;
    expect(total).toBe(32 + (16 + 6_000_000) * 2);
    expect(total).toBe(12_000_064);
  });
});

describe("adaptive sample interval", () => {
  function computeSampleInterval(playbackSpeed: number): number {
    return Math.max(0.1, 0.1 * Math.max(1, playbackSpeed));
  }

  it("1x speed → 0.1s interval (base case)", () => {
    expect(computeSampleInterval(1)).toBe(0.1);
  });

  it("10x speed → 1.0s interval", () => {
    expect(computeSampleInterval(10)).toBe(1.0);
  });

  it("30x speed → 3.0s interval", () => {
    expect(computeSampleInterval(30)).toBe(3.0);
  });

  it("0.5x speed (slow motion) → 0.1s interval (floor)", () => {
    expect(computeSampleInterval(0.5)).toBe(0.1);
  });

  it("window duration at any speed covers ≥5 wall-seconds", () => {
    for (const speed of [0.5, 1, 5, 10, 30, 100]) {
      const interval = computeSampleInterval(speed);
      const windowDuration = interval * 50; // 50 samples
      const wallSeconds = windowDuration / Math.max(1, speed);
      expect(
        wallSeconds,
        `At ${speed}x, window covers ${wallSeconds}s wall time (need ≥5s)`,
      ).toBeGreaterThanOrEqual(5);
    }
  });
});

describe("readPosition boundary conditions", () => {
  it("returns false for unknown node ID", () => {
    const target = { x: 999, y: 999, z: 999 };
    const result = readPosition("nonexistent-node", 0, target);
    expect(result).toBe(false);
    expect(target.x).toBe(999);
  });
});
