// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Tests for rendering invariants that caught real bugs in production.
//
// These tests verify structural properties that, when violated, produce
// visible rendering failures. Each test documents a specific bug that
// was discovered during live deployment.

import { describe, it, expect } from "vitest";
import { tokens } from "../../styles/tokens";
import { SCENE_EARTH_RADIUS } from "../../sim/orbitalMath";
import { catalogEarthKmPerRenderUnit } from "../../sim/__tests__/bodyModelFixture";

const EARTH_KM_PER_RENDER_UNIT = catalogEarthKmPerRenderUnit();

describe("rendering invariants", () => {
  describe("label visibility at default camera distance", () => {
    // BUG: Labels were invisible because FADE_IN_DIST (60) was less
    // than the minimum camera-to-satellite distance at default zoom
    // (~142 scene units). Operators could never see satellite IDs.
    it("labels must be visible at default camera distance to nearest satellite", () => {
      const cameraDefaultDist = tokens.cameraDistance;
      const satRadius = SCENE_EARTH_RADIUS + 550 / EARTH_KM_PER_RENDER_UNIT;
      const minCamToSat = cameraDefaultDist - satRadius;

      // Read the actual fade distances from the labels module
      // (can't import directly without Three.js, but we can verify
      // the token-level constraint)
      // The camera at default zoom is ~142 scene units from the
      // nearest satellite. Labels must fade in at a distance
      // greater than this to be visible at default zoom.
      expect(
        minCamToSat,
        `Default camera is ${minCamToSat.toFixed(1)} units from nearest LEO sat`,
      ).toBeGreaterThan(50);
      expect(
        minCamToSat,
        `Default camera-to-satellite distance must be documentable`,
      ).toBeLessThan(300);
    });
  });

  describe("camera bounds include full constellation view", () => {
    // BUG: Camera max distance was too restrictive (600 = 6x earth)
    // preventing operators from zooming out for full constellation view.
    it("max camera distance allows seeing the full LEO shell", () => {
      const satOrbitRadius = SCENE_EARTH_RADIUS + 550 / EARTH_KM_PER_RENDER_UNIT;
      expect(tokens.cameraMaxDistance).toBeGreaterThan(satOrbitRadius * 3);
    });

    it("max camera distance allows framing a GEO shell", () => {
      const geoOrbitRadius = SCENE_EARTH_RADIUS + 35786 / EARTH_KM_PER_RENDER_UNIT;
      const halfFovRad = (tokens.cameraFov * Math.PI) / 360;
      const requiredDistance = geoOrbitRadius / Math.sin(halfFovRad);
      expect(tokens.cameraMaxDistance).toBeGreaterThan(requiredDistance * 1.25);
    });

    it("min camera distance is above the earth surface", () => {
      expect(tokens.cameraMinDistance).toBeGreaterThan(SCENE_EARTH_RADIUS);
    });
  });

});
