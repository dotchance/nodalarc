// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { describe, expect, it } from "vitest";
import { CAMERA_MAX_DISTANCE } from "../../../config";
import { EARTH_RADIUS_KM, kmToRender } from "../units";
import {
  cameraDistanceForSceneRadius,
  cameraFarForMaxDistance,
  sceneRadiusForCamera,
  type CameraBoundsBody,
  type CameraBoundsNode,
} from "../cameraBounds";

describe("scene-aware camera bounds", () => {
  it("keeps Earth-only scenes on the existing camera-distance floor", () => {
    const bodies: CameraBoundsBody[] = [
      { id: "earth", radiusKm: EARTH_RADIUS_KM, position: [0, 0, 0] },
    ];

    const maxDistance = cameraDistanceForSceneRadius(sceneRadiusForCamera(bodies, []));

    expect(maxDistance).toBe(CAMERA_MAX_DISTANCE);
  });

  it("expands the control and clip envelope for an Earth-Luna scene", () => {
    const moonDistanceRender = kmToRender(384_400);
    const bodies: CameraBoundsBody[] = [
      { id: "earth", radiusKm: EARTH_RADIUS_KM, position: [0, 0, 0] },
      { id: "luna", radiusKm: 1737.4, position: [moonDistanceRender, 0, 0] },
    ];

    const maxDistance = cameraDistanceForSceneRadius(sceneRadiusForCamera(bodies, []));

    expect(maxDistance).toBeGreaterThan(CAMERA_MAX_DISTANCE);
    expect(cameraFarForMaxDistance(maxDistance)).toBeGreaterThan(maxDistance);
  });

  it("includes high-altitude nodes when fitting a single-body scene", () => {
    const bodies: CameraBoundsBody[] = [
      { id: "earth", radiusKm: EARTH_RADIUS_KM, position: [0, 0, 0] },
    ];
    const nodes: CameraBoundsNode[] = [{ reference_body: "earth", alt_km: 35_786 }];

    const sceneRadius = sceneRadiusForCamera(bodies, nodes);

    expect(sceneRadius).toBeCloseTo(kmToRender(EARTH_RADIUS_KM + 35_786), 6);
    expect(cameraDistanceForSceneRadius(sceneRadius)).toBe(CAMERA_MAX_DISTANCE);
  });
});
