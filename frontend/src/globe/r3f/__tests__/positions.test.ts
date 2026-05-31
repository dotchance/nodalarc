// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The R3F position registry: the per-body local/world position oracle that links,
 *  selection, labels, and the camera read. Mirrors the legacy positionLookup contract. */

import { describe, it, expect, beforeEach } from "vitest";
import * as THREE from "three";
import {
  clearPositions,
  getNodeLocalPosition,
  getNodeWorldPosition,
  removeNode,
  setBodyFrame,
  setNodeLocalPosition,
} from "../positions";

describe("r3f position registry (per-body)", () => {
  beforeEach(() => {
    clearPositions();
    setBodyFrame("earth", null);
    setBodyFrame("luna", null);
  });

  it("roundtrips a local position", () => {
    setNodeLocalPosition("sat-1", "earth", 10, 20, 30);
    const t = new THREE.Vector3();
    expect(getNodeLocalPosition("sat-1", t)).toBe(true);
    expect([t.x, t.y, t.z]).toEqual([10, 20, 30]);
  });

  it("returns false for an unknown node (local and world)", () => {
    expect(getNodeLocalPosition("nope", new THREE.Vector3())).toBe(false);
    expect(getNodeWorldPosition("nope", new THREE.Vector3())).toBe(false);
  });

  // CONTRACT GUARD (regression for the Suspense-race frame bug): world position is UNAVAILABLE —
  // not a silent raw-local fallback — until the node's BODY frame is registered.
  it("world position is UNAVAILABLE (false) until the node's body frame is registered", () => {
    setNodeLocalPosition("sat-1", "earth", 1, 2, 3);
    expect(getNodeWorldPosition("sat-1", new THREE.Vector3())).toBe(false);
    setBodyFrame("earth", new THREE.Group());
    expect(getNodeWorldPosition("sat-1", new THREE.Vector3())).toBe(true);
  });

  it("applies the body-frame rotation for world position", () => {
    const g = new THREE.Group();
    g.rotation.y = Math.PI / 2;
    setBodyFrame("earth", g);
    setNodeLocalPosition("sat-1", "earth", 1, 0, 0); // local +X
    const t = new THREE.Vector3();
    getNodeWorldPosition("sat-1", t);
    // R_y(90°) in three.js maps +X -> -Z.
    expect(t.x).toBeCloseTo(0);
    expect(t.y).toBeCloseTo(0);
    expect(t.z).toBeCloseTo(-1);
  });

  // INVARIANT: a world-frame consumer must see EXACTLY the position the renderer draws (a child of
  // the registered body group). getNodeWorldPosition == group.localToWorld for any rotation.
  it("world position equals what the renderer's body-child would compute (consumer/renderer agreement)", () => {
    for (const rotY of [0, Math.PI / 3, Math.PI, -1.2]) {
      const g = new THREE.Group();
      g.rotation.y = rotY;
      setBodyFrame("earth", g);
      setNodeLocalPosition("sat-x", "earth", 3, -4, 5);
      const fromRegistry = new THREE.Vector3();
      expect(getNodeWorldPosition("sat-x", fromRegistry)).toBe(true);
      g.updateWorldMatrix(true, false);
      const fromRenderer = g.localToWorld(new THREE.Vector3(3, -4, 5));
      expect(fromRegistry.x).toBeCloseTo(fromRenderer.x);
      expect(fromRegistry.y).toBeCloseTo(fromRenderer.y);
      expect(fromRegistry.z).toBeCloseTo(fromRenderer.z);
    }
  });

  // MULTI-BODY: two bodies, two frames — each node resolves through ITS OWN body's frame, no Earth
  // assumption. This is the parameterization the multi-body direction requires.
  it("resolves each node through its own body's frame (earth vs luna)", () => {
    const earth = new THREE.Group();
    earth.rotation.y = Math.PI; // +X -> -X
    earth.updateWorldMatrix(true, false);
    const luna = new THREE.Group();
    luna.position.set(100, 0, 0); // a body offset in the universe frame
    luna.updateWorldMatrix(true, false);
    setBodyFrame("earth", earth);
    setBodyFrame("luna", luna);

    setNodeLocalPosition("earth-sat", "earth", 5, 0, 0);
    setNodeLocalPosition("luna-sat", "luna", 5, 0, 0);

    const e = new THREE.Vector3();
    const l = new THREE.Vector3();
    getNodeWorldPosition("earth-sat", e);
    getNodeWorldPosition("luna-sat", l);
    expect(e.x).toBeCloseTo(-5); // earth's 180° rotation
    expect(l.x).toBeCloseTo(105); // luna offset (100) + local 5, no rotation
  });

  it("a node whose body frame is not registered is unavailable even if other bodies are", () => {
    setBodyFrame("earth", new THREE.Group());
    setNodeLocalPosition("luna-sat", "luna", 1, 2, 3); // luna frame NOT registered
    expect(getNodeWorldPosition("luna-sat", new THREE.Vector3())).toBe(false);
  });

  it("removes a node and clears all", () => {
    setNodeLocalPosition("a", "earth", 1, 1, 1);
    setNodeLocalPosition("b", "earth", 2, 2, 2);
    removeNode("a");
    expect(getNodeLocalPosition("a", new THREE.Vector3())).toBe(false);
    expect(getNodeLocalPosition("b", new THREE.Vector3())).toBe(true);
    clearPositions();
    expect(getNodeLocalPosition("b", new THREE.Vector3())).toBe(false);
  });

  it("updates a node's position in place", () => {
    setNodeLocalPosition("s", "earth", 1, 1, 1);
    setNodeLocalPosition("s", "earth", 9, 9, 9);
    const t = new THREE.Vector3();
    getNodeLocalPosition("s", t);
    expect([t.x, t.y, t.z]).toEqual([9, 9, 9]);
  });
});
