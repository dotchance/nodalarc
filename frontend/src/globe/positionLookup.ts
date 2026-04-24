// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Unified position lookup for all scene nodes (satellites + ground stations).
//
// All position consumers MUST use this API instead of accessing
// sat.mesh.position or gs.sprite.position directly. This decouples
// rendering (Mesh vs InstancedMesh vs Worker buffer) from the ~10
// modules that need node positions for links, selection, trails, etc.
//
// ZERO-ALLOCATION: callers provide a pre-allocated target Vector3.
// At 896+ lookups per frame (448 links × 2 endpoints × 60fps),
// returning new Vector3 objects would create 53K+ allocations/sec.

import * as THREE from "three";
import { getSatellites } from "./satellites";
import { getGroundStations } from "./groundStations";

export let earthFrameRef: THREE.Object3D | null = null;

export function setEarthFrame(earthFrame: THREE.Object3D): void {
  earthFrameRef = earthFrame;
}

export function getNodeLocalPosition(nodeId: string, target: THREE.Vector3): boolean {
  const sat = getSatellites().get(nodeId);
  if (sat) {
    target.copy(sat.mesh.position);
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
  const sat = getSatellites().get(nodeId);
  if (sat) {
    sat.mesh.getWorldPosition(target);
    return true;
  }
  const gs = getGroundStations().get(nodeId);
  if (gs) {
    gs.sprite.getWorldPosition(target);
    return true;
  }
  return false;
}
