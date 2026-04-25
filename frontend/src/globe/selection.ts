// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Selection highlight — glowing ring around selected entity. */

import * as THREE from "three";
import { SELECTION_COLOR, SAT_RADIUS } from "../config";
import { setSelectedGlow } from "./satellites";
import { setSelectedGroundStation } from "./groundStations";
import { getNodeWorldPosition } from "./positionLookup";
import type { Selection } from "../types";

let selectionRing: THREE.Mesh | null = null;
let currentSelection: Selection | null = null;

const ringGeo = new THREE.RingGeometry(1.0, 1.3, 32);
const ringMat = new THREE.MeshBasicMaterial({
  color: SELECTION_COLOR,
  transparent: true,
  opacity: 0.7,
  side: THREE.DoubleSide,
  depthWrite: false,
});

// Reusable temporary for world-space target reads.
const _selWorldPos = new THREE.Vector3();

export function updateSelection(
  selection: Selection | null,
  scene: THREE.Scene,
  camera: THREE.Camera,
): void {
  if (currentSelection?.type === "satellite") {
    setSelectedGlow(null);
  }
  if (currentSelection?.type === "ground_station") {
    setSelectedGroundStation(null);
  }

  if (!selection || selection.type === "link") {
    if (selectionRing) {
      selectionRing.visible = false;
    }
    currentSelection = null;
    return;
  }

  currentSelection = selection;

  if (!selectionRing) {
    selectionRing = new THREE.Mesh(ringGeo, ringMat);
    selectionRing.renderOrder = 999;
    scene.add(selectionRing);
  }

  let hasTarget = false;
  let scale = SAT_RADIUS * 3;

  if (selection.type === "satellite") {
    hasTarget = getNodeWorldPosition(selection.id, _selWorldPos);
    if (hasTarget) {
      setSelectedGlow(selection.id);
    }
  } else if (selection.type === "ground_station") {
    hasTarget = getNodeWorldPosition(selection.id, _selWorldPos);
    scale = SAT_RADIUS * 4;
    if (hasTarget) {
      setSelectedGroundStation(selection.id);
    }
  }

  if (hasTarget) {
    selectionRing.position.copy(_selWorldPos);
    selectionRing.lookAt(camera.position);
    selectionRing.scale.set(scale, scale, scale);
    selectionRing.visible = true;
  } else {
    selectionRing.visible = false;
  }
}

export function animateSelection(camera: THREE.Camera): void {
  if (!selectionRing || !selectionRing.visible || !currentSelection) return;

  const hasTarget = getNodeWorldPosition(currentSelection.id, _selWorldPos);

  if (hasTarget) {
    selectionRing.position.copy(_selWorldPos);
    selectionRing.lookAt(camera.position);

    // Pulse effect
    const t = (Math.sin(performance.now() * 0.004) + 1) * 0.5;
    ringMat.opacity = 0.4 + t * 0.4;
  }
}
