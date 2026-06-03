// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Pure math for orbital propagation — ZERO external dependencies.
// Importable by Web Workers (no THREE, no DOM, no browser APIs).
//
// Both the main thread (ephemeris.ts, astronomy.ts) and the SGP4 Worker
// import from here. This module is the single source for the physics.

// --- Constants ---

export const J2000_UNIX_SECONDS = 946728000.0;
export const EARTH_RADIUS_KM = 6371.0;
export const EARTH_MU = 398600.4418;
export const LUNA_RADIUS_KM = 1737.4;
export const LUNA_MU = 4902.800066;
export const MARS_RADIUS_KM = 3389.5;
export const MARS_MU = 42828.375214;
export const WGS84_A = 6378.137;
export const WGS84_E2 = 0.00669437999014;
export const EARTH_ROTATION_RATE = 7.2921159e-5;
export const LUNA_ROTATION_RATE = 2.6616995e-6;
export const MARS_ROTATION_RATE = 7.0882181e-5;

const DEG2RAD = Math.PI / 180.0;
const RAD2DEG = 180.0 / Math.PI;

// Scene constants (Three.js coordinate system)
export const SCENE_EARTH_RADIUS = 100;
export const SCENE_KM_PER_UNIT = EARTH_RADIUS_KM / SCENE_EARTH_RADIUS;

export interface BodyMath {
  radiusKm: number;
  muKm3S2: number;
  rotationRateRadS: number;
}

const BODY_MATH: Record<string, BodyMath> = {
  earth: {
    radiusKm: EARTH_RADIUS_KM,
    muKm3S2: EARTH_MU,
    rotationRateRadS: EARTH_ROTATION_RATE,
  },
  luna: {
    radiusKm: LUNA_RADIUS_KM,
    muKm3S2: LUNA_MU,
    rotationRateRadS: LUNA_ROTATION_RATE,
  },
  mars: {
    radiusKm: MARS_RADIUS_KM,
    muKm3S2: MARS_MU,
    rotationRateRadS: MARS_ROTATION_RATE,
  },
};

export function bodyMath(bodyId?: string | null): BodyMath {
  const key = bodyId ?? "earth";
  const body = BODY_MATH[key];
  if (!body) throw new Error(`Unsupported render reference_body: ${key}`);
  return body;
}

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
  muKm3S2 = EARTH_MU,
): [number, number, number] {
  const a = semiMajorAxisKm;
  const n = Math.sqrt(muKm3S2 / (a * a * a));
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
  rotationRateRadS = EARTH_ROTATION_RATE,
): [number, number, number] {
  const theta =
    rotationRateRadS === EARTH_ROTATION_RATE
      ? gmstRadians(unixTimestamp)
      : (unixTimestamp - J2000_UNIX_SECONDS) * rotationRateRadS;
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
  latDeg: number, lonDeg: number, altKm: number, radiusKm = EARTH_RADIUS_KM,
): [number, number, number] {
  const lat = latDeg * DEG2RAD;
  const lon = lonDeg * DEG2RAD;
  const r = radiusKm / SCENE_KM_PER_UNIT + altKm / SCENE_KM_PER_UNIT;

  return [
    r * Math.cos(lat) * Math.cos(lon),
    r * Math.sin(lat),
    -r * Math.cos(lat) * Math.sin(lon),
  ];
}

export function bodyFixedToSceneXYZ(xKm: number, yKm: number, zKm: number): [number, number, number] {
  return [xKm / SCENE_KM_PER_UNIT, zKm / SCENE_KM_PER_UNIT, -yKm / SCENE_KM_PER_UNIT];
}

// --- Combined propagation to scene coords ---

export interface KeplerianElements {
  altitude_km: number;
  inclination_deg: number;
  raan_deg: number;
  true_anomaly_deg: number;
  reference_body?: string | null;
}

export function propagateToSceneXYZ(
  elements: KeplerianElements,
  epochUnix: number,
  simTimeUnix: number,
): [number, number, number] {
  const body = bodyMath(elements.reference_body);
  const a = body.radiusKm + elements.altitude_km;
  const dt = simTimeUnix - epochUnix;

  const posEci = propagateEci(
    a,
    elements.inclination_deg * DEG2RAD,
    elements.raan_deg * DEG2RAD,
    elements.true_anomaly_deg * DEG2RAD,
    dt,
    body.muKm3S2,
  );

  const currentTime = epochUnix + dt;
  const posEcef = eciToEcef(
    posEci[0],
    posEci[1],
    posEci[2],
    currentTime,
    body.rotationRateRadS,
  );
  if ((elements.reference_body ?? "earth") !== "earth") {
    return bodyFixedToSceneXYZ(posEcef[0], posEcef[1], posEcef[2]);
  }

  const [latDeg, lonDeg, altKm] = ecefToGeodetic(posEcef[0], posEcef[1], posEcef[2]);
  return geoToSceneXYZ(latDeg, lonDeg, altKm, body.radiusKm);
}
