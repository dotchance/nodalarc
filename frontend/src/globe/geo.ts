// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Geographic coordinate conversions for Three.js scene. */

import * as THREE from "three";
import { EARTH_RADIUS, KM_PER_UNIT } from "../config";

/**
 * Convert geographic coordinates to Three.js world position.
 * Must match Three.js SphereGeometry UV mapping so markers align with texture.
 * Three.js sphere: lat=0,lon=0 → positive X axis (prime meridian, equator).
 */
export function geoToWorld(lat_deg: number, lon_deg: number, alt_km: number): THREE.Vector3 {
  const lat = (lat_deg * Math.PI) / 180;
  const lon = (lon_deg * Math.PI) / 180;
  const r = EARTH_RADIUS + alt_km / KM_PER_UNIT;

  return new THREE.Vector3(
    r * Math.cos(lat) * Math.cos(lon),   // X: prime meridian at equator
    r * Math.sin(lat),                    // Y: north pole
    -r * Math.cos(lat) * Math.sin(lon),  // Z: 90°W at equator (negative = 90°E)
  );
}

/**
 * Convert ECEF velocity (km/s) to scene-unit delta per second.
 * ECEF: X=prime meridian equator, Y=90°E equator, Z=north pole.
 * Three.js: X=prime meridian, Y=north, Z=negative 90°E.
 */
export function velocityToScene(vel_x: number, vel_y: number, vel_z: number): THREE.Vector3 {
  const scale = 1 / KM_PER_UNIT;
  return new THREE.Vector3(
    vel_x * scale,   // ECEF X (PM equator) → Three.js X
    vel_z * scale,   // ECEF Z (north pole) → Three.js Y
    -vel_y * scale,  // ECEF Y (90°E equator) → -Three.js Z
  );
}
