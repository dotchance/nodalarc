/** Ground station sprites with canvas-drawn antenna icons. */

import * as THREE from "three";
import { GS_COLOR, GS_SIZE, EARTH_RADIUS } from "../config";
import { geoToWorld } from "./geo";
import type { NodeState } from "../types";

export interface GroundStationEntry {
  sprite: THREE.Sprite;
  label: HTMLDivElement;
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

  // Antenna icon
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

export function updateGroundStations(
  nodes: NodeState[],
  scene: THREE.Scene,
  labelContainer: HTMLDivElement,
): void {
  const seen = new Set<string>();

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

      // HTML label
      const label = document.createElement("div");
      label.className = "gs-label";
      label.textContent = node.node_id.replace("gs-", "");
      label.style.cssText = `
        position: absolute;
        color: #00d4aa;
        font-size: 10px;
        pointer-events: none;
        white-space: nowrap;
        text-shadow: 0 0 4px rgba(0,0,0,0.8);
      `;
      labelContainer.appendChild(label);

      groundStations.set(node.node_id, { sprite, label, nodeState: node });
    }
  }

  // Remove missing
  for (const [id, entry] of groundStations) {
    if (!seen.has(id)) {
      scene.remove(entry.sprite);
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

    // Check if occluded by Earth (rough check: distance from center < EARTH_RADIUS)
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
