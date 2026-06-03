// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Scene-aware camera bounds.
 *
 * The legacy fixed max zoom was sized for Earth-orbit shells. Multi-body scenes
 * can place bodies thousands of render units apart at the same truth-preserving
 * km scale, so controls must derive their envelope from the active scene facts.
 */

import { CAMERA_FOV, CAMERA_MAX_DISTANCE } from "../../config";
import { EARTH_RADIUS_KM, kmToRender } from "./units";

export interface CameraBoundsBody {
  id: string;
  radiusKm: number;
  position: readonly [number, number, number];
}

export interface CameraBoundsNode {
  reference_body?: string | null;
  alt_km?: number | null;
}

function length3(v: readonly [number, number, number]): number {
  return Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
}

export function sceneRadiusForCamera(
  bodies: readonly CameraBoundsBody[],
  nodes: readonly CameraBoundsNode[],
): number {
  const bodyRadiusKm = new Map<string, number>();
  const bodyCenter = new Map<string, number>();
  let radius = kmToRender(EARTH_RADIUS_KM);

  for (const body of bodies) {
    const center = length3(body.position);
    const bodyRadius = kmToRender(body.radiusKm);
    bodyRadiusKm.set(body.id, body.radiusKm);
    bodyCenter.set(body.id, center);
    radius = Math.max(radius, center + bodyRadius);
  }

  for (const node of nodes) {
    const bodyId = node.reference_body ?? "earth";
    const center = bodyCenter.get(bodyId);
    if (center === undefined) continue;
    const surfaceKm = bodyRadiusKm.get(bodyId) ?? EARTH_RADIUS_KM;
    const altitudeKm = Math.max(0, node.alt_km ?? 0);
    radius = Math.max(radius, center + kmToRender(surfaceKm + altitudeKm));
  }

  return radius;
}

export function cameraDistanceForSceneRadius(
  sceneRadius: number,
  {
    fovDeg = CAMERA_FOV,
    floor = CAMERA_MAX_DISTANCE,
    margin = 1.25,
  }: { fovDeg?: number; floor?: number; margin?: number } = {},
): number {
  const halfFovRad = (fovDeg * Math.PI) / 360;
  const fitDistance = sceneRadius / Math.max(0.001, Math.sin(halfFovRad));
  return Math.max(floor, fitDistance * margin);
}

export function cameraFarForMaxDistance(maxDistance: number): number {
  return Math.max(10000, maxDistance * 4);
}
