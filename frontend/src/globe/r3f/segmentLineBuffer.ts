// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The GPU buffers of one dashed fat line of segments, rewritten in place every frame.
 *
 * three.js records how many instances an instanced geometry can draw the first time it renders
 * (WebGLBindingStates sets `_maxInstanceCount` once) and never draws more from that geometry.
 * So a geometry keeps a fixed capacity, draws `instanceCount` of it, and is replaced, never
 * grown, when a frame needs more segments.
 */

import * as THREE from "three";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";

const MIN_CAPACITY = 64;

export class SegmentLineBuffer {
  geometry!: LineSegmentsGeometry;
  /** Segments the current geometry holds. */
  capacity = 0;
  /** Segments written this frame. */
  count = 0;
  private positions!: Float32Array;
  private distances!: Float32Array;
  private positionBuffer!: THREE.InstancedInterleavedBuffer;
  private distanceBuffer!: THREE.InstancedInterleavedBuffer;

  constructor() {
    this.allocate(MIN_CAPACITY);
  }

  /** Start a frame of `segments` segments. True when the geometry was replaced to hold them. */
  begin(segments: number): boolean {
    this.count = 0;
    if (segments <= this.capacity) return false;
    const old = this.geometry;
    this.allocate(Math.max(segments, this.capacity * 2));
    old.dispose();
    return true;
  }

  push(a: THREE.Vector3, b: THREE.Vector3): void {
    if (this.count >= this.capacity) {
      throw new Error(`segment line holds ${this.capacity} segments; begin() sized it for fewer`);
    }
    const p = this.count * 6;
    this.positions[p] = a.x;
    this.positions[p + 1] = a.y;
    this.positions[p + 2] = a.z;
    this.positions[p + 3] = b.x;
    this.positions[p + 4] = b.y;
    this.positions[p + 5] = b.z;
    // Dash distances run on along the whole line, as LineSegments2.computeLineDistances does.
    const d = this.count * 2;
    const start = this.count === 0 ? 0 : this.distances[d - 1]!;
    this.distances[d] = start;
    this.distances[d + 1] = start + a.distanceTo(b);
    this.count++;
  }

  /** Finish the frame: draw the segments written, and upload them. */
  end(): void {
    this.geometry.instanceCount = this.count;
    this.positionBuffer.needsUpdate = true;
    this.distanceBuffer.needsUpdate = true;
  }

  dispose(): void {
    this.geometry.dispose();
  }

  private allocate(capacity: number): void {
    this.capacity = capacity;
    this.positions = new Float32Array(capacity * 6);
    this.distances = new Float32Array(capacity * 2);
    this.positionBuffer = new THREE.InstancedInterleavedBuffer(this.positions, 6, 1);
    this.distanceBuffer = new THREE.InstancedInterleavedBuffer(this.distances, 2, 1);
    const geometry = new LineSegmentsGeometry();
    geometry.setAttribute("instanceStart", new THREE.InterleavedBufferAttribute(this.positionBuffer, 3, 0));
    geometry.setAttribute("instanceEnd", new THREE.InterleavedBufferAttribute(this.positionBuffer, 3, 3));
    geometry.setAttribute(
      "instanceDistanceStart",
      new THREE.InterleavedBufferAttribute(this.distanceBuffer, 1, 0),
    );
    geometry.setAttribute(
      "instanceDistanceEnd",
      new THREE.InterleavedBufferAttribute(this.distanceBuffer, 1, 1),
    );
    // Unused capacity is zeros; the line is never culled (frustumCulled = false where it is
    // mounted), so fixed bounds stand in for computed ones.
    geometry.computeBoundingSphere = () => {};
    geometry.computeBoundingBox = () => {};
    geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 50000);
    geometry.boundingBox = new THREE.Box3(
      new THREE.Vector3(-50000, -50000, -50000),
      new THREE.Vector3(50000, 50000, 50000),
    );
    geometry.instanceCount = 0;
    this.geometry = geometry;
  }
}
