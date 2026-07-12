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

export interface AvoidSphere {
  center: THREE.Vector3;
  radius: number;
}

/**
 * End position for a focus flight: approach from the current camera side,
 * but never end inside the avoid sphere. A node on a body's far side would
 * otherwise put the camera underground (direction toward the old camera
 * passes through the body and the node-focus distance is short); when that
 * happens, approach along the surface normal at the target instead.
 */
export function flightEndPosition(
  target: THREE.Vector3,
  cameraPosition: THREE.Vector3,
  dist: number,
  avoid: AvoidSphere | null,
  out: THREE.Vector3,
): THREE.Vector3 {
  cameraDirectionFromTarget(cameraPosition, target, out);
  out.multiplyScalar(dist).add(target);
  if (avoid && out.distanceTo(avoid.center) < avoid.radius * 1.05) {
    const normal = target.clone().sub(avoid.center);
    if (normal.lengthSq() > 1e-6) {
      normal.normalize();
      out.copy(target).addScaledVector(normal, dist);
    }
  }
  return out;
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
