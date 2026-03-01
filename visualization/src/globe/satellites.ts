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

/** Shared glow texture for satellite visibility at distance. */
let glowTexture: THREE.Texture | null = null;
function getGlowTexture(): THREE.Texture {
  if (!glowTexture) {
    const size = 64;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d")!;
    const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    gradient.addColorStop(0, "rgba(255, 255, 255, 0.6)");
    gradient.addColorStop(0.3, "rgba(255, 255, 255, 0.15)");
    gradient.addColorStop(1, "rgba(255, 255, 255, 0)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
    glowTexture = new THREE.CanvasTexture(canvas);
    glowTexture.needsUpdate = true;
  }
  return glowTexture;
}

/** Correction blend per frame: how fast dead-reckoning steers toward snapshot truth.
 *  0.03 at 60fps → ~83% corrected after 1 second (before next snapshot). */
const CORRECTION_RATE = 0.03;

export interface SatelliteEntry {
  mesh: THREE.Mesh;
  glow: THREE.Sprite;
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
      const color = getSatColor(node, colorMode);
      const material = new THREE.MeshBasicMaterial({ color });
      const mesh = new THREE.Mesh(sharedGeo, material);
      mesh.position.copy(pos);
      mesh.userData["nodeId"] = node.node_id;
      mesh.userData["nodeType"] = "satellite";
      scene.add(mesh);

      // Glow sprite for far-distance visibility
      const glowMat = new THREE.SpriteMaterial({
        map: getGlowTexture(),
        color,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });
      const glow = new THREE.Sprite(glowMat);
      glow.scale.set(SAT_RADIUS * 5, SAT_RADIUS * 5, 1);
      glow.position.copy(pos);
      scene.add(glow);

      satellites.set(node.node_id, {
        mesh,
        glow,
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
      scene.remove(entry.glow);
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
    entry.glow.position.copy(entry.mesh.position);
  }
}

function getSatColor(node: NodeState, mode: ColorMode): number {
  if (mode === "area" && node.routing_area) {
    return AREA_COLORS[node.routing_area] ?? 0xaabbcc;
  }
  if (mode === "plane" && node.plane != null) {
    return PLANE_COLORS[node.plane % PLANE_COLORS.length] ?? 0xaabbcc;
  }
  return 0xccddee;
}

function updateSatColor(entry: SatelliteEntry, mode: ColorMode): void {
  const color = getSatColor(entry.nodeState, mode);
  (entry.mesh.material as THREE.MeshBasicMaterial).color.setHex(color);
  (entry.glow.material as THREE.SpriteMaterial).color.setHex(color);
}

export function recolorAllSatellites(colorMode: ColorMode): void {
  for (const entry of satellites.values()) {
    updateSatColor(entry, colorMode);
  }
}
