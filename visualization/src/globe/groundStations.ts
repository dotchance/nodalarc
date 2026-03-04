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
function computeConeRadius(minElevDeg: number, orbitalAltKm: number): number {
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

  // Position and orient to surface normal
  const surfaceNormal = pos.clone().normalize();
  const surfacePos = surfaceNormal.clone().multiplyScalar(EARTH_RADIUS * 1.001);

  cone.position.copy(surfacePos);
  outline.position.copy(surfacePos);

  // Orient ring to lie flat on globe surface
  cone.lookAt(surfaceNormal.clone().multiplyScalar(EARTH_RADIUS * 2));
  outline.lookAt(surfaceNormal.clone().multiplyScalar(EARTH_RADIUS * 2));

  return { cone, outline };
}

export function updateGroundStations(
  nodes: NodeState[],
  scene: THREE.Scene,
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
      scene.add(sprite);

      // Elevation cone — per-station radius from actual min_elevation_deg
      const minElev = node.min_elevation_deg ?? 25;
      const coneRadius = computeConeRadius(minElev, orbitalAltKm);
      const { cone, outline: coneOutline } = createElevationCone(pos, coneRadius);
      scene.add(cone);
      scene.add(coneOutline);

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
      scene.remove(entry.sprite);
      scene.remove(entry.cone);
      scene.remove(entry.coneOutline);
      entry.cone.geometry.dispose();
      entry.coneOutline.geometry.dispose();
      entry.label.remove();
      groundStations.delete(id);
    }
  }
}

export function updateGSLabels(camera: THREE.Camera, container: HTMLDivElement): void {
  const width = container.clientWidth;
  const height = container.clientHeight;

  for (const entry of groundStations.values()) {
    const pos = entry.sprite.position.clone();
    pos.project(camera);

    // Check if behind camera
    if (pos.z > 1) {
      entry.label.style.display = "none";
      continue;
    }

    // Check if occluded by Earth
    const worldPos = entry.sprite.position;
    const cameraPos = camera.position;
    const dirToGS = worldPos.clone().sub(cameraPos).normalize();
    const dirToCenter = new THREE.Vector3(0, 0, 0).sub(cameraPos).normalize();
    const dot = dirToGS.dot(dirToCenter);
    const distToCenter = cameraPos.length();
    const sinAngle = EARTH_RADIUS / distToCenter;
    if (dot > Math.sqrt(1 - sinAngle * sinAngle) && worldPos.length() < distToCenter) {
      entry.label.style.display = "none";
      continue;
    }

    const x = (pos.x * 0.5 + 0.5) * width;
    const y = (-pos.y * 0.5 + 0.5) * height;

    entry.label.style.display = "block";
    entry.label.style.left = `${x + 8}px`;
    entry.label.style.top = `${y - 6}px`;
  }
}
