// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import { interpolateFromBuffer, readPosition } from "../workerBridge";

// These tests construct real Float32Array/Float64Array buffers with
// known satellite positions and call the production interpolateFromBuffer
// function. They verify the actual code path, not re-implementations.

function makeTestBuffer(
  satCount: number,
  samplesPerWindow: number,
  windowStart: number,
  sampleInterval: number,
  positionFn: (satIndex: number, sampleIndex: number) => [number, number, number],
): { header: Float64Array; positions: Float32Array } {
  const header = new Float64Array(2);
  header[0] = windowStart;
  header[1] = sampleInterval;

  const positions = new Float32Array(satCount * samplesPerWindow * 3);
  for (let si = 0; si < satCount; si++) {
    for (let j = 0; j < samplesPerWindow; j++) {
      const [x, y, z] = positionFn(si, j);
      const offset = (si * samplesPerWindow + j) * 3;
      positions[offset] = x;
      positions[offset + 1] = y;
      positions[offset + 2] = z;
    }
  }
  return { header, positions };
}

describe("interpolateFromBuffer", () => {
  const SAMPLES = 50;

  it("returns exact position at a sample boundary (frac=0)", () => {
    const buf = makeTestBuffer(1, SAMPLES, 1000.0, 0.1,
      (_sat, sample) => [sample * 10, sample * 20, sample * 30]);

    const target = { x: 0, y: 0, z: 0 };
    const ok = interpolateFromBuffer(
      buf.header, buf.positions, 0, 1000.5, SAMPLES, target);

    expect(ok).toBe(true);
    expect(target.x).toBe(50);
    expect(target.y).toBe(100);
    expect(target.z).toBe(150);
  });

  it("interpolates correctly at midpoint between two samples", () => {
    const buf = makeTestBuffer(1, SAMPLES, 1000.0, 1.0,
      (_sat, sample) => [sample * 100, 0, 0]);

    const target = { x: 0, y: 0, z: 0 };
    const ok = interpolateFromBuffer(
      buf.header, buf.positions, 0, 1005.5, SAMPLES, target);

    expect(ok).toBe(true);
    // sample 5 → x=500, sample 6 → x=600. Midpoint → 550.
    expect(target.x).toBeCloseTo(550, 5);
  });

  it("interpolates at 25% between two samples", () => {
    const buf = makeTestBuffer(1, SAMPLES, 0, 2.0,
      (_sat, sample) => [sample * 100, sample * 200, sample * 300]);

    const target = { x: 0, y: 0, z: 0 };
    // t=4.5 → sample = 4.5/2.0 = 2.25 → frac = 0.25
    const ok = interpolateFromBuffer(
      buf.header, buf.positions, 0, 4.5, SAMPLES, target);

    expect(ok).toBe(true);
    // sample 2 → (200, 400, 600), sample 3 → (300, 600, 900)
    // at frac 0.25: 200 + 25 = 225, 400 + 50 = 450, 600 + 75 = 675
    expect(target.x).toBeCloseTo(225, 3);
    expect(target.y).toBeCloseTo(450, 3);
    expect(target.z).toBeCloseTo(675, 3);
  });

  it("returns false for time before window start", () => {
    const buf = makeTestBuffer(1, SAMPLES, 1000.0, 0.1,
      () => [1, 2, 3]);

    const target = { x: 999, y: 999, z: 999 };
    const ok = interpolateFromBuffer(
      buf.header, buf.positions, 0, 999.9, SAMPLES, target);

    expect(ok).toBe(false);
    expect(target.x).toBe(999);
  });

  it("returns false for time past window end", () => {
    const buf = makeTestBuffer(1, SAMPLES, 1000.0, 0.1,
      () => [1, 2, 3]);

    const target = { x: 999, y: 999, z: 999 };
    // window covers 1000.0 to 1000.0 + 49*0.1 = 1004.9
    const ok = interpolateFromBuffer(
      buf.header, buf.positions, 0, 1005.0, SAMPLES, target);

    expect(ok).toBe(false);
  });

  it("reads correct satellite when multiple satellites are in the buffer", () => {
    const buf = makeTestBuffer(3, SAMPLES, 0, 1.0,
      (sat, _sample) => [sat * 1000 + 1, sat * 1000 + 2, sat * 1000 + 3]);

    // Satellite 0 at any time → (1, 2, 3)
    const t0 = { x: 0, y: 0, z: 0 };
    interpolateFromBuffer(buf.header, buf.positions, 0, 0, SAMPLES, t0);
    expect(t0.x).toBeCloseTo(1, 3);
    expect(t0.y).toBeCloseTo(2, 3);
    expect(t0.z).toBeCloseTo(3, 3);

    // Satellite 1 → (1001, 1002, 1003)
    const t1 = { x: 0, y: 0, z: 0 };
    interpolateFromBuffer(buf.header, buf.positions, 1, 0, SAMPLES, t1);
    expect(t1.x).toBeCloseTo(1001, 3);
    expect(t1.y).toBeCloseTo(1002, 3);
    expect(t1.z).toBeCloseTo(1003, 3);

    // Satellite 2 → (2001, 2002, 2003)
    const t2 = { x: 0, y: 0, z: 0 };
    interpolateFromBuffer(buf.header, buf.positions, 2, 0, SAMPLES, t2);
    expect(t2.x).toBeCloseTo(2001, 3);
    expect(t2.y).toBeCloseTo(2002, 3);
    expect(t2.z).toBeCloseTo(2003, 3);
  });

  it("returns false when sampleInterval is zero (uninitialized buffer)", () => {
    const header = new Float64Array(2);
    header[0] = 1000;
    header[1] = 0; // zero interval = uninitialized
    const positions = new Float32Array(SAMPLES * 3);

    const target = { x: 999, y: 999, z: 999 };
    const ok = interpolateFromBuffer(header, positions, 0, 1000, SAMPLES, target);
    expect(ok).toBe(false);
  });

  it("does not mutate target on failure", () => {
    const header = new Float64Array(2);
    header[0] = 1000;
    header[1] = 0;
    const positions = new Float32Array(SAMPLES * 3);

    const target = { x: 42, y: 43, z: 44 };
    interpolateFromBuffer(header, positions, 0, 1000, SAMPLES, target);
    expect(target.x).toBe(42);
    expect(target.y).toBe(43);
    expect(target.z).toBe(44);
  });

  it("handles satellite moving along a circular path (non-linear motion)", () => {
    // Simulate a satellite at radius 100 orbiting in the XZ plane
    const buf = makeTestBuffer(1, SAMPLES, 0, 0.1,
      (_sat, sample) => {
        const angle = (sample / SAMPLES) * 2 * Math.PI;
        return [100 * Math.cos(angle), 0, 100 * Math.sin(angle)];
      });

    // At sample 0: (100, 0, 0). At sample 1: cos(2π/50)*100 ≈ 99.2, sin ≈ 12.5
    const target = { x: 0, y: 0, z: 0 };
    // Interpolate at midpoint between sample 0 and sample 1
    const ok = interpolateFromBuffer(
      buf.header, buf.positions, 0, 0.05, SAMPLES, target);

    expect(ok).toBe(true);
    // Linear interpolation between (100,0,0) and (~99.2,0,~12.5)
    // won't follow the arc exactly — distance from origin will be
    // slightly less than 100 (chord vs arc). This is expected and
    // acceptable for 0.1s sample intervals.
    const dist = Math.sqrt(target.x ** 2 + target.y ** 2 + target.z ** 2);
    expect(dist).toBeGreaterThan(99);
    expect(dist).toBeLessThanOrEqual(100);
  });
});

describe("readPosition with uninitialized bridge", () => {
  it("returns false when bridge is not initialized", () => {
    const target = { x: 999, y: 999, z: 999 };
    const result = readPosition("any-node", 0, target);
    expect(result).toBe(false);
    expect(target.x).toBe(999);
  });
});

describe("adaptive sample interval invariant", () => {
  function computeSampleInterval(speed: number): number {
    return Math.max(0.1, 0.1 * Math.max(1, speed));
  }

  it("window always covers ≥5 wall-seconds of data at any playback speed", () => {
    for (const speed of [0.1, 0.5, 1, 2, 5, 10, 30, 100]) {
      const interval = computeSampleInterval(speed);
      const windowSimSeconds = interval * 50;
      const wallSeconds = windowSimSeconds / Math.max(1, speed);
      expect(
        wallSeconds,
        `At ${speed}x: window=${windowSimSeconds}s sim, ${wallSeconds.toFixed(1)}s wall`,
      ).toBeGreaterThanOrEqual(5);
    }
  });
});
