// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
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
 * read from here. R3F is the production globe owner; the older positionCache utilities remain
 * as shared math/test helpers until the segment-frame registry fully replaces them. Zero-allocation steady state:
 * stored Vector3s are mutated in place, callers pass a target.
 */

import * as THREE from "three";
import type { BodySphere } from "./linkCurve";

interface NodeEntry {
  v: THREE.Vector3;
  /** Celestial body this node lives in — selects which frame maps it local→world. */
  body: string;
}

const localPositions = new Map<string, NodeEntry>();
const bodyFrames = new Map<string, THREE.Object3D>();
const bodyRadii = new Map<string, number>();

/** Register (or, with null, clear) a body's group — the frame its nodes map local→world through. */
export function setBodyFrame(
  bodyId: string,
  group: THREE.Object3D | null,
  radiusRender?: number,
): void {
  if (group) {
    bodyFrames.set(bodyId, group);
    if (radiusRender !== undefined) bodyRadii.set(bodyId, radiusRender);
  } else {
    bodyFrames.delete(bodyId);
    bodyRadii.delete(bodyId);
  }
}

/** Upsert a node's body-local position + the body it lives in (zero-alloc after first sighting). */
/** The registered group that holds a body's body-fixed frame. */
export function getBodyFrame(bodyId: string): THREE.Object3D | undefined {
  return bodyFrames.get(bodyId);
}

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
/**
 * Transform a body-local scene point into world space through the registered
 * body frame group. Same absent-frame contract as getNodeWorldPosition:
 * returns false (caller skips) rather than handing back a wrong-frame value.
 */
export function bodyLocalToWorld(bodyId: string, target: THREE.Vector3): boolean {
  const frame = bodyFrames.get(bodyId);
  if (!frame) return false;
  frame.updateWorldMatrix(true, false);
  frame.localToWorld(target);
  return true;
}

/**
 * Put an object at a body's world centre, keeping its own axes: overlays stored relative to a
 * body's centre (orbit rings) follow the body as it moves in a world centred on another body.
 * With the body's frame not registered the object is hidden, never left at a stale place.
 */
export function placeAtBodyCenter(object: THREE.Object3D | null, bodyId: string): void {
  if (!object) return;
  const placed = bodyLocalToWorld(bodyId, object.position.set(0, 0, 0));
  object.visible = placed;
}

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

/**
 * Resolve the body sphere that owns a node. Returns false until the node's frame and body radius
 * are both registered; callers should skip occlusion rather than assume Earth.
 */
export function getNodeBodySphere(
  nodeId: string,
  centerTarget: THREE.Vector3,
): { body: string; radius: number } | null {
  const e = localPositions.get(nodeId);
  if (!e) return null;
  const frame = bodyFrames.get(e.body);
  const radius = bodyRadii.get(e.body);
  if (!frame || radius === undefined) return null;
  frame.updateWorldMatrix(true, false);
  centerTarget.set(0, 0, 0);
  frame.localToWorld(centerTarget);
  return { body: e.body, radius };
}

/** Resolve a body's world-space center and render radius. */
export function getBodyWorldSphere(
  bodyId: string,
  centerTarget: THREE.Vector3,
): { body: string; radius: number } | null {
  const frame = bodyFrames.get(bodyId);
  const radius = bodyRadii.get(bodyId);
  if (!frame || radius === undefined) return null;
  frame.updateWorldMatrix(true, false);
  centerTarget.set(0, 0, 0);
  frame.localToWorld(centerTarget);
  return { body: bodyId, radius };
}

/**
 * A move of the world's centre: the render origin body changed (bodyPose.ts says why the world
 * is centred on one body), so every world coordinate held across frames is off by the move.
 * FrameDriver announces the move on the frame that first places bodies relative to the new
 * origin, before anything reads or draws them; each holder corrects itself in the same frame
 * (the camera rig moves by it, cached orbit rings move their group, trails drop their history).
 */
type WorldShiftListener = (shift: THREE.Vector3) => void;
const worldShiftListeners = new Set<WorldShiftListener>();

/** Listen for moves of the world's centre; returns the unsubscribe. */
export function onWorldShift(listener: WorldShiftListener): () => void {
  worldShiftListeners.add(listener);
  return () => {
    worldShiftListeners.delete(listener);
  };
}

/** Announce a move of the world's centre (render units, new origin minus old). */
export function shiftWorld(shift: THREE.Vector3): void {
  for (const listener of worldShiftListeners) listener(shift);
}

const _bodySpheres: BodySphere[] = [];

/** Every registered body's world-space center and render radius (reused array; read, don't keep). */
export function bodyWorldSpheres(): readonly BodySphere[] {
  let count = 0;
  for (const bodyId of bodyRadii.keys()) {
    const sphere = (_bodySpheres[count] ??= { center: new THREE.Vector3(), radius: 0 });
    const found = getBodyWorldSphere(bodyId, sphere.center);
    if (found === null) continue;
    sphere.radius = found.radius;
    count++;
  }
  _bodySpheres.length = count;
  return _bodySpheres;
}
