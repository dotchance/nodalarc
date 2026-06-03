// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import * as THREE from "three";
import { GS_SIZE, SAT_RADIUS } from "../../config";
import type { LinkState, NodeState } from "../../types";
import { isGroundLinkState } from "../../networkIdentity";
import { getNodeWorldPosition } from "./positions";

export interface ScreenRect {
  width: number;
  height: number;
}

export interface PickBody {
  id: string;
  center: THREE.Vector3;
  radius: number;
}

export type ScreenPick =
  | { kind: "node"; node: NodeState }
  | { kind: "link"; link: LinkState }
  | { kind: "body"; bodyId: string }
  | null;

const NODE_MIN_HIT_PX = 16;
const NODE_MAX_HIT_PX = 30;
const NODE_HIT_PADDING_PX = 7;
const BODY_MIN_HIT_PX = 24;
const BODY_MAX_EDGE_PADDING_PX = 28;
const LINK_HIT_PX = 12;

const _worldA = new THREE.Vector3();
const _worldB = new THREE.Vector3();
const _worldC = new THREE.Vector3();
const _cameraRight = new THREE.Vector3();

export function pointToSegment2D(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

export function nodeHitRadiusPx(projectedRadiusPx: number): number {
  if (!Number.isFinite(projectedRadiusPx) || projectedRadiusPx < 0) {
    throw new Error(`invalid projected node radius: ${projectedRadiusPx}`);
  }
  return Math.min(
    NODE_MAX_HIT_PX,
    Math.max(NODE_MIN_HIT_PX, projectedRadiusPx + NODE_HIT_PADDING_PX),
  );
}

export function bodyHitRadiusPx(projectedRadiusPx: number): number {
  if (!Number.isFinite(projectedRadiusPx) || projectedRadiusPx < 0) {
    throw new Error(`invalid projected body radius: ${projectedRadiusPx}`);
  }
  return projectedRadiusPx + Math.max(BODY_MIN_HIT_PX, Math.min(BODY_MAX_EDGE_PADDING_PX, projectedRadiusPx * 0.08));
}

function projectWorldToScreen(
  point: THREE.Vector3,
  camera: THREE.Camera,
  rect: ScreenRect,
  out: THREE.Vector2,
): boolean {
  _worldC.copy(point).project(camera);
  if (!Number.isFinite(_worldC.x) || !Number.isFinite(_worldC.y) || !Number.isFinite(_worldC.z)) {
    return false;
  }
  if (_worldC.z < -1 || _worldC.z > 1) return false;
  out.set(((_worldC.x + 1) * rect.width) / 2, ((1 - _worldC.y) * rect.height) / 2);
  return true;
}

function projectedRadiusPx(
  centerWorld: THREE.Vector3,
  radiusWorld: number,
  camera: THREE.Camera,
  rect: ScreenRect,
): number {
  if (!Number.isFinite(radiusWorld) || radiusWorld < 0) {
    throw new Error(`invalid world radius: ${radiusWorld}`);
  }
  camera.updateMatrixWorld();
  _cameraRight.setFromMatrixColumn(camera.matrixWorld, 0).normalize();
  const center = new THREE.Vector2();
  const edge = new THREE.Vector2();
  if (!projectWorldToScreen(centerWorld, camera, rect, center)) return 0;
  if (!projectWorldToScreen(_worldB.copy(centerWorld).addScaledVector(_cameraRight, radiusWorld), camera, rect, edge)) {
    return 0;
  }
  return center.distanceTo(edge);
}

function nodeVisualRadius(node: NodeState): number {
  // Kept as a single hook for model-specific glyphs: when satellite/terminal types get custom
  // glyph geometry, the visual radius and the screen-space pick target change in one place.
  return node.node_type === "ground_station" ? GS_SIZE * 0.5 : SAT_RADIUS;
}

function pickNode(
  xPx: number,
  yPx: number,
  camera: THREE.Camera,
  rect: ScreenRect,
  nodes: NodeState[],
): ScreenPick {
  let best: { score: number; node: NodeState } | null = null;
  const screen = new THREE.Vector2();
  for (const node of nodes) {
    if (!getNodeWorldPosition(node.node_id, _worldA)) continue;
    if (!projectWorldToScreen(_worldA, camera, rect, screen)) continue;
    const projected = projectedRadiusPx(_worldA, nodeVisualRadius(node), camera, rect);
    const hitRadius = nodeHitRadiusPx(projected);
    const dist = Math.hypot(xPx - screen.x, yPx - screen.y);
    if (dist > hitRadius) continue;
    const score = dist / hitRadius;
    if (!best || score < best.score) best = { score, node };
  }
  return best ? { kind: "node", node: best.node } : null;
}

function pickLink(
  xPx: number,
  yPx: number,
  camera: THREE.Camera,
  rect: ScreenRect,
  links: LinkState[],
  showIsl: boolean,
  showGnd: boolean,
): ScreenPick {
  let bestDist = LINK_HIT_PX;
  let bestLink: LinkState | null = null;
  const a = new THREE.Vector2();
  const b = new THREE.Vector2();
  for (const link of links) {
    if (link.state !== "active") continue;
    const ground = isGroundLinkState(link);
    if (ground ? !showGnd : !showIsl) continue;
    if (!getNodeWorldPosition(link.node_a, _worldA)) continue;
    if (!getNodeWorldPosition(link.node_b, _worldB)) continue;
    if (!projectWorldToScreen(_worldA, camera, rect, a)) continue;
    if (!projectWorldToScreen(_worldB, camera, rect, b)) continue;
    const dist = pointToSegment2D(xPx, yPx, a.x, a.y, b.x, b.y);
    if (dist < bestDist) {
      bestDist = dist;
      bestLink = link;
    }
  }
  return bestLink ? { kind: "link", link: bestLink } : null;
}

function pickBody(
  xPx: number,
  yPx: number,
  camera: THREE.Camera,
  rect: ScreenRect,
  bodies: PickBody[],
): ScreenPick {
  let best: { score: number; bodyId: string } | null = null;
  const screen = new THREE.Vector2();
  for (const body of bodies) {
    if (!projectWorldToScreen(body.center, camera, rect, screen)) continue;
    const projected = projectedRadiusPx(body.center, body.radius, camera, rect);
    const hitRadius = bodyHitRadiusPx(projected);
    const dist = Math.hypot(xPx - screen.x, yPx - screen.y);
    if (dist > hitRadius) continue;
    const edgeDist = Math.max(0, dist - projected);
    const score = edgeDist / Math.max(1, hitRadius - projected);
    if (!best || score < best.score) best = { score, bodyId: body.id };
  }
  return best ? { kind: "body", bodyId: best.bodyId } : null;
}

export function pickSceneAtScreenPoint({
  xPx,
  yPx,
  camera,
  rect,
  nodes,
  links,
  bodies,
  showIslLinks,
  showGroundLinks,
}: {
  xPx: number;
  yPx: number;
  camera: THREE.Camera;
  rect: ScreenRect;
  nodes: NodeState[];
  links: LinkState[];
  bodies: PickBody[];
  showIslLinks: boolean;
  showGroundLinks: boolean;
}): ScreenPick {
  const node = pickNode(xPx, yPx, camera, rect, nodes);
  if (node) return node;
  const link = pickLink(xPx, yPx, camera, rect, links, showIslLinks, showGroundLinks);
  if (link) return link;
  return pickBody(xPx, yPx, camera, rect, bodies);
}
