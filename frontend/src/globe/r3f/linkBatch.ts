// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * LinkBatch — all ISL + ground links in ONE LineSegments2 draw call, ported faithfully
 * as an injectable, instance-scoped class for the R3F scene. The
 * position source (getLocalPosition) is injected so the renderer reads the R3F
 * position registry; the parent Object3D is supplied by the component via <primitive>.
 *
 * Behaviour reproduced verbatim: ISL = 16-segment bowed arc (lift 3% of chord), ground =
 * 1 straight segment, NaN = hidden segment, in-place interleaved-buffer upload (no
 * per-frame allocation), 2x-headroom capacity with deterministic growth, fail-flash
 * (hold FAIL_HOLD_MS red, fade FAIL_FADE_MS to inactive, then hide), classification by
 * LinkState.link_type, sorted link key. Endpoints are re-resolved every frame, so a beam
 * tracks a propagating satellite with zero lag.
 */

import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import {
  LINK_ISL_COLOR,
  LINK_GROUND_COLOR,
  LINK_FAIL_COLOR,
  LINK_INACTIVE_COLOR,
  LINK_ISL_WIDTH,
  LINK_GROUND_WIDTH,
  FAIL_HOLD_MS,
  FAIL_FADE_MS,
} from "../../config";
import type { LinkState } from "../../types";
import { isGroundLinkState } from "../../networkIdentity";

const SEGMENTS_PER_ISL = 16;
const MIN_ISL_SLOTS = 200;
const MIN_GROUND_SLOTS = 100;
/** Brightness factor for an OME-desired link the kernel has not proven (in-flight/desired). */
const UNPROVEN_DIM = 0.35;

const ISL_R = ((LINK_ISL_COLOR >> 16) & 0xff) / 255;
const ISL_G = ((LINK_ISL_COLOR >> 8) & 0xff) / 255;
const ISL_B = (LINK_ISL_COLOR & 0xff) / 255;
const GND_R = ((LINK_GROUND_COLOR >> 16) & 0xff) / 255;
const GND_G = ((LINK_GROUND_COLOR >> 8) & 0xff) / 255;
const GND_B = (LINK_GROUND_COLOR & 0xff) / 255;
const FAIL_R = ((LINK_FAIL_COLOR >> 16) & 0xff) / 255;
const FAIL_G = ((LINK_FAIL_COLOR >> 8) & 0xff) / 255;
const FAIL_B = (LINK_FAIL_COLOR & 0xff) / 255;
const INACTIVE_R = ((LINK_INACTIVE_COLOR >> 16) & 0xff) / 255;
const INACTIVE_G = ((LINK_INACTIVE_COLOR >> 8) & 0xff) / 255;
const INACTIVE_B = (LINK_INACTIVE_COLOR & 0xff) / 255;

const _mid = new THREE.Vector3();
const _outward = new THREE.Vector3();
const _posA = new THREE.Vector3();
const _posB = new THREE.Vector3();

export function linkKey(a: string, b: string): string {
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function writeBowedSegments(buffer: Float32Array, offset: number, a: THREE.Vector3, b: THREE.Vector3): void {
  _mid.lerpVectors(a, b, 0.5);
  _outward.copy(_mid).normalize();
  const chord = a.distanceTo(b);
  const lift = chord * 0.03;
  for (let i = 0; i < SEGMENTS_PER_ISL; i++) {
    const t0 = i / SEGMENTS_PER_ISL;
    const t1 = (i + 1) / SEGMENTS_PER_ISL;
    const bow0 = 4 * t0 * (1 - t0) * lift;
    const bow1 = 4 * t1 * (1 - t1) * lift;
    const idx = offset + i * 6;
    buffer[idx] = a.x + (b.x - a.x) * t0 + _outward.x * bow0;
    buffer[idx + 1] = a.y + (b.y - a.y) * t0 + _outward.y * bow0;
    buffer[idx + 2] = a.z + (b.z - a.z) * t0 + _outward.z * bow0;
    buffer[idx + 3] = a.x + (b.x - a.x) * t1 + _outward.x * bow1;
    buffer[idx + 4] = a.y + (b.y - a.y) * t1 + _outward.y * bow1;
    buffer[idx + 5] = a.z + (b.z - a.z) * t1 + _outward.z * bow1;
  }
}

function writeNaN(buffer: Float32Array, segmentIndex: number, segmentCount: number): void {
  const start = segmentIndex * 6;
  const end = start + segmentCount * 6;
  for (let i = start; i < end; i++) buffer[i] = NaN;
}

function writeColor(
  buffer: Float32Array,
  segmentIndex: number,
  segmentCount: number,
  r: number,
  g: number,
  b: number,
): void {
  const start = segmentIndex * 6;
  const end = start + segmentCount * 6;
  for (let i = start; i < end; i += 6) {
    buffer[i] = r;
    buffer[i + 1] = g;
    buffer[i + 2] = b;
    buffer[i + 3] = r;
    buffer[i + 4] = g;
    buffer[i + 5] = b;
  }
}

interface LinkEntry {
  bufferIndex: number;
  segmentCount: number;
  state: "active" | "inactive" | "failing";
  nodeA: string;
  nodeB: string;
  isGround: boolean;
  failTime: number | null;
  upTime: number | null;
}

type GetLocalPosition = (nodeId: string, target: THREE.Vector3) => boolean;

export class LinkBatch {
  private readonly links = new Map<string, LinkEntry>();
  private batch: LineSegments2 | null = null;
  private geometry: LineSegmentsGeometry | null = null;
  private material: LineMaterial | null = null;
  private positionBuffer: Float32Array | null = null;
  private colorBuffer: Float32Array | null = null;
  private totalSegments = 0;
  private islSegmentEnd = 0;
  private maxIslSlots = 0;
  private maxGndSlots = 0;
  private usedIslSlots = 0;
  private usedGndSlots = 0;
  private initialized = false;

  constructor(private readonly getLocalPosition: GetLocalPosition) {}

  /** The LineSegments2 to mount via <primitive>. Null until the first update(). */
  get object3d(): LineSegments2 | null {
    return this.batch;
  }

  private createGeometry(positions: Float32Array, colors: Float32Array): LineSegmentsGeometry {
    const g = new LineSegmentsGeometry();
    // NaN-initialized hidden segments must not poison computed bounds (frustumCulled=false
    // means bounds are cosmetic). Override the compute methods and set fixed large bounds.
    g.computeBoundingSphere = () => {};
    g.computeBoundingBox = () => {};
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 50000);
    g.boundingBox = new THREE.Box3(
      new THREE.Vector3(-50000, -50000, -50000),
      new THREE.Vector3(50000, 50000, 50000),
    );
    g.setPositions(positions);
    g.setColors(colors);
    return g;
  }

  private reassignLinkSlots(): void {
    this.usedIslSlots = 0;
    this.usedGndSlots = 0;
    for (const entry of this.links.values()) {
      if (entry.isGround) {
        if (this.usedGndSlots >= this.maxGndSlots) {
          throw new Error(`Ground link buffer under-sized during rebuild: >${this.maxGndSlots}`);
        }
        entry.bufferIndex = this.islSegmentEnd + this.usedGndSlots++;
      } else {
        if (this.usedIslSlots >= this.maxIslSlots) {
          throw new Error(`ISL buffer under-sized during rebuild: >${this.maxIslSlots}`);
        }
        entry.bufferIndex = this.usedIslSlots++ * SEGMENTS_PER_ISL;
      }
    }
  }

  private growBuffers(requiredIslSlots: number, requiredGndSlots: number): void {
    if (!this.initialized || !this.batch) {
      throw new Error(`Link capacity requested before init: isl=${requiredIslSlots}, gnd=${requiredGndSlots}`);
    }
    this.maxIslSlots = Math.max(requiredIslSlots, this.maxIslSlots * 2, MIN_ISL_SLOTS);
    this.maxGndSlots = Math.max(requiredGndSlots, this.maxGndSlots * 2, MIN_GROUND_SLOTS);
    this.islSegmentEnd = this.maxIslSlots * SEGMENTS_PER_ISL;
    this.totalSegments = this.islSegmentEnd + this.maxGndSlots;
    this.positionBuffer = new Float32Array(this.totalSegments * 6);
    this.colorBuffer = new Float32Array(this.totalSegments * 6);
    this.positionBuffer.fill(NaN);
    this.colorBuffer.fill(0);
    this.reassignLinkSlots();
    const oldGeometry = this.geometry;
    this.geometry = this.createGeometry(this.positionBuffer, this.colorBuffer);
    this.batch.geometry = this.geometry;
    oldGeometry?.dispose();
  }

  private ensureCapacity(requiredIslSlots: number, requiredGndSlots: number): void {
    if (requiredIslSlots <= this.maxIslSlots && requiredGndSlots <= this.maxGndSlots) return;
    this.growBuffers(requiredIslSlots, requiredGndSlots);
  }

  private addLinkEntry(link: LinkState): LinkEntry {
    const nodeA = link.node_a;
    const nodeB = link.node_b;
    const key = linkKey(nodeA, nodeB);
    const existing = this.links.get(key);
    if (existing) return existing;
    const ground = isGroundLinkState(link);
    let bi: number;
    if (ground) {
      if (this.usedGndSlots >= this.maxGndSlots) this.ensureCapacity(this.usedIslSlots, this.usedGndSlots + 1);
      bi = this.islSegmentEnd + this.usedGndSlots++;
    } else {
      if (this.usedIslSlots >= this.maxIslSlots) this.ensureCapacity(this.usedIslSlots + 1, this.usedGndSlots);
      bi = this.usedIslSlots++ * SEGMENTS_PER_ISL;
    }
    const entry: LinkEntry = {
      bufferIndex: bi,
      segmentCount: ground ? 1 : SEGMENTS_PER_ISL,
      state: "inactive",
      nodeA,
      nodeB,
      isGround: ground,
      failTime: null,
      upTime: null,
    };
    this.links.set(key, entry);
    return entry;
  }

  private initBatch(linkStates: LinkState[], parent: THREE.Object3D): void {
    const islKeys = new Set<string>();
    const gndKeys = new Set<string>();
    for (const ls of linkStates) {
      const key = linkKey(ls.node_a, ls.node_b);
      if (isGroundLinkState(ls)) gndKeys.add(key);
      else islKeys.add(key);
    }
    this.maxIslSlots = Math.max(islKeys.size * 2, MIN_ISL_SLOTS);
    this.maxGndSlots = Math.max(gndKeys.size * 2, MIN_GROUND_SLOTS);
    this.islSegmentEnd = this.maxIslSlots * SEGMENTS_PER_ISL;
    this.totalSegments = this.islSegmentEnd + this.maxGndSlots;
    const posFloats = this.totalSegments * 6;
    this.positionBuffer = new Float32Array(posFloats);
    this.colorBuffer = new Float32Array(posFloats);
    this.positionBuffer.fill(NaN);
    this.colorBuffer.fill(0);
    this.usedIslSlots = 0;
    this.usedGndSlots = 0;
    for (const ls of linkStates) this.addLinkEntry(ls);
    this.geometry = this.createGeometry(this.positionBuffer, this.colorBuffer);
    this.material = new LineMaterial({
      color: 0xffffff,
      vertexColors: true,
      linewidth: Math.max(LINK_ISL_WIDTH, LINK_GROUND_WIDTH),
      transparent: true,
      opacity: 0.6,
      depthWrite: false,
      resolution: new THREE.Vector2(window.innerWidth, window.innerHeight),
    });
    this.batch = new LineSegments2(this.geometry, this.material);
    this.batch.frustumCulled = false;
    parent.add(this.batch);
    this.initialized = true;
  }

  /** Data-driven: reconcile link metadata + state transitions on each snapshot. */
  update(linkStates: LinkState[], parent: THREE.Object3D, now: number): void {
    if (!this.initialized) this.initBatch(linkStates, parent);
    const active = new Set<string>();
    for (const ls of linkStates) {
      const key = linkKey(ls.node_a, ls.node_b);
      active.add(key);
      const entry = this.links.get(key) ?? this.addLinkEntry(ls);
      if (entry.state !== "active" && ls.state === "active") {
        entry.upTime = now;
        entry.failTime = null;
      }
      entry.state = ls.state === "active" ? "active" : entry.state;
    }
    for (const [key, entry] of this.links) {
      if (!active.has(key) && entry.state === "active") {
        entry.state = "failing";
        entry.failTime = now;
        entry.upTime = null;
      }
    }
  }

  /** Per-frame: resolve endpoints, write positions/colors, upload in place.
   *
   * `kernelActual` (when provided) is the Scheduler-verified kernel-PROVEN link key set: a
   * proven link renders at full color, an OME-desired-but-not-proven link is DIMMED so a beam
   * never reads connected while the decision card says in_flight/faulted (truth over
   * availability). Null disables gating (every active link full color — legacy behavior). */
  animate(
    showIslLinks: boolean,
    showGroundLinks: boolean,
    now: number,
    kernelActual: ReadonlySet<string> | null = null,
  ): void {
    const pos = this.positionBuffer;
    const col = this.colorBuffer;
    if (!this.initialized || !pos || !col || !this.geometry || !this.batch) return;
    for (const [, entry] of this.links) {
      const hasA = this.getLocalPosition(entry.nodeA, _posA);
      const hasB = this.getLocalPosition(entry.nodeB, _posB);
      if (!hasA || !hasB) {
        writeNaN(pos, entry.bufferIndex, entry.segmentCount);
        continue;
      }
      if (entry.failTime === null) {
        if (entry.isGround && !showGroundLinks) {
          writeNaN(pos, entry.bufferIndex, entry.segmentCount);
          continue;
        }
        if (!entry.isGround && !showIslLinks) {
          writeNaN(pos, entry.bufferIndex, entry.segmentCount);
          continue;
        }
      }
      if (entry.state === "inactive") {
        writeNaN(pos, entry.bufferIndex, entry.segmentCount);
        continue;
      }
      if (entry.isGround) {
        const off = entry.bufferIndex * 6;
        pos[off] = _posA.x;
        pos[off + 1] = _posA.y;
        pos[off + 2] = _posA.z;
        pos[off + 3] = _posB.x;
        pos[off + 4] = _posB.y;
        pos[off + 5] = _posB.z;
      } else {
        writeBowedSegments(pos, entry.bufferIndex * 6, _posA, _posB);
      }
      if (entry.state === "failing" && entry.failTime !== null) {
        const elapsed = now - entry.failTime;
        if (elapsed < FAIL_HOLD_MS) {
          writeColor(col, entry.bufferIndex, entry.segmentCount, FAIL_R, FAIL_G, FAIL_B);
        } else if (elapsed < FAIL_HOLD_MS + FAIL_FADE_MS) {
          const t = (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS;
          writeColor(
            col,
            entry.bufferIndex,
            entry.segmentCount,
            FAIL_R + (INACTIVE_R - FAIL_R) * t,
            FAIL_G + (INACTIVE_G - FAIL_G) * t,
            FAIL_B + (INACTIVE_B - FAIL_B) * t,
          );
        } else {
          writeNaN(pos, entry.bufferIndex, entry.segmentCount);
          entry.state = "inactive";
          entry.failTime = null;
        }
      } else if (entry.state === "active") {
        // Kernel-actual gate: a proven link is full color, an OME-desired-but-not-proven
        // link is dimmed (in-flight / desired, not connected).
        const proven =
          kernelActual === null || kernelActual.has(linkKey(entry.nodeA, entry.nodeB));
        const k = proven ? 1 : UNPROVEN_DIM;
        if (entry.isGround) {
          writeColor(col, entry.bufferIndex, entry.segmentCount, GND_R * k, GND_G * k, GND_B * k);
        } else {
          writeColor(col, entry.bufferIndex, entry.segmentCount, ISL_R * k, ISL_G * k, ISL_B * k);
        }
      }
    }
    const posAttr = this.geometry.getAttribute("instanceStart") as THREE.InterleavedBufferAttribute | null;
    if (posAttr?.data) {
      (posAttr.data.array as Float32Array).set(pos);
      posAttr.data.needsUpdate = true;
    } else {
      this.geometry.setPositions(pos);
      this.geometry.setColors(col);
    }
    const colAttr = this.geometry.getAttribute("instanceColorStart") as THREE.InterleavedBufferAttribute | null;
    if (colAttr?.data) {
      (colAttr.data.array as Float32Array).set(col);
      colAttr.data.needsUpdate = true;
    }
  }

  setResolution(width: number, height: number): void {
    this.material?.resolution.set(width, height);
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
    this.links.clear();
    this.initialized = false;
    this.totalSegments = 0;
    this.islSegmentEnd = 0;
    this.maxIslSlots = 0;
    this.maxGndSlots = 0;
    this.usedIslSlots = 0;
    this.usedGndSlots = 0;
  }

  /** Test-only: inspect a link's buffer slot. */
  _debugEntry(nodeA: string, nodeB: string): { bufferIndex: number; segmentCount: number; state: string } | null {
    const e = this.links.get(linkKey(nodeA, nodeB));
    return e ? { bufferIndex: e.bufferIndex, segmentCount: e.segmentCount, state: e.state } : null;
  }

  /** Test-only: read the position buffer. */
  _debugPositions(): Float32Array | null {
    return this.positionBuffer;
  }

  /** Test-only: read the color buffer. */
  _debugColors(): Float32Array | null {
    return this.colorBuffer;
  }
}
