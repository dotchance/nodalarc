// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Link rendering — batched Line2 for minimal draw calls.
//
// Before: 448 Line2 = 448 draw calls
// After:  2 Line2 (ISL batch + ground batch) + N fail-flash Line2
//         (N = links currently in animation, typically 0-5)
//
// Steady-state links are batched into 2 Line2 objects with shared
// materials. Fail-flash and up-pulse animations use individual Line2
// objects with their own materials for per-link color control. At any
// moment, only a handful of links are animating (during handovers),
// so the per-link overhead is negligible.
//
// The plan's A6 (custom RawShaderMaterial with GPU quad extrusion) was
// based on faulty CPU cost analysis — LineGeometry.setPositions()
// interleaving costs ~84µs/frame at 440 links, not a bottleneck.
// If profiling at >2000 links shows interleaving as the bottleneck,
// the custom shader becomes the right solution. Not before.

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

// --- Link metadata ---

interface LinkMeta {
  state: string;
  nodeA: string;
  nodeB: string;
  isGround: boolean;
  failTime: number | null;
  upTime: number | null;
  failLine: Line2 | null;
  failGeometry: LineGeometry | null;
  failMaterial: LineMaterial | null;
}

const linkMetas = new Map<string, LinkMeta>();

// --- Batched Line2 for steady-state links ---

let islLine: Line2 | null = null;
let islGeometry: LineGeometry | null = null;
let islMaterial: LineMaterial | null = null;

let groundLine: Line2 | null = null;
let groundGeometry: LineGeometry | null = null;
let groundMaterial: LineMaterial | null = null;

let earthFrameRef: THREE.Object3D | null = null;
let resolution = new THREE.Vector2(window.innerWidth, window.innerHeight);

window.addEventListener("resize", () => {
  resolution.set(window.innerWidth, window.innerHeight);
  if (islMaterial) islMaterial.resolution.copy(resolution);
  if (groundMaterial) groundMaterial.resolution.copy(resolution);
});

function ensureBatchedLines(earthFrame: THREE.Object3D): void {
  if (islLine) return;
  earthFrameRef = earthFrame;

  islGeometry = new LineGeometry();
  islGeometry.setPositions([0, 0, 0, 0, 0, 1]);
  islMaterial = new LineMaterial({
    color: LINK_ISL_COLOR,
    linewidth: LINK_ISL_WIDTH,
    resolution,
    transparent: true,
    opacity: 0.55,
    depthWrite: false,
  });
  islLine = new Line2(islGeometry, islMaterial);
  islLine.frustumCulled = false;
  earthFrame.add(islLine);

  groundGeometry = new LineGeometry();
  groundGeometry.setPositions([0, 0, 0, 0, 0, 1]);
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
  groundLine = new Line2(groundGeometry, groundMaterial);
  groundLine.frustumCulled = false;
  earthFrame.add(groundLine);
}

function linkKey(a: string, b: string): string {
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function isGroundLink(nodeA: string, nodeB: string): boolean {
  return nodeA.startsWith("gs-") || nodeB.startsWith("gs-");
}

function createFailLine(meta: LinkMeta): void {
  if (!earthFrameRef) return;
  const geo = new LineGeometry();
  geo.setPositions([0, 0, 0, 0, 0, 1]);
  const mat = new LineMaterial({
    color: LINK_FAIL_COLOR,
    linewidth: meta.isGround ? LINK_GROUND_WIDTH : LINK_ISL_WIDTH,
    resolution,
    transparent: true,
    opacity: 0.7,
    depthWrite: false,
  });
  const line = new Line2(geo, mat);
  line.frustumCulled = false;
  earthFrameRef.add(line);
  meta.failLine = line;
  meta.failGeometry = geo;
  meta.failMaterial = mat;
}

function destroyFailLine(meta: LinkMeta): void {
  if (meta.failLine && earthFrameRef) {
    earthFrameRef.remove(meta.failLine);
    meta.failGeometry?.dispose();
    meta.failMaterial?.dispose();
  }
  meta.failLine = null;
  meta.failGeometry = null;
  meta.failMaterial = null;
}

export function updateLinks(
  linkStates: LinkState[],
  earthFrame: THREE.Object3D,
  _showAllLinks: boolean,
): void {
  ensureBatchedLines(earthFrame);
  const now = performance.now();
  const active = new Set<string>();

  for (const ls of linkStates) {
    const key = linkKey(ls.node_a, ls.node_b);
    active.add(key);
    const ground = isGroundLink(ls.node_a, ls.node_b);

    const existing = linkMetas.get(key);
    if (existing) {
      if (existing.state !== "active" && ls.state === "active") {
        existing.upTime = now;
        existing.failTime = null;
        destroyFailLine(existing);
      }
      existing.state = ls.state;
    } else {
      linkMetas.set(key, {
        state: ls.state,
        nodeA: ls.node_a,
        nodeB: ls.node_b,
        isGround: ground,
        failTime: null,
        upTime: now,
        failLine: null,
        failGeometry: null,
        failMaterial: null,
      });
    }
  }

  for (const [, meta] of linkMetas) {
    if (!active.has(linkKey(meta.nodeA, meta.nodeB)) && meta.state === "active") {
      meta.state = "inactive";
      meta.failTime = now;
      meta.upTime = null;
    }
  }
}

const _linkPosA = new THREE.Vector3();
const _linkPosB = new THREE.Vector3();

export function animateLinks(showIslLinks: boolean = true, showGroundLinks: boolean = true): void {
  if (!islGeometry || !groundGeometry) return;

  const now = performance.now();
  const islPositions: number[] = [];
  const groundPositions: number[] = [];

  for (const [key, meta] of linkMetas) {
    // Manage fail-flash lifecycle
    if (meta.failTime !== null) {
      const elapsed = now - meta.failTime;
      if (elapsed >= FAIL_HOLD_MS + FAIL_FADE_MS) {
        destroyFailLine(meta);
        linkMetas.delete(key);
        continue;
      }

      if (!meta.failLine) createFailLine(meta);
      if (meta.failMaterial) {
        if (elapsed < FAIL_HOLD_MS) {
          meta.failMaterial.color.setHex(LINK_FAIL_COLOR);
          meta.failMaterial.opacity = 0.7;
        } else {
          const t = (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS;
          const failColor = new THREE.Color(LINK_FAIL_COLOR);
          const darkColor = new THREE.Color(LINK_INACTIVE_COLOR);
          meta.failMaterial.color.copy(failColor).lerp(darkColor, t);
          meta.failMaterial.opacity = 0.7 * (1 - t);
        }
      }
    }

    const hasA = getNodeLocalPosition(meta.nodeA, _linkPosA);
    const hasB = getNodeLocalPosition(meta.nodeB, _linkPosB);
    if (!hasA || !hasB) continue;

    // Update fail-flash line geometry
    if (meta.failGeometry) {
      if (meta.isGround) {
        meta.failGeometry.setPositions([
          _linkPosA.x, _linkPosA.y, _linkPosA.z,
          _linkPosB.x, _linkPosB.y, _linkPosB.z,
        ]);
      } else {
        meta.failGeometry.setPositions(bowedPositions(_linkPosA, _linkPosB));
      }
    }

    // Active links go into their respective batches
    if (meta.state === "active") {
      if (meta.isGround) {
        if (showGroundLinks) {
          groundPositions.push(
            _linkPosA.x, _linkPosA.y, _linkPosA.z,
            _linkPosB.x, _linkPosB.y, _linkPosB.z,
          );
        }
      } else {
        if (showIslLinks) {
          islPositions.push(...bowedPositions(_linkPosA, _linkPosB));
        }
      }
    }
  }

  if (islPositions.length >= 6) {
    islGeometry.setPositions(islPositions);
    islLine!.visible = true;
  } else {
    islLine!.visible = false;
  }

  if (groundPositions.length >= 6) {
    groundGeometry.setPositions(groundPositions);
    groundLine!.computeLineDistances();
    groundLine!.visible = true;
  } else {
    groundLine!.visible = false;
  }
}

export function getLinks(): Map<string, LinkMeta> {
  return linkMetas;
}
