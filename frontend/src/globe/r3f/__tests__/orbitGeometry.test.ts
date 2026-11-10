// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { computeOrbitPositions, supportsStaticOrbitRing } from "../orbitGeometry";

describe("supportsStaticOrbitRing", () => {
  it("allows circular and near-circular orbit overlays", () => {
    expect(supportsStaticOrbitRing(0)).toBe(true);
    expect(supportsStaticOrbitRing(1e-7)).toBe(true);
  });

  it("rejects eccentric orbit overlays instead of drawing a false circular path", () => {
    expect(supportsStaticOrbitRing(0.01)).toBe(false);
    expect(supportsStaticOrbitRing(0.737)).toBe(false);
  });

  it("rejects nodes without Keplerian eccentricity facts", () => {
    expect(supportsStaticOrbitRing(null)).toBe(false);
    expect(supportsStaticOrbitRing(undefined)).toBe(false);
  });
});

describe("computeOrbitPositions", () => {
  it("turns the ring about its central body's centre, relative to it, wherever that body is", () => {
    // Earth placed where the world centre on Mars puts it; a satellite 110 units above its centre.
    const earth = new THREE.Vector3(-6.3e6, 1.2e5, 2.4e6);
    const satellite = earth.clone().add(new THREE.Vector3(0, 0, 110));
    const velocity = new THREE.Vector3(0.4, 0, 0);

    const ring = computeOrbitPositions(satellite, velocity, earth, 36);

    const vertex = new THREE.Vector3();
    for (let i = 0; i < ring.length; i += 3) {
      vertex.set(ring[i]!, ring[i + 1]!, ring[i + 2]!);
      // Within single precision at the ring's own size; a ring kept in world coordinates out
      // here (6.3e6 units from the origin) would be off by about 0.5.
      expect(vertex.length()).toBeCloseTo(110, 4);
      // The ring lies in the plane of the satellite's position about Earth and its velocity.
      expect(vertex.y).toBeCloseTo(0, 4);
    }
    // The ring passes through the satellite, placed at Earth's centre.
    const start = new THREE.Vector3(ring[0]!, ring[1]!, ring[2]!).add(earth);
    expect(start.distanceTo(satellite)).toBeLessThan(1e-6 * satellite.length());
  });
});
