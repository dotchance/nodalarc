/** Earth globe mesh with Blue Marble texture, day/night terminator,
 *  atmosphere glow, and starfield.
 */

import * as THREE from "three";
import { EARTH_RADIUS } from "../config";

let _sunLight: THREE.DirectionalLight | null = null;

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
  const ambient = new THREE.AmbientLight(0xffffff, 0.3);
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

  _sunLight.position.set(
    -dist * cosDecl * Math.sin(hourAngle),
    dist * sinDecl,
    dist * cosDecl * Math.cos(hourAngle),
  );
}
