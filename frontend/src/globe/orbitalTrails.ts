// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Orbital trails — batched into ONE draw call via LineSegments.
 *
 *  All satellite trails share a single BufferGeometry rendered as
 *  THREE.LineSegments (disconnected pairs). Per-vertex colors produce
 *  the head-to-tail brightness fade. Additive blending creates glow.
 *
 *  Zero per-frame allocation: positions and colors are written directly
 *  to pre-allocated Float32Arrays. No new Array(), no {x,y,z} objects.
 *
 *  Trail length is capped by arc distance (not time), so it stays a
 *  consistent visual size regardless of playback speed.
 *
 *  History: the original per-satellite THREE.Line approach created one
 *  draw call per satellite (2000 sats = 2000 draw calls). The
 *  extractDrawPoints function allocated new Array() + {x,y,z} objects
 *  2700 times per second at 90 sats, causing GC pressure.
 */

import * as THREE from "three";
import { getSatellites } from "./satellites";
import { getNodeWorldPosition } from "./positionLookup";

const TRAIL_LENGTH = 600;
const SAMPLE_EVERY = 2;
const MAX_ARC_LENGTH = 1.8;
const TRAIL_COLOR = new THREE.Color(0x6699dd);

// --- Per-satellite ring buffer metadata (no Three.js objects) ---

interface TrailMeta {
  head: number;
  count: number;
  frame: number;
  prevX: number;
  prevY: number;
  prevZ: number;
  hasPrev: boolean;
}

const trailMetas = new Map<string, TrailMeta>();
let satIndexMap = new Map<string, number>();

// --- Batch state ---

let batch: THREE.LineSegments | null = null;
let batchGeometry: THREE.BufferGeometry | null = null;
let positionBuffer: Float32Array | null = null;
let colorBuffer: Float32Array | null = null;
let posAttr: THREE.BufferAttribute | null = null;
let colAttr: THREE.BufferAttribute | null = null;
let maxSatellites = 0;
let initialized = false;
let trailsVisible = true;
let _lastEpochId: number | null = null;

const _trailWorldPos = new THREE.Vector3();

function initBatch(scene: THREE.Scene, satCount: number): void {
  maxSatellites = satCount;
  const totalSegments = maxSatellites * TRAIL_LENGTH;
  const floatCount = totalSegments * 6; // 2 endpoints × 3 xyz per segment

  positionBuffer = new Float32Array(floatCount);
  colorBuffer = new Float32Array(floatCount);

  // Initialize all positions to NaN (hidden)
  positionBuffer.fill(NaN);
  colorBuffer.fill(0);

  batchGeometry = new THREE.BufferGeometry();
  posAttr = new THREE.BufferAttribute(positionBuffer, 3);
  colAttr = new THREE.BufferAttribute(colorBuffer, 3);
  posAttr.setUsage(THREE.DynamicDrawUsage);
  colAttr.setUsage(THREE.DynamicDrawUsage);
  batchGeometry.setAttribute("position", posAttr);
  batchGeometry.setAttribute("color", colAttr);

  const material = new THREE.LineBasicMaterial({
    vertexColors: true,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  // Manual bounding sphere prevents NaN computation errors
  batchGeometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 1000);

  batch = new THREE.LineSegments(batchGeometry, material);
  batch.frustumCulled = false;
  scene.add(batch);

  initialized = true;
}

function ensureSatIndex(satId: string): number {
  let idx = satIndexMap.get(satId);
  if (idx !== undefined) return idx;
  idx = satIndexMap.size;
  satIndexMap.set(satId, idx);
  return idx;
}

function ensureTrailMeta(satId: string): TrailMeta {
  let meta = trailMetas.get(satId);
  if (!meta) {
    meta = { head: 0, count: 0, frame: 0, prevX: 0, prevY: 0, prevZ: 0, hasPrev: false };
    trailMetas.set(satId, meta);
  }
  return meta;
}

function pushSampleDirect(
  satIndex: number,
  meta: TrailMeta,
  x: number,
  y: number,
  z: number,
): void {
  if (!positionBuffer || !meta.hasPrev) {
    meta.prevX = x;
    meta.prevY = y;
    meta.prevZ = z;
    meta.hasPrev = true;
    return;
  }

  // Check arc length — skip if cumulative would exceed MAX_ARC_LENGTH
  const dx = x - meta.prevX;
  const dy = y - meta.prevY;
  const dz = z - meta.prevZ;
  const segLen = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (segLen < 0.0001) return; // degenerate

  const baseOffset = satIndex * TRAIL_LENGTH * 6;
  const segOffset = baseOffset + meta.head * 6;

  positionBuffer[segOffset] = meta.prevX;
  positionBuffer[segOffset + 1] = meta.prevY;
  positionBuffer[segOffset + 2] = meta.prevZ;
  positionBuffer[segOffset + 3] = x;
  positionBuffer[segOffset + 4] = y;
  positionBuffer[segOffset + 5] = z;

  meta.head = (meta.head + 1) % TRAIL_LENGTH;
  if (meta.count < TRAIL_LENGTH) meta.count++;

  meta.prevX = x;
  meta.prevY = y;
  meta.prevZ = z;
}

function updateTrailColors(satIndex: number, meta: TrailMeta): void {
  if (!colorBuffer || meta.count === 0) return;

  const baseOffset = satIndex * TRAIL_LENGTH * 6;

  // Walk from oldest to newest, compute brightness fade
  // We need to walk arc-length backward from head to find drawable segments
  const head = meta.head;
  const count = meta.count;
  let arcLen = 0;
  let drawCount = 0;

  // Walk backward from newest to compute arc length and drawable count
  for (let k = 0; k < count; k++) {
    const segIdx = ((head - 1 - k) + TRAIL_LENGTH) % TRAIL_LENGTH;
    const off = baseOffset + segIdx * 6;
    const sx = positionBuffer![off]!;
    const sy = positionBuffer![off + 1]!;
    const sz = positionBuffer![off + 2]!;
    const ex = positionBuffer![off + 3]!;
    const ey = positionBuffer![off + 4]!;
    const ez = positionBuffer![off + 5]!;
    if (isNaN(sx)) break;
    const dx = ex - sx;
    const dy = ey - sy;
    const dz = ez - sz;
    arcLen += Math.sqrt(dx * dx + dy * dy + dz * dz);
    drawCount++;
    if (arcLen > MAX_ARC_LENGTH) break;
  }

  // Color the drawable segments (newest = bright, oldest = dark)
  // Hide segments beyond arc length limit
  for (let k = 0; k < count; k++) {
    const segIdx = ((head - 1 - k) + TRAIL_LENGTH) % TRAIL_LENGTH;
    const off = baseOffset + segIdx * 6;

    if (k < drawCount) {
      const t = 1.0 - k / drawCount;
      const brightness = t * 0.8;
      const r = TRAIL_COLOR.r * brightness;
      const g = TRAIL_COLOR.g * brightness;
      const b = TRAIL_COLOR.b * brightness;
      colorBuffer[off] = r;
      colorBuffer[off + 1] = g;
      colorBuffer[off + 2] = b;
      colorBuffer[off + 3] = r;
      colorBuffer[off + 4] = g;
      colorBuffer[off + 5] = b;
    } else {
      // Beyond arc length — hide by setting color to black
      colorBuffer[off] = 0;
      colorBuffer[off + 1] = 0;
      colorBuffer[off + 2] = 0;
      colorBuffer[off + 3] = 0;
      colorBuffer[off + 4] = 0;
      colorBuffer[off + 5] = 0;
    }
  }
}

export function notifyEpochChange(epochId: number): void {
  if (_lastEpochId !== null && epochId !== _lastEpochId) {
    flushTrails();
  }
  _lastEpochId = epochId;
}

export function flushTrails(): void {
  for (const meta of trailMetas.values()) {
    meta.count = 0;
    meta.head = 0;
    meta.hasPrev = false;
  }
  if (positionBuffer) positionBuffer.fill(NaN);
  if (colorBuffer) colorBuffer.fill(0);
}

export function clearTrails(): void {
  if (batch) {
    batch.geometry.dispose();
    (batch.material as THREE.Material).dispose();
    batch.parent?.remove(batch);
    batch = null;
  }
  batchGeometry = null;
  positionBuffer = null;
  colorBuffer = null;
  posAttr = null;
  colAttr = null;
  trailMetas.clear();
  satIndexMap.clear();
  initialized = false;
  maxSatellites = 0;
}

export function updateOrbitalTrails(scene: THREE.Scene): void {
  if (!trailsVisible) return;

  const sats = getSatellites();

  if (!initialized) {
    initBatch(scene, Math.max(sats.size, 100));
  }

  // Grow buffer if needed (new session with more sats)
  if (sats.size > maxSatellites) {
    const parent = batch?.parent;
    clearTrails();
    initBatch(scene, sats.size);
    if (parent && batch) parent.add(batch);
  }

  for (const [id] of sats) {
    const satIdx = ensureSatIndex(id);
    if (satIdx >= maxSatellites) continue;

    const meta = ensureTrailMeta(id);
    meta.frame++;
    if (meta.frame % SAMPLE_EVERY !== 0) continue;

    if (!getNodeWorldPosition(id, _trailWorldPos)) continue;
    pushSampleDirect(satIdx, meta, _trailWorldPos.x, _trailWorldPos.y, _trailWorldPos.z);
    updateTrailColors(satIdx, meta);
  }

  if (posAttr) posAttr.needsUpdate = true;
  if (colAttr) colAttr.needsUpdate = true;
}

export function setTrailsVisible(visible: boolean): void {
  trailsVisible = visible;
  if (batch) batch.visible = visible;
  if (!visible) {
    flushTrails();
  }
}
