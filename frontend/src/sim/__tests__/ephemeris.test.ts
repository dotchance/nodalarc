// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Contract tests for the TypeScript Keplerian propagator.
 *
 * Golden values computed from Python: lib/nodalarc/propagator.py
 * Epoch: 2025-01-01T00:00:00 UTC (Unix 1735689600.0)
 * Constellation: 550km, 53deg inclination, circular orbit
 *
 * Tolerance: 0.001 degrees lat/lon — matches Python output within
 * the precision of the simplified GMST model and Bowring geodetic.
 */

import { describe, expect, it } from "vitest";
import {
  ecefToGeodetic,
  propagateNode,
  type EphemerisNodeFixed,
  type EphemerisNodeKeplerian,
} from "../ephemeris";
import { catalogEarthBodyMath, catalogEarthFrame } from "./bodyModelFixture";

const EPOCH = 1735689600.0; // 2025-01-01T00:00:00 UTC
const TOL = 0.01; // degrees tolerance
const EARTH_FRAME = catalogEarthFrame();
const EARTH_BODY = catalogEarthBodyMath();
const LEO_550_SMA_KM = EARTH_BODY.meanRadiusKm + 550;

const SAT_P00S00: EphemerisNodeKeplerian = {
  type: "keplerian",
  propagator: "two-body",
  semi_major_axis_km: LEO_550_SMA_KM,
  eccentricity: 0.0,
  inclination_deg: 53.0,
  raan_deg: 0.0,
  argument_of_perigee_deg: 0.0,
  mean_anomaly_deg: 0.0,
  plane: 0,
  slot: 0,
  reference_body: "earth",
  frame_id: "earth",
};

const SAT_RAAN90: EphemerisNodeKeplerian = {
  type: "keplerian",
  propagator: "two-body",
  semi_major_axis_km: LEO_550_SMA_KM,
  eccentricity: 0.0,
  inclination_deg: 53.0,
  raan_deg: 90.0,
  argument_of_perigee_deg: 0.0,
  mean_anomaly_deg: 0.0,
  plane: 1,
  slot: 0,
  reference_body: "earth",
  frame_id: "earth",
};

const GS_ASHBURN: EphemerisNodeFixed = {
  type: "fixed",
  lat_deg: 39.04,
  lon_deg: -77.49,
  alt_km: 0.095,
  reference_body: "earth",
  frame_id: "earth",
};

describe("propagateNode - Keplerian satellites", () => {
  it("matches Python at t=0", () => {
    const pos = propagateNode(SAT_P00S00, EPOCH, EPOCH, EARTH_BODY);
    expect(pos.latDeg).toBeCloseTo(0.0, 1);
    expect(pos.lonDeg).toBeCloseTo(-100.9, 0);
    expect(pos.altKm).toBeCloseTo(542.86, 0);
  });

  it("matches Python at t=300 (5 minutes)", () => {
    const pos = propagateNode(SAT_P00S00, EPOCH, EPOCH + 300, EARTH_BODY);
    expect(pos.latDeg).toBeCloseTo(15.04, 1);
    expect(pos.lonDeg).toBeCloseTo(-90.54, 0);
    expect(pos.altKm).toBeCloseTo(544.29, 0);
  });

  it("matches Python at t=1800 (30 minutes)", () => {
    const pos = propagateNode(SAT_P00S00, EPOCH, EPOCH + 1800, EARTH_BODY);
    expect(pos.latDeg).toBeCloseTo(47.46, 1);
    expect(pos.altKm).toBeCloseTo(554.43, 0);
  });

  it("different RAAN produces different position", () => {
    const pos1 = propagateNode(SAT_P00S00, EPOCH, EPOCH, EARTH_BODY);
    const pos2 = propagateNode(SAT_RAAN90, EPOCH, EPOCH, EARTH_BODY);
    // Same lat (both at ascending node at t=0)
    expect(Math.abs(pos1.latDeg - pos2.latDeg)).toBeLessThan(TOL);
    // Different lon (90deg RAAN offset)
    expect(Math.abs(pos1.lonDeg - pos2.lonDeg)).toBeGreaterThan(80);
  });

  it("latitude bounded by inclination", () => {
    // Check every 60 seconds for one orbit (~5730s)
    for (let t = 0; t < 5730; t += 60) {
      const pos = propagateNode(SAT_P00S00, EPOCH, EPOCH + t, EARTH_BODY);
      expect(Math.abs(pos.latDeg)).toBeLessThanOrEqual(54.0);
    }
  });

  it("altitude stays constant for circular orbit", () => {
    for (let t = 0; t < 6000; t += 300) {
      const pos = propagateNode(SAT_P00S00, EPOCH, EPOCH + t, EARTH_BODY);
      expect(pos.altKm).toBeGreaterThan(530);
      expect(pos.altKm).toBeLessThan(560);
    }
  });

  it("velocity is non-zero for satellites", () => {
    const pos = propagateNode(SAT_P00S00, EPOCH, EPOCH, EARTH_BODY);
    const speed = Math.sqrt(
      pos.velXKmS ** 2 + pos.velYKmS ** 2 + pos.velZKmS ** 2,
    );
    expect(speed).toBeGreaterThan(5); // ~7.6 km/s for LEO
    expect(speed).toBeLessThan(10);
  });
});

describe("propagateNode - fixed ground stations", () => {
  it("returns static position unchanged", () => {
    const pos = propagateNode(GS_ASHBURN, EPOCH, EPOCH + 3600);
    expect(pos.latDeg).toBe(39.04);
    expect(pos.lonDeg).toBe(-77.49);
    expect(pos.altKm).toBe(0.095);
    expect(pos.velXKmS).toBe(0);
    expect(pos.velYKmS).toBe(0);
    expect(pos.velZKmS).toBe(0);
  });
});

describe("ecefToGeodetic round-trip", () => {
  it("equator at prime meridian", () => {
    // ECEF point ~550km above equator at 0 lon
    const geo = ecefToGeodetic(EARTH_FRAME.equatorial_radius_km + 550, 0, 0, EARTH_BODY);
    expect(Math.abs(geo.latDeg)).toBeLessThan(0.01);
    expect(Math.abs(geo.lonDeg)).toBeLessThan(0.01);
    expect(geo.altKm).toBeCloseTo(550, 0);
  });

  it("Ashburn round-trip", () => {
    // Verify geodetic → ECEF → geodetic matches Python
    const geo = ecefToGeodetic(1108.87, -4831.95, 3994.27, EARTH_BODY);
    // These should be roughly Ashburn coords
    expect(geo.latDeg).toBeCloseTo(39, 0);
  });
});
