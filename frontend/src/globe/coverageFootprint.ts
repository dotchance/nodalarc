// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
/** Satellite coverage footprint — radial falloff shader on Earth surface below selected satellite.
 *
 * Replaces the flat disc with a radial gradient parameterized by beam_falloff_exponent
 * from the satellite type. Higher exponents (e.g. Iridium 3.5) produce tight center
 * concentration; lower exponents (e.g. Starlink 2.0) produce broader coverage.
 */

import * as THREE from "three";
import { EARTH_RADIUS } from "../config";
import { getSatellites } from "./satellites";
import { computeConeRadius } from "./groundStations";
import type { Selection } from "../types";

const FOOTPRINT_COLOR = new THREE.Color(0xff44aa);
const MIN_ELEV_DEG = 25;
const SEGMENTS = 96;

const vertexShader = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const fragmentShader = `
  uniform float u_falloff;
  uniform vec3 u_color;
  varying vec2 vUv;

  const float PI = 3.141592653589793;

  void main() {
    float r = length(vUv - 0.5) * 2.0;
    if (r > 0.98) discard;
    float sinElev = sin((1.0 - r) * PI * 0.5);
    float alpha = pow(sinElev, u_falloff) * 0.15;
    gl_FragColor = vec4(u_color, alpha);
  }
`;

let footprintMesh: THREE.Mesh | null = null;
let currentSatId: string | null = null;
let currentAltKm = 0;

function createFootprint(radius: number, falloff: number, scene: THREE.Scene): void {
  const geo = new THREE.CircleGeometry(radius, SEGMENTS);
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      u_falloff: { value: falloff },
      u_color: { value: FOOTPRINT_COLOR },
    },
    vertexShader,
    fragmentShader,
    transparent: true,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  footprintMesh = new THREE.Mesh(geo, mat);
  footprintMesh.renderOrder = 1;
  scene.add(footprintMesh);
}

function disposeFootprint(scene: THREE.Scene): void {
  if (footprintMesh) {
    scene.remove(footprintMesh);
    footprintMesh.geometry.dispose();
    (footprintMesh.material as THREE.Material).dispose();
    footprintMesh = null;
  }
}

function hideCoverageFootprint(): void {
  if (footprintMesh) footprintMesh.visible = false;
  currentSatId = null;
}

export function updateCoverageFootprint(
  selection: Selection | null,
  scene: THREE.Scene,
  _camera: THREE.Camera,
): void {
  if (!selection || selection.type !== "satellite") {
    hideCoverageFootprint();
    return;
  }

  const sat = getSatellites().get(selection.id);
  if (!sat) {
    hideCoverageFootprint();
    return;
  }

  const altKm = sat.nodeState.alt_km;
  const falloff = sat.nodeState.beam_falloff_exponent ?? 2.0;

  // Recreate geometry if satellite changed or altitude shifted significantly
  if (selection.id !== currentSatId || Math.abs(altKm - currentAltKm) > 1) {
    disposeFootprint(scene);
    const radius = computeConeRadius(MIN_ELEV_DEG, altKm);
    createFootprint(radius, falloff, scene);
    currentSatId = selection.id;
    currentAltKm = altKm;
  } else if (footprintMesh) {
    // Update falloff uniform cheaply (no geometry rebuild)
    const mat = footprintMesh.material as THREE.ShaderMaterial;
    if (mat.uniforms.u_falloff) {
      mat.uniforms.u_falloff.value = falloff;
    }
  }

  // Position disc on surface below satellite
  const surfaceNormal = sat.mesh.position.clone().normalize();
  const surfacePos = surfaceNormal.clone().multiplyScalar(EARTH_RADIUS * 1.002);
  const lookTarget = surfaceNormal.clone().multiplyScalar(EARTH_RADIUS * 2);

  footprintMesh!.position.copy(surfacePos);
  footprintMesh!.lookAt(lookTarget);
  footprintMesh!.visible = true;
}

export function clearCoverageFootprint(scene: THREE.Scene): void {
  disposeFootprint(scene);
  currentSatId = null;
  currentAltKm = 0;
}
