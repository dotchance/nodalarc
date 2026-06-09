// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Contract tests for astronomy.ts.
 *
 *  gmstRadians() MUST match services/ome/propagator.py:gmst() to machine
 *  precision. The reference values below were computed by running the
 *  backend's gmst() with the listed Unix timestamps.
 *
 *  If any value here diverges from backend output, frontend Earth rotation
 *  will drift relative to backend-computed satellite positions and the
 *  ground-track alignment will be wrong.
 */

import { describe, it, expect } from "vitest";
import * as THREE from "three";
import {
  gmstRadians,
  worldVelocity,
  simTimeIsoToUnixSeconds,
  J2000_UNIX_SECONDS,
} from "../astronomy";
import { catalogEarthFrame } from "../../sim/__tests__/bodyModelFixture";

const ROTATION_RATE_FROM_EPHEMERIS_RAD_S = catalogEarthFrame().rotation_rate_rad_s;

describe("gmstRadians — backend contract", () => {
  // Reference values computed from services/ome/propagator.py:gmst().
  // Do not edit without re-running the backend reference computation.
  const REFERENCE_VALUES: Array<{
    name: string;
    unixSeconds: number;
    expectedGmstRad: number;
  }> = [
    { name: "J2000 epoch", unixSeconds: 946728000.0, expectedGmstRad: 4.894961212735793 },
    { name: "J2000 + 12h", unixSeconds: 946771200.0, expectedGmstRad: 1.761969955048685 },
    { name: "J2000 + 1 sidereal day", unixSeconds: 946814164.0905, expectedGmstRad: 4.894961210487280 },
    { name: "2026-04-04T00:00:00Z", unixSeconds: 1775260800.0, expectedGmstRad: 3.356722590080810 },
    { name: "2026-04-04T12:00:00Z", unixSeconds: 1775304000.0, expectedGmstRad: 0.223731332394507 },
    { name: "Far future 2050-01-01", unixSeconds: 2524608000.0, expectedGmstRad: 1.760088545761268 },
  ];

  for (const ref of REFERENCE_VALUES) {
    it(`matches backend gmst() at ${ref.name}`, () => {
      const actual = gmstRadians(ref.unixSeconds);
      // 1e-9 rad ≈ 0.2 milliarcseconds — far below any visual resolution.
      expect(Math.abs(actual - ref.expectedGmstRad)).toBeLessThan(1e-9);
    });
  }

  it("returns values in [0, 2π)", () => {
    // Sample across a year at 1-hour intervals — all results must be wrapped.
    for (let h = 0; h < 24 * 365; h++) {
      const t = J2000_UNIX_SECONDS + h * 3600;
      const g = gmstRadians(t);
      expect(g).toBeGreaterThanOrEqual(0);
      expect(g).toBeLessThan(2 * Math.PI);
    }
  });

  it("handles negative days (pre-J2000) without sign errors", () => {
    // NodalArc sessions won't encounter these, but correctness over
    // assumption: JS % of float differs from Python for negative inputs.
    const preJ2000 = J2000_UNIX_SECONDS - 86400; // 1999-12-31 12:00:00 UTC
    const g = gmstRadians(preJ2000);
    expect(g).toBeGreaterThanOrEqual(0);
    expect(g).toBeLessThan(2 * Math.PI);
    expect(Number.isFinite(g)).toBe(true);
  });

  it("increases monotonically over short spans (modulo wrap)", () => {
    // GMST advances ~7.29e-5 rad/s. Over 1 second it advances but doesn't
    // wrap. Check that adjacent samples differ by roughly the right amount.
    const t0 = 1775260800; // 2026-04-04T00:00:00Z
    const t1 = t0 + 1;
    const g0 = gmstRadians(t0);
    const g1 = gmstRadians(t1);
    const delta = g1 - g0;
    // Expect ~7.292e-5 rad per second (sidereal rate, 360.985/86400 deg/sec).
    expect(delta).toBeGreaterThan(7.2e-5);
    expect(delta).toBeLessThan(7.4e-5);
  });

  it("wraps cleanly across the 2π boundary", () => {
    // Find a span where gmst wraps from just-below-2π to just-above-0.
    // Sidereal day ≈ 86164.0905s; pick start where gmst is near 2π.
    // We'll search from J2000 forward until we find the wrap.
    let prev = gmstRadians(J2000_UNIX_SECONDS);
    for (let i = 1; i < 90000; i++) {
      const curr = gmstRadians(J2000_UNIX_SECONDS + i);
      if (curr < prev) {
        // Wrap found: curr should be small, prev should be near 2π.
        expect(prev).toBeGreaterThan(2 * Math.PI - 0.01);
        expect(curr).toBeLessThan(0.01);
        return;
      }
      prev = curr;
    }
    throw new Error("No wrap found in 90000s — sidereal day is ~86164s");
  });
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
