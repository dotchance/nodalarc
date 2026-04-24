// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// SDF satellite labels via troika-three-text.
//
// GPU-rendered text — zero DOM elements, zero layout thrashing.
// Font bundled at /fonts/Inter.woff2 for fully offline operation.
//
// Two-tier visibility:
// 1. Distance-based: labels fade in/out based on camera distance
//    (global = dots only, regional = IDs, close = full labels)
// 2. Logical override: nodes in active traces, selections, or
//    1-hop neighbors get forced-on labels at any distance
//    (isHighlighted attribute in the visibility check)

// @ts-ignore — troika-three-text has no type declarations
import { Text } from "troika-three-text";
import * as THREE from "three";
import { getSatellites } from "./satellites";
import { getNodeLocalPosition } from "./positionLookup";
import { tokens } from "../styles/tokens";

const FONT_URL = "/fonts/Inter.woff2";
const LABEL_FONT_SIZE = 0.3;
const FADE_IN_DIST = 60;
const FADE_OUT_DIST = 120;

interface LabelEntry {
  text: InstanceType<typeof Text>;
  highlighted: boolean;
}

const labels = new Map<string, LabelEntry>();
let labelsParent: THREE.Object3D | null = null;
const _labelPos = new THREE.Vector3();

const highlightedNodes = new Set<string>();

export function setHighlightedNodes(nodeIds: Set<string>): void {
  highlightedNodes.clear();
  for (const id of nodeIds) highlightedNodes.add(id);
}

export function updateLabels(earthFrame: THREE.Object3D): void {
  labelsParent = earthFrame;
  const sats = getSatellites();

  for (const [id] of sats) {
    if (labels.has(id)) continue;

    const text = new Text();
    text.text = id.replace("sat-", "");
    text.font = FONT_URL;
    text.fontSize = LABEL_FONT_SIZE;
    text.color = tokens.textPrimary;
    text.anchorX = "center";
    text.anchorY = "bottom";
    text.outlineWidth = 0.02;
    text.outlineColor = "#000000";
    text.renderOrder = 10;
    text.visible = false;
    text.sync();

    earthFrame.add(text);
    labels.set(id, { text, highlighted: false });
  }

  for (const [id, entry] of labels) {
    if (!sats.has(id)) {
      earthFrame.remove(entry.text);
      entry.text.dispose();
      labels.delete(id);
    }
  }
}

export function animateLabels(camera: THREE.Camera): void {
  if (!labelsParent) return;

  const camWorldPos = camera.position;

  for (const [id, entry] of labels) {
    if (!getNodeLocalPosition(id, _labelPos)) {
      entry.text.visible = false;
      continue;
    }

    entry.text.position.copy(_labelPos);
    entry.text.position.y += tokens.satRadius * 2;

    entry.highlighted = highlightedNodes.has(id);

    if (entry.highlighted) {
      entry.text.visible = true;
      entry.text.fontSize = LABEL_FONT_SIZE * 1.5;
    } else {
      labelsParent.localToWorld(_labelPos);
      const dist = _labelPos.distanceTo(camWorldPos);

      if (dist < FADE_IN_DIST) {
        entry.text.visible = true;
        entry.text.fontSize = LABEL_FONT_SIZE;
      } else if (dist < FADE_OUT_DIST) {
        entry.text.visible = true;
        const t = (dist - FADE_IN_DIST) / (FADE_OUT_DIST - FADE_IN_DIST);
        entry.text.fontSize = LABEL_FONT_SIZE * (1 - t * 0.5);
      } else {
        entry.text.visible = false;
      }
    }

    entry.text.lookAt(camWorldPos);
  }
}

export function clearLabels(): void {
  for (const entry of labels.values()) {
    labelsParent?.remove(entry.text);
    entry.text.dispose();
  }
  labels.clear();
}
