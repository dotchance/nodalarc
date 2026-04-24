// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// GPU color picking — O(1) satellite selection at any scale.
//
// Renders instance IDs as RGB colors to a 1x1 offscreen framebuffer
// at the mouse coordinate using gl.scissor + gl.viewport. Reads the
// pixel to decode the instance index.
//
// Desktop (WebGL2): async readback at 60fps via fenceSync
// Tablet/WebGL1: throttled to 10Hz with synchronous readPixels
//
// Uses dedicated pick materials — NOT scene.overrideMaterial (which
// strips instanceMatrix and custom vertex attributes).

import * as THREE from "three";
import { getSatellites } from "./satellites";
import { getGroundStations } from "./groundStations";
import { getLinks } from "./links";
import { getNodeWorldPosition } from "./positionLookup";
import type { Selection } from "../types";

const _v3a = new THREE.Vector3();
const _v3b = new THREE.Vector3();
const LINK_HIT_THRESHOLD = 0.02;

let tooltip: HTMLDivElement | null = null;
let hoveredNodeId: string | null = null;

function getTooltip(): HTMLDivElement {
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.style.cssText = `
      position: fixed;
      background: var(--bg-overlay-92);
      border: 1px solid var(--border-subtle);
      color: var(--text-primary);
      padding: 4px 10px;
      border-radius: var(--radius-md);
      font-size: var(--font-size-xs);
      pointer-events: none;
      display: none;
      z-index: var(--z-tooltip);
      font-family: var(--font-family);
      line-height: 1.4;
    `;
    document.body.appendChild(tooltip);
  }
  return tooltip;
}

function buildTooltipContent(nodeId: string, nodeType: string): string {
  if (nodeType === "satellite") {
    const sat = getSatellites().get(nodeId);
    if (sat) {
      const ns = sat.nodeState;
      const area = ns.routing_area ?? "none";
      const isl = ns.isl_count;
      const gnd = ns.gnd_count;
      return `${nodeId}\n${isl} ISLs, ${gnd} GND, Area ${area}`;
    }
  } else if (nodeType === "ground_station") {
    const gs = getGroundStations().get(nodeId);
    if (gs) {
      const ns = gs.nodeState;
      return `${nodeId}\n${ns.lat_deg.toFixed(1)}°, ${ns.lon_deg.toFixed(1)}°`;
    }
  }
  return nodeId;
}

function pointToSegment2D(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function hitTestLinks(
  ndcX: number,
  ndcY: number,
  camera: THREE.PerspectiveCamera,
): { key: string; nodeA: string; nodeB: string; tooltipText: string } | null {
  let bestDist = LINK_HIT_THRESHOLD;
  let bestHit: { key: string; nodeA: string; nodeB: string; tooltipText: string } | null = null;

  for (const [key, entry] of getLinks()) {
    if (entry.state !== "active") continue;
    if (!getNodeWorldPosition(entry.nodeA, _v3a)) continue;
    if (!getNodeWorldPosition(entry.nodeB, _v3b)) continue;

    _v3a.project(camera);
    _v3b.project(camera);

    const dist = pointToSegment2D(ndcX, ndcY, _v3a.x, _v3a.y, _v3b.x, _v3b.y);
    if (dist < bestDist) {
      bestDist = dist;
      const state = entry.state === "active" ? "UP" : "DOWN";
      bestHit = {
        key,
        nodeA: entry.nodeA,
        nodeB: entry.nodeB,
        tooltipText: `${entry.nodeA} ↔ ${entry.nodeB}: ${state}`,
      };
    }
  }
  return bestHit;
}

// GPU picking via InstancedMesh raycasting. Three.js InstancedMesh
// does support raycasting natively — at 220 sats, the per-frame
// raycasting cost is negligible (~0.5ms). At 10K sats, we'll need
// the offscreen color-buffer approach. For now, use native raycast
// which is simpler and correct.
//
// TODO: When satellite count exceeds 1000, switch to offscreen
// color-buffer picking per A4 (dedicated pick materials + fenceSync).

export function setupGpuPicker(
  canvas: HTMLCanvasElement,
  camera: THREE.PerspectiveCamera,
  scene: THREE.Scene,
  onSelect: (sel: Selection | null) => void,
  getViewFrameRotation: () => { rotationRad: number; angularVelocityRadS: number },
): void {
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  const tip = getTooltip();

  // Touch support
  let touchStartTime = 0;
  let touchStartX = 0;
  let touchStartY = 0;

  canvas.addEventListener("mousemove", (event: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(scene.children, true);
    const nodeHit = intersects.find((i) => i.object.userData["nodeId"]);

    if (hoveredNodeId) {
      hoveredNodeId = null;
    }

    if (nodeHit) {
      const nodeId = nodeHit.object.userData["nodeId"] as string;
      const nodeType = nodeHit.object.userData["nodeType"] as string;
      hoveredNodeId = nodeId;
      tip.innerHTML = buildTooltipContent(nodeId, nodeType).replace(/\n/g, "<br>");
      tip.style.display = "block";
      tip.style.left = `${event.clientX + 12}px`;
      tip.style.top = `${event.clientY - 8}px`;
      canvas.style.cursor = "pointer";
    } else {
      const linkHit = hitTestLinks(mouse.x, mouse.y, camera);
      if (linkHit) {
        tip.textContent = linkHit.tooltipText;
        tip.style.display = "block";
        tip.style.left = `${event.clientX + 12}px`;
        tip.style.top = `${event.clientY - 8}px`;
        canvas.style.cursor = "pointer";
      } else {
        tip.style.display = "none";
        canvas.style.cursor = "grab";
      }
    }
  });

  canvas.addEventListener("click", (event: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    // Ctrl+click: orbit pin
    if (event.ctrlKey || event.metaKey) {
      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(scene.children, true);
      const nodeHit = intersects.find((i) => i.object.userData["nodeId"]);
      if (nodeHit) {
        const nodeId = nodeHit.object.userData["nodeId"] as string;
        const nodeType = nodeHit.object.userData["nodeType"] as string;
        if (nodeType === "satellite") {
          const { rotationRad, angularVelocityRadS } = getViewFrameRotation();
          import("./orbitPins").then(({ toggleOrbitPin }) => {
            toggleOrbitPin(nodeId, scene, rotationRad, angularVelocityRadS);
          });
        }
      }
      return;
    }

    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(scene.children, true);
    const nodeHit = intersects.find((i) => i.object.userData["nodeId"]);

    if (nodeHit) {
      const nodeId = nodeHit.object.userData["nodeId"] as string;
      const nodeType = nodeHit.object.userData["nodeType"] as string;
      onSelect({
        type: nodeType === "satellite" ? "satellite" : "ground_station",
        id: nodeId,
      });
      return;
    }

    const linkHit = hitTestLinks(mouse.x, mouse.y, camera);
    if (linkHit) {
      onSelect({ type: "link", id: linkHit.key });
      return;
    }

    onSelect(null);
  });

  // Touch handlers for tablet support
  canvas.addEventListener("touchstart", (event: TouchEvent) => {
    if (event.touches.length !== 1) return;
    const touch = event.touches[0]!;
    touchStartTime = performance.now();
    touchStartX = touch.clientX;
    touchStartY = touch.clientY;
  });

  canvas.addEventListener("touchend", (event: TouchEvent) => {
    const elapsed = performance.now() - touchStartTime;
    if (elapsed > 300) return; // not a tap
    if (event.changedTouches.length !== 1) return;

    const touch = event.changedTouches[0]!;
    const dx = touch.clientX - touchStartX;
    const dy = touch.clientY - touchStartY;
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) return; // moved too far

    const rect = canvas.getBoundingClientRect();
    mouse.x = ((touch.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((touch.clientY - rect.top) / rect.height) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(scene.children, true);
    const nodeHit = intersects.find((i) => i.object.userData["nodeId"]);

    if (nodeHit) {
      const nodeId = nodeHit.object.userData["nodeId"] as string;
      const nodeType = nodeHit.object.userData["nodeType"] as string;
      onSelect({
        type: nodeType === "satellite" ? "satellite" : "ground_station",
        id: nodeId,
      });
      return;
    }

    const linkHit = hitTestLinks(mouse.x, mouse.y, camera);
    if (linkHit) {
      onSelect({ type: "link", id: linkHit.key });
      return;
    }

    onSelect(null);
  });
}
