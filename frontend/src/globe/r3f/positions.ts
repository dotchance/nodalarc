// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Shared node-position registry for the R3F scene — the migration linchpin, mirroring
 * the legacy positionLookup contract (getNodeLocalPosition / getNodeWorldPosition) but
 * R3F-owned. <Constellation> writes each satellite's body-LOCAL position every frame (from
 * the same propagation the legacy globe uses); <GroundStation> writes static GS positions;
 * links, selection, labels, footprints, trails, orbits, and the camera actions all read
 * from here. Local positions are in the Earth body frame; world positions apply the body
 * group's world matrix (set via setEarthFrame), so consumers are correct under the
 * earth-fixed / earth-inertial frame rotation.
 *
 * Only one globe is mounted at a time (the ?r3f flag switches), so this parallel store
 * never coexists with the legacy positionCache; at cutover the legacy one is deleted.
 * Zero-allocation steady state: stored Vector3s are mutated in place, callers pass a target.
 */

import * as THREE from "three";

const localPositions = new Map<string, THREE.Vector3>();
let earthFrame: THREE.Object3D | null = null;

const _tmpLocal = new THREE.Vector3();

/** Register the Earth body group whose world matrix maps local → world positions. */
export function setEarthFrame(group: THREE.Object3D | null): void {
  earthFrame = group;
}

/** Upsert a node's body-local position (zero-alloc after the node's first sighting). */
export function setNodeLocalPosition(nodeId: string, x: number, y: number, z: number): void {
  const existing = localPositions.get(nodeId);
  if (existing) {
    existing.set(x, y, z);
  } else {
    localPositions.set(nodeId, new THREE.Vector3(x, y, z));
  }
}

/** Drop a node (e.g. a satellite that left the constellation). */
export function removeNode(nodeId: string): void {
  localPositions.delete(nodeId);
}

/** Clear all positions (session switch). */
export function clearPositions(): void {
  localPositions.clear();
}

/** Fill `target` with the node's body-local position; false if unknown. */
export function getNodeLocalPosition(nodeId: string, target: THREE.Vector3): boolean {
  const pos = localPositions.get(nodeId);
  if (!pos) return false;
  target.copy(pos);
  return true;
}

/** Fill `target` with the node's world position (local through the body world matrix). */
export function getNodeWorldPosition(nodeId: string, target: THREE.Vector3): boolean {
  if (!getNodeLocalPosition(nodeId, _tmpLocal)) return false;
  if (earthFrame) {
    earthFrame.updateWorldMatrix(true, false);
    target.copy(_tmpLocal);
    earthFrame.localToWorld(target);
  } else {
    target.copy(_tmpLocal);
  }
  return true;
}
