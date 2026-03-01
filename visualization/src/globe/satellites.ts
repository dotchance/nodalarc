/** Satellite mesh management — shared geometry, per-sat mesh + smooth motion.
 *
 *  Motion model: dead-reckoning via velocity vectors provides frame-to-frame
 *  continuity. When a new snapshot arrives (1Hz), the "true" position is
 *  stored separately and the dead-reckoned target is gently steered toward
 *  it, avoiding visible jumps.
 */

import * as THREE from "three";
import { SAT_RADIUS, SAT_SEGMENTS, AREA_COLORS, PLANE_COLORS, EARTH_RADIUS, KM_PER_UNIT } from "../config";
import { geoToWorld, velocityToScene } from "./geo";
import type { NodeState, ColorMode } from "../types";

/** Shared geometry for all satellites. */
const sharedGeo = new THREE.SphereGeometry(SAT_RADIUS, SAT_SEGMENTS, SAT_SEGMENTS);

/** Correction blend per frame: how fast dead-reckoning steers toward snapshot truth.
 *  0.03 at 60fps → ~83% corrected after 1 second (before next snapshot). */
const CORRECTION_RATE = 0.03;

export interface SatelliteEntry {
  mesh: THREE.Mesh;
  /** Dead-reckoned position (updated every frame by velocity). */
  targetPosition: THREE.Vector3;
  /** Latest ground-truth position from snapshot (updated at 1Hz). */
  snapshotPosition: THREE.Vector3;
  velocity: THREE.Vector3 | null;
  nodeState: NodeState;
}

const satellites = new Map<string, SatelliteEntry>();

export function getSatellites(): Map<string, SatelliteEntry> {
  return satellites;
}

export function updateSatellites(
  nodes: NodeState[],
  scene: THREE.Scene,
  colorMode: ColorMode,
): void {
  const seen = new Set<string>();

  for (const node of nodes) {
    if (node.node_type !== "satellite") continue;
    seen.add(node.node_id);

    const pos = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);
    const vel = node.vel_x_km_s != null && node.vel_y_km_s != null && node.vel_z_km_s != null
      ? velocityToScene(node.vel_x_km_s, node.vel_y_km_s, node.vel_z_km_s)
      : null;

    const existing = satellites.get(node.node_id);
    if (existing) {
      // Update snapshot truth — dead-reckoning will steer toward it
      existing.snapshotPosition.copy(pos);
      existing.velocity = vel;
      existing.nodeState = node;
      updateSatColor(existing, colorMode);
    } else {
      const material = new THREE.MeshBasicMaterial({ color: getSatColor(node, colorMode) });
      const mesh = new THREE.Mesh(sharedGeo, material);
      mesh.position.copy(pos);
      mesh.userData["nodeId"] = node.node_id;
      mesh.userData["nodeType"] = "satellite";
      scene.add(mesh);
      satellites.set(node.node_id, {
        mesh,
        targetPosition: pos.clone(),
        snapshotPosition: pos.clone(),
        velocity: vel,
        nodeState: node,
      });
    }
  }

  // Remove satellites no longer in snapshot
  for (const [id, entry] of satellites) {
    if (!seen.has(id)) {
      scene.remove(entry.mesh);
      satellites.delete(id);
    }
  }
}

export function animateSatellites(dt: number): void {
  for (const entry of satellites.values()) {
    if (entry.velocity) {
      // Dead-reckoning: advance target by velocity
      entry.targetPosition.addScaledVector(entry.velocity, dt);
    }

    // Gently steer dead-reckoned target toward snapshot truth
    entry.targetPosition.lerp(entry.snapshotPosition, CORRECTION_RATE);

    // Re-project onto orbital shell so satellites don't drift inward/outward
    const alt = EARTH_RADIUS + entry.nodeState.alt_km / KM_PER_UNIT;
    entry.targetPosition.normalize().multiplyScalar(alt);

    // Smooth mesh toward target
    entry.mesh.position.lerp(entry.targetPosition, 0.15);
  }
}

function getSatColor(node: NodeState, mode: ColorMode): number {
  if (mode === "area" && node.routing_area) {
    return AREA_COLORS[node.routing_area] ?? 0x888888;
  }
  if (mode === "plane" && node.plane != null) {
    return PLANE_COLORS[node.plane % PLANE_COLORS.length] ?? 0x888888;
  }
  return 0x888888;
}

function updateSatColor(entry: SatelliteEntry, mode: ColorMode): void {
  const color = getSatColor(entry.nodeState, mode);
  (entry.mesh.material as THREE.MeshBasicMaterial).color.setHex(color);
}

export function recolorAllSatellites(colorMode: ColorMode): void {
  for (const entry of satellites.values()) {
    updateSatColor(entry, colorMode);
  }
}
