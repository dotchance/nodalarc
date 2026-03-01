/** Ground track lines — faint orbit traces on Earth surface. Off by default.
 *  Extrapolates +-10 minutes using ECEF velocity, projects onto earth sphere.
 */

import * as THREE from "three";
import { EARTH_RADIUS } from "../config";
import { geoToWorld, velocityToScene } from "./geo";
import type { NodeState } from "../types";

const trackLines = new Map<string, THREE.Line>();
const trackMaterial = new THREE.LineBasicMaterial({
  color: 0x4488ff,
  transparent: true,
  opacity: 0.15,
  depthWrite: false,
});

/** Surface offset (scene units) to avoid z-fighting with earth sphere. */
const SURFACE_OFFSET = EARTH_RADIUS * 1.002;

export function updateGroundTracks(nodes: NodeState[], scene: THREE.Scene): void {
  const sats = nodes.filter(
    (n) => n.node_type === "satellite" && n.vel_x_km_s != null,
  );
  const seen = new Set<string>();

  for (const sat of sats) {
    seen.add(sat.node_id);

    if (trackLines.has(sat.node_id)) continue;

    // Get current position and velocity in scene coordinates
    const pos = geoToWorld(sat.lat_deg, sat.lon_deg, sat.alt_km);
    const vel = velocityToScene(
      sat.vel_x_km_s ?? 0,
      sat.vel_y_km_s ?? 0,
      sat.vel_z_km_s ?? 0,
    );

    // Extrapolate +-10 minutes (40 points total)
    const points: THREE.Vector3[] = [];
    const steps = 40;
    const dtPerStep = 30; // 30 seconds per step

    for (let i = -steps / 2; i <= steps / 2; i++) {
      const t = i * dtPerStep;
      // Extrapolate position in scene space
      const px = pos.x + vel.x * t;
      const py = pos.y + vel.y * t;
      const pz = pos.z + vel.z * t;
      // Project onto earth surface (normalize to SURFACE_OFFSET radius)
      const len = Math.sqrt(px * px + py * py + pz * pz);
      if (len < 0.01) continue;
      const scale = SURFACE_OFFSET / len;
      points.push(new THREE.Vector3(px * scale, py * scale, pz * scale));
    }

    if (points.length < 2) continue;

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
