// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import {
  createTrailBuffer,
  pushSample,
  extractDrawPoints,
  type TrailSample,
} from "../trailBuffer";

/** Dot product of two 3-vectors represented as TrailSample. */
function dot(a: TrailSample, b: TrailSample): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

/** Euclidean distance between two points. */
function dist(a: TrailSample, b: TrailSample): number {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  const dz = a.z - b.z;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

describe("orbitalTrails — physics-based trail correctness", () => {
  it("trail points are behind the satellite velocity vector", () => {
    const buf = createTrailBuffer(600);
    const V: TrailSample = { x: 1, y: 0, z: 0 };

    // Push 20 samples spaced 0.5 apart along +X
    for (let i = 0; i < 20; i++) {
      pushSample(buf, 100 + i * 0.5, 0, 0);
    }
    const currentPos: TrailSample = { x: 109.5, y: 0, z: 0 };

    const points = extractDrawPoints(buf, 5.0);
    expect(points.length).toBeGreaterThan(1);

    // Assertion 1: all trail points except newest are behind the satellite.
    // D = P - currentPosition; dot(D, V) < 0 means P is behind.
    for (let i = 0; i < points.length - 1; i++) {
      const D: TrailSample = {
        x: points[i]!.x - currentPos.x,
        y: points[i]!.y - currentPos.y,
        z: points[i]!.z - currentPos.z,
      };
      expect(dot(D, V)).toBeLessThan(0);
    }

    // Assertion 2: points are ordered oldest to newest.
    // Distance from currentPosition is monotonically non-increasing.
    for (let i = 0; i < points.length - 1; i++) {
      const d0 = dist(points[i]!, currentPos);
      const d1 = dist(points[i + 1]!, currentPos);
      expect(d0).toBeGreaterThanOrEqual(d1 - 1e-9);
    }

    // Assertion 3: newest point equals the most recently pushed position.
    const newest = points[points.length - 1]!;
    expect(newest.x).toBeCloseTo(109.5, 10);
    expect(newest.y).toBeCloseTo(0, 10);
    expect(newest.z).toBeCloseTo(0, 10);
  });

  it("trail does not flip on new samples", () => {
    const buf = createTrailBuffer(600);
    const V: TrailSample = { x: 0, y: 1, z: 0 };

    // Push 30 samples along +Y
    for (let i = 0; i < 30; i++) {
      pushSample(buf, 0, 50 + i * 0.3, 0);
    }

    // Verify all dot products negative
    let currentPos: TrailSample = { x: 0, y: 50 + 29 * 0.3, z: 0 };
    let points = extractDrawPoints(buf, 5.0);

    for (let i = 0; i < points.length - 1; i++) {
      const D: TrailSample = {
        x: points[i]!.x - currentPos.x,
        y: points[i]!.y - currentPos.y,
        z: points[i]!.z - currentPos.z,
      };
      expect(dot(D, V)).toBeLessThan(0);
    }

    // Push 10 more samples continuing along +Y
    for (let i = 30; i < 40; i++) {
      pushSample(buf, 0, 50 + i * 0.3, 0);
    }

    // Re-extract and verify no trail point has flipped
    currentPos = { x: 0, y: 50 + 39 * 0.3, z: 0 };
    points = extractDrawPoints(buf, 5.0);

    for (let i = 0; i < points.length - 1; i++) {
      const D: TrailSample = {
        x: points[i]!.x - currentPos.x,
        y: points[i]!.y - currentPos.y,
        z: points[i]!.z - currentPos.z,
      };
      expect(dot(D, V)).toBeLessThan(0);
    }
  });

  it("trail correct with diagonal velocity", () => {
    const buf = createTrailBuffer(600);
    // Normalized diagonal velocity
    const len = Math.sqrt(2);
    const V: TrailSample = { x: 1 / len, y: 1 / len, z: 0 };

    // Push 20 samples along the diagonal
    for (let i = 0; i < 20; i++) {
      const t = i * 0.5;
      pushSample(buf, 100 + t * V.x, 100 + t * V.y, 0);
    }

    const lastT = 19 * 0.5;
    const currentPos: TrailSample = {
      x: 100 + lastT * V.x,
      y: 100 + lastT * V.y,
      z: 0,
    };

    const points = extractDrawPoints(buf, 5.0);
    expect(points.length).toBeGreaterThan(1);

    // All trail points except newest must be behind velocity vector
    for (let i = 0; i < points.length - 1; i++) {
      const D: TrailSample = {
        x: points[i]!.x - currentPos.x,
        y: points[i]!.y - currentPos.y,
        z: points[i]!.z - currentPos.z,
      };
      expect(dot(D, V)).toBeLessThan(0);
    }

    // Points ordered oldest to newest
    for (let i = 0; i < points.length - 1; i++) {
      const d0 = dist(points[i]!, currentPos);
      const d1 = dist(points[i + 1]!, currentPos);
      expect(d0).toBeGreaterThanOrEqual(d1 - 1e-9);
    }
  });

  it("trail wraps ring buffer correctly", () => {
    const buf = createTrailBuffer(10);
    const V: TrailSample = { x: 1, y: 0, z: 0 };

    // Push 25 samples — wraps the ring buffer twice
    for (let i = 0; i < 25; i++) {
      pushSample(buf, i * 0.5, 0, 0);
    }

    const currentPos: TrailSample = { x: 24 * 0.5, y: 0, z: 0 };
    const points = extractDrawPoints(buf, 100); // large arc to get all valid points

    expect(points.length).toBeGreaterThan(0);
    expect(points.length).toBeLessThanOrEqual(10); // capped by capacity

    // All trail points except newest are behind the satellite
    for (let i = 0; i < points.length - 1; i++) {
      const D: TrailSample = {
        x: points[i]!.x - currentPos.x,
        y: points[i]!.y - currentPos.y,
        z: points[i]!.z - currentPos.z,
      };
      expect(dot(D, V)).toBeLessThan(0);
    }

    // Points ordered oldest to newest
    for (let i = 0; i < points.length - 1; i++) {
      const d0 = dist(points[i]!, currentPos);
      const d1 = dist(points[i + 1]!, currentPos);
      expect(d0).toBeGreaterThanOrEqual(d1 - 1e-9);
    }

    // No stale pre-wrap data: oldest returned point should be from the
    // last 10 samples pushed (indices 15-24, positions 7.5-12.0)
    const oldest = points[0]!;
    expect(oldest.x).toBeGreaterThanOrEqual(15 * 0.5 - 1e-9);

    // Newest point equals most recently pushed position
    const newest = points[points.length - 1]!;
    expect(newest.x).toBeCloseTo(12.0, 10);
    expect(newest.y).toBeCloseTo(0, 10);
    expect(newest.z).toBeCloseTo(0, 10);
  });
});
