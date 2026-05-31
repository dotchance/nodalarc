// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The R3F position registry: the shared local/world position oracle that links,
 *  selection, labels, and the camera read. Mirrors the legacy positionLookup contract. */

import { describe, it, expect, beforeEach } from "vitest";
import * as THREE from "three";
import {
  clearPositions,
  getNodeLocalPosition,
  getNodeWorldPosition,
  removeNode,
  setEarthFrame,
  setNodeLocalPosition,
} from "../positions";

describe("r3f position registry", () => {
  beforeEach(() => {
    clearPositions();
    setEarthFrame(null);
  });

  it("roundtrips a local position", () => {
    setNodeLocalPosition("sat-1", 10, 20, 30);
    const t = new THREE.Vector3();
    expect(getNodeLocalPosition("sat-1", t)).toBe(true);
    expect([t.x, t.y, t.z]).toEqual([10, 20, 30]);
  });

  it("returns false for an unknown node (local and world)", () => {
    expect(getNodeLocalPosition("nope", new THREE.Vector3())).toBe(false);
    expect(getNodeWorldPosition("nope", new THREE.Vector3())).toBe(false);
  });

  // CONTRACT GUARD (regression for the Suspense-race frame bug): world position must be
  // UNAVAILABLE — not a silent raw-local fallback — until the body frame is registered. The raw
  // local coord is in a different frame than the satellite dots render in (children of the
  // rotated body group), so returning it would diverge from the renderer (mirrored in
  // earth-inertial). False makes consumers skip loudly instead of drawing in the wrong frame.
  it("world position is UNAVAILABLE (false) until the earth frame is registered — never a silent local fallback", () => {
    setNodeLocalPosition("sat-1", 1, 2, 3);
    // No setEarthFrame yet (beforeEach set it null) → unresolvable, not (1,2,3).
    expect(getNodeWorldPosition("sat-1", new THREE.Vector3())).toBe(false);
    // Once a frame is registered it resolves.
    setEarthFrame(new THREE.Group());
    expect(getNodeWorldPosition("sat-1", new THREE.Vector3())).toBe(true);
  });

  it("applies the earth-frame rotation for world position", () => {
    const g = new THREE.Group();
    g.rotation.y = Math.PI / 2;
    setEarthFrame(g);
    setNodeLocalPosition("sat-1", 1, 0, 0); // local +X
    const t = new THREE.Vector3();
    getNodeWorldPosition("sat-1", t);
    // R_y(90°) in three.js maps +X -> -Z.
    expect(t.x).toBeCloseTo(0);
    expect(t.y).toBeCloseTo(0);
    expect(t.z).toBeCloseTo(-1);
  });

  // INVARIANT (the heart of the bug class): a world-frame consumer must see EXACTLY the position
  // the renderer draws the dot at. The dots are scene-graph children of the registered body
  // group, so their world position is group.localToWorld(local). getNodeWorldPosition must equal
  // that for any frame rotation — otherwise consumers (labels/orbits/...) and dots disagree.
  it("world position equals what the renderer's body-child would compute (consumer/renderer agreement)", () => {
    for (const rotY of [0, Math.PI / 3, Math.PI, -1.2]) {
      const g = new THREE.Group();
      g.rotation.y = rotY;
      setEarthFrame(g);
      setNodeLocalPosition("sat-x", 3, -4, 5);
      const fromRegistry = new THREE.Vector3();
      expect(getNodeWorldPosition("sat-x", fromRegistry)).toBe(true);
      // What the renderer (a child of the body group) ends up at:
      g.updateWorldMatrix(true, false);
      const fromRenderer = g.localToWorld(new THREE.Vector3(3, -4, 5));
      expect(fromRegistry.x).toBeCloseTo(fromRenderer.x);
      expect(fromRegistry.y).toBeCloseTo(fromRenderer.y);
      expect(fromRegistry.z).toBeCloseTo(fromRenderer.z);
    }
  });

  // The reason the silent fallback was dangerous: under a rotated frame, world != local, so a
  // consumer that received raw local would be visibly off (this is what produced the mirror).
  it("world position diverges from local under a rotated frame (why the silent fallback was wrong)", () => {
    const g = new THREE.Group();
    g.rotation.y = Math.PI; // 180° → +X maps to -X: a mirror
    setEarthFrame(g);
    setNodeLocalPosition("sat-1", 5, 0, 0);
    const world = new THREE.Vector3();
    getNodeWorldPosition("sat-1", world);
    const local = new THREE.Vector3();
    getNodeLocalPosition("sat-1", local);
    expect(world.x).toBeCloseTo(-5); // rotated
    expect(local.x).toBe(5); // raw — what the old fallback would have leaked
    expect(world.x).not.toBeCloseTo(local.x);
  });

  it("removes a node and clears all", () => {
    setNodeLocalPosition("a", 1, 1, 1);
    setNodeLocalPosition("b", 2, 2, 2);
    removeNode("a");
    expect(getNodeLocalPosition("a", new THREE.Vector3())).toBe(false);
    expect(getNodeLocalPosition("b", new THREE.Vector3())).toBe(true);
    clearPositions();
    expect(getNodeLocalPosition("b", new THREE.Vector3())).toBe(false);
  });

  it("updates a node's position in place", () => {
    setNodeLocalPosition("s", 1, 1, 1);
    setNodeLocalPosition("s", 9, 9, 9);
    const t = new THREE.Vector3();
    getNodeLocalPosition("s", t);
    expect([t.x, t.y, t.z]).toEqual([9, 9, 9]);
  });
});
