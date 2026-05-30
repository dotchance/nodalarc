// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** LinkBatch buffer behaviour: ISL = 16-segment bowed arc, ground = 1 straight segment,
 *  NaN = hidden (inactive / toggled-off / unresolved), classification by gs- prefix. The
 *  injectable position source lets us assert exact buffer contents without a renderer. */

import { describe, it, expect, beforeEach } from "vitest";
import * as THREE from "three";
import { LinkBatch } from "../linkBatch";
import type { LinkState } from "../../../types";

const POS: Record<string, [number, number, number]> = {
  "sat-a": [100, 0, 0],
  "sat-b": [0, 100, 0],
  "gs-x": [70, 70, 10],
};
const getPos = (id: string, t: THREE.Vector3): boolean => {
  const p = POS[id];
  if (!p) return false;
  t.set(p[0], p[1], p[2]);
  return true;
};

function link(node_a: string, node_b: string, state: string): LinkState {
  return {
    node_a,
    node_b,
    state,
    link_type: null,
    link_reason: null,
    latency_ms: 1,
    bandwidth_mbps: 1,
    range_km: 1,
    traffic_load_pct: null,
    interface_a: "a",
    interface_b: "b",
  };
}

function segIsNaN(buf: Float32Array, segIndex: number, segCount: number): boolean {
  const start = segIndex * 6;
  for (let i = start; i < start + segCount * 6; i++) if (!Number.isNaN(buf[i])) return false;
  return true;
}
function segIsFinite(buf: Float32Array, segIndex: number, segCount: number): boolean {
  const start = segIndex * 6;
  for (let i = start; i < start + segCount * 6; i++) if (!Number.isFinite(buf[i])) return false;
  return true;
}

describe("LinkBatch", () => {
  let parent: THREE.Group;
  let batch: LinkBatch;
  beforeEach(() => {
    parent = new THREE.Group();
    batch = new LinkBatch(getPos);
  });

  it("classifies links: ISL = 16 segments, ground (gs- prefix) = 1 segment", () => {
    batch.update([link("sat-a", "sat-b", "active"), link("gs-x", "sat-a", "active")], parent, 0);
    expect(batch._debugEntry("sat-a", "sat-b")?.segmentCount).toBe(16);
    expect(batch._debugEntry("gs-x", "sat-a")?.segmentCount).toBe(1);
  });

  it("renders an active ISL as finite bowed segments and an inactive link as NaN", () => {
    batch.update([link("sat-a", "sat-b", "active"), link("sat-a", "gs-x", "inactive")], parent, 0);
    batch.animate(true, true, 0);
    const buf = batch._debugPositions()!;
    const isl = batch._debugEntry("sat-a", "sat-b")!;
    const inactive = batch._debugEntry("sat-a", "gs-x")!;
    expect(segIsFinite(buf, isl.bufferIndex, 16)).toBe(true);
    expect(segIsNaN(buf, inactive.bufferIndex, 1)).toBe(true);
  });

  it("hides ISL links when showIslLinks is false but keeps ground links", () => {
    batch.update([link("sat-a", "sat-b", "active"), link("gs-x", "sat-a", "active")], parent, 0);
    batch.animate(false, true, 0);
    const buf = batch._debugPositions()!;
    const isl = batch._debugEntry("sat-a", "sat-b")!;
    const gnd = batch._debugEntry("gs-x", "sat-a")!;
    expect(segIsNaN(buf, isl.bufferIndex, 16)).toBe(true);
    expect(segIsFinite(buf, gnd.bufferIndex, 1)).toBe(true);
  });

  it("hides a link whose endpoint position is unresolved", () => {
    batch.update([link("sat-a", "sat-unknown", "active")], parent, 0);
    batch.animate(true, true, 0);
    const buf = batch._debugPositions()!;
    const e = batch._debugEntry("sat-a", "sat-unknown")!;
    expect(segIsNaN(buf, e.bufferIndex, 16)).toBe(true);
  });

  it("a link that disappears from the snapshot flashes then hides past the fade window", () => {
    batch.update([link("sat-a", "sat-b", "active")], parent, 0);
    batch.animate(true, true, 0);
    // Disappears -> failing at t=1000.
    batch.update([], parent, 1000);
    expect(batch._debugEntry("sat-a", "sat-b")?.state).toBe("failing");
    // Still within hold+fade (1500+1000) -> still drawn.
    batch.animate(true, true, 1000 + 2000);
    expect(segIsFinite(batch._debugPositions()!, batch._debugEntry("sat-a", "sat-b")!.bufferIndex, 16)).toBe(true);
    // Past the fade window -> hidden + inactive.
    batch.animate(true, true, 1000 + 2600);
    expect(batch._debugEntry("sat-a", "sat-b")?.state).toBe("inactive");
    expect(segIsNaN(batch._debugPositions()!, batch._debugEntry("sat-a", "sat-b")!.bufferIndex, 16)).toBe(true);
  });
});
