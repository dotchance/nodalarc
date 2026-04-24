// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Pure math for orbital propagation — ZERO external dependencies.
// Importable by Web Workers (no THREE, no DOM, no browser APIs).
//
// Both the main thread (ephemeris.ts, astronomy.ts) and the SGP4 Worker
// import from here. This module is the single source for the physics.

// --- Constants ---

export const J2000_UNIX_SECONDS = 946728000.0;
export const EARTH_RADIUS_KM = 6371.0;
export const EARTH_MU = 398600.4418;
export const WGS84_A = 6378.137;
export const WGS84_E2 = 0.00669437999014;
export const EARTH_ROTATION_RATE = 7.2921159e-5;

const DEG2RAD = Math.PI / 180.0;
const RAD2DEG = 180.0 / Math.PI;

// Scene constants (Three.js coordinate system)
export const SCENE_EARTH_RADIUS = 100;
export const SCENE_KM_PER_UNIT = EARTH_RADIUS_KM / SCENE_EARTH_RADIUS;

// --- GMST ---

export function gmstRadians(unixSeconds: number): number {
  const daysSinceJ2000 = (unixSeconds - J2000_UNIX_SECONDS) / 86400.0;
  const degrees = 280.46061837 + 360.98564736629 * daysSinceJ2000;
  let wrapped = degrees % 360.0;
  if (wrapped < 0) wrapped += 360.0;
  return wrapped * DEG2RAD;
}

// --- Keplerian propagation (circular orbit, e=0) ---

function propagateEci(
  semiMajorAxisKm: number,
  inclinationRad: number,
  raanRad: number,
  trueAnomalyRad: number,
  dt: number,
): [number, number, number] {
  const a = semiMajorAxisKm;
  const n = Math.sqrt(EARTH_MU / (a * a * a));
  const nu = trueAnomalyRad + n * dt;

  const r = a;
  const xPf = r * Math.cos(nu);
  const yPf = r * Math.sin(nu);

  const cosRaan = Math.cos(raanRad);
  const sinRaan = Math.sin(raanRad);
  const cosI = Math.cos(inclinationRad);
  const sinI = Math.sin(inclinationRad);

  return [
    cosRaan * xPf - sinRaan * cosI * yPf,
    sinRaan * xPf + cosRaan * cosI * yPf,
    sinI * yPf,
  ];
}

function eciToEcef(
  x: number, y: number, z: number,
  unixTimestamp: number,
): [number, number, number] {
  const theta = gmstRadians(unixTimestamp);
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);
  return [
    cosT * x + sinT * y,
    -sinT * x + cosT * y,
    z,
  ];
}

function ecefToGeodetic(
  x: number, y: number, z: number,
): [number, number, number] {
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

  const altKm = Math.abs(cosLat) > 1e-10
    ? p / cosLat - N
    : Math.abs(z) - N * (1.0 - WGS84_E2);

  return [latRad * RAD2DEG, lonRad * RAD2DEG, altKm];
}

// --- Scene coordinate conversion ---

export function geoToSceneXYZ(
  latDeg: number, lonDeg: number, altKm: number,
): [number, number, number] {
  const lat = latDeg * DEG2RAD;
  const lon = lonDeg * DEG2RAD;
  const r = SCENE_EARTH_RADIUS + altKm / SCENE_KM_PER_UNIT;

  return [
    r * Math.cos(lat) * Math.cos(lon),
    r * Math.sin(lat),
    -r * Math.cos(lat) * Math.sin(lon),
  ];
}

// --- Combined propagation to scene coords ---

export interface KeplerianElements {
  altitude_km: number;
  inclination_deg: number;
  raan_deg: number;
  true_anomaly_deg: number;
}

export function propagateToSceneXYZ(
  elements: KeplerianElements,
  epochUnix: number,
  simTimeUnix: number,
): [number, number, number] {
  const a = EARTH_RADIUS_KM + elements.altitude_km;
  const dt = simTimeUnix - epochUnix;

  const posEci = propagateEci(
    a,
    elements.inclination_deg * DEG2RAD,
    elements.raan_deg * DEG2RAD,
    elements.true_anomaly_deg * DEG2RAD,
    dt,
  );

  const currentTime = epochUnix + dt;
  const posEcef = eciToEcef(posEci[0], posEci[1], posEci[2], currentTime);
  const [latDeg, lonDeg, altKm] = ecefToGeodetic(posEcef[0], posEcef[1], posEcef[2]);

  return geoToSceneXYZ(latDeg, lonDeg, altKm);
}
