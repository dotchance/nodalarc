// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Orbit pin display — Ctrl+click to pin up to 3 orbital great circles. */

import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { getSatellites } from "./satellites";
import { getNodeLocalPosition, getNodeWorldPosition } from "./positionLookup";
import { getPlaneColor } from "../config";
import { velocityToScene } from "./geo";
import { worldVelocity } from "./astronomy";

// Reusable temporaries for seed sampling.
const _pinWorldPos = new THREE.Vector3();
const _pinLocalPos = new THREE.Vector3();
const _pinVelEcef = new THREE.Vector3();
const _pinVelWorld = new THREE.Vector3();

interface OrbitPin {
  nodeId: string;
  plane: number;
  line: Line2;
  geometry: LineGeometry;
  material: LineMaterial;
  altitude_km: number;
  pinnedAt: number;
}

const pins = new Map<string, OrbitPin>();
const MAX_PINS = 7;
const ORBIT_SAMPLES = 180;
const ORBIT_LINE_WIDTH = 6;

/** Compute closed orbit ring positions from position and velocity vectors. */
export function computeOrbitPositions(pos: THREE.Vector3, vel: THREE.Vector3): Float32Array {
  const normal = new THREE.Vector3().crossVectors(pos, vel).normalize();
  const radius = pos.length();
  const positions = new Float32Array((ORBIT_SAMPLES + 1) * 3);
  const q = new THREE.Quaternion();

  for (let i = 0; i <= ORBIT_SAMPLES; i++) {
    const angle = (i * 2 * Math.PI) / ORBIT_SAMPLES;
    q.setFromAxisAngle(normal, angle);
    const p = pos.clone().normalize().multiplyScalar(radius).applyQuaternion(q);
    positions[i * 3] = p.x;
    positions[i * 3 + 1] = p.y;
    positions[i * 3 + 2] = p.z;
  }

  return positions;
}

export function toggleOrbitPin(
  nodeId: string,
  scene: THREE.Scene,
  viewFrameRotationRad: number,
  frameAngularVelocityRadS: number,
): void {
  // If already pinned, unpin
  const existing = pins.get(nodeId);
  if (existing) {
    scene.remove(existing.line);
    existing.geometry.dispose();
    existing.material.dispose();
    pins.delete(nodeId);
    return;
  }

  // Look up satellite
  const sat = getSatellites().get(nodeId);
  if (!sat) return;

  const ns = sat.nodeState;
  if (ns.vel_x_km_s == null || ns.vel_y_km_s == null || ns.vel_z_km_s == null) return;
  if (ns.plane == null) return;

  // Evict oldest if at capacity
  if (pins.size >= MAX_PINS) {
    let oldestKey: string | null = null;
    let oldestTime = Infinity;
    for (const [key, pin] of pins) {
      if (pin.pinnedAt < oldestTime) {
        oldestTime = pin.pinnedAt;
        oldestKey = key;
      }
    }
    if (oldestKey) {
      const old = pins.get(oldestKey)!;
      scene.remove(old.line);
      old.geometry.dispose();
      old.material.dispose();
      pins.delete(oldestKey);
    }
  }

  if (!getNodeWorldPosition(nodeId, _pinWorldPos)) return;
  if (!getNodeLocalPosition(nodeId, _pinLocalPos)) return;
  _pinVelEcef.copy(velocityToScene(ns.vel_x_km_s, ns.vel_y_km_s, ns.vel_z_km_s));
  worldVelocity(
    _pinLocalPos,
    _pinVelEcef,
    viewFrameRotationRad,
    frameAngularVelocityRadS,
    _pinVelWorld,
  );

  const positions = computeOrbitPositions(_pinWorldPos, _pinVelWorld);
  const geometry = new LineGeometry();
  geometry.setPositions(positions);

  const color = new THREE.Color(getPlaneColor(ns.plane));
  const material = new LineMaterial({
    color: color.getHex(),
    linewidth: ORBIT_LINE_WIDTH,
    worldUnits: false,
  });
  material.resolution.set(window.innerWidth, window.innerHeight);

  const line = new Line2(geometry, material);
  line.computeLineDistances();
  line.frustumCulled = false;
  scene.add(line);

  pins.set(nodeId, {
    nodeId,
    plane: ns.plane,
    line,
    geometry,
    material,
    altitude_km: ns.alt_km,
    pinnedAt: performance.now(),
  });
}

/** Re-seed every pinned orbit's ring geometry from the pinned satellite's
 *  current world pos + world velocity. Called from GlobeView.tsx on
 *  reference-frame toggle (plan §1.7) to preserve the user's pin list
 *  while updating ring geometry into the new frame. */
export function reseedAllPins(
  viewFrameRotationRad: number,
  frameAngularVelocityRadS: number,
): void {
  const sats = getSatellites();
  for (const pin of pins.values()) {
    const sat = sats.get(pin.nodeId);
    if (!sat) continue;
    const ns = sat.nodeState;
    if (ns.vel_x_km_s == null || ns.vel_y_km_s == null || ns.vel_z_km_s == null) continue;

    if (!getNodeWorldPosition(pin.nodeId, _pinWorldPos)) continue;
    if (!getNodeLocalPosition(pin.nodeId, _pinLocalPos)) continue;
    _pinVelEcef.copy(velocityToScene(ns.vel_x_km_s, ns.vel_y_km_s, ns.vel_z_km_s));
    worldVelocity(
      _pinLocalPos,
      _pinVelEcef,
      viewFrameRotationRad,
      frameAngularVelocityRadS,
      _pinVelWorld,
    );

    const positions = computeOrbitPositions(_pinWorldPos, _pinVelWorld);
    pin.geometry.setPositions(positions);
    pin.line.computeLineDistances();
  }
}

export function updateOrbitPins(scene: THREE.Scene): void {
  const sats = getSatellites();
  for (const [id, pin] of pins) {
    if (!sats.has(id)) {
      scene.remove(pin.line);
      pin.geometry.dispose();
      pin.material.dispose();
      pins.delete(id);
    }
  }
  // Keep resolution in sync with window size
  for (const pin of pins.values()) {
    pin.material.resolution.set(window.innerWidth, window.innerHeight);
  }
}

export function clearOrbitPins(scene: THREE.Scene): void {
  for (const pin of pins.values()) {
    scene.remove(pin.line);
    pin.geometry.dispose();
    pin.material.dispose();
  }
  pins.clear();
}

export function isOrbitPinned(nodeId: string): boolean {
  return pins.has(nodeId);
}
