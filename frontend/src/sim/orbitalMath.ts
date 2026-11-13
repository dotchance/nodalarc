// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
// Pure math for orbital propagation — ZERO external dependencies.
// Importable by Web Workers (no THREE, no DOM, no browser APIs).
//
// Both the main thread (ephemeris.ts, astronomy.ts) and the SGP4 Worker
// import from here. This module is the single source for the physics.

import { matTVec, matVec, type Matrix3, type Vector3 } from "./bodySamples";

// --- Constants ---

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
  /** Rows are the body's orbit-element frame axes on ICRF axes (SessionEphemeris). */
  orbitBasis: Matrix3;
  kmPerRenderUnit: number;
}

export function ellipsoidE2(body: BodyMath): number {
  const a = body.equatorialRadiusKm;
  const b = body.polarRadiusKm;
  return 1.0 - (b * b) / (a * a);
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

/** The secular rates of a mean-element orbit, in rad/s. */
export interface SecularRates {
  raanDot: number;
  argumentOfPerigeeDot: number;
  meanAnomalyDot: number;
}

/** Two-body motion: no node or perigee motion, mean motion from GM alone. */
function twoBodyRates(semiMajorAxisKm: number, body: BodyMath): SecularRates {
  const a = semiMajorAxisKm;
  return {
    raanDot: 0.0,
    argumentOfPerigeeDot: 0.0,
    meanAnomalyDot: Math.sqrt(body.gravitationalParameterKm3S2 / (a * a * a)),
  };
}

/** NodalArc's declared first-order secular J2 rates. */
function j2SecularRates(
  semiMajorAxisKm: number,
  eccentricity: number,
  inclinationRad: number,
  body: BodyMath,
): SecularRates {
  const a = semiMajorAxisKm;
  const n = Math.sqrt(body.gravitationalParameterKm3S2 / (a * a * a));
  const cosI = Math.cos(inclinationRad);
  const p = a * (1.0 - eccentricity * eccentricity);
  const j2Factor = body.j2 * (body.equatorialRadiusKm / p) ** 2;
  return {
    raanDot: -1.5 * j2Factor * n * cosI,
    // At every eccentricity: on a circular orbit it moves the satellite along its orbit.
    argumentOfPerigeeDot: 0.75 * j2Factor * n * (5.0 * cosI * cosI - 1.0),
    meanAnomalyDot:
      n *
      (1.0 +
        0.75 * j2Factor * Math.sqrt(1.0 - eccentricity * eccentricity) * (3.0 * cosI * cosI - 1.0)),
  };
}

/**
 * The secular rates of the orbit model a node declares: the page's one copy of
 * the backend's `secular_rates` (lib/nodalarc/physics/kepler.py), which the
 * satellite states parity test holds it to. `keplerian-circular` is two-body
 * motion.
 */
export function secularRates(
  propagator: string,
  semiMajorAxisKm: number,
  eccentricity: number,
  inclinationRad: number,
  body: BodyMath,
): SecularRates {
  if (propagator === "two-body" || propagator === "keplerian-circular") {
    return twoBodyRates(semiMajorAxisKm, body);
  }
  if (propagator === "j2-mean-elements") {
    return j2SecularRates(semiMajorAxisKm, eccentricity, inclinationRad, body);
  }
  throw new Error(`Unsupported orbit model: ${propagator}`);
}

function perifocalState(
  semiMajorAxisKm: number,
  eccentricity: number,
  meanAnomalyRad: number,
  meanAnomalyDot: number,
): [number, number, number, number] {
  const eccentricAnomaly = solveEccentricAnomaly(meanAnomalyRad, eccentricity);
  const cosE = Math.cos(eccentricAnomaly);
  const sinE = Math.sin(eccentricAnomaly);
  const sqrtOneMinusE2 = Math.sqrt(1.0 - eccentricity * eccentricity);
  const denom = 1.0 - eccentricity * cosE;
  const eccentricAnomalyDot = meanAnomalyDot / denom;
  return [
    semiMajorAxisKm * (cosE - eccentricity),
    semiMajorAxisKm * sqrtOneMinusE2 * sinE,
    -semiMajorAxisKm * sinE * eccentricAnomalyDot,
    semiMajorAxisKm * sqrtOneMinusE2 * cosE * eccentricAnomalyDot,
  ];
}

function rotationTerms(
  raanRad: number,
  inclinationRad: number,
  argumentOfPerigeeRad: number,
): [[number, number], [number, number], [number, number]] {
  const cosRaan = Math.cos(raanRad);
  const sinRaan = Math.sin(raanRad);
  const cosI = Math.cos(inclinationRad);
  const sinI = Math.sin(inclinationRad);
  const cosArgp = Math.cos(argumentOfPerigeeRad);
  const sinArgp = Math.sin(argumentOfPerigeeRad);
  return [
    [cosRaan * cosArgp - sinRaan * sinArgp * cosI,
      -cosRaan * sinArgp - sinRaan * cosArgp * cosI],
    [sinRaan * cosArgp + cosRaan * sinArgp * cosI,
      -sinRaan * sinArgp + cosRaan * cosArgp * cosI],
    [sinArgp * sinI, cosArgp * sinI],
  ];
}

function rotationDerivativeTerms(
  raanRad: number,
  inclinationRad: number,
  argumentOfPerigeeRad: number,
): [
  [[number, number], [number, number], [number, number]],
  [[number, number], [number, number], [number, number]],
] {
  const cosRaan = Math.cos(raanRad);
  const sinRaan = Math.sin(raanRad);
  const cosI = Math.cos(inclinationRad);
  const sinI = Math.sin(inclinationRad);
  const cosArgp = Math.cos(argumentOfPerigeeRad);
  const sinArgp = Math.sin(argumentOfPerigeeRad);
  return [
    [
      [-sinRaan * cosArgp - cosRaan * sinArgp * cosI,
        sinRaan * sinArgp - cosRaan * cosArgp * cosI],
      [cosRaan * cosArgp - sinRaan * sinArgp * cosI,
        -cosRaan * sinArgp - sinRaan * cosArgp * cosI],
      [0.0, 0.0],
    ],
    [
      [-cosRaan * sinArgp - sinRaan * cosArgp * cosI,
        -cosRaan * cosArgp + sinRaan * sinArgp * cosI],
      [-sinRaan * sinArgp + cosRaan * cosArgp * cosI,
        -sinRaan * cosArgp - cosRaan * sinArgp * cosI],
      [cosArgp * sinI, -sinArgp * sinI],
    ],
  ];
}

function applyRotation(
  matrix: [[number, number], [number, number], [number, number]],
  xPf: number,
  yPf: number,
): [number, number, number] {
  return [
    matrix[0][0] * xPf + matrix[0][1] * yPf,
    matrix[1][0] * xPf + matrix[1][1] * yPf,
    matrix[2][0] * xPf + matrix[2][1] * yPf,
  ];
}

/**
 * Mean elements propagated `dt` seconds at their secular rates: position and
 * velocity on the body's orbit-element axes. The velocity includes the motion
 * of the node and of the perigee.
 */
export function propagateElementsEci(
  semiMajorAxisKm: number,
  eccentricity: number,
  inclinationRad: number,
  raanRad: number,
  argumentOfPerigeeRad: number,
  meanAnomalyRad: number,
  dt: number,
  rates: SecularRates,
): [[number, number, number], [number, number, number]] {
  const raan = raanRad + rates.raanDot * dt;
  const argumentOfPerigee = argumentOfPerigeeRad + rates.argumentOfPerigeeDot * dt;
  const [xPf, yPf, vxPf, vyPf] = perifocalState(
    semiMajorAxisKm,
    eccentricity,
    meanAnomalyRad + rates.meanAnomalyDot * dt,
    rates.meanAnomalyDot,
  );
  const rotation = rotationTerms(raan, inclinationRad, argumentOfPerigee);
  const [dRaan, dArgp] = rotationDerivativeTerms(raan, inclinationRad, argumentOfPerigee);
  const baseVel = applyRotation(rotation, vxPf, vyPf);
  const raanVel = applyRotation(dRaan, xPf, yPf);
  const argpVel = applyRotation(dArgp, xPf, yPf);
  return [
    applyRotation(rotation, xPf, yPf),
    [
      baseVel[0] + rates.raanDot * raanVel[0] + rates.argumentOfPerigeeDot * argpVel[0],
      baseVel[1] + rates.raanDot * raanVel[1] + rates.argumentOfPerigeeDot * argpVel[1],
      baseVel[2] + rates.raanDot * raanVel[2] + rates.argumentOfPerigeeDot * argpVel[2],
    ],
  ];
}

/**
 * A state computed from orbital elements, in the body's orbit-element frame,
 * to the body-fixed frame: the basis puts it on ICRF axes, and `rotation` (the
 * body's sampled orientation at the instant, ICRF to body-fixed) turns it into
 * the body-fixed frame. The backend does the same in physics/satellites.py.
 */
export function elementsToBodyFixed(
  position: Vector3,
  body: BodyMath,
  rotation: Matrix3,
): Vector3 {
  return matVec(rotation, elementsToIcrf(position, body));
}

/** A vector in the body's orbit-element frame to ICRF axes (body-centred). */
export function elementsToIcrf(vector: Vector3, body: BodyMath): Vector3 {
  return matTVec(body.orbitBasis, vector);
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

/** `rotation` is the body's orientation at `simTimeUnix`, from the body samples. */
export function propagateToSceneXYZ(
  elements: KeplerianElements,
  epochUnix: number,
  simTimeUnix: number,
  rotation: Matrix3,
): [number, number, number] {
  const body = elements.body;
  const dt = simTimeUnix - epochUnix;
  const inclinationRad = elements.inclination_deg * DEG2RAD;
  const rates = secularRates(
    elements.propagator ?? "two-body",
    elements.semi_major_axis_km,
    elements.eccentricity,
    inclinationRad,
    body,
  );
  const [posEci] = propagateElementsEci(
    elements.semi_major_axis_km,
    elements.eccentricity,
    inclinationRad,
    elements.raan_deg * DEG2RAD,
    elements.argument_of_perigee_deg * DEG2RAD,
    elements.mean_anomaly_deg * DEG2RAD,
    dt,
    rates,
  );

  const posEcef = elementsToBodyFixed(posEci, body, rotation);
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
 * frozen at `simTimeUnix` (`rotation`, the body's sampled orientation then) —
 * the same snapshot semantics as the circular rings, which are also seeded
 * once and inertially static.
 *
 * Coordinates are scene-LOCAL to the node's body frame (identical pipeline to
 * propagateToSceneXYZ); callers transform them into world space through the
 * registered body group.
 */
export function sampleOrbitPathSceneXYZ(
  elements: KeplerianElements,
  epochUnix: number,
  simTimeUnix: number,
  rotation: Matrix3,
  samples = 180,
): Float32Array {
  const body = elements.body;
  const dt = simTimeUnix - epochUnix;
  const a = elements.semi_major_axis_km;
  const e = elements.eccentricity;
  const inclinationRad = elements.inclination_deg * DEG2RAD;
  // Same secular drift the live position propagation applies.
  const rates = secularRates(elements.propagator ?? "two-body", a, e, inclinationRad, body);
  const rotationAtSeed = rotationTerms(
    elements.raan_deg * DEG2RAD + rates.raanDot * dt,
    inclinationRad,
    elements.argument_of_perigee_deg * DEG2RAD + rates.argumentOfPerigeeDot * dt,
  );

  const sqrtOneMinusE2 = Math.sqrt(1.0 - e * e);
  const positions = new Float32Array((samples + 1) * 3);
  for (let i = 0; i <= samples; i++) {
    const eccentricAnomaly = (i * 2 * Math.PI) / samples;
    const xPf = a * (Math.cos(eccentricAnomaly) - e);
    const yPf = a * sqrtOneMinusE2 * Math.sin(eccentricAnomaly);
    const eci = applyRotation(rotationAtSeed, xPf, yPf);
    const ecef = elementsToBodyFixed(eci, body, rotation);
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
