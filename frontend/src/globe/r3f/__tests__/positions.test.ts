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

  it("world equals local when no earth frame is set", () => {
    setNodeLocalPosition("sat-1", 1, 2, 3);
    const t = new THREE.Vector3();
    expect(getNodeWorldPosition("sat-1", t)).toBe(true);
    expect([t.x, t.y, t.z]).toEqual([1, 2, 3]);
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
