// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import {
  gmstRadians,
  propagateToSceneXYZ,
  geoToSceneXYZ,
  SCENE_EARTH_RADIUS,
  SCENE_KM_PER_UNIT,
  J2000_UNIX_SECONDS,
  EARTH_RADIUS_KM,
  EARTH_MU,
} from "../orbitalMath";

describe("orbitalMath", () => {
  describe("gmstRadians matches astronomy.ts contract", () => {
    it("produces [0, 2π) at J2000 epoch", () => {
      const g = gmstRadians(J2000_UNIX_SECONDS);
      expect(g).toBeGreaterThanOrEqual(0);
      expect(g).toBeLessThan(2 * Math.PI);
    });

    it("advances ~360° per sidereal day", () => {
      const siderealDayS = 86164.0905;
      const g0 = gmstRadians(J2000_UNIX_SECONDS);
      const g1 = gmstRadians(J2000_UNIX_SECONDS + siderealDayS);
      const diff = Math.abs(g1 - g0);
      expect(diff).toBeLessThan(0.001);
    });

    it("matches known J2000 value (~280.46°)", () => {
      const g = gmstRadians(J2000_UNIX_SECONDS);
      const gDeg = (g * 180) / Math.PI;
      expect(gDeg).toBeCloseTo(280.46, 1);
    });
  });

  describe("geoToSceneXYZ geometric correctness", () => {
    it("equator/prime meridian → positive X, near-zero Y and Z", () => {
      const [x, y, z] = geoToSceneXYZ(0, 0, 0);
      expect(x).toBeCloseTo(SCENE_EARTH_RADIUS, 3);
      expect(Math.abs(y)).toBeLessThan(0.001);
      expect(Math.abs(z)).toBeLessThan(0.001);
    });

    it("north pole → positive Y, near-zero X and Z", () => {
      const [x, y, z] = geoToSceneXYZ(90, 0, 0);
      expect(Math.abs(x)).toBeLessThan(0.001);
      expect(y).toBeCloseTo(SCENE_EARTH_RADIUS, 3);
      expect(Math.abs(z)).toBeLessThan(0.001);
    });

    it("south pole → negative Y", () => {
      const [x, y, z] = geoToSceneXYZ(-90, 0, 0);
      expect(Math.abs(x)).toBeLessThan(0.001);
      expect(y).toBeCloseTo(-SCENE_EARTH_RADIUS, 3);
      expect(Math.abs(z)).toBeLessThan(0.001);
    });

    it("altitude increases distance from origin", () => {
      const [x0, y0, z0] = geoToSceneXYZ(45, 30, 0);
      const [x1, y1, z1] = geoToSceneXYZ(45, 30, 550);
      const dist0 = Math.sqrt(x0 * x0 + y0 * y0 + z0 * z0);
      const dist1 = Math.sqrt(x1 * x1 + y1 * y1 + z1 * z1);
      expect(dist1).toBeGreaterThan(dist0);
      const expectedIncrease = 550 / SCENE_KM_PER_UNIT;
      expect(dist1 - dist0).toBeCloseTo(expectedIncrease, 2);
    });

    it("all points on the surface have the same distance from origin", () => {
      const points = [
        geoToSceneXYZ(0, 0, 0),
        geoToSceneXYZ(45, 90, 0),
        geoToSceneXYZ(-30, -120, 0),
        geoToSceneXYZ(89, 179, 0),
      ];
      for (const [x, y, z] of points) {
        const dist = Math.sqrt(x * x + y * y + z * z);
        expect(dist).toBeCloseTo(SCENE_EARTH_RADIUS, 3);
      }
    });
  });

  describe("propagateToSceneXYZ", () => {
    const iss = {
      altitude_km: 420,
      inclination_deg: 51.6,
      raan_deg: 0,
      true_anomaly_deg: 0,
    };

    it("produces a position at the correct orbital altitude", () => {
      const epoch = J2000_UNIX_SECONDS;
      const [x, y, z] = propagateToSceneXYZ(iss, epoch, epoch);
      const dist = Math.sqrt(x * x + y * y + z * z);
      const expectedDist = SCENE_EARTH_RADIUS + iss.altitude_km / SCENE_KM_PER_UNIT;
      // WGS84 ellipsoid altitude differs from spherical by up to ~21km
      // at high inclinations. Allow 0.5 scene units (~32km) tolerance.
      expect(Math.abs(dist - expectedDist)).toBeLessThan(0.5);
    });

    it("moves the satellite over time (not static)", () => {
      const epoch = J2000_UNIX_SECONDS;
      const [x0, y0, z0] = propagateToSceneXYZ(iss, epoch, epoch);
      const [x1, y1, z1] = propagateToSceneXYZ(iss, epoch, epoch + 600);
      const moved = Math.sqrt(
        (x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2,
      );
      expect(moved).toBeGreaterThan(1);
    });

    it("preserves orbital altitude over a full orbit", () => {
      const epoch = J2000_UNIX_SECONDS;
      const a = EARTH_RADIUS_KM + iss.altitude_km;
      const period = 2 * Math.PI * Math.sqrt((a ** 3) / EARTH_MU);
      const expectedDist = SCENE_EARTH_RADIUS + iss.altitude_km / SCENE_KM_PER_UNIT;

      for (let t = 0; t < period; t += period / 20) {
        const [x, y, z] = propagateToSceneXYZ(iss, epoch, epoch + t);
        const dist = Math.sqrt(x * x + y * y + z * z);
        expect(dist).toBeCloseTo(expectedDist, 0);
      }
    });

    it("returns to approximately the same position after one orbit", () => {
      const epoch = J2000_UNIX_SECONDS;
      const a = EARTH_RADIUS_KM + iss.altitude_km;
      const period = 2 * Math.PI * Math.sqrt((a ** 3) / EARTH_MU);

      const [x0, y0, z0] = propagateToSceneXYZ(iss, epoch, epoch);
      const [x1, y1, z1] = propagateToSceneXYZ(iss, epoch, epoch + period);

      // Won't be exact due to Earth rotation (ECEF coords), but the
      // orbital radius and inclination should match closely.
      const dist0 = Math.sqrt(x0 * x0 + y0 * y0 + z0 * z0);
      const dist1 = Math.sqrt(x1 * x1 + y1 * y1 + z1 * z1);
      expect(dist1).toBeCloseTo(dist0, 2);
    });

    it("matches existing ephemeris.ts propagateNode + geoToWorld pipeline", async () => {
      const { propagateNode } = await import("../ephemeris");
      const { geoToWorld } = await import("../../globe/geo");

      const epoch = J2000_UNIX_SECONDS + 86400 * 100;
      const simTime = epoch + 1200;
      const elements = {
        type: "keplerian" as const,
        altitude_km: 550,
        inclination_deg: 53,
        raan_deg: 45,
        true_anomaly_deg: 120,
        plane: 0,
        slot: 0,
      };

      const legacyPos = propagateNode(elements, epoch, simTime);
      const legacyWorld = geoToWorld(legacyPos.latDeg, legacyPos.lonDeg, legacyPos.altKm);

      const [nx, ny, nz] = propagateToSceneXYZ(
        {
          altitude_km: elements.altitude_km,
          inclination_deg: elements.inclination_deg,
          raan_deg: elements.raan_deg,
          true_anomaly_deg: elements.true_anomaly_deg,
        },
        epoch,
        simTime,
      );

      expect(nx).toBeCloseTo(legacyWorld.x, 2);
      expect(ny).toBeCloseTo(legacyWorld.y, 2);
      expect(nz).toBeCloseTo(legacyWorld.z, 2);
    });
  });
});
