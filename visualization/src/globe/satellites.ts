/** Satellite mesh management — shared geometry, per-sat mesh + smooth motion.
 *
 *  Motion model: velocity-based dead-reckoning with correction blending.
 *
 *  Each snapshot delivers a ground-truth position AND an ECEF velocity from
 *  the OME propagator.  Between snapshots the mesh advances at that velocity.
 *  When a new snapshot arrives, any positional error is blended out over
 *  CORRECTION_DURATION seconds so the transition is invisible.
 *
 *  This model does not depend on snapshot timing — it works at any cadence.
 */

import * as THREE from "three";
import { SAT_RADIUS, SAT_SEGMENTS, AREA_COLORS, getPlaneColor } from "../config";
import { geoToWorld, velocityToScene } from "./geo";
import type { NodeState, ColorMode } from "../types";

/** Duration over which position corrections are blended out (seconds). */
const CORRECTION_DURATION = 0.3;

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
  /** Latest ground-truth position (scene coords). */
  snapshotPosition: THREE.Vector3;
  /** Velocity in scene units/second. */
  snapshotVelocity: THREE.Vector3;
  /** performance.now() when snapshot arrived. */
  snapshotWallTime: number;
  /** Error to blend out (starts non-zero, decays to zero). */
  correctionOffset: THREE.Vector3;
  /** Seconds of correction remaining. */
  correctionRemaining: number;
  nodeState: NodeState;
}

const satellites = new Map<string, SatelliteEntry>();

export function getSatellites(): Map<string, SatelliteEntry> {
  return satellites;
}

// Reusable temporaries (avoid per-frame allocation)
const _tmpExtrapolated = new THREE.Vector3();
const _tmpPos = new THREE.Vector3();

export function updateSatellites(
  nodes: NodeState[],
  scene: THREE.Scene,
  colorMode: ColorMode,
): void {
  const seen = new Set<string>();
  const now = performance.now();

  for (const node of nodes) {
    if (node.node_type !== "satellite") continue;
    seen.add(node.node_id);

    const newPos = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);
    const newVel = velocityToScene(
      node.vel_x_km_s ?? 0,
      node.vel_y_km_s ?? 0,
      node.vel_z_km_s ?? 0,
    );

    const existing = satellites.get(node.node_id);
    if (existing) {
      // Where the mesh WOULD be right now by extrapolating from the old snapshot
      const elapsedS = (now - existing.snapshotWallTime) / 1000;
      _tmpExtrapolated.copy(existing.snapshotPosition)
        .addScaledVector(existing.snapshotVelocity, elapsedS);

      // Correction = where we ARE (old extrapolation) minus where we SHOULD BE (new truth).
      // This keeps the mesh at its current position at blend start, then decays to zero.
      const correction = new THREE.Vector3().subVectors(_tmpExtrapolated, newPos);

      // If correction is huge (tab switch / reconnect), snap immediately
      if (correction.length() > SAT_RADIUS * 4) {
        existing.correctionOffset.set(0, 0, 0);
        existing.correctionRemaining = 0;
      } else {
        existing.correctionOffset.copy(correction);
        existing.correctionRemaining = CORRECTION_DURATION;
      }

      existing.snapshotPosition.copy(newPos);
      existing.snapshotVelocity.copy(newVel);
      existing.snapshotWallTime = now;
      existing.nodeState = node;
      updateSatColor(existing, colorMode);
    } else {
      // New satellite — place directly, no correction needed
      const color = getSatColor(node, colorMode);
      const material = new THREE.MeshBasicMaterial({ color });
      const mesh = new THREE.Mesh(sharedGeo, material);
      mesh.position.copy(newPos);
      mesh.userData["nodeId"] = node.node_id;
      mesh.userData["nodeType"] = "satellite";
      scene.add(mesh);

      const glowMat = new THREE.SpriteMaterial({
        map: getGlowTexture(),
        color,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });
      const glow = new THREE.Sprite(glowMat);
      glow.scale.set(SAT_RADIUS * 5, SAT_RADIUS * 5, 1);
      glow.position.copy(newPos);
      scene.add(glow);

      satellites.set(node.node_id, {
        mesh,
        glow,
        snapshotPosition: newPos.clone(),
        snapshotVelocity: newVel.clone(),
        snapshotWallTime: now,
        correctionOffset: new THREE.Vector3(0, 0, 0),
        correctionRemaining: 0,
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
  const now = performance.now();

  for (const entry of satellites.values()) {
    const elapsed = (now - entry.snapshotWallTime) / 1000;

    // Extrapolate from snapshot truth using velocity
    _tmpExtrapolated.copy(entry.snapshotPosition)
      .addScaledVector(entry.snapshotVelocity, elapsed);

    if (entry.correctionRemaining > 0) {
      // Blend out the correction offset over CORRECTION_DURATION
      const blend = 1.0 - (entry.correctionRemaining / CORRECTION_DURATION);
      _tmpPos.copy(_tmpExtrapolated)
        .addScaledVector(entry.correctionOffset, 1.0 - blend);
      entry.correctionRemaining -= dt;
      if (entry.correctionRemaining <= 0) {
        entry.correctionRemaining = 0;
        entry.correctionOffset.set(0, 0, 0);
      }
      entry.mesh.position.copy(_tmpPos);
    } else {
      entry.mesh.position.copy(_tmpExtrapolated);
    }

    entry.glow.position.copy(entry.mesh.position);
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
