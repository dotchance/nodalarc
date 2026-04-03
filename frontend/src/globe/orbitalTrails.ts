// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Orbital trails — fading tail behind each satellite showing recent path.
 *
 *  Uses brightness fade (RGB → black) with additive blending rather than
 *  alpha, since Three.js LineBasicMaterial ignores per-vertex alpha.
 *
 *  Trail length is capped by arc distance (not time), so it stays a
 *  consistent visual size regardless of playback speed.
 */

import * as THREE from "three";
import { getSatellites } from "./satellites";
import {
  createTrailBuffer,
  pushSample,
  extractDrawPoints,
  type TrailBufferState,
} from "./trailBuffer";

/** Max trail points in the ring buffer (upper bound for memory). */
const TRAIL_LENGTH = 600;

/** Sample every Nth frame. */
const SAMPLE_EVERY = 2;

/** Max trail arc length in scene units. Tuned so the trail is a short
 *  directional indicator, not a full orbit trace. */
const MAX_ARC_LENGTH = 1.8;

interface TrailEntry {
  buffer: TrailBufferState;
  line: THREE.Line;
  geometry: THREE.BufferGeometry;
  posAttr: THREE.BufferAttribute;
  colAttr: THREE.BufferAttribute;
  frame: number;
}

const trails = new Map<string, TrailEntry>();
let trailsVisible = true;

const TRAIL_COLOR = new THREE.Color(0x6699dd);

function createTrail(scene: THREE.Scene): TrailEntry {
  // Pre-allocate max-size attributes
  const posArr = new Float32Array(TRAIL_LENGTH * 3);
  const colArr = new Float32Array(TRAIL_LENGTH * 3);
  const geometry = new THREE.BufferGeometry();
  const posAttr = new THREE.BufferAttribute(posArr, 3);
  const colAttr = new THREE.BufferAttribute(colArr, 3);
  posAttr.setUsage(THREE.DynamicDrawUsage);
  colAttr.setUsage(THREE.DynamicDrawUsage);
  geometry.setAttribute("position", posAttr);
  geometry.setAttribute("color", colAttr);
  geometry.setDrawRange(0, 0);

  const material = new THREE.LineBasicMaterial({
    vertexColors: true,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const line = new THREE.Line(geometry, material);
  line.frustumCulled = false;
  scene.add(line);

  return {
    buffer: createTrailBuffer(TRAIL_LENGTH),
    line,
    geometry,
    posAttr,
    colAttr,
    frame: 0,
  };
}

/** Flush all trail history (call after tab-resume to avoid ghost lines). */
export function flushTrails(): void {
  for (const trail of trails.values()) {
    trail.buffer.count = 0;
    trail.buffer.head = 0;
    trail.geometry.setDrawRange(0, 0);
  }
}

/** Remove all trail objects from their scenes and clear the map. */
export function clearTrails(): void {
  for (const trail of trails.values()) {
    trail.line.geometry.dispose();
    (trail.line.material as THREE.Material).dispose();
    trail.line.parent?.remove(trail.line);
  }
  trails.clear();
}

export function updateOrbitalTrails(scene: THREE.Scene): void {
  if (!trailsVisible) return;

  const sats = getSatellites();

  for (const [id, sat] of sats) {
    let trail = trails.get(id);
    if (!trail) {
      trail = createTrail(scene);
      trails.set(id, trail);
    }

    trail.frame++;
    if (trail.frame % SAMPLE_EVERY !== 0) continue;

    // Record the mesh's actual rendered position (post-lerp), not the
    // snapshot target — otherwise the trail leads the satellite.
    const pos = sat.mesh.position;
    pushSample(trail.buffer, pos.x, pos.y, pos.z);

    // Extract draw-order points capped by arc length
    const points = extractDrawPoints(trail.buffer, MAX_ARC_LENGTH);
    const drawCount = points.length;

    const pa = trail.posAttr.array as Float32Array;
    const ca = trail.colAttr.array as Float32Array;

    for (let j = 0; j < drawCount; j++) {
      const d3 = j * 3;
      pa[d3] = points[j]!.x;
      pa[d3 + 1] = points[j]!.y;
      pa[d3 + 2] = points[j]!.z;

      // Linear fade: zero at oldest drawn point, full brightness at newest.
      const t = j / drawCount;
      const brightness = t * 0.8;
      ca[d3] = TRAIL_COLOR.r * brightness;
      ca[d3 + 1] = TRAIL_COLOR.g * brightness;
      ca[d3 + 2] = TRAIL_COLOR.b * brightness;
    }

    trail.posAttr.needsUpdate = true;
    trail.colAttr.needsUpdate = true;
    trail.geometry.setDrawRange(0, drawCount);
  }

  // Remove trails for satellites that no longer exist
  for (const [id, trail] of trails) {
    if (!sats.has(id)) {
      trail.line.geometry.dispose();
      (trail.line.material as THREE.Material).dispose();
      trail.line.parent?.remove(trail.line);
      trails.delete(id);
    }
  }
}

export function setTrailsVisible(visible: boolean): void {
  trailsVisible = visible;
  for (const trail of trails.values()) {
    trail.line.visible = visible;
  }
  if (!visible) {
    for (const trail of trails.values()) {
      trail.buffer.count = 0;
      trail.buffer.head = 0;
    }
  }
}
