// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** A trace line draws every segment it was given, however much the line grows. three.js fixes a
 *  geometry's drawable instances when it first renders, so growth must come from a new geometry. */

import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { SegmentLineBuffer } from "../segmentLineBuffer";

function frame(buffer: SegmentLineBuffer, segments: number): void {
  buffer.begin(segments);
  for (let i = 0; i < segments; i++) {
    buffer.push(new THREE.Vector3(i, 0, 0), new THREE.Vector3(i + 1, 0, 0));
  }
  buffer.end();
}

/** Instances a geometry can ever draw: what it held when first rendered. */
function drawable(geometry: THREE.InstancedBufferGeometry): number {
  return Math.min(geometry.instanceCount, geometry.getAttribute("instanceStart").count);
}

describe("SegmentLineBuffer", () => {
  it("draws all segments of a line that grows past what its first frame held", () => {
    const buffer = new SegmentLineBuffer();
    frame(buffer, 3);
    const first = buffer.geometry;
    let disposed = false;
    first.addEventListener("dispose", () => {
      disposed = true;
    });

    frame(buffer, 500);

    expect(buffer.geometry).not.toBe(first);
    expect(disposed).toBe(true);
    expect(drawable(buffer.geometry)).toBe(500);
  });

  it("keeps its geometry and draws fewer segments when the line shrinks", () => {
    const buffer = new SegmentLineBuffer();
    frame(buffer, 40);
    const geometry = buffer.geometry;
    frame(buffer, 5);
    expect(buffer.geometry).toBe(geometry);
    expect(drawable(geometry)).toBe(5);
  });

  it("runs dash distances on along the whole line", () => {
    const buffer = new SegmentLineBuffer();
    frame(buffer, 3);
    const start = buffer.geometry.getAttribute("instanceDistanceStart");
    const end = buffer.geometry.getAttribute("instanceDistanceEnd");
    expect([0, 1, 2].map((i) => [start.getX(i), end.getX(i)])).toEqual([
      [0, 1],
      [1, 2],
      [2, 3],
    ]);
  });
});
