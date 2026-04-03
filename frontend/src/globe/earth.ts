// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Earth globe mesh with Blue Marble texture, day/night terminator,
 *  atmosphere glow, and starfield.
 */

import * as THREE from "three";
import { EARTH_RADIUS } from "../config";
import type { GlobeMode } from "../types";

let _sunLight: THREE.DirectionalLight | null = null;
let _earthBlueMarble: THREE.Mesh | null = null;
let _earthDayNight: THREE.Mesh | null = null;
let _dayNightMaterial: THREE.ShaderMaterial | null = null;

const DAY_NIGHT_VERTEX = `
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vWorldPosition;
  void main() {
    vUv = uv;
    vWorldNormal = normalize((modelMatrix * vec4(normal, 0.0)).xyz);
    vWorldPosition = (modelMatrix * vec4(position, 1.0)).xyz;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const DAY_NIGHT_FRAGMENT = `
  uniform sampler2D u_dayMap;
  uniform sampler2D u_nightMap;
  uniform vec3 u_sunDirection;
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vWorldPosition;
  void main() {
    vec3 N = normalize(vWorldNormal);
    float NdotL = dot(N, u_sunDirection);
    float blend = smoothstep(-0.15, 0.15, NdotL);
    vec3 dayColor = texture2D(u_dayMap, vUv).rgb;
    vec3 nightColor = texture2D(u_nightMap, vUv).rgb;
    // Day side: modulate by sun angle for shading
    float dayShading = 0.3 + 0.7 * max(0.0, NdotL);
    dayColor *= dayShading;
    // Night side: show city lights at fixed brightness
    nightColor *= 0.8;
    vec3 color = mix(nightColor, dayColor, blend);
    gl_FragColor = vec4(color, 1.0);
  }
`;

function createDayNightEarth(scene: THREE.Scene): void {
  const geometry = new THREE.SphereGeometry(EARTH_RADIUS, 64, 64);
  const textureLoader = new THREE.TextureLoader();

  const dayTexture = textureLoader.load("/earth-blue-marble.jpg");
  dayTexture.colorSpace = THREE.SRGBColorSpace;

  const nightTexture = textureLoader.load("/earth-night.jpg");
  nightTexture.colorSpace = THREE.SRGBColorSpace;

  const material = new THREE.ShaderMaterial({
    uniforms: {
      u_dayMap: { value: dayTexture },
      u_nightMap: { value: nightTexture },
      u_sunDirection: { value: new THREE.Vector3(1, 0, 0) },
    },
    vertexShader: DAY_NIGHT_VERTEX,
    fragmentShader: DAY_NIGHT_FRAGMENT,
  });

  const mesh = new THREE.Mesh(geometry, material);
  mesh.visible = false;
  scene.add(mesh);
  _earthDayNight = mesh;
  _dayNightMaterial = material;
}

export function createEarth(scene: THREE.Scene): THREE.Mesh {
  const geometry = new THREE.SphereGeometry(EARTH_RADIUS, 64, 64);
  const textureLoader = new THREE.TextureLoader();
  const texture = textureLoader.load("/earth-blue-marble.jpg");
  texture.colorSpace = THREE.SRGBColorSpace;

  const material = new THREE.MeshPhongMaterial({
    map: texture,
    shininess: 5,
  });

  const earth = new THREE.Mesh(geometry, material);
  scene.add(earth);
  _earthBlueMarble = earth;

  createDayNightEarth(scene);

  return earth;
}

export function createAtmosphere(scene: THREE.Scene): THREE.Mesh {
  const geometry = new THREE.SphereGeometry(EARTH_RADIUS * 1.015, 32, 32);
  const material = new THREE.ShaderMaterial({
    vertexShader: `
      varying vec3 vNormal;
      varying vec3 vViewDir;
      void main() {
        vNormal = normalize(normalMatrix * normal);
        vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
        vViewDir = normalize(-mvPos.xyz);
        gl_Position = projectionMatrix * mvPos;
      }
    `,
    fragmentShader: `
      varying vec3 vNormal;
      varying vec3 vViewDir;
      void main() {
        float rim = 1.0 - max(0.0, dot(vNormal, vViewDir));
        float intensity = pow(rim, 3.0) * 0.6;
        gl_FragColor = vec4(0.4, 0.65, 1.0, intensity);
      }
    `,
    blending: THREE.AdditiveBlending,
    side: THREE.BackSide,
    transparent: true,
    depthWrite: false,
  });

  const atmosphere = new THREE.Mesh(geometry, material);
  scene.add(atmosphere);
  return atmosphere;
}

export function createStarfield(scene: THREE.Scene): void {
  const count = 2000;
  const positions = new Float32Array(count * 3);
  const sizes = new Float32Array(count);

  for (let i = 0; i < count; i++) {
    // Random points on a distant sphere
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = EARTH_RADIUS * 50;
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
    sizes[i] = 0.5 + Math.random() * 1.5;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("size", new THREE.BufferAttribute(sizes, 1));

  const material = new THREE.PointsMaterial({
    color: 0xffffff,
    size: 1.0,
    sizeAttenuation: false,
    transparent: true,
    opacity: 0.6,
  });

  const stars = new THREE.Points(geometry, material);
  scene.add(stars);
}

export function createLights(scene: THREE.Scene): void {
  // Ambient light for night side visibility
  const ambient = new THREE.AmbientLight(0xffffff, 0.5);
  scene.add(ambient);

  // Directional light simulating sun — positioned based on sim time
  const sunLight = new THREE.DirectionalLight(0xffffff, 1.0);
  sunLight.position.set(1000, 0, 0);
  scene.add(sunLight);
  _sunLight = sunLight;
}

/** Compute approximate sun position from sim time and update directional light.
 *  Uses simple solar declination + hour angle model.
 */
export function updateSunPosition(simTime: string): void {
  if (!_sunLight) return;

  let date: Date;
  try {
    date = new Date(simTime);
    if (isNaN(date.getTime())) return;
  } catch {
    return;
  }

  // Day of year
  const startOfYear = new Date(date.getFullYear(), 0, 0);
  const dayOfYear = (date.getTime() - startOfYear.getTime()) / (1000 * 60 * 60 * 24);

  // Solar declination (approximate): δ = -23.44° × cos(360/365 × (dayOfYear + 10))
  const declRad = (-23.44 * Math.PI / 180) *
    Math.cos((2 * Math.PI / 365) * (dayOfYear + 10));

  // Hour angle from UTC time (solar noon at 0° longitude = 12:00 UTC)
  const hourUTC = date.getUTCHours() + date.getUTCMinutes() / 60 + date.getUTCSeconds() / 3600;
  const hourAngle = ((hourUTC - 12) / 24) * 2 * Math.PI;

  // Sun direction in Three.js coords (right-hand, Y-up)
  // Longitude corresponds to rotation around Y axis
  // Latitude (declination) corresponds to elevation above equatorial plane
  const dist = EARTH_RADIUS * 50;
  const cosDecl = Math.cos(declRad);
  const sinDecl = Math.sin(declRad);

  const sunX = dist * cosDecl * Math.cos(hourAngle);
  const sunY = dist * sinDecl;
  const sunZ = dist * cosDecl * Math.sin(hourAngle);

  _sunLight.position.set(sunX, sunY, sunZ);

  if (_dayNightMaterial) {
    const sunDir = new THREE.Vector3(sunX, sunY, sunZ).normalize();
    _dayNightMaterial.uniforms.u_sunDirection!.value.copy(sunDir);
  }
}

export function setGlobeMode(mode: GlobeMode): void {
  if (_earthBlueMarble) {
    _earthBlueMarble.visible = mode === "blue-marble";
  }
  if (_earthDayNight) {
    _earthDayNight.visible = mode === "day-night";
  }
  if (_sunLight) {
    _sunLight.intensity = mode === "day-night" ? 0.0 : 1.0;
  }
}
