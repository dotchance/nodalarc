// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Render orbit rings for ALL satellites — batched into ONE draw call.
 *
 *  When "Satellite Paths" toggle is on, computes orbit rings from each
 *  satellite's position and velocity, packs all segments into a single
 *  LineSegments2 batch with per-vertex plane colors.
 *
 *  History: the original per-satellite Line2 approach created one draw
 *  call per satellite (90 Line2 = 90 TRIANGLES draw calls). Batching
 *  into one LineSegments2 reduces to 1 draw call.
 */

import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { getSatellites } from "./satellites";
import { getNodeLocalPosition, getNodeWorldPosition } from "./positionLookup";
import { computeOrbitPositions } from "./orbitPins";
import { getPlaneColor } from "../config";
import { velocityToScene } from "./geo";
import { worldVelocity } from "./astronomy";

const ORBIT_SAMPLES = 180;
const SEGMENTS_PER_ORBIT = ORBIT_SAMPLES; // closed ring = N segments from N+1 vertices

const _worldPos = new THREE.Vector3();
const _localPos = new THREE.Vector3();
const _velEcef = new THREE.Vector3();
const _velWorld = new THREE.Vector3();

let batch: LineSegments2 | null = null;
let geometry: LineSegmentsGeometry | null = null;
let material: LineMaterial | null = null;
let lastSatCount = 0;

export function updateAllOrbits(
  scene: THREE.Scene,
  show: boolean,
  viewFrameRotationRad: number,
  frameAngularVelocityRadS: number,
): void {
  if (!show) {
    clearAllOrbits(scene);
    return;
  }

  const sats = getSatellites();

  // Only rebuild when satellite set changes
  if (sats.size === lastSatCount && batch) {
    if (material) material.resolution.set(window.innerWidth, window.innerHeight);
    return;
  }

  // Clear old batch if sat count changed
  clearAllOrbits(scene);

  // Collect all orbit segments
  const allPositions: number[] = [];
  const allColors: number[] = [];

  for (const [id, sat] of sats) {
    const ns = sat.nodeState;
    if (ns.vel_x_km_s == null || ns.vel_y_km_s == null || ns.vel_z_km_s == null) continue;
    if (ns.plane == null) continue;

    if (!getNodeWorldPosition(id, _worldPos)) continue;
    if (!getNodeLocalPosition(id, _localPos)) continue;
    _velEcef.copy(velocityToScene(ns.vel_x_km_s, ns.vel_y_km_s, ns.vel_z_km_s));
    worldVelocity(_localPos, _velEcef, viewFrameRotationRad, frameAngularVelocityRadS, _velWorld);

    const positions = computeOrbitPositions(_worldPos, _velWorld);
    const color = new THREE.Color(getPlaneColor(ns.plane));
    const r = color.r;
    const g = color.g;
    const b = color.b;

    // Convert polyline vertices to segment pairs
    for (let i = 0; i < SEGMENTS_PER_ORBIT; i++) {
      const i0 = i * 3;
      const i1 = (i + 1) * 3;
      allPositions.push(
        positions[i0]!, positions[i0 + 1]!, positions[i0 + 2]!,
        positions[i1]!, positions[i1 + 1]!, positions[i1 + 2]!,
      );
      allColors.push(r, g, b, r, g, b);
    }
  }

  if (allPositions.length === 0) return;

  geometry = new LineSegmentsGeometry();
  geometry.computeBoundingSphere = () => {};
  geometry.computeBoundingBox = () => {};
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 1000);
  geometry.boundingBox = new THREE.Box3(
    new THREE.Vector3(-1000, -1000, -1000),
    new THREE.Vector3(1000, 1000, 1000),
  );
  geometry.setPositions(new Float32Array(allPositions));
  geometry.setColors(new Float32Array(allColors));

  material = new LineMaterial({
    color: 0xffffff,
    vertexColors: true,
    linewidth: 2,
    worldUnits: false,
    transparent: true,
    opacity: 0.2,
    depthWrite: false,
    resolution: new THREE.Vector2(window.innerWidth, window.innerHeight),
  });

  batch = new LineSegments2(geometry, material);
  batch.frustumCulled = false;
  scene.add(batch);

  lastSatCount = sats.size;
}

export function clearAllOrbits(scene: THREE.Scene): void {
  if (batch) {
    scene.remove(batch);
    batch.geometry.dispose();
    (batch.material as THREE.Material).dispose();
    batch = null;
  }
  geometry = null;
  material = null;
  lastSatCount = 0;
}
