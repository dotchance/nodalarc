// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Render orbit rings for ALL satellites when "Satellite Paths" toggle is on.
 *  Reuses computeOrbitPositions from orbitPins.ts.
 *  Uses plane color, thinner line (width 2), lower opacity (0.2).
 */

import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { getSatellites } from "./satellites";
import { computeOrbitPositions } from "./orbitPins";
import { getPlaneColor } from "../config";
import { velocityToScene } from "./geo";

interface OrbitRing {
  line: Line2;
  geometry: LineGeometry;
  material: LineMaterial;
}

const orbits = new Map<string, OrbitRing>();
let lastSatCount = 0;

export function updateAllOrbits(scene: THREE.Scene, show: boolean): void {
  if (!show) {
    clearAllOrbits(scene);
    return;
  }

  const sats = getSatellites();

  // Only recompute when satellite set changes
  if (sats.size === lastSatCount && orbits.size === sats.size) {
    // Keep resolution in sync
    for (const ring of orbits.values()) {
      ring.material.resolution.set(window.innerWidth, window.innerHeight);
    }
    return;
  }

  // Clear stale orbits for nodes no longer present
  for (const [id, ring] of orbits) {
    if (!sats.has(id)) {
      scene.remove(ring.line);
      ring.geometry.dispose();
      ring.material.dispose();
      orbits.delete(id);
    }
  }

  // Add orbits for new satellites
  for (const [id, sat] of sats) {
    if (orbits.has(id)) continue;

    const ns = sat.nodeState;
    if (ns.vel_x_km_s == null || ns.vel_y_km_s == null || ns.vel_z_km_s == null) continue;
    if (ns.plane == null) continue;

    const pos = sat.mesh.position.clone();
    const vel = velocityToScene(ns.vel_x_km_s, ns.vel_y_km_s, ns.vel_z_km_s);

    const positions = computeOrbitPositions(pos, vel);
    const geometry = new LineGeometry();
    geometry.setPositions(positions);

    const color = new THREE.Color(getPlaneColor(ns.plane));
    const material = new LineMaterial({
      color: color.getHex(),
      linewidth: 2,
      worldUnits: false,
      transparent: true,
      opacity: 0.2,
      depthWrite: false,
    });
    material.resolution.set(window.innerWidth, window.innerHeight);

    const line = new Line2(geometry, material);
    line.computeLineDistances();
    line.frustumCulled = false;
    scene.add(line);

    orbits.set(id, { line, geometry, material });
  }

  lastSatCount = sats.size;
}

export function clearAllOrbits(scene: THREE.Scene): void {
  for (const ring of orbits.values()) {
    scene.remove(ring.line);
    ring.geometry.dispose();
    ring.material.dispose();
  }
  orbits.clear();
  lastSatCount = 0;
}
