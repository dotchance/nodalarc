/** Earth globe mesh with Blue Marble texture and atmosphere glow. */

import * as THREE from "three";
import { EARTH_RADIUS } from "../config";

export function createEarth(scene: THREE.Scene): THREE.Mesh {
  const geometry = new THREE.SphereGeometry(EARTH_RADIUS, 64, 64);
  const textureLoader = new THREE.TextureLoader();
  const texture = textureLoader.load("/earth-blue-marble.jpg");
  texture.colorSpace = THREE.SRGBColorSpace;

  const material = new THREE.MeshPhongMaterial({
    map: texture,
    specular: new THREE.Color(0x222244),
    shininess: 15,
  });

  const earth = new THREE.Mesh(geometry, material);
  scene.add(earth);
  return earth;
}

export function createAtmosphere(scene: THREE.Scene): THREE.Mesh {
  const geometry = new THREE.SphereGeometry(EARTH_RADIUS * 1.01, 32, 32);
  const material = new THREE.ShaderMaterial({
    vertexShader: `
      varying vec3 vNormal;
      void main() {
        vNormal = normalize(normalMatrix * normal);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying vec3 vNormal;
      void main() {
        float intensity = pow(0.65 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.0);
        gl_FragColor = vec4(0.3, 0.6, 1.0, 1.0) * intensity;
      }
    `,
    blending: THREE.AdditiveBlending,
    side: THREE.BackSide,
    transparent: true,
  });

  const atmosphere = new THREE.Mesh(geometry, material);
  scene.add(atmosphere);
  return atmosphere;
}

export function createLights(scene: THREE.Scene): void {
  const ambient = new THREE.AmbientLight(0x444466);
  scene.add(ambient);

  const directional = new THREE.DirectionalLight(0xffffff, 1.0);
  directional.position.set(5, 3, 5).normalize();
  scene.add(directional);
}
