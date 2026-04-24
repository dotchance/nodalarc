// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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

const FADE_IN_DIST = 150;
const FADE_OUT_DIST = 350;

interface LabelEntry {
  div: HTMLDivElement;
}

const labels = new Map<string, LabelEntry>();
let labelContainer: HTMLDivElement | null = null;
const _labelLocalPos = new THREE.Vector3();
const _labelWorldPos = new THREE.Vector3();
const _labelNdc = new THREE.Vector3();
const _dirToLabel = new THREE.Vector3();
const _dirToCenter = new THREE.Vector3();

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
  if (!labelContainer || !earthFrameRef) return;

  const width = labelContainer.clientWidth;
  const height = labelContainer.clientHeight;
  const cameraPos = camera.position;
  const distToCenter = cameraPos.length();
  const sinAngle = EARTH_RADIUS / distToCenter;
  const occlusionThreshold = Math.sqrt(1 - sinAngle * sinAngle);

  for (const [id, entry] of labels) {
    if (!getNodeLocalPosition(id, _labelLocalPos)) {
      entry.div.style.display = "none";
      continue;
    }

    // Convert local to world
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

    // Earth occlusion
    _dirToLabel.copy(_labelWorldPos).sub(cameraPos).normalize();
    _dirToCenter.copy(cameraPos).multiplyScalar(-1).normalize();
    const dot = _dirToLabel.dot(_dirToCenter);
    if (dot > occlusionThreshold && _labelWorldPos.length() < distToCenter) {
      entry.div.style.display = "none";
      continue;
    }

    const x = (_labelNdc.x * 0.5 + 0.5) * width;
    const y = (-_labelNdc.y * 0.5 + 0.5) * height;

    entry.div.style.display = "block";
    entry.div.style.left = `${x + 6}px`;
    entry.div.style.top = `${y - 14}px`;

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
