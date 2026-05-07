// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Satellite rendering — InstancedMesh for O(1) draw calls at any scale.
//
// Before: 220 Mesh + 220 Sprite = 440 draw calls
// After:  1 InstancedMesh + 1 glow Sprite (selected only) = 2 draw calls
//
// All position consumers read via positionLookup.ts, which reads from
// the positionCache Float32Array — not from individual mesh objects.

import * as THREE from "three";
import { SAT_RADIUS, SAT_SEGMENTS, AREA_COLORS, getPlaneColor } from "../config";
import { geoToWorld } from "./geo";
import { interpolatedSimTimeMs } from "../sim/simClock";
import { isWorkerReady, readPosition, requestPropagate } from "../sim/workerBridge";
import { propagateToSceneXYZ } from "../sim/orbitalMath";
import type { SessionEphemeris } from "../sim/ephemeris";
import type { NodeState, ColorMode } from "../types";

const MAX_SATELLITES = 10_000;

export interface SatelliteEntry {
  instanceIndex: number;
  nodeState: NodeState;
}

const satellites = new Map<string, SatelliteEntry>();
export const indexToId: string[] = [];
let satCount = 0;

let instancedMesh: THREE.InstancedMesh | null = null;
export let satEarthFrame: THREE.Object3D | null = null;

const positionCache = new Float32Array(MAX_SATELLITES * 3);

const sharedGeo = new THREE.SphereGeometry(SAT_RADIUS, SAT_SEGMENTS, SAT_SEGMENTS);
const sharedMat = new THREE.MeshBasicMaterial({ vertexColors: false });

const _tmpMatrix = new THREE.Matrix4();
const _tmpColor = new THREE.Color();

let glowSprite: THREE.Sprite | null = null;
let glowTarget: string | null = null;

let _ephemeris: SessionEphemeris | null = null;

export function getSatellites(): Map<string, SatelliteEntry> {
  return satellites;
}

export function getPositionCache(): Float32Array {
  return positionCache;
}

export function getSatCount(): number {
  return satCount;
}

export function setEphemeris(eph: SessionEphemeris | null): void {
  _ephemeris = eph;
}

export function getEphemeris(): SessionEphemeris | null {
  return _ephemeris;
}

export function setSelectedGlow(nodeId: string | null): void {
  glowTarget = nodeId;
  if (glowSprite) {
    glowSprite.visible = nodeId !== null;
  }
}

function getOrCreateGlowSprite(parent: THREE.Object3D): THREE.Sprite {
  if (!glowSprite) {
    const size = 64;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d")!;
    if (ctx.createRadialGradient) {
      const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
      gradient.addColorStop(0, "rgba(255, 255, 255, 0.6)");
      gradient.addColorStop(0.3, "rgba(255, 255, 255, 0.15)");
      gradient.addColorStop(1, "rgba(255, 255, 255, 0)");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, size, size);
    }
    const texture = new THREE.CanvasTexture(canvas);
    const mat = new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    glowSprite = new THREE.Sprite(mat);
    glowSprite.scale.set(SAT_RADIUS * 5, SAT_RADIUS * 5, 1);
    glowSprite.visible = false;
    parent.add(glowSprite);
  }
  return glowSprite;
}

function ensureInstancedMesh(parent: THREE.Object3D): THREE.InstancedMesh {
  if (!instancedMesh) {
    instancedMesh = new THREE.InstancedMesh(sharedGeo, sharedMat, MAX_SATELLITES);
    instancedMesh.count = 0;
    instancedMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    instancedMesh.name = "satellites";
    parent.add(instancedMesh);
    satEarthFrame = parent;
    getOrCreateGlowSprite(parent);
  }
  return instancedMesh;
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

export function updateSatellites(
  nodes: NodeState[],
  earthFrame: THREE.Object3D,
  colorMode: ColorMode,
  _simTime: string,
): void {
  const mesh = ensureInstancedMesh(earthFrame);
  const seen = new Set<string>();

  for (const node of nodes) {
    if (node.node_type !== "satellite") continue;
    seen.add(node.node_id);

    const existing = satellites.get(node.node_id);
    if (existing) {
      existing.nodeState = node;
      _tmpColor.setHex(getSatColor(node, colorMode));
      mesh.setColorAt(existing.instanceIndex, _tmpColor);
    } else {
      const idx = satCount;
      satCount++;
      indexToId[idx] = node.node_id;

      const pos = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);
      positionCache[idx * 3] = pos.x;
      positionCache[idx * 3 + 1] = pos.y;
      positionCache[idx * 3 + 2] = pos.z;

      _tmpMatrix.makeTranslation(pos.x, pos.y, pos.z);
      mesh.setMatrixAt(idx, _tmpMatrix);

      _tmpColor.setHex(getSatColor(node, colorMode));
      mesh.setColorAt(idx, _tmpColor);

      satellites.set(node.node_id, { instanceIndex: idx, nodeState: node });
    }
  }

  // Mark removed satellites by zeroing their matrix (degenerate at origin)
  for (const [id, entry] of satellites) {
    if (!seen.has(id)) {
      _tmpMatrix.makeScale(0, 0, 0);
      mesh.setMatrixAt(entry.instanceIndex, _tmpMatrix);
      positionCache[entry.instanceIndex * 3] = 0;
      positionCache[entry.instanceIndex * 3 + 1] = 0;
      positionCache[entry.instanceIndex * 3 + 2] = 0;
      satellites.delete(id);
    }
  }

  mesh.count = satCount;
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
}

const _workerPos = { x: 0, y: 0, z: 0 };
let _lastPropagateRequestTime = 0;

export function animateSatellites(_dt: number): void {
  if (!_ephemeris || !instancedMesh) return;

  const now = performance.now();
  const simMs = interpolatedSimTimeMs(now);
  if (simMs === null) return;

  const simTimeUnix = simMs / 1000;
  const epochUnix = _ephemeris.epoch_unix;
  const workerReady = isWorkerReady();

  if (workerReady && now - _lastPropagateRequestTime > 2000) {
    requestPropagate(simTimeUnix, 1.0);
    _lastPropagateRequestTime = now;
  }

  for (const [nodeId, entry] of satellites) {
    const ephNode = _ephemeris.nodes[nodeId];
    if (!ephNode || ephNode.type !== "keplerian") continue;

    let x: number, y: number, z: number;

    if (workerReady && readPosition(nodeId, simTimeUnix, _workerPos)) {
      x = _workerPos.x;
      y = _workerPos.y;
      z = _workerPos.z;
    } else {
      [x, y, z] = propagateToSceneXYZ(ephNode, epochUnix, simTimeUnix);
    }

    const idx = entry.instanceIndex;
    positionCache[idx * 3] = x;
    positionCache[idx * 3 + 1] = y;
    positionCache[idx * 3 + 2] = z;

    _tmpMatrix.makeTranslation(x, y, z);
    instancedMesh.setMatrixAt(idx, _tmpMatrix);
  }

  instancedMesh.instanceMatrix.needsUpdate = true;

  if (glowSprite && glowTarget) {
    const entry = satellites.get(glowTarget);
    if (entry) {
      const idx = entry.instanceIndex;
      glowSprite.position.set(
        positionCache[idx * 3]!,
        positionCache[idx * 3 + 1]!,
        positionCache[idx * 3 + 2]!,
      );
      glowSprite.visible = true;
    }
  }
}

export function recolorAllSatellites(colorMode: ColorMode): void {
  if (!instancedMesh) return;
  for (const entry of satellites.values()) {
    _tmpColor.setHex(getSatColor(entry.nodeState, colorMode));
    instancedMesh.setColorAt(entry.instanceIndex, _tmpColor);
  }
  if (instancedMesh.instanceColor) instancedMesh.instanceColor.needsUpdate = true;
}
