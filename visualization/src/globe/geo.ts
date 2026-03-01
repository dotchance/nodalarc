/** Geographic coordinate conversions for Three.js scene. */

import * as THREE from "three";
import { EARTH_RADIUS, KM_PER_UNIT } from "../config";

/**
 * Convert geographic coordinates to Three.js world position.
 * Three.js: Y-up, Z-toward-viewer at (0,0).
 * Geographic: lat=0,lon=0 → positive Z axis (prime meridian, equator).
 */
export function geoToWorld(lat_deg: number, lon_deg: number, alt_km: number): THREE.Vector3 {
  const lat = (lat_deg * Math.PI) / 180;
  const lon = (lon_deg * Math.PI) / 180;
  const r = EARTH_RADIUS + alt_km / KM_PER_UNIT;

  return new THREE.Vector3(
    -r * Math.cos(lat) * Math.sin(lon), // X: negative for right-hand rule
    r * Math.sin(lat),                   // Y: up
    r * Math.cos(lat) * Math.cos(lon),   // Z: toward viewer at equator/prime meridian
  );
}

/**
 * Convert ECEF velocity (km/s) to scene-unit delta per second.
 * ECEF: X=prime meridian equator, Y=north pole, Z=90E equator.
 * Three.js: X=-lon direction, Y=north, Z=prime meridian.
 */
export function velocityToScene(vel_x: number, vel_y: number, vel_z: number): THREE.Vector3 {
  const scale = 1 / KM_PER_UNIT;
  return new THREE.Vector3(
    -vel_z * scale, // ECEF Z (90E) → -Three.js X
    vel_y * scale,  // ECEF Y (north) → Three.js Y
    vel_x * scale,  // ECEF X (prime meridian) → Three.js Z
  );
}
