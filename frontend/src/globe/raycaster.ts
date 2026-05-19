// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Raycaster for satellite/GS/link picking with hover highlight. */

import * as THREE from "three";
import { getSatellites } from "./satellites";
import { getGroundStations } from "./groundStations";
import { getLinks } from "./links";
import { getNodeWorldPosition } from "./positionLookup";
import { toggleOrbitPin, isOrbitPinned } from "./orbitPins";
import type { Selection } from "../types";

let tooltip: HTMLDivElement | null = null;
let hoveredObject: THREE.Object3D | null = null;
const HOVER_SCALE = 1.3;

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

/** Build tooltip content with contextual info. */
function buildTooltipContent(nodeId: string, nodeType: string): string {
  if (nodeType === "satellite") {
    const sat = getSatellites().get(nodeId);
    if (sat) {
      const ns = sat.nodeState;
      const area = ns.routing_area ?? "none";
      const isl = ns.isl_count;
      const gnd = ns.gnd_count;
      // Check ABR: links to nodes in different areas
      const linkedAreas = new Set<string>();
      if (ns.routing_area) linkedAreas.add(ns.routing_area);
      const links = getLinks();
      for (const [, entry] of links) {
        if (entry.nodeA === nodeId || entry.nodeB === nodeId) {
          const peerId = entry.nodeA === nodeId ? entry.nodeB : entry.nodeA;
          const peerSat = getSatellites().get(peerId);
          if (peerSat?.nodeState.routing_area) linkedAreas.add(peerSat.nodeState.routing_area);
        }
      }
      const abrTag = linkedAreas.size > 1 ? " [ABR]" : "";
      const pinHint = isOrbitPinned(nodeId) ? "\n[Ctrl+click to unpin orbit]" : "";
      return `${nodeId}\n${isl} ISLs, ${gnd} GND, Area ${area}${abrTag}${pinHint}`;
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

function clearHover(): void {
  if (hoveredObject) {
    hoveredObject.scale.set(1, 1, 1);
    hoveredObject = null;
  }
}

const _v3a = new THREE.Vector3();
const _v3b = new THREE.Vector3();

/**
 * Screen-space hit test for Line2 links.
 * Line2 doesn't work with the standard Three.js raycaster, so we project
 * both endpoints to screen space and check distance from the mouse point
 * to the resulting 2D line segment.
 */
function hitTestLinks(
  ndcX: number,
  ndcY: number,
  camera: THREE.PerspectiveCamera,
  threshold: number = 0.02,
): { key: string; nodeA: string; nodeB: string; tooltipText: string } | null {
  let bestDist = threshold;
  let bestHit: { key: string; nodeA: string; nodeB: string; tooltipText: string } | null = null;

  for (const [key, entry] of getLinks()) {
    if (entry.state !== "active" && entry.failTime === null) continue;
    if (!getNodeWorldPosition(entry.nodeA, _v3a)) continue;
    if (!getNodeWorldPosition(entry.nodeB, _v3b)) continue;

    // Project endpoints to NDC (-1..1)
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

function pointToSegment2D(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

export function setupRaycaster(
  canvas: HTMLCanvasElement,
  camera: THREE.PerspectiveCamera,
  scene: THREE.Scene,
  onSelect: (sel: Selection | null) => void,
  getViewFrameRotation: () => { rotationRad: number; angularVelocityRadS: number },
): void {
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  const tip = getTooltip();

  canvas.addEventListener("mousemove", (event: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);
    // Only test nodes (meshes/sprites), not Line2
    const intersects = raycaster.intersectObjects(scene.children, true);

    const nodeHit = intersects.find((i) => i.object.userData["nodeId"]);

    clearHover();

    if (nodeHit) {
      const nodeId = nodeHit.object.userData["nodeId"] as string;
      const nodeType = nodeHit.object.userData["nodeType"] as string;
      tip.innerHTML = buildTooltipContent(nodeId, nodeType).replace(/\n/g, "<br>");
      nodeHit.object.scale.set(HOVER_SCALE, HOVER_SCALE, HOVER_SCALE);
      hoveredObject = nodeHit.object;
      tip.style.display = "block";
      tip.style.left = `${event.clientX + 12}px`;
      tip.style.top = `${event.clientY - 8}px`;
      canvas.style.cursor = "pointer";
    } else {
      // Check links via screen-space distance
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

    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(scene.children, true);

    // Ctrl+click (or Cmd+click on macOS): toggle orbit pin for satellites
    if (event.ctrlKey || event.metaKey) {
      const nodeHit = intersects.find((i) => i.object.userData["nodeId"]);
      if (nodeHit) {
        const nodeId = nodeHit.object.userData["nodeId"] as string;
        const nodeType = nodeHit.object.userData["nodeType"] as string;
        if (nodeType === "satellite") {
          const { rotationRad, angularVelocityRadS } = getViewFrameRotation();
          toggleOrbitPin(nodeId, scene, rotationRad, angularVelocityRadS);
        }
      }
      return;
    }

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

    // Check links
    const linkHit = hitTestLinks(mouse.x, mouse.y, camera);
    if (linkHit) {
      onSelect({ type: "link", id: linkHit.key });
      return;
    }

    onSelect(null);
  });
}
