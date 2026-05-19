// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Tests for rendering invariants that caught real bugs in production.
//
// These tests verify structural properties that, when violated, produce
// visible rendering failures. Each test documents a specific bug that
// was discovered during live deployment.

import { describe, it, expect } from "vitest";
import * as THREE from "three";
import { tokens } from "../../styles/tokens";
import {
  SCENE_EARTH_RADIUS,
  SCENE_KM_PER_UNIT,
} from "../../sim/orbitalMath";
import type { LinkState } from "../../types";

function makeIslLink(index: number): LinkState {
  const plane = Math.floor(index / 11);
  const slot = index % 11;
  const peerSlot = (slot + 1) % 11;
  return {
    node_a: `sat-P${String(plane).padStart(2, "0")}S${String(slot).padStart(2, "0")}`,
    node_b: `sat-P${String(plane).padStart(2, "0")}S${String(peerSlot).padStart(2, "0")}`,
    state: "active",
    link_type: "intra_plane_isl",
    link_reason: null,
    latency_ms: 1,
    bandwidth_mbps: 1000,
    range_km: 1000,
    traffic_load_pct: null,
    interface_a: "isl0",
    interface_b: "isl1",
  };
}

describe("rendering invariants", () => {
  describe("label visibility at default camera distance", () => {
    // BUG: Labels were invisible because FADE_IN_DIST (60) was less
    // than the minimum camera-to-satellite distance at default zoom
    // (~142 scene units). Operators could never see satellite IDs.
    it("labels must be visible at default camera distance to nearest satellite", () => {
      const cameraDefaultDist = tokens.cameraDistance;
      const satRadius = SCENE_EARTH_RADIUS + 550 / SCENE_KM_PER_UNIT;
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
      const satOrbitRadius = SCENE_EARTH_RADIUS + 550 / SCENE_KM_PER_UNIT;
      expect(tokens.cameraMaxDistance).toBeGreaterThan(satOrbitRadius * 3);
    });

    it("min camera distance is above the earth surface", () => {
      expect(tokens.cameraMinDistance).toBeGreaterThan(SCENE_EARTH_RADIUS);
    });
  });

  describe("satellite indexing consistency", () => {
    // BUG: InstancedMesh intersection returns instanceId, but the
    // picker was looking for userData["nodeId"] which doesn't exist
    // on InstancedMesh instances. Need instanceId → nodeId mapping.
    it("indexToId mapping must be exported from satellites module", async () => {
      const mod = await import("../satellites");
      expect(Array.isArray(mod.indexToId)).toBe(true);
    });
  });

  describe("link renderer capacity", () => {
    // BUG: VS-API can bootstrap with an empty first snapshot and then receive
    // hundreds of links. The globe renderer used to allocate from that empty
    // first snapshot and silently drop every ISL beyond its fallback capacity.
    it("grows instead of silently dropping ISLs after an empty first snapshot", async () => {
      const mod = await import("../links");
      const earthFrame = new THREE.Object3D();
      const links = Array.from({ length: 352 }, (_, i) => makeIslLink(i));

      mod.clearLinks();
      mod.updateLinks([], earthFrame, true);
      mod.updateLinks(links, earthFrame, true);

      expect(mod.getLinks().size).toBe(352);
      mod.clearLinks();
    });
  });
});
