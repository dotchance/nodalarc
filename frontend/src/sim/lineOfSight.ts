// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Line-of-sight geometry in physical (km, body-fixed) space.
 *
 *  Pure functions, zero dependencies, worker-importable — the physics-layer
 *  sibling of orbitalMath. Consumers decide visibility in KM SPACE and draw
 *  in render space; render scale never feeds these tests (the presentation-
 *  only scale law). The runtime authority for visibility remains OME — these
 *  power previews that must agree with what OME would decide geometrically:
 *  ellipsoidal surface positions, minimum elevation, body occlusion, range.
 */

import { ellipsoidE2, type BodyMath } from "./orbitalMath";

export type Vec3Km = [number, number, number];

const DEG2RAD = Math.PI / 180;

/** Geodetic → body-fixed km (exact ellipsoidal forward of ecefToGeodetic). */
export function geodeticToBodyFixedKm(
  latDeg: number,
  lonDeg: number,
  altKm: number,
  body: BodyMath,
): Vec3Km {
  const lat = latDeg * DEG2RAD;
  const lon = lonDeg * DEG2RAD;
  const e2 = ellipsoidE2(body);
  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  const N = body.equatorialRadiusKm / Math.sqrt(1 - e2 * sinLat * sinLat);
  return [
    (N + altKm) * cosLat * Math.cos(lon),
    (N + altKm) * cosLat * Math.sin(lon),
    (N * (1 - e2) + altKm) * sinLat,
  ];
}

/** Elevation (deg) of `target` above the geodetic horizon at `site`. */
export function elevationDeg(
  siteLatDeg: number,
  siteLonDeg: number,
  site: Vec3Km,
  target: Vec3Km,
): number {
  const lat = siteLatDeg * DEG2RAD;
  const lon = siteLonDeg * DEG2RAD;
  // Geodetic up (ellipsoid normal) at the site.
  const ux = Math.cos(lat) * Math.cos(lon);
  const uy = Math.cos(lat) * Math.sin(lon);
  const uz = Math.sin(lat);
  const dx = target[0] - site[0];
  const dy = target[1] - site[1];
  const dz = target[2] - site[2];
  const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (len < 1e-9) return 90;
  const sinEl = (ux * dx + uy * dy + uz * dz) / len;
  return Math.asin(Math.max(-1, Math.min(1, sinEl))) / DEG2RAD;
}

/**
 * True when the segment a→b passes through a sphere of `radiusKm` centered
 * at the origin. Endpoints on the surface get a small margin so a site does
 * not occlude itself.
 */
export function segmentIntersectsBody(a: Vec3Km, b: Vec3Km, radiusKm: number): boolean {
  const abx = b[0] - a[0];
  const aby = b[1] - a[1];
  const abz = b[2] - a[2];
  const abLenSq = abx * abx + aby * aby + abz * abz;
  if (abLenSq < 1e-9) return false;
  let t = -(a[0] * abx + a[1] * aby + a[2] * abz) / abLenSq;
  t = Math.max(0, Math.min(1, t));
  const cx = a[0] + t * abx;
  const cy = a[1] + t * aby;
  const cz = a[2] + t * abz;
  const closestSq = cx * cx + cy * cy + cz * cz;
  const occluding = radiusKm * 0.999;
  return closestSq < occluding * occluding;
}

export function distanceKm(a: Vec3Km, b: Vec3Km): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const dz = b[2] - a[2];
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}
