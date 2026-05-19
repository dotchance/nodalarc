// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Local Keplerian orbital propagator for the Visualization Frontend.
 *
 * Direct port of lib/nodalarc/propagator.py. Computes satellite positions
 * from orbital elements at any sim_time. Ground stations are static.
 *
 * This module enables 60fps satellite rendering without per-tick position
 * data from the WebSocket. The OME distributes orbital elements once per
 * epoch via SessionEphemeris; the VF propagates locally every frame.
 *
 * Contract: propagateNode() MUST produce results matching the Python
 * propagator within 0.001 degrees lat/lon for a 95-minute propagation
 * horizon. See __tests__/ephemeris.test.ts.
 */

import { gmstRadians } from "../globe/astronomy";

// ---------------------------------------------------------------------------
// Constants (must match lib/nodalarc/constants.py)
// ---------------------------------------------------------------------------

const EARTH_RADIUS_KM = 6371.0;
const EARTH_MU = 398600.4418; // km^3/s^2
const WGS84_A = 6378.137; // Semi-major axis, km
const WGS84_E2 = 0.00669437999014; // First eccentricity squared
const EARTH_ROTATION_RATE = 7.2921159e-5; // rad/s

// ---------------------------------------------------------------------------
// Types (match lib/nodalarc/models/events.py SessionEphemeris)
// ---------------------------------------------------------------------------

export interface EphemerisNodeKeplerian {
  type: "keplerian";
  altitude_km: number;
  inclination_deg: number;
  raan_deg: number;
  true_anomaly_deg: number;
  plane: number;
  slot: number;
}

export interface EphemerisNodeFixed {
  type: "fixed";
  lat_deg: number;
  lon_deg: number;
  alt_km: number;
}

export type EphemerisNode = EphemerisNodeKeplerian | EphemerisNodeFixed;

export interface SessionEphemeris {
  epoch_id: number;
  sim_time: string; // ISO 8601
  epoch_unix: number;
  nodes: Record<string, EphemerisNode>;
}

export interface PlaybackStateMsg {
  epoch_id: number;
  state: "seeking" | "playing" | "paused";
}

export interface PropagatedPosition {
  latDeg: number;
  lonDeg: number;
  altKm: number;
  velXKmS: number;
  velYKmS: number;
  velZKmS: number;
}

// ---------------------------------------------------------------------------
// Keplerian propagation (circular orbit, e=0)
// ---------------------------------------------------------------------------

function deg2rad(deg: number): number {
  return (deg * Math.PI) / 180.0;
}

function rad2deg(rad: number): number {
  return (rad * 180.0) / Math.PI;
}

/**
 * Propagate circular orbit by dt seconds in ECI frame.
 * Returns [posEci, velEci] as [x,y,z] arrays in km and km/s.
 */
function propagateEci(
  semiMajorAxisKm: number,
  inclinationRad: number,
  raanRad: number,
  trueAnomalyRad: number,
  dt: number,
): [[number, number, number], [number, number, number]] {
  const a = semiMajorAxisKm;
  const n = Math.sqrt(EARTH_MU / (a * a * a)); // mean motion
  const nu = trueAnomalyRad + n * dt;

  // Perifocal frame
  const r = a;
  const xPf = r * Math.cos(nu);
  const yPf = r * Math.sin(nu);

  const v = Math.sqrt(EARTH_MU / a);
  const vxPf = -v * Math.sin(nu);
  const vyPf = v * Math.cos(nu);

  // Rotation to ECI
  const cosRaan = Math.cos(raanRad);
  const sinRaan = Math.sin(raanRad);
  const cosI = Math.cos(inclinationRad);
  const sinI = Math.sin(inclinationRad);

  const xEci = cosRaan * xPf - sinRaan * cosI * yPf;
  const yEci = sinRaan * xPf + cosRaan * cosI * yPf;
  const zEci = sinI * yPf;

  const vxEci = cosRaan * vxPf - sinRaan * cosI * vyPf;
  const vyEci = sinRaan * vxPf + cosRaan * cosI * vyPf;
  const vzEci = sinI * vyPf;

  return [
    [xEci, yEci, zEci],
    [vxEci, vyEci, vzEci],
  ];
}

/**
 * Convert ECI position to ECEF via GMST rotation.
 */
function eciToEcef(
  posEci: [number, number, number],
  unixTimestamp: number,
): [number, number, number] {
  const theta = gmstRadians(unixTimestamp);
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);
  return [
    cosT * posEci[0] + sinT * posEci[1],
    -sinT * posEci[0] + cosT * posEci[1],
    posEci[2],
  ];
}

/**
 * Convert ECI velocity to ECEF velocity (includes Earth rotation subtraction).
 */
function eciToEcefVelocity(
  posEci: [number, number, number],
  velEci: [number, number, number],
  unixTimestamp: number,
): [number, number, number] {
  const theta = gmstRadians(unixTimestamp);
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);

  let vx = cosT * velEci[0] + sinT * velEci[1];
  let vy = -sinT * velEci[0] + cosT * velEci[1];
  const vz = velEci[2];

  // Subtract Earth rotation: omega x r_ecef
  const posEcef = eciToEcef(posEci, unixTimestamp);
  vx -= -EARTH_ROTATION_RATE * posEcef[1];
  vy -= EARTH_ROTATION_RATE * posEcef[0];

  return [vx, vy, vz];
}

/**
 * Convert ECEF (km) to geodetic (lat_deg, lon_deg, alt_km).
 * Iterative Bowring method on WGS84 ellipsoid.
 */
export function ecefToGeodetic(
  x: number,
  y: number,
  z: number,
): { latDeg: number; lonDeg: number; altKm: number } {
  const lonRad = Math.atan2(y, x);
  const p = Math.sqrt(x * x + y * y);

  let latRad = Math.atan2(z, p * (1.0 - WGS84_E2));

  for (let i = 0; i < 10; i++) {
    const sinLat = Math.sin(latRad);
    const N = WGS84_A / Math.sqrt(1.0 - WGS84_E2 * sinLat * sinLat);
    latRad = Math.atan2(z + WGS84_E2 * N * sinLat, p);
  }

  const sinLat = Math.sin(latRad);
  const cosLat = Math.cos(latRad);
  const N = WGS84_A / Math.sqrt(1.0 - WGS84_E2 * sinLat * sinLat);

  const altKm =
    Math.abs(cosLat) > 1e-10
      ? p / cosLat - N
      : Math.abs(z) - N * (1.0 - WGS84_E2);

  return {
    latDeg: rad2deg(latRad),
    lonDeg: rad2deg(lonRad),
    altKm,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Propagate a single node to the given sim_time.
 *
 * For keplerian nodes: runs full ECI → ECEF → geodetic pipeline.
 * For fixed nodes: returns static position.
 */
export function propagateNode(
  node: EphemerisNode,
  epochUnix: number,
  simTimeUnix: number,
): PropagatedPosition {
  if (node.type === "fixed") {
    return {
      latDeg: node.lat_deg,
      lonDeg: node.lon_deg,
      altKm: node.alt_km,
      velXKmS: 0,
      velYKmS: 0,
      velZKmS: 0,
    };
  }

  const a = EARTH_RADIUS_KM + node.altitude_km;
  const iRad = deg2rad(node.inclination_deg);
  const raanRad = deg2rad(node.raan_deg);
  const nuRad = deg2rad(node.true_anomaly_deg);
  const dt = simTimeUnix - epochUnix;

  const [posEci, velEci] = propagateEci(a, iRad, raanRad, nuRad, dt);
  const currentTime = epochUnix + dt;
  const posEcef = eciToEcef(posEci, currentTime);
  const velEcef = eciToEcefVelocity(posEci, velEci, currentTime);
  const geo = ecefToGeodetic(posEcef[0], posEcef[1], posEcef[2]);

  return {
    latDeg: geo.latDeg,
    lonDeg: geo.lonDeg,
    altKm: geo.altKm,
    velXKmS: velEcef[0],
    velYKmS: velEcef[1],
    velZKmS: velEcef[2],
  };
}
