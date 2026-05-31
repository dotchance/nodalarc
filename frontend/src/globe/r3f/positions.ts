// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Shared node-position registry for the R3F scene — the migration linchpin, mirroring the legacy
 * positionLookup contract (getNodeLocalPosition / getNodeWorldPosition) but R3F-owned and
 * PER-BODY: every node carries the celestial body it lives in (earth | luna | mars ...), and each
 * <Body> registers its own local→world frame (setBodyFrame, via the Body's callback ref). A node's
 * world position is its body-local position through THAT body's world matrix, so an Earth satellite
 * and a lunar satellite resolve through different frames with no Earth assumption — the
 * parameterization the multi-body direction requires. Single-Earth sessions are unchanged: one
 * body ("earth"), one frame.
 *
 * <Constellation> writes each satellite's body-LOCAL position every frame; <GroundStation> writes
 * static GS positions; links, selection, labels, footprints, trails, orbits, and the camera actions
 * read from here. Only one globe is mounted at a time (the legacy globe behind ?legacy uses its own
 * positionCache), so this parallel store never coexists with it. Zero-allocation steady state:
 * stored Vector3s are mutated in place, callers pass a target.
 */

import * as THREE from "three";

interface NodeEntry {
  v: THREE.Vector3;
  /** Celestial body this node lives in — selects which frame maps it local→world. */
  body: string;
}

const localPositions = new Map<string, NodeEntry>();
const bodyFrames = new Map<string, THREE.Object3D>();

/** Register (or, with null, clear) a body's group — the frame its nodes map local→world through. */
export function setBodyFrame(bodyId: string, group: THREE.Object3D | null): void {
  if (group) bodyFrames.set(bodyId, group);
  else bodyFrames.delete(bodyId);
}

/** Upsert a node's body-local position + the body it lives in (zero-alloc after first sighting). */
export function setNodeLocalPosition(
  nodeId: string,
  body: string,
  x: number,
  y: number,
  z: number,
): void {
  const existing = localPositions.get(nodeId);
  if (existing) {
    existing.v.set(x, y, z);
    existing.body = body;
  } else {
    localPositions.set(nodeId, { v: new THREE.Vector3(x, y, z), body });
  }
}

/** Drop a node (e.g. a satellite that left the constellation). */
export function removeNode(nodeId: string): void {
  localPositions.delete(nodeId);
}

/** Clear all positions (session switch). Body frames persist (re-registered on remount). */
export function clearPositions(): void {
  localPositions.clear();
}

/** Fill `target` with the node's body-local position; false if unknown. */
export function getNodeLocalPosition(nodeId: string, target: THREE.Vector3): boolean {
  const e = localPositions.get(nodeId);
  if (!e) return false;
  target.copy(e.v);
  return true;
}

/**
 * Fill `target` with the node's world position (local through ITS BODY's world matrix), or return
 * false if it is not yet resolvable.
 *
 * CONTRACT — fail loud, never silently wrong: world position is UNAVAILABLE until the node's body
 * frame is registered (setBodyFrame). We deliberately do NOT fall back to the raw local coordinate
 * when the frame is missing: that local value is in a DIFFERENT frame than the renderer draws the
 * node in (a scene-graph child of the rotated body group), so handing it back would put every
 * world-frame consumer (labels, orbit rings, trails, selection, link-picking, camera) on a frame
 * the renderer doesn't share — invisibly in earth-fixed (rotation 0), but mirrored in
 * earth-inertial. Returning false makes consumers skip (a loud, obvious "absent") instead of
 * rendering a plausible-but-wrong position. The frame registers the moment its <Body> mounts.
 */
export function getNodeWorldPosition(nodeId: string, target: THREE.Vector3): boolean {
  const e = localPositions.get(nodeId);
  if (!e) return false;
  const frame = bodyFrames.get(e.body);
  if (!frame) return false;
  frame.updateWorldMatrix(true, false);
  target.copy(e.v);
  frame.localToWorld(target);
  return true;
}
