// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Pure math for orbital propagation — ZERO external dependencies.
// Importable by Web Workers (no THREE, no DOM, no browser APIs).
//
// Both the main thread (ephemeris.ts, astronomy.ts) and the SGP4 Worker
// import from here. This module is the single source for the physics.

// --- Constants ---

export const J2000_UNIX_SECONDS = 946728000.0;

const DEG2RAD = Math.PI / 180.0;

// Scene constants (Three.js coordinate system). This is an arbitrary visual
// scale, not a physical body fact; body radii come from SessionEphemeris.
export const SCENE_EARTH_RADIUS = 100;

export interface BodyMath {
  bodyId: string;
  meanRadiusKm: number;
  equatorialRadiusKm: number;
  polarRadiusKm: number;
  gravitationalParameterKm3S2: number;
  j2: number;
  rotationRateRadS: number;
  kmPerRenderUnit: number;
}

export function ellipsoidE2(body: BodyMath): number {
  const a = body.equatorialRadiusKm;
  const b = body.polarRadiusKm;
  return 1.0 - (b * b) / (a * a);
}

// --- GMST ---

export function gmstRadians(unixSeconds: number): number {
  const daysSinceJ2000 = (unixSeconds - J2000_UNIX_SECONDS) / 86400.0;
  const degrees = 280.46061837 + 360.98564736629 * daysSinceJ2000;
  let wrapped = degrees % 360.0;
  if (wrapped < 0) wrapped += 360.0;
  return wrapped * DEG2RAD;
}

// --- Keplerian mean-element propagation ---

export function solveEccentricAnomaly(
  meanAnomalyRad: number,
  eccentricity: number,
): number {
  if (eccentricity === 0) return meanAnomalyRad;
  const twoPi = Math.PI * 2;
  let mean = meanAnomalyRad % twoPi;
  if (mean < 0) mean += twoPi;
  let eccentricAnomaly = eccentricity < 0.8 ? mean : Math.PI;
  for (let i = 0; i < 12; i++) {
    const f = eccentricAnomaly - eccentricity * Math.sin(eccentricAnomaly) - mean;
    const fp = 1.0 - eccentricity * Math.cos(eccentricAnomaly);
    const step = f / fp;
    eccentricAnomaly -= step;
    if (Math.abs(step) < 1e-14) break;
  }
  return eccentricAnomaly;
}

function perifocalPosition(
  semiMajorAxisKm: number,
  eccentricity: number,
  meanAnomalyRad: number,
): [number, number] {
  const eccentricAnomaly = solveEccentricAnomaly(meanAnomalyRad, eccentricity);
  const sqrtOneMinusE2 = Math.sqrt(1.0 - eccentricity * eccentricity);
  return [
    semiMajorAxisKm * (Math.cos(eccentricAnomaly) - eccentricity),
    semiMajorAxisKm * sqrtOneMinusE2 * Math.sin(eccentricAnomaly),
  ];
}

function rotatePerifocal(
  xPf: number,
  yPf: number,
  raanRad: number,
  inclinationRad: number,
  argumentOfPerigeeRad: number,
): [number, number, number] {
  const cosRaan = Math.cos(raanRad);
  const sinRaan = Math.sin(raanRad);
  const cosI = Math.cos(inclinationRad);
  const sinI = Math.sin(inclinationRad);
  const cosArgp = Math.cos(argumentOfPerigeeRad);
  const sinArgp = Math.sin(argumentOfPerigeeRad);
  const r11 = cosRaan * cosArgp - sinRaan * sinArgp * cosI;
  const r12 = -cosRaan * sinArgp - sinRaan * cosArgp * cosI;
  const r21 = sinRaan * cosArgp + cosRaan * sinArgp * cosI;
  const r22 = -sinRaan * sinArgp + cosRaan * cosArgp * cosI;
  const r31 = sinArgp * sinI;
  const r32 = cosArgp * sinI;
  return [
    r11 * xPf + r12 * yPf,
    r21 * xPf + r22 * yPf,
    r31 * xPf + r32 * yPf,
  ];
}

function propagateEci(
  semiMajorAxisKm: number,
  eccentricity: number,
  inclinationRad: number,
  raanRad: number,
  argumentOfPerigeeRad: number,
  meanAnomalyRad: number,
  dt: number,
  muKm3S2: number,
): [number, number, number] {
  const a = semiMajorAxisKm;
  const n = Math.sqrt(muKm3S2 / (a * a * a));
  const [xPf, yPf] = perifocalPosition(a, eccentricity, meanAnomalyRad + n * dt);
  return rotatePerifocal(xPf, yPf, raanRad, inclinationRad, argumentOfPerigeeRad);
}

function propagateEciJ2MeanElements(
  semiMajorAxisKm: number,
  eccentricity: number,
  inclinationRad: number,
  raanRad: number,
  argumentOfPerigeeRad: number,
  meanAnomalyRad: number,
  dt: number,
  muKm3S2: number,
  j2: number,
  referenceRadiusKm: number,
): [number, number, number] {
  const a = semiMajorAxisKm;
  const n = Math.sqrt(muKm3S2 / (a * a * a));
  const cosI = Math.cos(inclinationRad);
  const p = a * (1.0 - eccentricity * eccentricity);
  const j2Factor = j2 * (referenceRadiusKm / p) ** 2;
  const raanDot = -1.5 * j2Factor * n * cosI;
  const argumentOfPerigeeDot =
    eccentricity === 0 ? 0.0 : 0.75 * j2Factor * n * (5.0 * cosI * cosI - 1.0);
  const meanAnomalyDot =
    n * (1.0 + 0.75 * j2Factor * Math.sqrt(1.0 - eccentricity * eccentricity) *
      (3.0 * cosI * cosI - 1.0));

  const raan = raanRad + raanDot * dt;
  const argumentOfPerigee = argumentOfPerigeeRad + argumentOfPerigeeDot * dt;
  const [xPf, yPf] = perifocalPosition(
    a,
    eccentricity,
    meanAnomalyRad + meanAnomalyDot * dt,
  );
  return rotatePerifocal(xPf, yPf, raan, inclinationRad, argumentOfPerigee);
}

function eciToEcef(
  x: number, y: number, z: number,
  unixTimestamp: number,
  body: BodyMath,
): [number, number, number] {
  const theta =
    body.bodyId === "earth"
      ? gmstRadians(unixTimestamp)
      : (unixTimestamp - J2000_UNIX_SECONDS) * body.rotationRateRadS;
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
  body: BodyMath,
): [number, number, number] {
  const lonRad = Math.atan2(y, x);
  const p = Math.sqrt(x * x + y * y);
  const a = body.equatorialRadiusKm;
  const e2 = ellipsoidE2(body);

  let latRad = Math.atan2(z, p * (1.0 - e2));

  for (let i = 0; i < 10; i++) {
    const sinLat = Math.sin(latRad);
    const N = a / Math.sqrt(1.0 - e2 * sinLat * sinLat);
    latRad = Math.atan2(z + e2 * N * sinLat, p);
  }

  const sinLat = Math.sin(latRad);
  const cosLat = Math.cos(latRad);
  const N = a / Math.sqrt(1.0 - e2 * sinLat * sinLat);

  const altKm = Math.abs(cosLat) > 1e-10
    ? p / cosLat - N
    : Math.abs(z) - N * (1.0 - e2);

  return [latRad / DEG2RAD, lonRad / DEG2RAD, altKm];
}

// --- Scene coordinate conversion ---

export function geoToSceneXYZ(
  latDeg: number, lonDeg: number, altKm: number, radiusKm: number, kmPerRenderUnit: number,
): [number, number, number] {
  const lat = latDeg * DEG2RAD;
  const lon = lonDeg * DEG2RAD;
  const r = radiusKm / kmPerRenderUnit + altKm / kmPerRenderUnit;

  return [
    r * Math.cos(lat) * Math.cos(lon),
    r * Math.sin(lat),
    -r * Math.cos(lat) * Math.sin(lon),
  ];
}

export function bodyFixedToSceneXYZ(
  xKm: number,
  yKm: number,
  zKm: number,
  kmPerRenderUnit: number,
): [number, number, number] {
  return [xKm / kmPerRenderUnit, zKm / kmPerRenderUnit, -yKm / kmPerRenderUnit];
}

// --- Combined propagation to scene coords ---

export interface KeplerianElements {
  propagator?: "two-body" | "keplerian-circular" | "j2-mean-elements";
  semi_major_axis_km: number;
  eccentricity: number;
  inclination_deg: number;
  raan_deg: number;
  argument_of_perigee_deg: number;
  mean_anomaly_deg: number;
  body: BodyMath;
}

export function propagateToSceneXYZ(
  elements: KeplerianElements,
  epochUnix: number,
  simTimeUnix: number,
): [number, number, number] {
  const body = elements.body;
  const referenceRadiusKm = body.equatorialRadiusKm;
  const dt = simTimeUnix - epochUnix;
  const propagator = elements.propagator ?? "two-body";
  if (
    propagator !== "two-body" &&
    propagator !== "keplerian-circular" &&
    propagator !== "j2-mean-elements"
  ) {
    throw new Error(`Unsupported render propagator: ${String(propagator)}`);
  }

  const inclinationRad = elements.inclination_deg * DEG2RAD;
  const raanRad = elements.raan_deg * DEG2RAD;
  const argumentOfPerigeeRad = elements.argument_of_perigee_deg * DEG2RAD;
  const meanAnomalyRad = elements.mean_anomaly_deg * DEG2RAD;
  const posEci =
    propagator === "j2-mean-elements"
      ? propagateEciJ2MeanElements(
          elements.semi_major_axis_km,
          elements.eccentricity,
          inclinationRad,
          raanRad,
          argumentOfPerigeeRad,
          meanAnomalyRad,
          dt,
          body.gravitationalParameterKm3S2,
          body.j2,
          referenceRadiusKm,
        )
      : propagateEci(
          elements.semi_major_axis_km,
          elements.eccentricity,
          inclinationRad,
          raanRad,
          argumentOfPerigeeRad,
          meanAnomalyRad,
          dt,
          body.gravitationalParameterKm3S2,
        );

  const currentTime = epochUnix + dt;
  const posEcef = eciToEcef(
    posEci[0],
    posEci[1],
    posEci[2],
    currentTime,
    body,
  );
  if (body.bodyId !== "earth") {
    return bodyFixedToSceneXYZ(
      posEcef[0],
      posEcef[1],
      posEcef[2],
      body.kmPerRenderUnit,
    );
  }

  const [latDeg, lonDeg, altKm] = ecefToGeodetic(posEcef[0], posEcef[1], posEcef[2], body);
  return geoToSceneXYZ(
    latDeg,
    lonDeg,
    altKm,
    body.equatorialRadiusKm,
    body.kmPerRenderUnit,
  );
}

/**
 * Sample one full osculating orbit as a closed scene-local path.
 *
 * This is the truthful path overlay for eccentric orbits (HEO, lunar ELFO),
 * where a great-circle ring would lie about the trajectory, and for any
 * non-Earth body, where the world-frame circle trick assumes an Earth-centred
 * scene. The path is sampled uniformly in ECCENTRIC anomaly (dense around
 * perigee where the trajectory curves fastest), with J2 secular drift applied
 * to RAAN/argument-of-perigee at the seed instant and the body rotation
 * frozen at `simTimeUnix` — the same snapshot semantics as the circular
 * rings, which are also seeded once and inertially static.
 *
 * Coordinates are scene-LOCAL to the node's body frame (identical pipeline to
 * propagateToSceneXYZ); callers transform them into world space through the
 * registered body group.
 */
export function sampleOrbitPathSceneXYZ(
  elements: KeplerianElements,
  epochUnix: number,
  simTimeUnix: number,
  samples = 180,
): Float32Array {
  const body = elements.body;
  const dt = simTimeUnix - epochUnix;
  const a = elements.semi_major_axis_km;
  const e = elements.eccentricity;
  const inclinationRad = elements.inclination_deg * DEG2RAD;
  let raanRad = elements.raan_deg * DEG2RAD;
  let argumentOfPerigeeRad = elements.argument_of_perigee_deg * DEG2RAD;

  if ((elements.propagator ?? "two-body") === "j2-mean-elements") {
    // Same secular drift the live position propagation applies.
    const n = Math.sqrt(body.gravitationalParameterKm3S2 / (a * a * a));
    const cosI = Math.cos(inclinationRad);
    const p = a * (1.0 - e * e);
    const j2Factor = body.j2 * (body.equatorialRadiusKm / p) ** 2;
    raanRad += -1.5 * j2Factor * n * cosI * dt;
    argumentOfPerigeeRad +=
      e === 0 ? 0.0 : 0.75 * j2Factor * n * (5.0 * cosI * cosI - 1.0) * dt;
  }

  const sqrtOneMinusE2 = Math.sqrt(1.0 - e * e);
  const positions = new Float32Array((samples + 1) * 3);
  for (let i = 0; i <= samples; i++) {
    const eccentricAnomaly = (i * 2 * Math.PI) / samples;
    const xPf = a * (Math.cos(eccentricAnomaly) - e);
    const yPf = a * sqrtOneMinusE2 * Math.sin(eccentricAnomaly);
    const eci = rotatePerifocal(xPf, yPf, raanRad, inclinationRad, argumentOfPerigeeRad);
    const ecef = eciToEcef(eci[0], eci[1], eci[2], simTimeUnix, body);
    let scene: [number, number, number];
    if (body.bodyId !== "earth") {
      scene = bodyFixedToSceneXYZ(ecef[0], ecef[1], ecef[2], body.kmPerRenderUnit);
    } else {
      const [latDeg, lonDeg, altKm] = ecefToGeodetic(ecef[0], ecef[1], ecef[2], body);
      scene = geoToSceneXYZ(latDeg, lonDeg, altKm, body.equatorialRadiusKm, body.kmPerRenderUnit);
    }
    positions[i * 3] = scene[0];
    positions[i * 3 + 1] = scene[1];
    positions[i * 3 + 2] = scene[2];
  }
  return positions;
}
