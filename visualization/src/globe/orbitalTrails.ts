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

/** Max trail points in the ring buffer (upper bound for memory). */
const TRAIL_LENGTH = 600;

/** Sample every Nth frame. */
const SAMPLE_EVERY = 2;

/** Max trail arc length in scene units. Tuned so the trail is a short
 *  directional indicator, not a full orbit trace. */
const MAX_ARC_LENGTH = 1.8;

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

    // Record ground-truth snapshot position (not mesh, which is mid-lerp)
    const pos = sat.currPosition;
    const i3 = trail.head * 3;
    trail.buf[i3] = pos.x;
    trail.buf[i3 + 1] = pos.y;
    trail.buf[i3 + 2] = pos.z;

    trail.head = (trail.head + 1) % TRAIL_LENGTH;
    if (trail.count < TRAIL_LENGTH) trail.count++;

    // Walk backward from newest point, accumulating arc length,
    // and only draw points within MAX_ARC_LENGTH.
    const pa = trail.posAttr.array as Float32Array;
    const ca = trail.colAttr.array as Float32Array;

    // Newest point index in ring buffer
    const newestRing = (trail.head - 1 + TRAIL_LENGTH) % TRAIL_LENGTH;

    // Collect points newest-first, stop when arc budget is exhausted
    let drawCount = 1;
    let arcLen = 0;
    let prevX = trail.buf[newestRing * 3]!;
    let prevY = trail.buf[newestRing * 3 + 1]!;
    let prevZ = trail.buf[newestRing * 3 + 2]!;

    for (let k = 1; k < trail.count; k++) {
      const ringIdx = (newestRing - k + TRAIL_LENGTH) % TRAIL_LENGTH;
      const rx = trail.buf[ringIdx * 3]!;
      const ry = trail.buf[ringIdx * 3 + 1]!;
      const rz = trail.buf[ringIdx * 3 + 2]!;
      const dx = rx - prevX;
      const dy = ry - prevY;
      const dz = rz - prevZ;
      arcLen += Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (arcLen > MAX_ARC_LENGTH) break;
      drawCount++;
      prevX = rx;
      prevY = ry;
      prevZ = rz;
    }

    // Write draw-order: oldest (index 0) to newest (index drawCount-1)
    for (let j = 0; j < drawCount; j++) {
      // j=0 is the oldest drawn point, j=drawCount-1 is newest
      const k = drawCount - 1 - j; // offset from newest
      const ringIdx = (newestRing - k + TRAIL_LENGTH) % TRAIL_LENGTH;
      const s3 = ringIdx * 3;
      const d3 = j * 3;
      pa[d3] = trail.buf[s3]!;
      pa[d3 + 1] = trail.buf[s3 + 1]!;
      pa[d3 + 2] = trail.buf[s3 + 2]!;

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
      trail.count = 0;
      trail.head = 0;
    }
  }
}
