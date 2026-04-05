// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Ground station sprites with canvas-drawn antenna icons.
 *  Includes elevation cone (RingGeometry) per VF spec Section 4.
 */

import * as THREE from "three";
import { GS_COLOR, GS_SIZE, EARTH_RADIUS, KM_PER_UNIT } from "../config";
import { geoToWorld } from "./geo";
import type { NodeState } from "../types";

export interface GroundStationEntry {
  sprite: THREE.Sprite;
  label: HTMLDivElement;
  cone: THREE.Mesh;
  coneOutline: THREE.LineLoop;
  nodeState: NodeState;
}

const groundStations = new Map<string, GroundStationEntry>();

export function getGroundStations(): Map<string, GroundStationEntry> {
  return groundStations;
}

function createGSTexture(): THREE.Texture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;

  const color = `#${GS_COLOR.toString(16).padStart(6, "0")}`;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 3;

  // Base
  ctx.beginPath();
  ctx.arc(size / 2, size * 0.7, 8, 0, Math.PI * 2);
  ctx.fill();

  // Dish
  ctx.beginPath();
  ctx.moveTo(size * 0.2, size * 0.35);
  ctx.quadraticCurveTo(size / 2, size * 0.1, size * 0.8, size * 0.35);
  ctx.stroke();

  // Stem
  ctx.beginPath();
  ctx.moveTo(size / 2, size * 0.7);
  ctx.lineTo(size / 2, size * 0.3);
  ctx.stroke();

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

let gsTexture: THREE.Texture | null = null;

function getSharedTexture(): THREE.Texture {
  if (!gsTexture) {
    gsTexture = createGSTexture();
  }
  return gsTexture;
}

/** Compute elevation cone radius on the globe surface for given parameters. */
export function computeConeRadius(minElevDeg: number, orbitalAltKm: number): number {
  const earthRadiusKm = 6371;

  // Slant angle from zenith to horizon at min elevation
  const elevRad = (minElevDeg * Math.PI) / 180;
  // Central angle subtended by footprint
  const centralAngle = Math.acos(
    (earthRadiusKm * Math.cos(elevRad)) / (earthRadiusKm + orbitalAltKm),
  ) - elevRad;

  // Arc distance on surface in km, converted to scene units
  const arcKm = earthRadiusKm * centralAngle;
  return arcKm / KM_PER_UNIT;
}

/** Axis used to orient flat circular geometries (cone rings, footprint disc)
 *  so their local -Z faces outward along the radial. Declared at module
 *  scope to avoid re-allocation on every ground-station creation. */
const _RING_LOCAL_Z_AXIS = new THREE.Vector3(0, 0, -1);

/** Create the elevation cone ring (coverage area indicator) positioned on the surface. */
function createElevationCone(
  pos: THREE.Vector3,
  radius: number,
): { cone: THREE.Mesh; outline: THREE.LineLoop } {
  // Ring on the surface plane
  const ringGeo = new THREE.RingGeometry(0, radius, 48);
  const ringMat = new THREE.MeshBasicMaterial({
    color: GS_COLOR,
    transparent: true,
    opacity: 0.05,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const cone = new THREE.Mesh(ringGeo, ringMat);

  // Outline ring
  const outlineGeo = new THREE.BufferGeometry();
  const outlinePoints: number[] = [];
  for (let i = 0; i <= 48; i++) {
    const angle = (i / 48) * Math.PI * 2;
    outlinePoints.push(
      Math.cos(angle) * radius,
      Math.sin(angle) * radius,
      0,
    );
  }
  outlineGeo.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(outlinePoints, 3),
  );
  const outlineMat = new THREE.LineBasicMaterial({
    color: GS_COLOR,
    transparent: true,
    opacity: 0.2,
    depthWrite: false,
  });
  const outline = new THREE.LineLoop(outlineGeo, outlineMat);

  // Position on surface and orient tangent to the sphere at that point.
  // Both computations are done in the local (ECEF) frame since the cone
  // and GS sprite share parent (earthFrame). We use setFromUnitVectors
  // rather than lookAt because lookAt takes a world-space target — see
  // plan §1.12. Computing the orientation in local coords produces a
  // local quaternion that is invariant under any rotation of earthFrame.
  const outward = pos.clone().normalize();
  const surfacePos = outward.clone().multiplyScalar(EARTH_RADIUS * 1.001);

  cone.position.copy(surfacePos);
  outline.position.copy(surfacePos);

  // Rotate so the ring's +Z axis (its surface normal) points outward.
  // lookAt target was "2·EARTH_RADIUS along radial", which pointed the
  // ring's -Z at the outside → +Z pointed inward at Earth's center.
  // Preserve that semantic: map (0,0,-1) to outward.
  cone.quaternion.setFromUnitVectors(_RING_LOCAL_Z_AXIS, outward);
  outline.quaternion.setFromUnitVectors(_RING_LOCAL_Z_AXIS, outward);

  return { cone, outline };
}

export function updateGroundStations(
  nodes: NodeState[],
  earthFrame: THREE.Object3D,
  labelContainer: HTMLDivElement,
): void {
  const seen = new Set<string>();

  // Derive orbital altitude from first satellite node (fallback 550km)
  const firstSat = nodes.find((n) => n.node_type === "satellite");
  const orbitalAltKm = firstSat ? firstSat.alt_km : 550;

  for (const node of nodes) {
    if (node.node_type !== "ground_station") continue;
    seen.add(node.node_id);

    const existing = groundStations.get(node.node_id);
    if (existing) {
      existing.nodeState = node;
    } else {
      const material = new THREE.SpriteMaterial({
        map: getSharedTexture(),
        sizeAttenuation: true,
      });
      const sprite = new THREE.Sprite(material);
      sprite.scale.set(GS_SIZE, GS_SIZE, 1);
      const pos = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);
      sprite.position.copy(pos);
      sprite.userData["nodeId"] = node.node_id;
      sprite.userData["nodeType"] = "ground_station";
      earthFrame.add(sprite);

      // Elevation cone — per-station radius from actual min_elevation_deg
      const minElev = node.min_elevation_deg ?? 25;
      const coneRadius = computeConeRadius(minElev, orbitalAltKm);
      const { cone, outline: coneOutline } = createElevationCone(pos, coneRadius);
      earthFrame.add(cone);
      earthFrame.add(coneOutline);

      // HTML label
      const label = document.createElement("div");
      label.className = "gs-label";
      label.textContent = node.node_id.replace("gs-", "");
      label.style.cssText = `
        position: absolute;
        color: #00d4aa;
        font-size: 11px;
        font-weight: bold;
        pointer-events: none;
        white-space: nowrap;
        text-shadow: 0 0 6px rgba(0,0,0,0.95), 0 0 2px rgba(0,0,0,0.95);
        background: rgba(13, 13, 26, 0.6);
        padding: 1px 4px;
        border-radius: 2px;
      `;
      labelContainer.appendChild(label);

      groundStations.set(node.node_id, { sprite, label, cone, coneOutline, nodeState: node });
    }
  }

  // Remove missing
  for (const [id, entry] of groundStations) {
    if (!seen.has(id)) {
      earthFrame.remove(entry.sprite);
      earthFrame.remove(entry.cone);
      earthFrame.remove(entry.coneOutline);
      entry.cone.geometry.dispose();
      entry.coneOutline.geometry.dispose();
      entry.label.remove();
      groundStations.delete(id);
    }
  }
}

// Reusable temporaries for label projection math — avoid per-frame alloc.
const _gsWorldPos = new THREE.Vector3();
const _gsNdc = new THREE.Vector3();
const _gsDirA = new THREE.Vector3();
const _gsDirB = new THREE.Vector3();

export function updateGSLabels(camera: THREE.Camera, container: HTMLDivElement): void {
  const width = container.clientWidth;
  const height = container.clientHeight;
  const cameraPos = camera.position;
  const distToCenter = cameraPos.length();
  const sinAngle = EARTH_RADIUS / distToCenter;
  const occlusionThreshold = Math.sqrt(1 - sinAngle * sinAngle);

  for (const entry of groundStations.values()) {
    // World-space position (traverses earthFrame transform). Required:
    // Vector3.project(camera) treats its input as world coords, and the
    // occlusion dot-product compares against world camera.position.
    entry.sprite.getWorldPosition(_gsWorldPos);

    _gsNdc.copy(_gsWorldPos).project(camera);

    // Behind camera
    if (_gsNdc.z > 1) {
      entry.label.style.display = "none";
      continue;
    }

    // Earth-occlusion test (world-space)
    _gsDirA.copy(_gsWorldPos).sub(cameraPos).normalize();
    _gsDirB.copy(cameraPos).multiplyScalar(-1).normalize();
    const dot = _gsDirA.dot(_gsDirB);
    if (dot > occlusionThreshold && _gsWorldPos.length() < distToCenter) {
      entry.label.style.display = "none";
      continue;
    }

    const x = (_gsNdc.x * 0.5 + 0.5) * width;
    const y = (-_gsNdc.y * 0.5 + 0.5) * height;

    entry.label.style.display = "block";
    entry.label.style.left = `${x + 8}px`;
    entry.label.style.top = `${y - 6}px`;
  }
}
