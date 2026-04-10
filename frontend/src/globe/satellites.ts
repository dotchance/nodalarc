// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Satellite mesh management — shared geometry, per-sat mesh + smooth motion.
 *
 *  Motion model (PRD v0.71): local Keplerian propagation from SessionEphemeris
 *  at 60fps via propagateNode(). Positions computed every frame from orbital
 *  elements — no lerp interpolation between snapshots.
 *
 *  Fallback: when ephemeris is unavailable (pre-v0.71 VS-API), falls back to
 *  NodeState positions updated at ~1Hz.
 *
 *  Metadata (plane, slot, routing_area, neighbor_count, etc.) still comes
 *  from NodeState via the WebSocket StateSnapshot.
 */

import * as THREE from "three";
import { SAT_RADIUS, SAT_SEGMENTS, AREA_COLORS, getPlaneColor } from "../config";
import { geoToWorld } from "./geo";
import { simTimeIsoToUnixSeconds } from "./astronomy";
import { interpolatedSimTimeMs } from "../sim/simClock";
import { propagateNode } from "../sim/ephemeris";
import type { SessionEphemeris } from "../sim/ephemeris";
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
  nodeState: NodeState;
}

const satellites = new Map<string, SatelliteEntry>();

export function getSatellites(): Map<string, SatelliteEntry> {
  return satellites;
}

/** Current ephemeris for local propagation. Set from the WebSocket handler. */
let _ephemeris: SessionEphemeris | null = null;

export function setEphemeris(eph: SessionEphemeris | null): void {
  _ephemeris = eph;
}

export function getEphemeris(): SessionEphemeris | null {
  return _ephemeris;
}

/**
 * Update satellite metadata from WebSocket StateSnapshot.
 *
 * Creates new meshes for satellites that appear, removes meshes for
 * satellites that disappear, and updates metadata (routing_area,
 * neighbor_count, etc.) for existing satellites.
 *
 * Positions are NOT set here when ephemeris is available — they are
 * computed every frame in animateSatellites(). When ephemeris is null
 * (fallback), positions come from NodeState.
 */
export function updateSatellites(
  nodes: NodeState[],
  earthFrame: THREE.Object3D,
  colorMode: ColorMode,
  _simTime: string,
): void {
  const seen = new Set<string>();

  for (const node of nodes) {
    if (node.node_type !== "satellite") continue;
    seen.add(node.node_id);

    const existing = satellites.get(node.node_id);
    if (existing) {
      existing.nodeState = node;
      updateSatColor(existing, colorMode);
    } else {
      // New satellite — create mesh at initial position
      const pos = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);
      const color = getSatColor(node, colorMode);
      const material = new THREE.MeshBasicMaterial({ color });
      const mesh = new THREE.Mesh(sharedGeo, material);
      mesh.position.copy(pos);
      mesh.userData["nodeId"] = node.node_id;
      mesh.userData["nodeType"] = "satellite";
      earthFrame.add(mesh);

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
      glow.visible = false;
      earthFrame.add(glow);

      satellites.set(node.node_id, { mesh, glow, nodeState: node });
    }
  }

  for (const [id, entry] of satellites) {
    if (!seen.has(id)) {
      earthFrame.remove(entry.mesh);
      earthFrame.remove(entry.glow);
      satellites.delete(id);
    }
  }
}

/**
 * Animate satellites — called every frame (~60fps).
 *
 * When ephemeris is available: propagates each satellite from orbital
 * elements at the current interpolated sim_time. Smooth 60fps motion
 * with no lerp artifacts.
 *
 * When ephemeris is null: positions are set from NodeState in
 * updateSatellites() and remain static between snapshots.
 */
export function animateSatellites(_dt: number): void {
  if (!_ephemeris) return;

  const now = performance.now();
  const simMs = interpolatedSimTimeMs(now);
  if (simMs === null) return;

  const simTimeUnix = simMs / 1000;
  const epochUnix = _ephemeris.epoch_unix;

  for (const [nodeId, entry] of satellites) {
    const ephNode = _ephemeris.nodes[nodeId];
    if (!ephNode || ephNode.type !== "keplerian") continue;

    const pos = propagateNode(ephNode, epochUnix, simTimeUnix);
    const worldPos = geoToWorld(pos.latDeg, pos.lonDeg, pos.altKm);
    entry.mesh.position.copy(worldPos);
    entry.glow.position.copy(worldPos);
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
