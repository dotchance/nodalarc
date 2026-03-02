/** Orbital trails — fading tail behind each satellite showing recent path.
 *
 *  Uses brightness fade (RGB → black) with additive blending rather than
 *  alpha, since Three.js LineBasicMaterial ignores per-vertex alpha.
 */

import * as THREE from "three";
import { getSatellites } from "./satellites";

/** Max trail points per satellite. At 60fps sampling every 3rd frame,
 *  450 points ≈ 22 seconds of visible trail. */
const TRAIL_LENGTH = 450;

/** Sample every Nth frame. */
const SAMPLE_EVERY = 3;

interface TrailEntry {
  /** Ring buffer of recorded positions (x,y,z triples). */
  buf: Float32Array;
  head: number;
  count: number;
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
    buf: new Float32Array(TRAIL_LENGTH * 3),
    head: 0,
    count: 0,
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
    trail.count = 0;
    trail.head = 0;
    trail.geometry.setDrawRange(0, 0);
  }
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

    // Record current position into ring buffer
    const pos = sat.mesh.position;
    const i3 = trail.head * 3;
    trail.buf[i3] = pos.x;
    trail.buf[i3 + 1] = pos.y;
    trail.buf[i3 + 2] = pos.z;

    trail.head = (trail.head + 1) % TRAIL_LENGTH;
    if (trail.count < TRAIL_LENGTH) trail.count++;

    // Write draw-order positions and brightness-faded colors into attributes
    const oldest = trail.count < TRAIL_LENGTH ? 0 : trail.head;
    const pa = trail.posAttr.array as Float32Array;
    const ca = trail.colAttr.array as Float32Array;

    for (let j = 0; j < trail.count; j++) {
      const srcIdx = (oldest + j) % TRAIL_LENGTH;
      const s3 = srcIdx * 3;
      const d3 = j * 3;
      pa[d3] = trail.buf[s3]!;
      pa[d3 + 1] = trail.buf[s3 + 1]!;
      pa[d3 + 2] = trail.buf[s3 + 2]!;

      // Linear fade: full brightness at head, zero at tail.
      const t = j / trail.count;
      const brightness = t * 0.8;
      ca[d3] = TRAIL_COLOR.r * brightness;
      ca[d3 + 1] = TRAIL_COLOR.g * brightness;
      ca[d3 + 2] = TRAIL_COLOR.b * brightness;
    }

    trail.posAttr.needsUpdate = true;
    trail.colAttr.needsUpdate = true;
    trail.geometry.setDrawRange(0, trail.count);
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
      trail.count = 0;
      trail.head = 0;
    }
  }
}
