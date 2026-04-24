// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Unified position lookup for all scene nodes (satellites + ground stations).
//
// Satellites: reads from the positionCache Float32Array (populated by
// animateSatellites from either Worker or main-thread propagation).
// Ground stations: reads from the Sprite position (static, set once).
//
// ZERO-ALLOCATION: callers provide a pre-allocated target Vector3.

import * as THREE from "three";
import { getSatellites, getPositionCache } from "./satellites";
import { getGroundStations } from "./groundStations";

export let earthFrameRef: THREE.Object3D | null = null;

export function setEarthFrame(earthFrame: THREE.Object3D): void {
  earthFrameRef = earthFrame;
}

const _tmpLocal = new THREE.Vector3();

export function getNodeLocalPosition(nodeId: string, target: THREE.Vector3): boolean {
  const sat = getSatellites().get(nodeId);
  if (sat) {
    const cache = getPositionCache();
    const idx = sat.instanceIndex * 3;
    target.set(cache[idx]!, cache[idx + 1]!, cache[idx + 2]!);
    return true;
  }
  const gs = getGroundStations().get(nodeId);
  if (gs) {
    target.copy(gs.sprite.position);
    return true;
  }
  return false;
}

export function getNodeWorldPosition(nodeId: string, target: THREE.Vector3): boolean {
  if (!getNodeLocalPosition(nodeId, _tmpLocal)) return false;

  if (earthFrameRef) {
    target.copy(_tmpLocal);
    earthFrameRef.localToWorld(target);
  } else {
    target.copy(_tmpLocal);
  }
  return true;
}
