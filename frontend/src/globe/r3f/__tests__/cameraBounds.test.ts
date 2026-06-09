// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { describe, expect, it } from "vitest";
import { CAMERA_MAX_DISTANCE } from "../../../config";
import {
  catalogBodyRadiusKm,
  catalogEarthKmPerRenderUnit,
} from "../../../sim/__tests__/bodyModelFixture";
import { kmToRender } from "../units";
import {
  cameraDistanceForSceneRadius,
  cameraFarForMaxDistance,
  sceneFrameForCamera,
  sceneRadiusForCamera,
  type CameraBoundsBody,
  type CameraBoundsNode,
} from "../cameraBounds";

const EARTH_RADIUS_FROM_CATALOG_KM = catalogBodyRadiusKm("earth");
const LUNA_RADIUS_KM = catalogBodyRadiusKm("luna");
const EARTH_KM_PER_RENDER_UNIT = catalogEarthKmPerRenderUnit();

describe("scene-aware camera bounds", () => {
  it("keeps Earth-only scenes on the existing camera-distance floor", () => {
    const bodies: CameraBoundsBody[] = [
      { id: "earth", radiusKm: EARTH_RADIUS_FROM_CATALOG_KM, position: [0, 0, 0] },
    ];

    const maxDistance = cameraDistanceForSceneRadius(
      sceneRadiusForCamera(bodies, [], EARTH_KM_PER_RENDER_UNIT),
    );

    expect(maxDistance).toBe(CAMERA_MAX_DISTANCE);
  });

  it("expands the control and clip envelope for an Earth-Luna scene", () => {
    const moonDistanceRender = kmToRender(384_400, EARTH_KM_PER_RENDER_UNIT);
    const bodies: CameraBoundsBody[] = [
      { id: "earth", radiusKm: EARTH_RADIUS_FROM_CATALOG_KM, position: [0, 0, 0] },
      { id: "luna", radiusKm: LUNA_RADIUS_KM, position: [moonDistanceRender, 0, 0] },
    ];

    const maxDistance = cameraDistanceForSceneRadius(
      sceneRadiusForCamera(bodies, [], EARTH_KM_PER_RENDER_UNIT),
    );

    expect(maxDistance).toBeGreaterThan(CAMERA_MAX_DISTANCE);
    expect(cameraFarForMaxDistance(maxDistance)).toBeGreaterThan(maxDistance);
  });

  it("centers Frame All on the active multi-body scene, not Earth origin", () => {
    const moonDistanceRender = kmToRender(384_400, EARTH_KM_PER_RENDER_UNIT);
    const bodies: CameraBoundsBody[] = [
      { id: "earth", radiusKm: EARTH_RADIUS_FROM_CATALOG_KM, position: [0, 0, 0] },
      { id: "luna", radiusKm: LUNA_RADIUS_KM, position: [moonDistanceRender, 0, 0] },
    ];

    const frame = sceneFrameForCamera(bodies, [], EARTH_KM_PER_RENDER_UNIT);

    expect(frame.center[0]).toBeGreaterThan(0);
    expect(frame.center[0]).toBeLessThan(moonDistanceRender);
    expect(frame.radius).toBeGreaterThan(moonDistanceRender * 0.4);
  });

  it("includes high-altitude nodes when fitting a single-body scene", () => {
    const bodies: CameraBoundsBody[] = [
      { id: "earth", radiusKm: EARTH_RADIUS_FROM_CATALOG_KM, position: [0, 0, 0] },
    ];
    const nodes: CameraBoundsNode[] = [{ reference_body: "earth", alt_km: 35_786 }];

    const sceneRadius = sceneRadiusForCamera(bodies, nodes, EARTH_KM_PER_RENDER_UNIT);

    expect(sceneRadius).toBeCloseTo(
      kmToRender(EARTH_RADIUS_FROM_CATALOG_KM + 35_786, EARTH_KM_PER_RENDER_UNIT),
      6,
    );
    expect(cameraDistanceForSceneRadius(sceneRadius)).toBe(CAMERA_MAX_DISTANCE);
  });
});
