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
import { J2000_UNIX_SECONDS, SCENE_EARTH_RADIUS, type BodyMath } from "./orbitalMath";

// ---------------------------------------------------------------------------
// Types (match lib/nodalarc/models/events.py SessionEphemeris)
// ---------------------------------------------------------------------------

export interface EphemerisNodeKeplerian {
  type: "keplerian";
  propagator: "two-body" | "keplerian-circular" | "j2-mean-elements";
  semi_major_axis_km: number;
  eccentricity: number;
  inclination_deg: number;
  raan_deg: number;
  argument_of_perigee_deg: number;
  mean_anomaly_deg: number;
  plane: number;
  slot: number;
  segment_id?: string | null;
  local_node_id?: string | null;
  namespace?: string | null;
  tags?: string[];
  reference_body: string;
  frame_id: string;
}

export interface EphemerisNodeTLE {
  type: "tle";
  tle_line_1: string;
  tle_line_2: string;
  plane: number;
  slot: number;
  norad_id?: number | null;
  segment_id?: string | null;
  local_node_id?: string | null;
  namespace?: string | null;
  tags?: string[];
  reference_body: string;
  frame_id: string;
}

export interface EphemerisNodeFixed {
  type: "fixed";
  lat_deg: number;
  lon_deg: number;
  alt_km: number;
  segment_id?: string | null;
  local_node_id?: string | null;
  namespace?: string | null;
  tags?: string[];
  reference_body: string;
  frame_id: string;
}

export type EphemerisNode = EphemerisNodeKeplerian | EphemerisNodeTLE | EphemerisNodeFixed;

export interface EphemerisBodyFrame {
  body_id: string;
  mean_radius_km: number;
  equatorial_radius_km: number;
  polar_radius_km: number;
  gravitational_parameter_km3_s2: number;
  rotation_rate_rad_s: number;
  j2: number;
  origin_x_km: number;
  origin_y_km: number;
  origin_z_km: number;
  vel_x_km_s: number;
  vel_y_km_s: number;
  vel_z_km_s: number;
  provider: string;
  kernel_id: string;
  quality_tier: string;
  frame: string;
}

export interface SessionEphemeris {
  epoch_id: number;
  sim_time: string; // ISO 8601
  epoch_unix: number;
  nodes: Record<string, EphemerisNode>;
  body_frames: Record<string, EphemerisBodyFrame>;
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

export function kmPerRenderUnitFromEphemeris(ephemeris: SessionEphemeris): number {
  const earth = ephemeris.body_frames.earth;
  if (!earth) {
    throw new Error("SessionEphemeris missing earth body frame required for render scale");
  }
  return earth.equatorial_radius_km / SCENE_EARTH_RADIUS;
}

export function bodyMathFromFrame(frame: EphemerisBodyFrame, kmPerRenderUnit: number): BodyMath {
  return {
    bodyId: frame.body_id,
    meanRadiusKm: frame.mean_radius_km,
    equatorialRadiusKm: frame.equatorial_radius_km,
    polarRadiusKm: frame.polar_radius_km,
    gravitationalParameterKm3S2: frame.gravitational_parameter_km3_s2,
    rotationRateRadS: frame.rotation_rate_rad_s,
    j2: frame.j2,
    kmPerRenderUnit,
  };
}

function ellipsoidE2(body: BodyMath): number {
  const a = body.equatorialRadiusKm;
  const b = body.polarRadiusKm;
  return 1.0 - (b * b) / (a * a);
}

// ---------------------------------------------------------------------------
// Keplerian mean-element propagation
// ---------------------------------------------------------------------------

function deg2rad(deg: number): number {
  return (deg * Math.PI) / 180.0;
}

function rad2deg(rad: number): number {
  return (rad * 180.0) / Math.PI;
}

function solveEccentricAnomaly(meanAnomalyRad: number, eccentricity: number): number {
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

function propagateEci(
  semiMajorAxisKm: number,
  eccentricity: number,
  inclinationRad: number,
  raanRad: number,
  argumentOfPerigeeRad: number,
  meanAnomalyRad: number,
  dt: number,
  body: BodyMath,
): [[number, number, number], [number, number, number]] {
  const a = semiMajorAxisKm;
  const n = Math.sqrt(body.gravitationalParameterKm3S2 / (a * a * a));
  const [xPf, yPf, vxPf, vyPf] = perifocalState(
    a,
    eccentricity,
    meanAnomalyRad + n * dt,
    n,
  );
  const rotation = rotationTerms(raanRad, inclinationRad, argumentOfPerigeeRad);

  return [
    applyRotation(rotation, xPf, yPf),
    applyRotation(rotation, vxPf, vyPf),
  ];
}

function propagateEciJ2MeanElements(
  semiMajorAxisKm: number,
  eccentricity: number,
  inclinationRad: number,
  raanRad: number,
  argumentOfPerigeeRad: number,
  meanAnomalyRad: number,
  dt: number,
  body: BodyMath,
): [[number, number, number], [number, number, number]] {
  const a = semiMajorAxisKm;
  const n = Math.sqrt(body.gravitationalParameterKm3S2 / (a * a * a));
  const cosI = Math.cos(inclinationRad);
  const p = a * (1.0 - eccentricity * eccentricity);
  const j2Factor = body.j2 * (body.equatorialRadiusKm / p) ** 2;
  const raanDot = -1.5 * j2Factor * n * cosI;
  const argumentOfPerigeeDot =
    eccentricity === 0 ? 0.0 : 0.75 * j2Factor * n * (5.0 * cosI * cosI - 1.0);
  const meanAnomalyDot =
    n * (1.0 + 0.75 * j2Factor * Math.sqrt(1.0 - eccentricity * eccentricity) *
      (3.0 * cosI * cosI - 1.0));

  const raan = raanRad + raanDot * dt;
  const argumentOfPerigee = argumentOfPerigeeRad + argumentOfPerigeeDot * dt;
  const [xPf, yPf, vxPf, vyPf] = perifocalState(
    a,
    eccentricity,
    meanAnomalyRad + meanAnomalyDot * dt,
    meanAnomalyDot,
  );
  const rotation = rotationTerms(raan, inclinationRad, argumentOfPerigee);
  const [dRaan, dArgp] = rotationDerivativeTerms(raan, inclinationRad, argumentOfPerigee);
  const baseVel = applyRotation(rotation, vxPf, vyPf);
  const raanVel = applyRotation(dRaan, xPf, yPf);
  const argpVel = applyRotation(dArgp, xPf, yPf);

  return [
    applyRotation(rotation, xPf, yPf),
    [
      baseVel[0] + raanDot * raanVel[0] + argumentOfPerigeeDot * argpVel[0],
      baseVel[1] + raanDot * raanVel[1] + argumentOfPerigeeDot * argpVel[1],
      baseVel[2] + raanDot * raanVel[2] + argumentOfPerigeeDot * argpVel[2],
    ],
  ];
}

/**
 * Convert ECI position to ECEF via GMST rotation.
 */
function eciToEcef(
  posEci: [number, number, number],
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
  body: BodyMath,
): [number, number, number] {
  const theta =
    body.bodyId === "earth"
      ? gmstRadians(unixTimestamp)
      : (unixTimestamp - J2000_UNIX_SECONDS) * body.rotationRateRadS;
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);

  let vx = cosT * velEci[0] + sinT * velEci[1];
  let vy = -sinT * velEci[0] + cosT * velEci[1];
  const vz = velEci[2];

  // Subtract Earth rotation: omega x r_ecef
  const posEcef = eciToEcef(posEci, unixTimestamp, body);
  vx -= -body.rotationRateRadS * posEcef[1];
  vy -= body.rotationRateRadS * posEcef[0];

  return [vx, vy, vz];
}

/**
 * Convert body-fixed XYZ (km) to geodetic (lat_deg, lon_deg, alt_km).
 * Iterative Bowring method on the supplied body ellipsoid.
 */
export function ecefToGeodetic(
  x: number,
  y: number,
  z: number,
  body: BodyMath,
): { latDeg: number; lonDeg: number; altKm: number } {
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

  const altKm =
    Math.abs(cosLat) > 1e-10
      ? p / cosLat - N
      : Math.abs(z) - N * (1.0 - e2);

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
  body?: BodyMath,
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

  if (node.type === "tle") {
    throw new Error("propagateNode does not support tle");
  }
  if (!body) {
    throw new Error(
      `propagateNode requires a body frame for keplerian node reference_body=${node.reference_body}`,
    );
  }

  if (
    node.propagator !== "two-body" &&
    node.propagator !== "keplerian-circular" &&
    node.propagator !== "j2-mean-elements"
  ) {
    throw new Error(`propagateNode does not support ${String(node.propagator)}`);
  }

  const iRad = deg2rad(node.inclination_deg);
  const raanRad = deg2rad(node.raan_deg);
  const argumentOfPerigeeRad = deg2rad(node.argument_of_perigee_deg);
  const meanAnomalyRad = deg2rad(node.mean_anomaly_deg);
  const dt = simTimeUnix - epochUnix;

  const [posEci, velEci] =
    node.propagator === "j2-mean-elements"
      ? propagateEciJ2MeanElements(
          node.semi_major_axis_km,
          node.eccentricity,
          iRad,
          raanRad,
          argumentOfPerigeeRad,
          meanAnomalyRad,
          dt,
          body,
        )
      : propagateEci(
          node.semi_major_axis_km,
          node.eccentricity,
          iRad,
          raanRad,
          argumentOfPerigeeRad,
          meanAnomalyRad,
          dt,
          body,
        );
  const currentTime = epochUnix + dt;
  const posEcef = eciToEcef(posEci, currentTime, body);
  const velEcef = eciToEcefVelocity(posEci, velEci, currentTime, body);
  const geo = ecefToGeodetic(posEcef[0], posEcef[1], posEcef[2], body);

  return {
    latDeg: geo.latDeg,
    lonDeg: geo.lonDeg,
    altKm: geo.altKm,
    velXKmS: velEcef[0],
    velYKmS: velEcef[1],
    velZKmS: velEcef[2],
  };
}
