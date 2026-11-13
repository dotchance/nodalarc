// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/**
 * Local Keplerian orbital propagator for the Visualization Frontend.
 *
 * The same computation as the backend's shared satellite function
 * (lib/nodalarc/physics/satellites.py): elements to ICRF axes through the body's
 * orbit basis, then to the body-fixed frame through the body's sampled
 * orientation. Ground stations are static.
 *
 * This module enables 60fps satellite rendering without per-tick position
 * data from the WebSocket. The OME distributes orbital elements once per
 * epoch via SessionEphemeris; the VF propagates locally every frame.
 *
 * __tests__/satelliteStates.test.ts compares the page's results with the
 * backend's, from fixtures the backend writes.
 */

import type { EphemerisBodyFrame, SessionEphemeris } from "../builder/generated/builderApi";
import { cross, matVec, type BodyStateAt } from "./bodySamples";
import {
  SCENE_EARTH_RADIUS,
  elementsToBodyFixed,
  elementsToIcrf,
  ellipsoidE2,
  propagateElementsEci,
  secularRates,
  type BodyMath,
} from "./orbitalMath";

/** One node's ephemeris entry, as SessionEphemeris carries it. */
export type EphemerisNode = SessionEphemeris["nodes"][string];

interface PropagatedPosition {
  latDeg: number;
  lonDeg: number;
  altKm: number;
  velXKmS: number;
  velYKmS: number;
  velZKmS: number;
}

export function kmPerRenderUnitFromEphemeris(ephemeris: SessionEphemeris): number {
  // Render scale is presentation only: anchor on Earth when present so every
  // Earth session keeps its exact historical scale; a session with no Earth
  // frame (luna-only, for instance) anchors on its largest body instead.
  const earth = ephemeris.body_frames.earth;
  if (earth) return earth.equatorial_radius_km / SCENE_EARTH_RADIUS;
  const frames = Object.values(ephemeris.body_frames);
  if (frames.length === 0) {
    throw new Error("SessionEphemeris carries no body frames to derive render scale");
  }
  const anchor = frames.reduce((a, b) =>
    b.equatorial_radius_km > a.equatorial_radius_km ? b : a,
  );
  return anchor.equatorial_radius_km / SCENE_EARTH_RADIUS;
}

export function bodyMathFromFrame(frame: EphemerisBodyFrame, kmPerRenderUnit: number): BodyMath {
  return {
    bodyId: frame.body_id,
    meanRadiusKm: frame.mean_radius_km,
    equatorialRadiusKm: frame.equatorial_radius_km,
    polarRadiusKm: frame.polar_radius_km,
    gravitationalParameterKm3S2: frame.gravitational_parameter_km3_s2,
    j2: frame.j2,
    orbitBasis: frame.orbit_basis,
    kmPerRenderUnit,
  };
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

/**
 * A velocity computed from orbital elements to the body-fixed frame:
 * rotation · (v − ω × r), with r and v on ICRF axes and the body's sampled
 * orientation and angular velocity at the instant.
 */
function elementsVelocityToBodyFixed(
  posElements: [number, number, number],
  velElements: [number, number, number],
  body: BodyMath,
  orientation: Pick<BodyStateAt, "rotation" | "angularVelocityRadS">,
): readonly [number, number, number] {
  const r = elementsToIcrf(posElements, body);
  const v = elementsToIcrf(velElements, body);
  const w = cross(orientation.angularVelocityRadS, r);
  return matVec(orientation.rotation, [v[0] - w[0], v[1] - w[1], v[2] - w[2]]);
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
 * For keplerian nodes: elements → ICRF axes (the body's orbit basis) →
 * body-fixed (the body's sampled orientation at simTimeUnix) → geodetic.
 * For fixed nodes: returns static position.
 */
export function propagateNode(
  node: EphemerisNode,
  epochUnix: number,
  simTimeUnix: number,
  body?: BodyMath,
  orientation?: Pick<BodyStateAt, "rotation" | "angularVelocityRadS">,
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
  if (!orientation) {
    throw new Error(
      `propagateNode requires the sampled orientation of ${node.reference_body} at ${simTimeUnix}`,
    );
  }

  const iRad = deg2rad(node.inclination_deg);
  const rates = secularRates(node.propagator, node.semi_major_axis_km, node.eccentricity, iRad, body);
  const [posEci, velEci] = propagateElementsEci(
    node.semi_major_axis_km,
    node.eccentricity,
    iRad,
    deg2rad(node.raan_deg),
    deg2rad(node.argument_of_perigee_deg),
    deg2rad(node.mean_anomaly_deg),
    simTimeUnix - epochUnix,
    rates,
  );
  const posEcef = elementsToBodyFixed(posEci, body, orientation.rotation);
  const velEcef = elementsVelocityToBodyFixed(posEci, velEci, body, orientation);
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
