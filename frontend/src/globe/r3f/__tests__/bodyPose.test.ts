// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The frame law: where each body sits and how it is turned in each view, from real body
 *  samples (fixtures/bodySamples.json, written by the backend). */

import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { interpolateWindows, matMul, matVec, transpose, type Matrix3 } from "../../../sim/bodySamples";
import { BODY_SAMPLE_FIXTURE } from "../../../sim/__tests__/bodySampleFixture";
import {
  bodyPose,
  sunDirection,
  toRenderQuaternion,
  toRenderVector,
  viewRotation,
  worldVelocity,
} from "../bodyPose";

const { windows, bodies_truth: truth } = BODY_SAMPLE_FIXTURE;
const firstTruth = truth[0]!;
const sky = interpolateWindows(windows, firstTruth.sim_time_unix)!;
const earth = sky.bodies.earth!;
const luna = sky.bodies.luna!;

function maxDifference(a: Matrix3, b: Matrix3): number {
  let worst = 0;
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) worst = Math.max(worst, Math.abs(a[i]![j]! - b[i]![j]!));
  }
  return worst;
}

const IDENTITY: Matrix3 = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];

describe("inertial view (ICRF axes)", () => {
  it("turns Earth by its sampled orientation and keeps the stars fixed", () => {
    expect(viewRotation(sky, "earth-inertial")).toEqual(IDENTITY);
    const pose = bodyPose(sky, "earth", "earth-inertial")!;
    expect(pose.positionKm).toEqual([0, 0, 0]);
    expect(maxDifference(pose.rotation, transpose(earth.rotation))).toBe(0);
  });

  it("places Luna at its sampled position", () => {
    expect(bodyPose(sky, "luna", "earth-inertial")!.positionKm).toEqual(luna.positionKm);
  });

  it("points the Sun along its sampled position", () => {
    const direction = sunDirection(sky, "earth-inertial")!;
    const n = Math.hypot(...sky.sunPositionKm);
    expect(direction[0]).toBeCloseTo(sky.sunPositionKm[0] / n, 12);
    expect(Math.hypot(...direction)).toBeCloseTo(1, 12);
  });
});

describe("Earth-fixed view (Earth's axes)", () => {
  it("holds Earth still and turns the stars and Luna about it", () => {
    expect(viewRotation(sky, "earth-fixed")).toBe(earth.rotation);
    const pose = bodyPose(sky, "earth", "earth-fixed")!;
    expect(maxDifference(pose.rotation, IDENTITY)).toBeLessThan(1e-15);
    expect(Math.hypot(...pose.angularVelocityLocalRadS)).toBeLessThan(1e-20);
    const lunaPose = bodyPose(sky, "luna", "earth-fixed")!;
    const expected = matVec(earth.rotation, luna.positionKm);
    expect(Math.hypot(...lunaPose.positionKm.map((v, i) => v - expected[i]!))).toBeLessThan(1e-9);
    expect(maxDifference(lunaPose.rotation, matMul(earth.rotation, transpose(luna.rotation)))).toBe(0);
  });

  it("has no axes for a session without Earth", () => {
    const { earth: _earth, ...withoutEarth } = sky.bodies;
    const lunaOnly = { ...sky, bodies: withoutEarth };
    expect(viewRotation(lunaOnly, "earth-fixed")).toBeNull();
    expect(bodyPose(lunaOnly, "luna", "earth-fixed")).toBeNull();
    expect(bodyPose(lunaOnly, "luna", "earth-inertial")).not.toBeNull();
  });
});

describe("render axes", () => {
  it("maps km (x, y, z) to render (x, z, -y)", () => {
    expect(toRenderVector([1, 2, 3], new THREE.Vector3()).toArray()).toEqual([1, 3, -2]);
  });

  it("renders a turn about the km pole as the same turn about render +Y", () => {
    const angle = 0.7;
    const aboutPole: Matrix3 = [
      [Math.cos(angle), -Math.sin(angle), 0],
      [Math.sin(angle), Math.cos(angle), 0],
      [0, 0, 1],
    ];
    const quaternion = toRenderQuaternion(aboutPole, new THREE.Quaternion());
    const expected = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), angle);
    expect(quaternion.angleTo(expected)).toBeLessThan(1e-12);
  });

  it("gives a body-local point its world velocity", () => {
    // A point fixed on Earth's equator in the inertial view moves at ω × r.
    const pose = bodyPose(sky, "earth", "earth-inertial")!;
    const omega = toRenderVector(pose.angularVelocityLocalRadS, new THREE.Vector3());
    const local = new THREE.Vector3(6378.137, 0, 0);
    const group = toRenderQuaternion(pose.rotation, new THREE.Quaternion());
    const velocity = worldVelocity(local, new THREE.Vector3(), group, omega, new THREE.Vector3());
    expect(velocity.length()).toBeCloseTo(6378.137 * Math.hypot(...earth.angularVelocityRadS), 9);
  });
});
