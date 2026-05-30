// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Earth visuals — the blue-marble textured sphere + the backside rim-glow atmosphere,
 * and the inertial starfield. Geometry, shaders, and constants reproduce the legacy
 * imperative globe (globe/earth.ts) verbatim for visual parity. The day/night shader,
 * political/day-night globe modes, the sun model, and country boundaries are added in a
 * later phase; this phase is the default blue-marble appearance.
 *
 * <Earth> is a body APPEARANCE component, rendered as a child of <Body> so it lives in
 * the Earth local frame. <Starfield> is inertial and lives at the scene root.
 */

import { useMemo } from "react";
import * as THREE from "three";
import { useLoader } from "@react-three/fiber";
import { EARTH_RADIUS_RENDER } from "./units";

// Backside rim-glow atmosphere shader (globe/earth.ts createAtmosphere) — verbatim.
const ATMO_VERT = `
varying vec3 vNormal;
varying vec3 vViewDir;
void main() {
  vNormal = normalize(normalMatrix * normal);
  vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
  vViewDir = normalize(-mvPos.xyz);
  gl_Position = projectionMatrix * mvPos;
}
`;
const ATMO_FRAG = `
varying vec3 vNormal;
varying vec3 vViewDir;
void main() {
  float rim = 1.0 - max(0.0, dot(vNormal, vViewDir));
  float intensity = pow(rim, 3.0) * 0.6;
  gl_FragColor = vec4(0.4, 0.65, 1.0, intensity);
}
`;

function Atmosphere() {
  return (
    <mesh>
      <sphereGeometry args={[EARTH_RADIUS_RENDER * 1.015, 32, 32]} />
      <shaderMaterial
        vertexShader={ATMO_VERT}
        fragmentShader={ATMO_FRAG}
        side={THREE.BackSide}
        blending={THREE.AdditiveBlending}
        transparent
        depthWrite={false}
      />
    </mesh>
  );
}

/** The blue-marble Earth + its atmosphere shell, in the Earth local frame. */
export function Earth() {
  const dayTexture = useLoader(THREE.TextureLoader, "/earth-blue-marble.jpg");
  useMemo(() => {
    dayTexture.colorSpace = THREE.SRGBColorSpace;
  }, [dayTexture]);
  return (
    <>
      <mesh>
        <sphereGeometry args={[EARTH_RADIUS_RENDER, 64, 64]} />
        <meshPhongMaterial map={dayTexture} shininess={5} />
      </mesh>
      <Atmosphere />
    </>
  );
}

/** 2000 procedural points on a shell at 50 Earth radii — the inertial star background. */
export function Starfield() {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const count = 2000;
    const r = EARTH_RADIUS_RENDER * 50;
    const pos = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return g;
  }, []);
  return (
    <points geometry={geometry}>
      <pointsMaterial
        color={0xffffff}
        size={1.0}
        sizeAttenuation={false}
        transparent
        opacity={0.6}
      />
    </points>
  );
}
