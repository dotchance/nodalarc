// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** LinkBatch buffer behaviour: ISL = 16-segment bowed arc, ground = 1 straight segment,
 *  NaN = hidden (inactive / toggled-off / unresolved), classification by link_type. The
 *  injectable position source lets us assert exact buffer contents without a renderer. */

import { describe, it, expect, beforeEach } from "vitest";
import * as THREE from "three";
import { LinkBatch } from "../linkBatch";
import type { LinkState } from "../../../types";

const POS: Record<string, [number, number, number]> = {
  "sat-a": [100, 0, 0],
  "sat-b": [0, 100, 0],
  "ground-gs-x": [70, 70, 10],
};
const getPos = (id: string, t: THREE.Vector3): boolean => {
  const p = POS[id];
  if (p) {
    t.set(p[0], p[1], p[2]);
    return true;
  }
  const dynamic = /^sat-(\d+)$/.exec(id);
  if (!dynamic) return false;
  const idx = Number(dynamic[1]);
  const angle = (idx * Math.PI * 2) / 221;
  t.set(100 * Math.cos(angle), 100 * Math.sin(angle), idx % 17);
  return true;
};

function link(
  node_a: string,
  node_b: string,
  state: string,
  link_type: LinkState["link_type"] = "intra_plane_isl",
): LinkState {
  return {
    node_a,
    node_b,
    state,
    link_type,
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

  it("classifies links: ISL = 16 segments, ground link_type = 1 segment", () => {
    batch.update([
      link("sat-a", "sat-b", "active"),
      link("ground-gs-x", "sat-a", "active", "ground"),
    ], parent, 0);
    expect(batch._debugEntry("sat-a", "sat-b")?.segmentCount).toBe(16);
    expect(batch._debugEntry("ground-gs-x", "sat-a")?.segmentCount).toBe(1);
  });

  it("renders an active ISL as finite bowed segments and an inactive link as NaN", () => {
    batch.update([
      link("sat-a", "sat-b", "active"),
      link("sat-a", "ground-gs-x", "inactive", "ground"),
    ], parent, 0);
    batch.animate(true, true, 0);
    const buf = batch._debugPositions()!;
    const isl = batch._debugEntry("sat-a", "sat-b")!;
    const inactive = batch._debugEntry("sat-a", "ground-gs-x")!;
    expect(segIsFinite(buf, isl.bufferIndex, 16)).toBe(true);
    expect(segIsNaN(buf, inactive.bufferIndex, 1)).toBe(true);
  });

  it("hides ISL links when showIslLinks is false but keeps ground links", () => {
    batch.update([
      link("sat-a", "sat-b", "active"),
      link("ground-gs-x", "sat-a", "active", "ground"),
    ], parent, 0);
    batch.animate(false, true, 0);
    const buf = batch._debugPositions()!;
    const isl = batch._debugEntry("sat-a", "sat-b")!;
    const gnd = batch._debugEntry("ground-gs-x", "sat-a")!;
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

  it("renders a kernel-proven link full color and an unproven one dimmed", () => {
    batch.update([link("sat-a", "sat-b", "active")], parent, 0);
    const e = batch._debugEntry("sat-a", "sat-b")!;
    // Proven (key in the kernel-actual set) -> full ISL color.
    batch.animate(true, true, 0, new Set(["sat-a:sat-b"]));
    const provenR = batch._debugColors()![e.bufferIndex * 6] ?? 0;
    // OME-active but NOT kernel-proven -> dimmed.
    batch.animate(true, true, 0, new Set());
    const dimR = batch._debugColors()![e.bufferIndex * 6] ?? 0;
    expect(provenR).toBeGreaterThan(0);
    expect(dimR).toBeCloseTo(provenR * 0.35, 5);
    // null gate (legacy) -> full color, no dimming.
    batch.animate(true, true, 0, null);
    expect(batch._debugColors()![e.bufferIndex * 6] ?? 0).toBeCloseTo(provenR, 5);
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

  it("grows beyond the initial ISL headroom without dropping later links", () => {
    batch.update([], parent, 0);
    const links = Array.from({ length: 220 }, (_, i) =>
      link(`sat-${i}`, `sat-${i + 1}`, "active"),
    );

    batch.update(links, parent, 1000);
    batch.animate(true, true, 1000);

    const first = batch._debugEntry("sat-0", "sat-1")!;
    const last = batch._debugEntry("sat-219", "sat-220")!;
    expect(first.segmentCount).toBe(16);
    expect(last.segmentCount).toBe(16);
    expect(segIsFinite(batch._debugPositions()!, first.bufferIndex, 16)).toBe(true);
    expect(segIsFinite(batch._debugPositions()!, last.bufferIndex, 16)).toBe(true);
  });

});
