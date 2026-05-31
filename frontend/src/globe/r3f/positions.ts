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

/**
 * Fill `target` with the node's world position (local through the body world matrix), or return
 * false if it is not yet resolvable.
 *
 * CONTRACT — fail loud, never silently wrong: world position is UNAVAILABLE until the body frame
 * is registered (setEarthFrame). We deliberately do NOT fall back to the raw local coordinate
 * when no frame is set: that local value is in a DIFFERENT frame than the one the satellite dots
 * render in (scene-graph children of the rotated body group), so handing it back would put every
 * world-frame consumer (labels, orbit rings, trails, selection, link-picking, camera) on a frame
 * the renderer doesn't share — invisibly in earth-fixed (rotation 0), but mirrored in
 * earth-inertial. Returning false makes consumers skip (a loud, obvious "absent") instead of
 * rendering a plausible-but-wrong position. The frame is registered the moment <Body> mounts, so
 * the unavailable window is the pre-mount frame where there are no positions to read anyway.
 */
export function getNodeWorldPosition(nodeId: string, target: THREE.Vector3): boolean {
  if (!earthFrame) return false;
  if (!getNodeLocalPosition(nodeId, _tmpLocal)) return false;
  earthFrame.updateWorldMatrix(true, false);
  target.copy(_tmpLocal);
  earthFrame.localToWorld(target);
  return true;
}
