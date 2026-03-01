/** Satellite mesh management — shared geometry, per-sat mesh + lerp. */

import * as THREE from "three";
import { SAT_RADIUS, SAT_SEGMENTS, LERP_FACTOR, AREA_COLORS, PLANE_COLORS } from "../config";
import { geoToWorld, velocityToScene } from "./geo";
import type { NodeState, ColorMode } from "../types";

/** Shared geometry for all satellites. */
const sharedGeo = new THREE.SphereGeometry(SAT_RADIUS, SAT_SEGMENTS, SAT_SEGMENTS);

export interface SatelliteEntry {
  mesh: THREE.Mesh;
  targetPosition: THREE.Vector3;
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

    const target = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);
    const vel = node.vel_x_km_s != null && node.vel_y_km_s != null && node.vel_z_km_s != null
      ? velocityToScene(node.vel_x_km_s, node.vel_y_km_s, node.vel_z_km_s)
      : null;

    const existing = satellites.get(node.node_id);
    if (existing) {
      existing.targetPosition.copy(target);
      existing.velocity = vel;
      existing.nodeState = node;
      updateSatColor(existing, colorMode);
    } else {
      const material = new THREE.MeshBasicMaterial({ color: getSatColor(node, colorMode) });
      const mesh = new THREE.Mesh(sharedGeo, material);
      mesh.position.copy(target);
      mesh.userData["nodeId"] = node.node_id;
      mesh.userData["nodeType"] = "satellite";
      scene.add(mesh);
      satellites.set(node.node_id, {
        mesh,
        targetPosition: target,
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
      // Dead-reckoning: move target by velocity * dt
      entry.targetPosition.addScaledVector(entry.velocity, dt);
    }
    entry.mesh.position.lerp(entry.targetPosition, LERP_FACTOR);
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
