// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  cameraDirectionFromTarget,
  fitDistanceForRadius,
  frameEndpoints,
  framePoints,
} from "../cameraFocus";
import { EARTH_RADIUS_RENDER } from "../units";

describe("camera focus framing", () => {
  it("frames a link at the midpoint of its endpoints", () => {
    const a = new THREE.Vector3(0, 0, 0);
    const b = new THREE.Vector3(100, 0, 0);
    const center = new THREE.Vector3();

    const frame = frameEndpoints(a, b, center);

    expect(frame.center.x).toBe(50);
    expect(frame.center.y).toBe(0);
    expect(frame.center.z).toBe(0);
    expect(frame.radius).toBe(50);
  });

  it("frames a point set by centroid and maximum radius", () => {
    const center = new THREE.Vector3();
    const frame = framePoints(
      [new THREE.Vector3(0, 0, 0), new THREE.Vector3(20, 0, 0), new THREE.Vector3(10, 30, 0)],
      center,
    );

    expect(frame).not.toBeNull();
    expect(frame!.center.x).toBeCloseTo(10);
    expect(frame!.center.y).toBeCloseTo(10);
    expect(frame!.radius).toBeCloseTo(20);
  });

  it("keeps camera focus distances at or above the requested floor", () => {
    expect(fitDistanceForRadius(0, EARTH_RADIUS_RENDER)).toBe(EARTH_RADIUS_RENDER);
    expect(fitDistanceForRadius(10, EARTH_RADIUS_RENDER)).toBe(EARTH_RADIUS_RENDER);
    expect(fitDistanceForRadius(10_000, EARTH_RADIUS_RENDER)).toBeGreaterThan(10_000);
  });

  it("derives a stable camera direction even when camera equals target", () => {
    const out = new THREE.Vector3();

    cameraDirectionFromTarget(new THREE.Vector3(1, 2, 3), new THREE.Vector3(1, 2, 3), out);

    expect(out.length()).toBeCloseTo(1);
  });
});
