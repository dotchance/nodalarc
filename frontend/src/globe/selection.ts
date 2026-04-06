// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Selection highlight — glowing ring around selected entity. */

import * as THREE from "three";
import { SELECTION_COLOR, SAT_RADIUS } from "../config";
import { getSatellites } from "./satellites";
import { getGroundStations } from "./groundStations";
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
  // Hide glow on previously selected satellite
  if (currentSelection?.type === "satellite") {
    const prevSat = getSatellites().get(currentSelection.id);
    if (prevSat) prevSat.glow.visible = false;
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

  // Find the target position
  const sats = getSatellites();
  const gss = getGroundStations();

  // Selection ring lives in scene root; entity positions live in earthFrame.
  // Read world coords so the ring tracks through the group rotation.
  let hasTarget = false;
  let scale = SAT_RADIUS * 3;

  if (selection.type === "satellite") {
    const sat = sats.get(selection.id);
    if (sat) {
      sat.mesh.getWorldPosition(_selWorldPos);
      sat.glow.visible = true;
      hasTarget = true;
    }
  } else if (selection.type === "ground_station") {
    const gs = gss.get(selection.id);
    if (gs) {
      gs.sprite.getWorldPosition(_selWorldPos);
      scale = SAT_RADIUS * 4;
      hasTarget = true;
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

  const sats = getSatellites();
  const gss = getGroundStations();

  let hasTarget = false;
  if (currentSelection.type === "satellite") {
    const sat = sats.get(currentSelection.id);
    if (sat) {
      sat.mesh.getWorldPosition(_selWorldPos);
      hasTarget = true;
    }
  } else if (currentSelection.type === "ground_station") {
    const gs = gss.get(currentSelection.id);
    if (gs) {
      gs.sprite.getWorldPosition(_selWorldPos);
      hasTarget = true;
    }
  }

  if (hasTarget) {
    selectionRing.position.copy(_selWorldPos);
    selectionRing.lookAt(camera.position);

    // Pulse effect
    const t = (Math.sin(performance.now() * 0.004) + 1) * 0.5;
    ringMat.opacity = 0.4 + t * 0.4;
  }
}
