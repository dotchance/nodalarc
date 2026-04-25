// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Tests for earth occlusion logic — the source of three consecutive bugs.
//
// The function isOccludedByEarth performs a ray-sphere intersection:
// a ray from camera to satellite that hits the earth sphere (at 97%
// radius to account for above-surface rendering) before reaching the
// satellite means the satellite is behind the earth.
//
// These tests use concrete geometric scenarios with known answers.

import { describe, it, expect } from "vitest";
import { isOccludedByEarth } from "../labels";

const R = 100; // earth radius in scene units

// Camera at default zoom on the +X axis, looking toward origin.
// Satellite orbital radius ~108.6 (550km LEO at scene scale).
const CAM_X = 250, CAM_Y = 0, CAM_Z = 0;
const SAT_R = 108.6;

describe("isOccludedByEarth", () => {
  // -----------------------------------------------------------------
  // Trace table for all test cases:
  //
  // | Case | Satellite position        | Expected | Why                                    |
  // |------|---------------------------|----------|----------------------------------------|
  // | 1    | (108.6, 0, 0) — front     | VISIBLE  | Directly between camera and earth       |
  // | 2    | (-108.6, 0, 0) — back     | HIDDEN   | Directly behind earth                   |
  // | 3    | (0, 108.6, 0) — top       | VISIBLE  | Above earth, visible from equatorial cam |
  // | 4    | (0, -108.6, 0) — bottom   | VISIBLE  | Below earth, visible from equatorial cam |
  // | 5    | (0, 0, 108.6) — side      | VISIBLE  | 90° from camera axis, at limb            |
  // | 6    | (-50, 0, 93) — back-side  | HIDDEN   | Behind earth, past the limb              |
  // | 7    | (50, 0, 93) — front-side  | VISIBLE  | In front of earth, near limb             |
  // | 8    | Camera AT sat position    | VISIBLE  | Zero distance, degenerate case           |
  // | 9    | Satellite at earth center | HIDDEN   | Inside earth                             |
  // -----------------------------------------------------------------

  it("Case 1: satellite directly in front of camera — VISIBLE", () => {
    expect(isOccludedByEarth(SAT_R, 0, 0, CAM_X, CAM_Y, CAM_Z, R)).toBe(false);
  });

  it("Case 2: satellite directly behind earth — HIDDEN", () => {
    expect(isOccludedByEarth(-SAT_R, 0, 0, CAM_X, CAM_Y, CAM_Z, R)).toBe(true);
  });

  it("Case 3: satellite at north pole (above earth) — VISIBLE", () => {
    expect(isOccludedByEarth(0, SAT_R, 0, CAM_X, CAM_Y, CAM_Z, R)).toBe(false);
  });

  it("Case 4: satellite at south pole (below earth) — VISIBLE", () => {
    expect(isOccludedByEarth(0, -SAT_R, 0, CAM_X, CAM_Y, CAM_Z, R)).toBe(false);
  });

  it("Case 5: satellite at 90° on the limb — VISIBLE", () => {
    expect(isOccludedByEarth(0, 0, SAT_R, CAM_X, CAM_Y, CAM_Z, R)).toBe(false);
  });

  it("Case 6: satellite behind earth off to one side — HIDDEN", () => {
    // (-50, 0, 93) is at radius ~105.6, behind the earth from camera
    expect(isOccludedByEarth(-50, 0, 93, CAM_X, CAM_Y, CAM_Z, R)).toBe(true);
  });

  it("Case 7: satellite in front of earth off to one side — VISIBLE", () => {
    expect(isOccludedByEarth(50, 0, 93, CAM_X, CAM_Y, CAM_Z, R)).toBe(false);
  });

  it("Case 8: camera at satellite position (degenerate) — VISIBLE", () => {
    expect(isOccludedByEarth(CAM_X, CAM_Y, CAM_Z, CAM_X, CAM_Y, CAM_Z, R)).toBe(false);
  });

  it("Case 9: satellite at earth center — HIDDEN", () => {
    expect(isOccludedByEarth(0, 0, 0, CAM_X, CAM_Y, CAM_Z, R)).toBe(true);
  });

  describe("camera at different positions", () => {
    it("camera at north pole, sat at equator front — VISIBLE", () => {
      expect(isOccludedByEarth(SAT_R, 0, 0, 0, 250, 0, R)).toBe(false);
    });

    it("camera at north pole, sat at south pole — HIDDEN", () => {
      expect(isOccludedByEarth(0, -SAT_R, 0, 0, 250, 0, R)).toBe(true);
    });

    it("camera close to earth surface, sat directly ahead — VISIBLE", () => {
      // Camera at 1.05x earth radius (minimum zoom)
      expect(isOccludedByEarth(SAT_R, 0, 0, 105, 0, 0, R)).toBe(false);
    });

    it("camera far away, sat behind earth — HIDDEN", () => {
      expect(isOccludedByEarth(-SAT_R, 0, 0, 1000, 0, 0, R)).toBe(true);
    });
  });

  describe("symmetry", () => {
    it("occlusion is symmetric for satellites at equal angles", () => {
      // Two satellites at +Z and -Z, same distance from camera axis
      const a = isOccludedByEarth(0, 0, SAT_R, CAM_X, CAM_Y, CAM_Z, R);
      const b = isOccludedByEarth(0, 0, -SAT_R, CAM_X, CAM_Y, CAM_Z, R);
      expect(a).toBe(b);
    });

    it("occlusion is symmetric for Y axis", () => {
      const a = isOccludedByEarth(0, SAT_R, 0, CAM_X, CAM_Y, CAM_Z, R);
      const b = isOccludedByEarth(0, -SAT_R, 0, CAM_X, CAM_Y, CAM_Z, R);
      expect(a).toBe(b);
    });
  });

  describe("boundary: satellite exactly at the limb", () => {
    it("satellite just inside the visual limb — VISIBLE", () => {
      // Satellite at a position where the ray to it just barely
      // misses the earth. At SAT_R=108.6 and 90° from camera axis,
      // the ray clears the earth.
      const angle = Math.PI / 2 - 0.1; // just inside 90°
      const sx = SAT_R * Math.cos(angle);
      const sz = SAT_R * Math.sin(angle);
      expect(isOccludedByEarth(sx, 0, sz, CAM_X, 0, 0, R)).toBe(false);
    });

    it("satellite just past the limb — HIDDEN", () => {
      // Satellite past 90° from camera axis — ray must pass through earth
      const angle = Math.PI / 2 + 0.3;
      const sx = SAT_R * Math.cos(angle);
      const sz = SAT_R * Math.sin(angle);
      expect(isOccludedByEarth(sx, 0, sz, CAM_X, 0, 0, R)).toBe(true);
    });
  });
});
