/** Ground track lines — faint orbit traces on Earth surface. Off by default. */

import * as THREE from "three";
import { KM_PER_UNIT } from "../config";
import { geoToWorld } from "./geo";
import type { NodeState } from "../types";

const trackLines = new Map<string, THREE.Line>();
const trackMaterial = new THREE.LineBasicMaterial({
  color: 0x4488ff,
  transparent: true,
  opacity: 0.15,
});

export function updateGroundTracks(nodes: NodeState[], scene: THREE.Scene): void {
  const sats = nodes.filter((n) => n.node_type === "satellite" && n.vel_x_km_s != null);
  const seen = new Set<string>();

  for (const sat of sats) {
    seen.add(sat.node_id);

    if (trackLines.has(sat.node_id)) continue;

    // Extrapolate +-10 minutes of ground track (20 points)
    const points: THREE.Vector3[] = [];
    const steps = 20;
    const dtPerStep = 60; // 1 minute per step

    for (let i = -steps / 2; i <= steps / 2; i++) {
      const t = i * dtPerStep;
      // Simple linear extrapolation in geodetic (rough approximation)
      const lonRate = (sat.vel_x_km_s ?? 0) / (6371 * Math.cos((sat.lat_deg * Math.PI) / 180)) * (180 / Math.PI);
      const latRate = (sat.vel_y_km_s ?? 0) / 6371 * (180 / Math.PI);
      const lat = sat.lat_deg + latRate * t;
      const lon = sat.lon_deg + lonRate * t;
      // Surface point (alt_km = 0, slight offset to avoid z-fighting)
      const surfaceAlt = 2 / KM_PER_UNIT;
      points.push(geoToWorld(lat, lon, surfaceAlt));
    }

    const geometry = new THREE.BufferGeometry().setFromPoints(points);
    const line = new THREE.Line(geometry, trackMaterial);
    scene.add(line);
    trackLines.set(sat.node_id, line);
  }

  // Remove tracks for missing satellites
  for (const [id, line] of trackLines) {
    if (!seen.has(id)) {
      scene.remove(line);
      line.geometry.dispose();
      trackLines.delete(id);
    }
  }
}

export function clearGroundTracks(scene: THREE.Scene): void {
  for (const [id, line] of trackLines) {
    scene.remove(line);
    line.geometry.dispose();
    trackLines.delete(id);
  }
}
