/** Satellite mesh management — shared geometry, per-sat mesh + smooth motion.
 *
 *  Motion model: snapshots arrive at ~1Hz with ground-truth positions.
 *  Between snapshots, mesh.position linearly interpolates from prevPosition
 *  (where the mesh was when the snapshot arrived) to snapshotPosition
 *  (the new ground-truth). This gives constant-speed motion with no
 *  stop-and-jump artifacts.
 */

import * as THREE from "three";
import { SAT_RADIUS, SAT_SEGMENTS, AREA_COLORS, getPlaneColor } from "../config";
import { geoToWorld } from "./geo";
import type { NodeState, ColorMode } from "../types";

/** Expected seconds between snapshots (VS-API broadcasts at ~1Hz). */
export const SNAPSHOT_INTERVAL = 1.0;

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

export interface SatelliteEntry {
  mesh: THREE.Mesh;
  glow: THREE.Sprite;
  /** Position at the moment the latest snapshot arrived (interpolation start). */
  prevPosition: THREE.Vector3;
  /** Latest ground-truth position from snapshot (interpolation end). */
  snapshotPosition: THREE.Vector3;
  /** Seconds elapsed since the last snapshot update. */
  snapshotAge: number;
  nodeState: NodeState;
}

const satellites = new Map<string, SatelliteEntry>();

export function getSatellites(): Map<string, SatelliteEntry> {
  return satellites;
}

/**
 * Compute linear interpolation parameter for satellite animation.
 * Returns t in [0, 1]: 0 = at prevPosition, 1 = at snapshotPosition.
 * Clamps at 1.0 so the satellite holds at the target if the next snapshot is late.
 */
export function interpParam(snapshotAge: number, interval: number): number {
  if (interval <= 0) return 1;
  return Math.min(snapshotAge / interval, 1.0);
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

    const existing = satellites.get(node.node_id);
    if (existing) {
      // Save current mesh position as interpolation start
      existing.prevPosition.copy(existing.mesh.position);
      existing.snapshotPosition.copy(pos);
      existing.snapshotAge = 0;
      existing.nodeState = node;
      updateSatColor(existing, colorMode);

      // If mesh drifted far from truth (tab switch, reconnect), snap immediately
      if (existing.prevPosition.distanceTo(pos) > SAT_RADIUS * 4) {
        existing.prevPosition.copy(pos);
        existing.mesh.position.copy(pos);
        existing.glow.position.copy(pos);
      }
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
        prevPosition: pos.clone(),
        snapshotPosition: pos.clone(),
        snapshotAge: 0,
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

const _tmpVec = new THREE.Vector3();

export function animateSatellites(dt: number): void {
  // Tab was backgrounded — snap everything to snapshot truth.
  if (dt > 0.15) {
    for (const entry of satellites.values()) {
      entry.prevPosition.copy(entry.snapshotPosition);
      entry.snapshotAge = SNAPSHOT_INTERVAL;
      entry.mesh.position.copy(entry.snapshotPosition);
      entry.glow.position.copy(entry.snapshotPosition);
    }
    return;
  }

  // Linear interpolation from prevPosition to snapshotPosition over ~1 second.
  // Constant speed — no stop-and-jump at snapshot boundaries.
  for (const entry of satellites.values()) {
    entry.snapshotAge += dt;
    const t = interpParam(entry.snapshotAge, SNAPSHOT_INTERVAL);
    _tmpVec.copy(entry.prevPosition).lerp(entry.snapshotPosition, t);
    entry.mesh.position.copy(_tmpVec);
    entry.glow.position.copy(_tmpVec);
  }
}

function getSatColor(node: NodeState, mode: ColorMode): number {
  if (mode === "area" && node.routing_area) {
    return AREA_COLORS[node.routing_area] ?? 0xaabbcc;
  }
  if (mode === "plane" && node.plane != null) {
    return getPlaneColor(node.plane);
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
