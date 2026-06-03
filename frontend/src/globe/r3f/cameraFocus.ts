// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import * as THREE from "three";
import { CAMERA_FOV } from "../../config";
import { EARTH_RADIUS_RENDER } from "./units";

const HALF_FOV_SIN = Math.sin((CAMERA_FOV * Math.PI) / 360);

export interface FocusFrame {
  center: THREE.Vector3;
  radius: number;
}

export function fitDistanceForRadius(radius: number, floor: number): number {
  if (radius <= 0) return floor;
  return Math.max(floor, (radius / Math.max(0.001, HALF_FOV_SIN)) * 1.35);
}

export function focusDistanceForFrame(
  frame: FocusFrame,
  floor = EARTH_RADIUS_RENDER * 2.5,
): number {
  return fitDistanceForRadius(frame.radius, floor);
}

export function cameraDirectionFromTarget(
  cameraPosition: THREE.Vector3,
  target: THREE.Vector3,
  out: THREE.Vector3,
): THREE.Vector3 {
  out.copy(cameraPosition).sub(target);
  if (out.lengthSq() < 1e-6) out.set(0, 0, 1);
  return out.normalize();
}

export function frameEndpoints(
  a: THREE.Vector3,
  b: THREE.Vector3,
  outCenter: THREE.Vector3,
): FocusFrame {
  outCenter.copy(a).add(b).multiplyScalar(0.5);
  return { center: outCenter, radius: Math.max(a.distanceTo(b) * 0.5, EARTH_RADIUS_RENDER * 0.08) };
}

export function framePoints(points: readonly THREE.Vector3[], outCenter: THREE.Vector3): FocusFrame | null {
  if (points.length === 0) return null;
  outCenter.set(0, 0, 0);
  for (const point of points) outCenter.add(point);
  outCenter.multiplyScalar(1 / points.length);
  let radius = 0;
  for (const point of points) radius = Math.max(radius, point.distanceTo(outCenter));
  return { center: outCenter, radius };
}
