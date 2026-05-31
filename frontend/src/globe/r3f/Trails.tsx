// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Trails — every satellite's orbital trail batched into ONE THREE.LineSegments draw call,
 * a faithful R3F port of globe/orbitalTrails.ts. Each satellite owns a TRAIL_LENGTH ring
 * buffer of disconnected segment pairs in pre-allocated Float32 position+color buffers
 * (NaN positions = hidden, vertex colors carry the newest→oldest brightness fade). The
 * trail is ARC-capped (MAX_ARC_LENGTH, time-independent) so it stays a constant visual
 * size regardless of playback speed; additive blending makes it glow. Zero per-frame heap
 * allocation: samples are written straight into the buffers, no Array()/{x,y,z}.
 *
 * Scene-root (world frame): trails are sampled from getNodeWorldPosition so a recorded path
 * stays put in world space while the Earth frame rotates underneath it — matching the legacy
 * orbitalTrails.ts, which read positionLookup.getNodeWorldPosition and added its LineSegments
 * straight to the scene. (A body-local trail would smear under earth-fixed rotation.)
 *
 * Satellite set: the same NodeState[] that seeds <Constellation>; we filter node_type ===
 * "satellite" so the ids line up exactly with the registry <Constellation> populates each
 * frame. Buffers grow (preserving history) when the satellite count rises.
 *
 * Backgrounded-tab guard: if the wall-clock frame delta exceeds MAX_FRAME_DELTA_S we SKIP
 * the frame (do not extend trails), reproducing GlobeView's `skipTrails = dt > 0.15`. We
 * track our own performance.now() delta rather than R3F's clamped `delta` so the threshold
 * sees the true gap (R3F clamps its internal clock delta, which would mask a long stall).
 *
 * Runs at DEFAULT useFrame priority (0): after FrameDriver(-2) sets the Earth-frame rotation
 * and <Constellation>(-1) writes this frame's positions, so the world positions we read are
 * current.
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import type { NodeState } from "../../types";
import { getNodeWorldPosition } from "./positions";

// --- Verbatim constants from globe/orbitalTrails.ts ---
const TRAIL_LENGTH = 600;
const SAMPLE_EVERY = 2;
const MAX_ARC_LENGTH = 1.8;
const TRAIL_COLOR = new THREE.Color(0x6699dd);
/** Wall-clock frame-delta (s) above which we skip extending trails (backgrounded tab). */
const MAX_FRAME_DELTA_S = 0.15;

// --- Per-satellite ring buffer metadata (plain numbers, no three.js objects) ---
interface TrailMeta {
  head: number;
  count: number;
  frame: number;
  prevX: number;
  prevY: number;
  prevZ: number;
  hasPrev: boolean;
}

// Module-scope temporary; the registry fills it in place (zero per-frame alloc).
const _trailWorldPos = new THREE.Vector3();

/**
 * The batched-trail object + buffer state, factored into an instance-scoped class (mirroring
 * LinkBatch) so the imperative ring-buffer/arc-fade algorithm — ported byte-for-byte from
 * orbitalTrails.ts — lives in one place and the React component only owns lifecycle. The
 * THREE.LineSegments is created lazily and added to the supplied parent (like LinkBatch),
 * so R3F never co-owns the geometry/material and there is no placeholder to dispose.
 */
class TrailBatch {
  private readonly trailMetas = new Map<string, TrailMeta>();
  private readonly satIndexMap = new Map<string, number>();
  private batch: THREE.LineSegments | null = null;
  private geometry: THREE.BufferGeometry | null = null;
  private material: THREE.LineBasicMaterial | null = null;
  private positionBuffer: Float32Array | null = null;
  private colorBuffer: Float32Array | null = null;
  private posAttr: THREE.BufferAttribute | null = null;
  private colAttr: THREE.BufferAttribute | null = null;
  private maxSatellites = 0;
  private initialized = false;

  private initBatch(parent: THREE.Object3D, satCount: number): void {
    this.maxSatellites = satCount;
    const floatCount = this.maxSatellites * TRAIL_LENGTH * 6; // 2 endpoints x 3 xyz per segment

    this.positionBuffer = new Float32Array(floatCount);
    this.colorBuffer = new Float32Array(floatCount);
    this.positionBuffer.fill(NaN); // NaN = hidden until a segment is written
    this.colorBuffer.fill(0);

    this.geometry = new THREE.BufferGeometry();
    this.posAttr = new THREE.BufferAttribute(this.positionBuffer, 3);
    this.colAttr = new THREE.BufferAttribute(this.colorBuffer, 3);
    this.posAttr.setUsage(THREE.DynamicDrawUsage);
    this.colAttr.setUsage(THREE.DynamicDrawUsage);
    this.geometry.setAttribute("position", this.posAttr);
    this.geometry.setAttribute("color", this.colAttr);
    // Fixed bounding sphere — NaN-initialized hidden segments must not poison computed
    // bounds, and frustumCulled is off so the bounds are cosmetic.
    this.geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 50000);

    this.material = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    this.batch = new THREE.LineSegments(this.geometry, this.material);
    this.batch.frustumCulled = false;
    this.batch.name = "trails";
    parent.add(this.batch);
    this.initialized = true;
  }

  /** Grow buffers when the satellite count rises, copying existing trail data verbatim. */
  private growBuffers(satCount: number): void {
    const newMax = Math.max(satCount * 2, this.maxSatellites * 2);
    const newFloatCount = newMax * TRAIL_LENGTH * 6;
    const newPosBuf = new Float32Array(newFloatCount);
    const newColBuf = new Float32Array(newFloatCount);
    newPosBuf.fill(NaN);
    newColBuf.fill(0);

    if (this.positionBuffer && this.colorBuffer) {
      const copyLen = Math.min(this.positionBuffer.length, newPosBuf.length);
      newPosBuf.set(this.positionBuffer.subarray(0, copyLen));
      newColBuf.set(this.colorBuffer.subarray(0, copyLen));
    }

    this.positionBuffer = newPosBuf;
    this.colorBuffer = newColBuf;

    if (this.geometry) {
      this.posAttr = new THREE.BufferAttribute(this.positionBuffer, 3);
      this.colAttr = new THREE.BufferAttribute(this.colorBuffer, 3);
      this.posAttr.setUsage(THREE.DynamicDrawUsage);
      this.colAttr.setUsage(THREE.DynamicDrawUsage);
      this.geometry.setAttribute("position", this.posAttr);
      this.geometry.setAttribute("color", this.colAttr);
    }

    this.maxSatellites = newMax;
  }

  private ensureSatIndex(satId: string): number {
    let idx = this.satIndexMap.get(satId);
    if (idx !== undefined) return idx;
    idx = this.satIndexMap.size;
    this.satIndexMap.set(satId, idx);
    return idx;
  }

  private ensureTrailMeta(satId: string): TrailMeta {
    let meta = this.trailMetas.get(satId);
    if (!meta) {
      meta = { head: 0, count: 0, frame: 0, prevX: 0, prevY: 0, prevZ: 0, hasPrev: false };
      this.trailMetas.set(satId, meta);
    }
    return meta;
  }

  private pushSampleDirect(satIndex: number, meta: TrailMeta, x: number, y: number, z: number): void {
    const buf = this.positionBuffer;
    if (!buf || !meta.hasPrev) {
      meta.prevX = x;
      meta.prevY = y;
      meta.prevZ = z;
      meta.hasPrev = true;
      return;
    }

    const dx = x - meta.prevX;
    const dy = y - meta.prevY;
    const dz = z - meta.prevZ;
    const segLen = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (segLen < 0.0001) return; // degenerate — skip

    const baseOffset = satIndex * TRAIL_LENGTH * 6;
    const segOffset = baseOffset + meta.head * 6;

    buf[segOffset] = meta.prevX;
    buf[segOffset + 1] = meta.prevY;
    buf[segOffset + 2] = meta.prevZ;
    buf[segOffset + 3] = x;
    buf[segOffset + 4] = y;
    buf[segOffset + 5] = z;

    meta.head = (meta.head + 1) % TRAIL_LENGTH;
    if (meta.count < TRAIL_LENGTH) meta.count++;

    meta.prevX = x;
    meta.prevY = y;
    meta.prevZ = z;
  }

  private updateTrailColors(satIndex: number, meta: TrailMeta): void {
    const pos = this.positionBuffer;
    const col = this.colorBuffer;
    if (!pos || !col || meta.count === 0) return;

    const baseOffset = satIndex * TRAIL_LENGTH * 6;
    const head = meta.head;
    const count = meta.count;
    let arcLen = 0;
    let drawCount = 0;

    // Walk backward from newest, accumulating arc length and the drawable count.
    for (let k = 0; k < count; k++) {
      const segIdx = (head - 1 - k + TRAIL_LENGTH) % TRAIL_LENGTH;
      const off = baseOffset + segIdx * 6;
      const sx = pos[off]!;
      const sy = pos[off + 1]!;
      const sz = pos[off + 2]!;
      const ex = pos[off + 3]!;
      const ey = pos[off + 4]!;
      const ez = pos[off + 5]!;
      if (isNaN(sx)) break;
      const dx = ex - sx;
      const dy = ey - sy;
      const dz = ez - sz;
      arcLen += Math.sqrt(dx * dx + dy * dy + dz * dz);
      drawCount++;
      if (arcLen > MAX_ARC_LENGTH) break;
    }

    // Color drawable segments (newest = bright, oldest = dark); hide segments past the cap.
    for (let k = 0; k < count; k++) {
      const segIdx = (head - 1 - k + TRAIL_LENGTH) % TRAIL_LENGTH;
      const off = baseOffset + segIdx * 6;
      if (k < drawCount) {
        const t = 1.0 - k / drawCount;
        const brightness = t * 0.8;
        const r = TRAIL_COLOR.r * brightness;
        const g = TRAIL_COLOR.g * brightness;
        const b = TRAIL_COLOR.b * brightness;
        col[off] = r;
        col[off + 1] = g;
        col[off + 2] = b;
        col[off + 3] = r;
        col[off + 4] = g;
        col[off + 5] = b;
      } else {
        col[off] = 0;
        col[off + 1] = 0;
        col[off + 2] = 0;
        col[off + 3] = 0;
        col[off + 4] = 0;
        col[off + 5] = 0;
      }
    }
  }

  /** Per-frame: sample each satellite's world position into its ring buffer + recolor. */
  update(parent: THREE.Object3D, satIds: readonly string[]): void {
    if (!this.initialized) this.initBatch(parent, Math.max(satIds.length, 100));

    if (satIds.length > this.maxSatellites) this.growBuffers(satIds.length);

    for (const id of satIds) {
      const satIdx = this.ensureSatIndex(id);
      if (satIdx >= this.maxSatellites) continue;

      const meta = this.ensureTrailMeta(id);
      meta.frame++;
      if (meta.frame % SAMPLE_EVERY !== 0) continue;

      if (!getNodeWorldPosition(id, _trailWorldPos)) continue;
      this.pushSampleDirect(satIdx, meta, _trailWorldPos.x, _trailWorldPos.y, _trailWorldPos.z);
      this.updateTrailColors(satIdx, meta);
    }

    if (this.posAttr) this.posAttr.needsUpdate = true;
    if (this.colAttr) this.colAttr.needsUpdate = true;
  }

  /** Flush all history (constellation / epoch change) — reset metas, blank the buffers. */
  flush(): void {
    for (const meta of this.trailMetas.values()) {
      meta.count = 0;
      meta.head = 0;
      meta.hasPrev = false;
    }
    if (this.positionBuffer) this.positionBuffer.fill(NaN);
    if (this.colorBuffer) this.colorBuffer.fill(0);
    if (this.posAttr) this.posAttr.needsUpdate = true;
    if (this.colAttr) this.colAttr.needsUpdate = true;
  }

  dispose(): void {
    if (this.batch) {
      this.batch.geometry.dispose();
      (this.batch.material as THREE.Material).dispose();
      this.batch.parent?.remove(this.batch);
      this.batch = null;
    }
    this.geometry = null;
    this.material = null;
    this.positionBuffer = null;
    this.colorBuffer = null;
    this.posAttr = null;
    this.colAttr = null;
    this.trailMetas.clear();
    this.satIndexMap.clear();
    this.maxSatellites = 0;
    this.initialized = false;
  }

  setVisible(visible: boolean): void {
    if (this.batch) this.batch.visible = visible;
  }
}

interface TrailsProps {
  /** Whether trails render + extend. When false, history is flushed and nothing is drawn. */
  enabled: boolean;
  /** The session nodes (same source as <Constellation>); satellites are filtered out here. */
  nodes: NodeState[];
  /**
   * Bump on any constellation / epoch change to flush trail history (declarative reset).
   * Pass `ephemeris.epoch_id` (and/or a session id) — the legacy code flushed on epoch_id
   * change and on constellation reset; a changing key reproduces both without an imperative
   * ref the integrator would have to wire and remember to call.
   */
  resetKey?: string | number;
}

export function Trails({ enabled, nodes, resetKey }: TrailsProps) {
  const groupRef = useRef<THREE.Group>(null);
  const lastFrameRef = useRef<number | null>(null);

  const batch = useMemo(() => new TrailBatch(), []);
  useEffect(() => () => batch.dispose(), [batch]);

  // The satellite id list — stable identity unless the satellite set changes, so the
  // useFrame closure does not rebuild every render. Same filter <Constellation> applies.
  const satIds = useMemo(
    () => nodes.filter((n) => n.node_type === "satellite").map((n) => n.node_id),
    [nodes],
  );

  // Declarative flush: constellation / epoch change (resetKey) flips → drop all history.
  useEffect(() => {
    batch.flush();
    lastFrameRef.current = null;
  }, [batch, resetKey]);

  // Hide + flush when disabled (matches setTrailsVisible(false) → flushTrails()), so
  // re-enabling starts clean rather than connecting across the gap.
  useEffect(() => {
    batch.setVisible(enabled);
    if (!enabled) {
      batch.flush();
      lastFrameRef.current = null;
    }
  }, [batch, enabled]);

  useFrame(() => {
    if (!enabled) return;
    const parent = groupRef.current;
    if (!parent) return;
    // Wall-clock frame delta (true gap, not R3F's clamped clock) — skip a backgrounded-tab
    // catch-up frame so trails do not extend across a long stall. Reproduces GlobeView's
    // `skipTrails = dt > 0.15`.
    const now = performance.now();
    const last = lastFrameRef.current;
    lastFrameRef.current = now;
    if (last !== null && (now - last) / 1000 > MAX_FRAME_DELTA_S) return;

    batch.update(parent, satIds);
  }); // DEFAULT priority (0): after FrameDriver(-2) + Constellation(-1) write this frame.

  // An empty group that the batch's LineSegments is added to. The group is a scene-root
  // child (no Earth-frame parent), so the world-space samples are rendered in world space.
  return <group ref={groupRef} />;
}
