// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Link rendering — batched into ONE draw call via LineSegments2.
//
// All ISL and ground links share a single LineSegmentsGeometry with a
// solid LineMaterial and per-vertex colors. ISL links are green bowed
// arcs (16 segments each). Ground links are cyan straight lines (1
// segment each). Both are in the same buffer and batch.
//
// Fail-flash animations use vertex colors (red → dark fade). No
// per-link objects, no pools, no per-frame allocation.
//
// History: commit 83e86cf batched links into 2 draw calls. Commit
// afb5381 reverted because LineGeometry (connected polyline) created
// spurious connecting segments. LineSegmentsGeometry (disconnected
// pairs) is the correct primitive — no connecting segments.

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
} from "../config";
import { getNodeLocalPosition } from "./positionLookup";
import type { LinkState } from "../types";

// --- Bowing geometry for ISL arcs ---

const SEGMENTS_PER_ISL = 16;
// Buffer math: SEGMENTS_PER_ISL * 6 floats per ISL link, 6 floats per ground link

const _mid = new THREE.Vector3();
const _outward = new THREE.Vector3();
const _posA = new THREE.Vector3();
const _posB = new THREE.Vector3();

function writeBowedSegments(
  buffer: Float32Array,
  offset: number,
  a: THREE.Vector3,
  b: THREE.Vector3,
): void {
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

// --- Link metadata (no Three.js object references) ---

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

const links = new Map<string, LinkEntry>();

// --- Batch state ---

let batch: LineSegments2 | null = null;
let geometry: LineSegmentsGeometry | null = null;
let material: LineMaterial | null = null;
let positionBuffer: Float32Array | null = null;
let colorBuffer: Float32Array | null = null;
let totalSegments = 0;
let initialized = false;

// Track ISL vs ground regions for visibility toggles
let islSegmentEnd = 0; // segments [0, islSegmentEnd) are ISL
// segments [islSegmentEnd, totalSegments) are ground

// Color constants as RGB floats
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

function linkKey(a: string, b: string): string {
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function isGroundLink(nodeA: string, nodeB: string): boolean {
  return nodeA.startsWith("gs-") || nodeB.startsWith("gs-");
}

// Track allocated vs used capacity for dynamic growth
let maxIslSlots = 0;
let maxGndSlots = 0;
let usedIslSlots = 0;
let usedGndSlots = 0;

function initBatch(linkStates: LinkState[], earthFrame: THREE.Object3D): void {
  // Count ISL and ground links from initial snapshot
  const islKeys = new Set<string>();
  const gndKeys = new Set<string>();
  for (const ls of linkStates) {
    const key = linkKey(ls.node_a, ls.node_b);
    if (isGroundLink(ls.node_a, ls.node_b)) {
      gndKeys.add(key);
    } else {
      islKeys.add(key);
    }
  }

  // Allocate 2x headroom for links that appear after first snapshot
  maxIslSlots = Math.max(islKeys.size * 2, 200);
  maxGndSlots = Math.max(gndKeys.size * 2, 100);
  islSegmentEnd = maxIslSlots * SEGMENTS_PER_ISL;
  totalSegments = islSegmentEnd + maxGndSlots;

  // Allocate buffers
  const posFloats = totalSegments * 6;
  positionBuffer = new Float32Array(posFloats);
  colorBuffer = new Float32Array(posFloats);

  // Fill with NaN (hidden segments)
  positionBuffer.fill(NaN);
  colorBuffer.fill(0);

  // Assign buffer indices — ISLs first, then ground
  usedIslSlots = 0;
  usedGndSlots = 0;
  for (const ls of linkStates) {
    addLinkEntry(ls.node_a, ls.node_b);
  }

  // Create geometry + material + mesh.
  // Set bounding sphere BEFORE setPositions to prevent NaN computation
  // errors from the NaN-initialized buffer. frustumCulled=false means
  // bounding sphere isn't used for culling — this is purely cosmetic.
  geometry = new LineSegmentsGeometry();
  // Override computeBoundingSphere to prevent NaN errors from NaN-initialized
  // buffers. The real bounding sphere is set manually below. frustumCulled=false
  // means Three.js never uses it for culling.
  geometry.computeBoundingSphere = () => {};
  geometry.computeBoundingBox = () => {};
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 50000);
  geometry.boundingBox = new THREE.Box3(
    new THREE.Vector3(-50000, -50000, -50000),
    new THREE.Vector3(50000, 50000, 50000),
  );
  geometry.setPositions(positionBuffer);
  geometry.setColors(colorBuffer);

  const lineWidth = Math.max(LINK_ISL_WIDTH, LINK_GROUND_WIDTH);
  material = new LineMaterial({
    color: 0xffffff,
    vertexColors: true,
    linewidth: lineWidth,
    transparent: true,
    opacity: 0.6,
    depthWrite: false,
    resolution: new THREE.Vector2(window.innerWidth, window.innerHeight),
  });

  batch = new LineSegments2(geometry, material);
  batch.frustumCulled = false;
  earthFrame.add(batch);

  // Listen for resize to update material resolution
  window.addEventListener("resize", () => {
    if (material) material.resolution.set(window.innerWidth, window.innerHeight);
  });

  initialized = true;
}

function addLinkEntry(nodeA: string, nodeB: string): LinkEntry | undefined {
  const key = linkKey(nodeA, nodeB);
  if (links.has(key)) return links.get(key)!;

  const ground = isGroundLink(nodeA, nodeB);
  let bi: number;

  if (ground) {
    if (usedGndSlots >= maxGndSlots) return undefined;
    bi = islSegmentEnd + usedGndSlots++;
  } else {
    if (usedIslSlots >= maxIslSlots) return undefined;
    bi = usedIslSlots++ * SEGMENTS_PER_ISL;
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
  links.set(key, entry);
  return entry;
}

export function updateLinks(
  linkStates: LinkState[],
  earthFrame: THREE.Object3D,
  _showAllLinks: boolean,
): void {
  if (!initialized) {
    initBatch(linkStates, earthFrame);
  }

  const now = performance.now();
  const active = new Set<string>();

  for (const ls of linkStates) {
    const key = linkKey(ls.node_a, ls.node_b);
    active.add(key);

    let entry = links.get(key);
    if (!entry) {
      entry = addLinkEntry(ls.node_a, ls.node_b);
      if (!entry) continue; // buffer full
    }

    if (entry.state !== "active" && ls.state === "active") {
      entry.upTime = now;
      entry.failTime = null;
    }
    entry.state = ls.state === "active" ? "active" : entry.state;
  }

  // Mark disappeared links as failing
  for (const [key, entry] of links) {
    if (!active.has(key) && entry.state === "active") {
      entry.state = "failing";
      entry.failTime = now;
      entry.upTime = null;
    }
  }
}

export function animateLinks(showIslLinks: boolean = true, showGroundLinks: boolean = true): void {
  if (!initialized || !positionBuffer || !colorBuffer || !geometry || !batch) return;

  const now = performance.now();

  for (const [, entry] of links) {
    const hasA = getNodeLocalPosition(entry.nodeA, _posA);
    const hasB = getNodeLocalPosition(entry.nodeB, _posB);

    if (!hasA || !hasB) {
      // Node positions not available — hide
      writeNaN(positionBuffer, entry.bufferIndex, entry.segmentCount);
      continue;
    }

    // Visibility toggles
    if (entry.failTime === null) {
      if (entry.isGround && !showGroundLinks) {
        writeNaN(positionBuffer, entry.bufferIndex, entry.segmentCount);
        continue;
      }
      if (!entry.isGround && !showIslLinks) {
        writeNaN(positionBuffer, entry.bufferIndex, entry.segmentCount);
        continue;
      }
    }

    if (entry.state === "inactive") {
      writeNaN(positionBuffer, entry.bufferIndex, entry.segmentCount);
      continue;
    }

    // Write positions
    if (entry.isGround) {
      const off = entry.bufferIndex * 6;
      positionBuffer[off] = _posA.x;
      positionBuffer[off + 1] = _posA.y;
      positionBuffer[off + 2] = _posA.z;
      positionBuffer[off + 3] = _posB.x;
      positionBuffer[off + 4] = _posB.y;
      positionBuffer[off + 5] = _posB.z;
    } else {
      writeBowedSegments(positionBuffer, entry.bufferIndex * 6, _posA, _posB);
    }

    // Write colors based on state
    if (entry.state === "failing" && entry.failTime !== null) {
      const elapsed = now - entry.failTime;
      if (elapsed < FAIL_HOLD_MS) {
        writeColor(colorBuffer, entry.bufferIndex, entry.segmentCount, FAIL_R, FAIL_G, FAIL_B);
      } else if (elapsed < FAIL_HOLD_MS + FAIL_FADE_MS) {
        const t = (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS;
        const r = FAIL_R + (INACTIVE_R - FAIL_R) * t;
        const g = FAIL_G + (INACTIVE_G - FAIL_G) * t;
        const b = FAIL_B + (INACTIVE_B - FAIL_B) * t;
        writeColor(colorBuffer, entry.bufferIndex, entry.segmentCount, r, g, b);
      } else {
        // Fade complete — hide and mark inactive
        writeNaN(positionBuffer, entry.bufferIndex, entry.segmentCount);
        entry.state = "inactive";
        entry.failTime = null;
      }
    } else if (entry.state === "active") {
      if (entry.isGround) {
        writeColor(colorBuffer, entry.bufferIndex, entry.segmentCount, GND_R, GND_G, GND_B);
      } else {
        writeColor(colorBuffer, entry.bufferIndex, entry.segmentCount, ISL_R, ISL_G, ISL_B);
      }
    }
  }

  // Upload buffers — update existing interleaved buffer arrays in-place
  // instead of calling setPositions() which allocates new InstancedInterleavedBuffers.
  // getAttribute returns BufferAttribute | InterleavedBufferAttribute;
  // LineSegmentsGeometry uses InterleavedBufferAttribute with a shared .data buffer.
  const posAttr = geometry.getAttribute("instanceStart") as THREE.InterleavedBufferAttribute | null;
  if (posAttr?.data) {
    (posAttr.data.array as Float32Array).set(positionBuffer);
    posAttr.data.needsUpdate = true;
  } else {
    geometry.setPositions(positionBuffer);
    geometry.setColors(colorBuffer);
  }

  const colAttr = geometry.getAttribute("instanceColorStart") as THREE.InterleavedBufferAttribute | null;
  if (colAttr?.data) {
    (colAttr.data.array as Float32Array).set(colorBuffer);
    colAttr.data.needsUpdate = true;
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

export function getLinks(): Map<string, LinkEntry> {
  return links;
}

export function clearLinks(): void {
  if (batch) {
    batch.geometry.dispose();
    (batch.material as THREE.Material).dispose();
    batch.parent?.remove(batch);
    batch = null;
  }
  geometry = null;
  material = null;
  positionBuffer = null;
  colorBuffer = null;
  links.clear();
  initialized = false;
  totalSegments = 0;
  islSegmentEnd = 0;
  maxIslSlots = 0;
  maxGndSlots = 0;
  usedIslSlots = 0;
  usedGndSlots = 0;
}
