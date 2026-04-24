// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Link rendering — per-link Line2 with shared materials.
//
// LineGeometry renders a CONNECTED polyline — you cannot batch
// multiple disconnected link curves into a single LineGeometry
// without spurious connecting segments between them. Each link
// gets its own Line2 object but shares a material instance to
// minimize material state changes.
//
// Fail-flash links get a separate material for color animation.

import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import {
  LINK_ISL_COLOR,
  LINK_GROUND_COLOR,
  LINK_FAIL_COLOR,
  LINK_INACTIVE_COLOR,
  LINK_ISL_WIDTH,
  LINK_GROUND_WIDTH,
  FAIL_HOLD_MS,
  FAIL_FADE_MS,
} from "../config";
import { getNodeLocalPosition } from "./positionLookup";
import type { LinkState } from "../types";

const _mid = new THREE.Vector3();
const _outward = new THREE.Vector3();
const SEGMENTS_PER_ISL = 16;

function bowedPositions(a: THREE.Vector3, b: THREE.Vector3): number[] {
  const positions: number[] = [];
  _mid.lerpVectors(a, b, 0.5);
  _outward.copy(_mid).normalize();
  const chord = a.distanceTo(b);
  const lift = chord * 0.03;

  for (let i = 0; i <= SEGMENTS_PER_ISL; i++) {
    const t = i / SEGMENTS_PER_ISL;
    const bow = 4 * t * (1 - t) * lift;
    positions.push(
      a.x + (b.x - a.x) * t + _outward.x * bow,
      a.y + (b.y - a.y) * t + _outward.y * bow,
      a.z + (b.z - a.z) * t + _outward.z * bow,
    );
  }
  return positions;
}

interface LinkEntry {
  line: Line2;
  geometry: LineGeometry;
  material: LineMaterial;
  state: string;
  nodeA: string;
  nodeB: string;
  isGround: boolean;
  failTime: number | null;
  upTime: number | null;
  baseColor: THREE.Color;
  baseOpacity: number;
}

const links = new Map<string, LinkEntry>();
let earthFrameRef: THREE.Object3D | null = null;

let islMaterial: LineMaterial | null = null;
let groundMaterial: LineMaterial | null = null;
let resolution = new THREE.Vector2(window.innerWidth, window.innerHeight);

window.addEventListener("resize", () => {
  resolution.set(window.innerWidth, window.innerHeight);
  if (islMaterial) islMaterial.resolution.copy(resolution);
  if (groundMaterial) groundMaterial.resolution.copy(resolution);
  for (const entry of links.values()) {
    entry.material.resolution.copy(resolution);
  }
});

function getIslMaterial(): LineMaterial {
  if (!islMaterial) {
    islMaterial = new LineMaterial({
      color: LINK_ISL_COLOR,
      linewidth: LINK_ISL_WIDTH,
      resolution,
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
    });
  }
  return islMaterial;
}

function getGroundMaterial(): LineMaterial {
  if (!groundMaterial) {
    groundMaterial = new LineMaterial({
      color: LINK_GROUND_COLOR,
      linewidth: LINK_GROUND_WIDTH,
      resolution,
      transparent: true,
      opacity: 0.6,
      dashed: true,
      dashScale: 1,
      dashSize: 16,
      gapSize: 8,
      depthWrite: false,
    });
  }
  return groundMaterial;
}

function linkKey(a: string, b: string): string {
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function isGroundLink(nodeA: string, nodeB: string): boolean {
  return nodeA.startsWith("gs-") || nodeB.startsWith("gs-");
}

export function updateLinks(
  linkStates: LinkState[],
  earthFrame: THREE.Object3D,
  _showAllLinks: boolean,
): void {
  earthFrameRef = earthFrame;
  const now = performance.now();
  const active = new Set<string>();

  for (const ls of linkStates) {
    const key = linkKey(ls.node_a, ls.node_b);
    active.add(key);
    const ground = isGroundLink(ls.node_a, ls.node_b);

    const existing = links.get(key);
    if (existing) {
      if (existing.state !== "active" && ls.state === "active") {
        existing.upTime = now;
        existing.failTime = null;
        existing.line.visible = true;
      }
      existing.state = ls.state;
    } else {
      const geometry = new LineGeometry();
      geometry.setPositions([0, 0, 0, 0, 0, 1]);

      const mat = ground ? getGroundMaterial() : getIslMaterial();

      const line = new Line2(geometry, mat);
      line.frustumCulled = false;
      earthFrame.add(line);

      const baseColor = new THREE.Color(ground ? LINK_GROUND_COLOR : LINK_ISL_COLOR);
      const baseOpacity = ground ? 0.6 : 0.55;

      links.set(key, {
        line,
        geometry,
        material: mat,
        state: ls.state,
        nodeA: ls.node_a,
        nodeB: ls.node_b,
        isGround: ground,
        failTime: null,
        upTime: now,
        baseColor,
        baseOpacity,
      });
    }
  }

  for (const [key, entry] of links) {
    if (!active.has(key) && entry.state === "active") {
      entry.state = "inactive";
      entry.failTime = now;
      entry.upTime = null;
    }
  }
}

const _linkPosA = new THREE.Vector3();
const _linkPosB = new THREE.Vector3();

export function animateLinks(showIslLinks: boolean = true, showGroundLinks: boolean = true): void {
  const now = performance.now();

  for (const [key, entry] of links) {
    const hasA = getNodeLocalPosition(entry.nodeA, _linkPosA);
    const hasB = getNodeLocalPosition(entry.nodeB, _linkPosB);

    if (!hasA || !hasB) {
      entry.line.visible = false;
      continue;
    }

    if (entry.failTime === null) {
      if (entry.isGround && !showGroundLinks) {
        entry.line.visible = false;
        continue;
      }
      if (!entry.isGround && !showIslLinks) {
        entry.line.visible = false;
        continue;
      }
    }

    if (entry.isGround) {
      entry.geometry.setPositions([
        _linkPosA.x, _linkPosA.y, _linkPosA.z,
        _linkPosB.x, _linkPosB.y, _linkPosB.z,
      ]);
      entry.line.computeLineDistances();
    } else {
      entry.geometry.setPositions(bowedPositions(_linkPosA, _linkPosB));
    }

    if (entry.failTime !== null) {
      const elapsed = now - entry.failTime;
      if (elapsed < FAIL_HOLD_MS) {
        if (entry.material === islMaterial || entry.material === groundMaterial) {
          const failMat = new LineMaterial({
            color: LINK_FAIL_COLOR,
            linewidth: entry.isGround ? LINK_GROUND_WIDTH : LINK_ISL_WIDTH,
            resolution,
            transparent: true,
            opacity: 0.7,
            depthWrite: false,
          });
          entry.line.material = failMat;
          entry.material = failMat;
        }
        entry.material.color.setHex(LINK_FAIL_COLOR);
        entry.material.opacity = 0.7;
        entry.line.visible = true;
      } else if (elapsed < FAIL_HOLD_MS + FAIL_FADE_MS) {
        const t = (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS;
        const failColor = new THREE.Color(LINK_FAIL_COLOR);
        const darkColor = new THREE.Color(LINK_INACTIVE_COLOR);
        entry.material.color.copy(failColor).lerp(darkColor, t);
        entry.material.opacity = 0.7 * (1 - t);
        entry.line.visible = true;
      } else {
        entry.line.visible = false;
        if (earthFrameRef) earthFrameRef.remove(entry.line);
        entry.geometry.dispose();
        if (entry.material !== islMaterial && entry.material !== groundMaterial) {
          entry.material.dispose();
        }
        links.delete(key);
        continue;
      }
    } else if (entry.upTime !== null) {
      const elapsed = now - entry.upTime;
      const UP_DURATION = 750;
      if (elapsed >= UP_DURATION) {
        entry.upTime = null;
      }
      entry.line.visible = true;
    } else {
      entry.line.visible = true;
    }
  }
}

export function getLinks(): Map<string, LinkEntry> {
  return links;
}
