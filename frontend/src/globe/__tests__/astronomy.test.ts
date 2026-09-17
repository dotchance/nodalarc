// @vitest-environment node
// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import * as THREE from "three";
import {
  gmstRadians,
  worldVelocity,
  simTimeIsoToUnixSeconds,
  J2000_UNIX_SECONDS,
} from "../astronomy";
import {
  gmstRadians as sharedGmstRadians,
  J2000_UNIX_SECONDS as SHARED_J2000,
} from "../../sim/orbitalMath";
import { catalogEarthFrame } from "../../sim/__tests__/bodyModelFixture";

const ROTATION_RATE_FROM_EPHEMERIS_RAD_S = catalogEarthFrame().rotation_rate_rad_s;

it("reexports the shared GMST function and epoch", () => {
  expect(gmstRadians).toBe(sharedGmstRadians);
  expect(J2000_UNIX_SECONDS).toBe(SHARED_J2000);
});

describe("simTimeIsoToUnixSeconds", () => {
  it("parses ISO-8601 correctly", () => {
    expect(simTimeIsoToUnixSeconds("2026-04-04T00:00:00Z")).toBe(1775260800);
    expect(simTimeIsoToUnixSeconds("2000-01-01T12:00:00Z")).toBe(946728000);
  });
});

describe("worldVelocity", () => {
  it("returns v_local when frame angular velocity is zero (static frame)", () => {
    const pLocal = new THREE.Vector3(63.71, 0, 0);    // ~equator, scene units
    const vLocal = new THREE.Vector3(0, 7.5, 0);       // arbitrary
    const target = new THREE.Vector3();
    worldVelocity(pLocal, vLocal, 0, 0, target);
    expect(target.x).toBeCloseTo(0, 10);
    expect(target.y).toBeCloseTo(7.5, 10);
    expect(target.z).toBeCloseTo(0, 10);
  });

  it("returns v_local under static frame at non-zero θ (theta is frozen)", () => {
    // If frameAngularVelocityRadS=0, the frame is static even at non-zero θ.
    // v_world = R_z(θ)·v_local. For θ=π/2: (x,z) → (z, -x).
    const pLocal = new THREE.Vector3(10, 0, 0);
    const vLocal = new THREE.Vector3(1, 0, 0);
    const target = new THREE.Vector3();
    worldVelocity(pLocal, vLocal, Math.PI / 2, 0, target);
    // R_z(π/2) maps +X to -Z: v = (1,0,0) → (0,0,-1)
    expect(target.x).toBeCloseTo(0, 10);
    expect(target.y).toBeCloseTo(0, 10);
    expect(target.z).toBeCloseTo(-1, 10);
  });

  it("adds the Ω × r term when the frame rotates (Earth-inertial at θ=0)", () => {
    // Equatorial point at radius r in scene units, stationary in ECEF.
    // In earth-inertial view at θ=0, its ECI velocity is:
    //   Ω × r = (0,ω,0) × (r,0,0) = (0, 0, -ω·r)
    const r = 100;
    const pLocal = new THREE.Vector3(r, 0, 0);
    const vLocal = new THREE.Vector3(0, 0, 0);
    const target = new THREE.Vector3();
    worldVelocity(pLocal, vLocal, 0, ROTATION_RATE_FROM_EPHEMERIS_RAD_S, target);
    expect(target.x).toBeCloseTo(0, 10);
    expect(target.y).toBeCloseTo(0, 10);
    expect(target.z).toBeCloseTo(-ROTATION_RATE_FROM_EPHEMERIS_RAD_S * r, 12);
  });

  it("composes rotation × (Ω × r + v) correctly", () => {
    // Full test: rotating frame, non-zero θ, non-zero v_local.
    // Expected: R_z(θ) · (Ω × p_local + v_local)
    const p = new THREE.Vector3(50, 10, 20);
    const v = new THREE.Vector3(1, 2, 3);
    const theta = Math.PI / 4;
    const omega = ROTATION_RATE_FROM_EPHEMERIS_RAD_S;

    // Expected local: Ω × p + v
    //   Ω × p = (ω·pz, 0, -ω·px) = (ω·20, 0, -ω·50)
    const lx = omega * 20 + 1;
    const ly = 0 + 2;
    const lz = -omega * 50 + 3;
    // Expected world: R_z(θ)·local where R_z maps (x,z) → (x cosθ + z sinθ, -x sinθ + z cosθ)
    const c = Math.cos(theta);
    const s = Math.sin(theta);
    const wx = lx * c + lz * s;
    const wy = ly;
    const wz = -lx * s + lz * c;

    const target = new THREE.Vector3();
    worldVelocity(p, v, theta, omega, target);
    expect(target.x).toBeCloseTo(wx, 12);
    expect(target.y).toBeCloseTo(wy, 12);
    expect(target.z).toBeCloseTo(wz, 12);
  });

  it("is unaffected by Ω at the pole (p = (0, r, 0))", () => {
    // A satellite on the rotation axis has Ω × p = 0.
    // v_world should equal R_z(θ)·v_local regardless of ω.
    const p = new THREE.Vector3(0, 100, 0);
    const v = new THREE.Vector3(5, 0, 0);
    const theta = 0.5;
    const target = new THREE.Vector3();
    worldVelocity(p, v, theta, ROTATION_RATE_FROM_EPHEMERIS_RAD_S, target);
    // R_z(0.5) applied to (5, 0, 0): (5 cos0.5, 0, -5 sin0.5)
    expect(target.x).toBeCloseTo(5 * Math.cos(0.5), 10);
    expect(target.y).toBeCloseTo(0, 10);
    expect(target.z).toBeCloseTo(-5 * Math.sin(0.5), 10);
  });
});
