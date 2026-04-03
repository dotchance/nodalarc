// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
/** Satellite mesh management — shared geometry, per-sat mesh + smooth motion.
 *
 *  Motion model: linear interpolation between consecutive snapshot positions.
 *
 *  Distance comes from sim_time (deterministic orbital mechanics).
 *  Duration comes from an EMA-smoothed wall-clock delivery rate.
 *  This decouples visual speed from packet delivery jitter.
 *
 *  The delivery rate EMA is module-level because all satellites arrive in the
 *  same snapshot at the same wall-clock instant.
 */

import * as THREE from "three";
import { SAT_RADIUS, SAT_SEGMENTS, AREA_COLORS, getPlaneColor } from "../config";
import { geoToWorld } from "./geo";
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

export interface SatelliteEntry {
  mesh: THREE.Mesh;
  glow: THREE.Sprite;
  /** Start of current lerp (set from mesh.position to avoid discontinuity). */
  prevPosition: THREE.Vector3;
  /** End of current lerp (ground-truth snapshot position). */
  currPosition: THREE.Vector3;
  /** performance.now() when this lerp segment started. */
  snapshotTime: number;
  /** Duration of current lerp segment in wall-ms (from sim_time × EMA rate). */
  interval: number;
  nodeState: NodeState;
}

const satellites = new Map<string, SatelliteEntry>();

export function getSatellites(): Map<string, SatelliteEntry> {
  return satellites;
}

// --- Module-level delivery rate EMA ---

/** EMA of wall-ms per sim-ms.  Shared across all satellites. */
let _wallMsPerSimMs = 1.0;
/** Whether the EMA has been seeded with a real measurement. */
let _rateSeeded = false;
/** sim_time (ms since epoch) of the last snapshot that advanced sim_time. */
let _lastSimTimeMs: number | null = null;
/** performance.now() when that snapshot arrived. */
let _lastSimWallTime: number | null = null;

const RATE_EMA_ALPHA = 0.15;

// Reusable temporary
const _tmpPos = new THREE.Vector3();

export function updateSatellites(
  nodes: NodeState[],
  scene: THREE.Scene,
  colorMode: ColorMode,
  simTime: string,
): void {
  const seen = new Set<string>();
  const now = performance.now();

  // --- Update delivery rate EMA (once per snapshot, not per satellite) ---
  const simTimeMs = new Date(simTime).getTime();
  let simDeltaMs = 0;

  if (_lastSimTimeMs !== null && simTimeMs > _lastSimTimeMs) {
    simDeltaMs = simTimeMs - _lastSimTimeMs;
    const wallDelta = now - _lastSimWallTime!;
    if (wallDelta > 10) {
      const instantRate = wallDelta / simDeltaMs;
      if (!_rateSeeded) {
        _wallMsPerSimMs = instantRate;
        _rateSeeded = true;
      } else {
        const ratio = instantRate / _wallMsPerSimMs;
        if (ratio > 0.2 && ratio < 5.0) {
          _wallMsPerSimMs = _wallMsPerSimMs * (1 - RATE_EMA_ALPHA) + instantRate * RATE_EMA_ALPHA;
        }
      }
    }
    _lastSimWallTime = now;
    _lastSimTimeMs = simTimeMs;
  } else if (_lastSimTimeMs === null) {
    _lastSimTimeMs = simTimeMs;
    _lastSimWallTime = now;
  }

  // --- Per-satellite updates ---
  for (const node of nodes) {
    if (node.node_type !== "satellite") continue;
    seen.add(node.node_id);

    const newPos = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);

    const existing = satellites.get(node.node_id);
    if (existing) {
      // Skip duplicate positions (WS pushes faster than dispatcher)
      if (newPos.distanceTo(existing.currPosition) < 0.0001) {
        existing.nodeState = node;
        updateSatColor(existing, colorMode);
        continue;
      }

      // Start new lerp from current visual position (no snap-back).
      existing.prevPosition.copy(existing.mesh.position);
      existing.currPosition.copy(newPos);
      // Duration from sim_time delta × smoothed delivery rate.
      // Falls back to previous interval if sim_time didn't advance (shouldn't
      // happen since we already skip duplicate positions, but defensive).
      if (simDeltaMs > 0) {
        existing.interval = simDeltaMs * _wallMsPerSimMs;
      }
      existing.snapshotTime = now;
      existing.nodeState = node;
      updateSatColor(existing, colorMode);
    } else {
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
        prevPosition: newPos.clone(),
        currPosition: newPos.clone(),
        snapshotTime: now,
        interval: 1000,
        nodeState: node,
      });
    }
  }

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
  const tabResumed = dt > 0.2;

  for (const entry of satellites.values()) {
    if (tabResumed) {
      // Tab was backgrounded — snap to ground truth, reset lerp.
      entry.mesh.position.copy(entry.currPosition);
      entry.glow.position.copy(entry.currPosition);
      entry.prevPosition.copy(entry.currPosition);
      entry.snapshotTime = now;
      continue;
    }

    const t = Math.min((now - entry.snapshotTime) / entry.interval, 1.5);
    _tmpPos.lerpVectors(entry.prevPosition, entry.currPosition, t);
    entry.mesh.position.copy(_tmpPos);
    entry.glow.position.copy(_tmpPos);
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

/** Reset delivery rate EMA — call on session switch. */
export function resetDeliveryRate(): void {
  _wallMsPerSimMs = 1.0;
  _rateSeeded = false;
  _lastSimTimeMs = null;
  _lastSimWallTime = null;
}
