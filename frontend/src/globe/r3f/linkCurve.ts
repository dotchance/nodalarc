// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The drawn shape of a line between two world-space points, shared by the link and trace
 * renderers so a traced segment lies on the link it crossed.
 *
 * A ground line is straight. Any other line is the bowed arc every ISL is drawn with:
 * SEGMENTS_PER_ISL pieces, lifted at the middle by ARC_LIFT of its chord, away from the world
 * origin. A trace line that would still enter a body (a bridge across hops the view cannot
 * place, or a measurement the geometry has outrun) follows that body's surface instead: an arc
 * around the body's center from one end's altitude to the other's.
 */

import * as THREE from "three";

export const SEGMENTS_PER_ISL = 16;
const ARC_LIFT = 0.03;
const SURFACE_ARC_SEGMENTS = 48;
/** Extra height a surface arc gains at its middle, as a fraction of the body's radius. */
const SURFACE_ARC_CLEARANCE = 0.02;

/** Most points traceLinePoints writes; size a point pool with it. */
export const MAX_TRACE_LINE_POINTS = SURFACE_ARC_SEGMENTS + 1;

export interface BodySphere {
  center: THREE.Vector3;
  radius: number;
}

const _outward = new THREE.Vector3();
const _p0 = new THREE.Vector3();
const _p1 = new THREE.Vector3();
const _fromCenterA = new THREE.Vector3();
const _fromCenterB = new THREE.Vector3();
const _axis = new THREE.Vector3();
const _turn = new THREE.Quaternion();
const _closest = new THREE.Vector3();

function bowedPoint(
  a: THREE.Vector3,
  b: THREE.Vector3,
  outward: THREE.Vector3,
  lift: number,
  t: number,
  target: THREE.Vector3,
): THREE.Vector3 {
  return target.lerpVectors(a, b, t).addScaledVector(outward, 4 * t * (1 - t) * lift);
}

/** Write the ISL bowed arc from a to b as SEGMENTS_PER_ISL segment pairs at `offset`. */
export function writeBowedArc(
  buffer: Float32Array,
  offset: number,
  a: THREE.Vector3,
  b: THREE.Vector3,
): void {
  _outward.lerpVectors(a, b, 0.5).normalize();
  const lift = a.distanceTo(b) * ARC_LIFT;
  for (let i = 0; i < SEGMENTS_PER_ISL; i++) {
    bowedPoint(a, b, _outward, lift, i / SEGMENTS_PER_ISL, _p0);
    bowedPoint(a, b, _outward, lift, (i + 1) / SEGMENTS_PER_ISL, _p1);
    const idx = offset + i * 6;
    buffer[idx] = _p0.x;
    buffer[idx + 1] = _p0.y;
    buffer[idx + 2] = _p0.z;
    buffer[idx + 3] = _p1.x;
    buffer[idx + 4] = _p1.y;
    buffer[idx + 5] = _p1.z;
  }
}

/** Whether the straight segment from a to b comes closer to `center` than `radius`. */
export function segmentEntersSphere(
  a: THREE.Vector3,
  b: THREE.Vector3,
  center: THREE.Vector3,
  radius: number,
): boolean {
  const lengthSq = a.distanceToSquared(b);
  const t =
    lengthSq === 0
      ? 0
      : Math.min(
          1,
          Math.max(
            0,
            ((center.x - a.x) * (b.x - a.x) + (center.y - a.y) * (b.y - a.y) + (center.z - a.z) * (b.z - a.z)) /
              lengthSq,
          ),
        );
  _closest.lerpVectors(a, b, t);
  return _closest.distanceToSquared(center) < radius * radius;
}

/** The first body a polyline of `count` points enters, or null. */
function bodyEntered(
  points: readonly THREE.Vector3[],
  count: number,
  bodies: readonly BodySphere[],
): BodySphere | null {
  for (const body of bodies) {
    for (let i = 0; i + 1 < count; i++) {
      if (segmentEntersSphere(points[i]!, points[i + 1]!, body.center, body.radius)) return body;
    }
  }
  return null;
}

/** Fill `points` with an arc around `body` from a to b; returns the number of points. */
function surfaceArcPoints(
  a: THREE.Vector3,
  b: THREE.Vector3,
  body: BodySphere,
  points: THREE.Vector3[],
): number {
  const fromA = _fromCenterA.subVectors(a, body.center);
  const fromB = _fromCenterB.subVectors(b, body.center);
  const radiusA = fromA.length();
  const radiusB = fromB.length();
  fromA.normalize();
  fromB.normalize();
  const angle = fromA.angleTo(fromB);
  _axis.crossVectors(fromA, fromB);
  if (_axis.lengthSq() < 1e-12) {
    // Opposite ends: any great circle through both will do.
    _axis.crossVectors(fromA, Math.abs(fromA.y) < 0.9 ? _p0.set(0, 1, 0) : _p0.set(1, 0, 0));
  }
  _axis.normalize();
  for (let i = 0; i <= SURFACE_ARC_SEGMENTS; i++) {
    const t = i / SURFACE_ARC_SEGMENTS;
    const height =
      radiusA + (radiusB - radiusA) * t + 4 * t * (1 - t) * body.radius * SURFACE_ARC_CLEARANCE;
    _turn.setFromAxisAngle(_axis, angle * t);
    points[i]!.copy(fromA).applyQuaternion(_turn).multiplyScalar(height).add(body.center);
  }
  return SURFACE_ARC_SEGMENTS + 1;
}

/**
 * Fill `points` with the drawn trace line from a to b and return how many points it has: the
 * ground or ISL shape, or the surface arc around the first body that shape would enter.
 * `points` holds at least MAX_TRACE_LINE_POINTS vectors.
 */
export function traceLinePoints(
  a: THREE.Vector3,
  b: THREE.Vector3,
  ground: boolean,
  bodies: readonly BodySphere[],
  points: THREE.Vector3[],
): number {
  let count: number;
  if (ground) {
    points[0]!.copy(a);
    points[1]!.copy(b);
    count = 2;
  } else {
    _outward.lerpVectors(a, b, 0.5).normalize();
    const lift = a.distanceTo(b) * ARC_LIFT;
    for (let i = 0; i <= SEGMENTS_PER_ISL; i++) {
      bowedPoint(a, b, _outward, lift, i / SEGMENTS_PER_ISL, points[i]!);
    }
    count = SEGMENTS_PER_ISL + 1;
  }
  const body = bodyEntered(points, count, bodies);
  return body === null ? count : surfaceArcPoints(a, b, body, points);
}
