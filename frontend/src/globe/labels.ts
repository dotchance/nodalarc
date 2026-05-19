// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Satellite labels — DOM-based positioning for reliable rendering.
//
// Uses the same approach as ground station labels (groundStations.ts):
// HTML div elements positioned in screen space via camera projection.
// At 220 satellites, the DOM cost is negligible. At 1000+ sats,
// replace with troika-three-text SDF rendering.

import * as THREE from "three";
import { getSatellites } from "./satellites";
import { getNodeLocalPosition, earthFrameRef } from "./positionLookup";
import { EARTH_RADIUS } from "../config";

const FADE_IN_DIST = 200;
const FADE_OUT_DIST = 500;

let labelsEnabled = true;

export function setLabelsEnabled(enabled: boolean): void {
  labelsEnabled = enabled;
  if (!enabled) {
    for (const entry of labels.values()) {
      entry.div.style.display = "none";
    }
  }
}

export function getLabelsEnabled(): boolean {
  return labelsEnabled;
}

interface LabelEntry {
  div: HTMLDivElement;
}

const labels = new Map<string, LabelEntry>();
let labelContainer: HTMLDivElement | null = null;
const _labelLocalPos = new THREE.Vector3();
const _labelWorldPos = new THREE.Vector3();
const _labelNdc = new THREE.Vector3();

const OCC_RADIUS_FACTOR = 0.985;

export function isOccludedByEarth(
  satWorldX: number, satWorldY: number, satWorldZ: number,
  camX: number, camY: number, camZ: number,
  earthRadius: number,
): boolean {
  const occR = earthRadius * OCC_RADIUS_FACTOR;
  const dx = satWorldX - camX;
  const dy = satWorldY - camY;
  const dz = satWorldZ - camZ;
  const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (len < 0.001) return false;
  const dirX = dx / len;
  const dirY = dy / len;
  const dirZ = dz / len;

  const bHalf = camX * dirX + camY * dirY + camZ * dirZ;
  const c = camX * camX + camY * camY + camZ * camZ - occR * occR;
  const discrim = bHalf * bHalf - c;
  if (discrim <= 0) return false;

  const tNear = -bHalf - Math.sqrt(discrim);
  return tNear > 0 && tNear < len;
}

const highlightedNodes = new Set<string>();

export function setHighlightedNodes(nodeIds: Set<string>): void {
  highlightedNodes.clear();
  for (const id of nodeIds) highlightedNodes.add(id);
}

export function setLabelContainer(container: HTMLDivElement): void {
  labelContainer = container;
}

export function updateLabels(_earthFrame: THREE.Object3D): void {
  if (!labelContainer) return;
  const sats = getSatellites();

  for (const [id] of sats) {
    if (labels.has(id)) continue;

    const div = document.createElement("div");
    div.className = "sat-label";
    div.textContent = id.replace("sat-", "");
    div.style.cssText = `
      position: absolute;
      color: var(--text-primary);
      font-size: var(--font-size-xxs);
      pointer-events: none;
      white-space: nowrap;
      text-shadow: 0 0 4px rgba(0,0,0,0.95), 0 0 2px rgba(0,0,0,0.95);
      display: none;
    `;
    labelContainer.appendChild(div);
    labels.set(id, { div });
  }

  for (const [id, entry] of labels) {
    if (!sats.has(id)) {
      entry.div.remove();
      labels.delete(id);
    }
  }
}

export function animateLabels(camera: THREE.Camera): void {
  if (!labelContainer || !earthFrameRef || !labelsEnabled) return;

  const width = labelContainer.clientWidth;
  const height = labelContainer.clientHeight;
  const cameraPos = camera.position;

  earthFrameRef.updateWorldMatrix(true, false);

  for (const [id, entry] of labels) {
    if (!getNodeLocalPosition(id, _labelLocalPos)) {
      entry.div.style.display = "none";
      continue;
    }

    _labelWorldPos.copy(_labelLocalPos);
    earthFrameRef.localToWorld(_labelWorldPos);

    const dist = _labelWorldPos.distanceTo(cameraPos);
    const isHighlighted = highlightedNodes.has(id);

    if (!isHighlighted && dist > FADE_OUT_DIST) {
      entry.div.style.display = "none";
      continue;
    }

    // Project to NDC
    _labelNdc.copy(_labelWorldPos).project(camera);

    // Behind camera
    if (_labelNdc.z > 1) {
      entry.div.style.display = "none";
      continue;
    }

    if (isOccludedByEarth(
      _labelWorldPos.x, _labelWorldPos.y, _labelWorldPos.z,
      cameraPos.x, cameraPos.y, cameraPos.z,
      EARTH_RADIUS,
    )) {
      entry.div.style.display = "none";
      continue;
    }

    const sx = (_labelNdc.x * 0.5 + 0.5) * width;
    const sy = (-_labelNdc.y * 0.5 + 0.5) * height;

    // Offset label away from screen center so limb labels
    // point outward instead of overlapping the earth surface.
    const cx = width / 2;
    const cy = height / 2;
    const dx = sx - cx;
    const dy = sy - cy;
    const screenDist = Math.sqrt(dx * dx + dy * dy);
    const nx = screenDist > 1 ? dx / screenDist : 1;
    const ny = screenDist > 1 ? dy / screenDist : 0;

    entry.div.style.display = "block";
    entry.div.style.left = `${sx + nx * 10}px`;
    entry.div.style.top = `${sy + ny * 10 - 6}px`;

    if (isHighlighted) {
      entry.div.style.opacity = "1";
      entry.div.style.fontSize = "var(--font-size-xs)";
      entry.div.style.color = "var(--accent-teal)";
    } else if (dist < FADE_IN_DIST) {
      entry.div.style.opacity = "1";
      entry.div.style.fontSize = "var(--font-size-xxs)";
      entry.div.style.color = "var(--text-primary)";
    } else {
      const t = (dist - FADE_IN_DIST) / (FADE_OUT_DIST - FADE_IN_DIST);
      entry.div.style.opacity = String(1 - t * 0.7);
      entry.div.style.fontSize = "var(--font-size-xxs)";
      entry.div.style.color = "var(--text-secondary)";
    }
  }
}

export function clearLabels(): void {
  for (const entry of labels.values()) {
    entry.div.remove();
  }
  labels.clear();
}
