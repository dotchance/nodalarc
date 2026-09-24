// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The drawn shape of trace lines: ground segments are straight, and no drawn trace line
 *  enters a body. */

import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  MAX_TRACE_LINE_POINTS,
  segmentEntersSphere,
  traceLinePoints,
  type BodySphere,
} from "../linkCurve";

const R = 100;
const EARTH: BodySphere = { center: new THREE.Vector3(0, 0, 0), radius: R };
const SAT = R * 1.086; // 550 km LEO at render scale
const GS = R * 1.001; // ground stations sit just above the surface

const deg = Math.PI / 180;
/** A point at `radius` from the center, `lonDeg` round the equator and `latDeg` up. */
function at(radius: number, lonDeg: number, latDeg = 0, center = EARTH.center): THREE.Vector3 {
  return new THREE.Vector3(
    radius * Math.cos(latDeg * deg) * Math.cos(lonDeg * deg),
    radius * Math.sin(latDeg * deg),
    radius * Math.cos(latDeg * deg) * Math.sin(lonDeg * deg),
  ).add(center);
}

function drawn(a: THREE.Vector3, b: THREE.Vector3, ground: boolean, bodies = [EARTH]) {
  const points = Array.from({ length: MAX_TRACE_LINE_POINTS }, () => new THREE.Vector3());
  const count = traceLinePoints(a, b, ground, bodies, points);
  return points.slice(0, count);
}

/** Whether any drawn piece of a polyline enters the body. */
function entersBody(points: THREE.Vector3[], body: BodySphere): boolean {
  return points.some(
    (point, i) => i + 1 < points.length && segmentEntersSphere(point, points[i + 1]!, body.center, body.radius),
  );
}

describe("segmentEntersSphere", () => {
  it("finds the closest point anywhere along the segment", () => {
    expect(segmentEntersSphere(at(SAT, 0), at(SAT, 180), EARTH.center, R)).toBe(true);
    expect(segmentEntersSphere(at(SAT, 0), at(SAT, 20), EARTH.center, R)).toBe(false);
    // A ground station to a satellite above its horizon, then below it.
    expect(segmentEntersSphere(at(GS, 0), at(SAT, 5), EARTH.center, R)).toBe(false);
    expect(segmentEntersSphere(at(GS, 0), at(SAT, 60), EARTH.center, R)).toBe(true);
  });
});

describe("traceLinePoints", () => {
  it("draws a traced ground segment straight, like a ground link", () => {
    const a = at(GS, 0);
    const b = at(SAT, 5);
    expect(drawn(a, b, true).map((p) => p.toArray())).toEqual([a.toArray(), b.toArray()]);
  });

  it("never draws into the planet, and still joins the two ends", () => {
    const cases: [THREE.Vector3, THREE.Vector3, boolean][] = [
      [at(SAT, 0), at(SAT, 180), false], // opposite sides
      [at(SAT, 0), at(SAT, 179.9, 0.05), false], // almost opposite
      [at(SAT, 10, 40), at(SAT, 150, -30), false], // far apart, off the equator
      [at(SAT, 0), at(SAT, 100), false], // a bridge across several silent hops
      [at(GS, 0), at(SAT, 60), true], // a ground station to a satellite below its horizon
      [at(GS, 0), at(GS, 120), true], // two ground stations far apart
      [at(SAT, 0, 90), at(SAT, 0, -90), false], // pole to pole
    ];
    for (const [a, b, ground] of cases) {
      const points = drawn(a, b, ground);
      expect(entersBody(points, EARTH)).toBe(false);
      expect(points[0]!.distanceTo(a)).toBeLessThan(1e-9);
      expect(points[points.length - 1]!.distanceTo(b)).toBeLessThan(1e-9);
    }
  });

  it("follows the surface of whichever body the line would enter", () => {
    const luna: BodySphere = { center: new THREE.Vector3(1000, 0, 0), radius: 27 };
    const a = at(30, 0, 0, luna.center);
    const b = at(30, 180, 0, luna.center);
    const points = drawn(a, b, false, [EARTH, luna]);
    expect(entersBody(points, luna)).toBe(false);
    expect(entersBody(points, EARTH)).toBe(false);
  });
});
