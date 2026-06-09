// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Earth visuals: three globe modes
 * (blue-marble | day-night | political) selected by `globeMode`:
 *   blue-marble: Phong-lit textured sphere (sun directional + ambient), boundaries shown.
 *   day-night:   custom terminator shader (city lights on the night side),
 *                boundaries shown.
 *   political:   both Earth meshes hidden; country boundaries over the scene background.
 * Atmosphere (backside rim glow) is always on; the starfield is inertial (scene root).
 *
 * The sun directional light lives in the Earth body frame and is positioned from sim_time by
 * the approximate UTC declination/hour-angle model (NOT gmst — that drives the frame
 * rotation); its post-rotation world direction feeds the day/night shader each frame.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { useFrame, useLoader, useThree } from "@react-three/fiber";
import type { GlobeMode } from "../../types";
import { useBodyFrame } from "./BodyFrame";
import { EARTH_RADIUS_RENDER } from "./units";

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

// Day/night terminator shader (globe/earth.ts createDayNightEarth) — verbatim.
const DAYNIGHT_VERT = `
varying vec2 vUv;
varying vec3 vWorldNormal;
void main() {
  vUv = uv;
  vWorldNormal = normalize((modelMatrix * vec4(normal, 0.0)).xyz);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;
const DAYNIGHT_FRAG = `
uniform sampler2D u_dayMap;
uniform sampler2D u_nightMap;
uniform vec3 u_sunDirection;
varying vec2 vUv;
varying vec3 vWorldNormal;
void main() {
  vec3 N = normalize(vWorldNormal);
  float NdotL = dot(N, u_sunDirection);
  float blend = smoothstep(-0.15, 0.15, NdotL);
  vec3 dayColor = texture2D(u_dayMap, vUv).rgb;
  vec3 nightColor = texture2D(u_nightMap, vUv).rgb;
  float dayShading = 0.3 + 0.7 * max(0.0, NdotL);
  dayColor *= dayShading;
  nightColor *= 0.8;
  vec3 color = mix(nightColor, dayColor, blend);
  gl_FragColor = vec4(color, 1.0);
}
`;

export function sunDirectionForDate(date: Date, target = new THREE.Vector3()): THREE.Vector3 {
  if (Number.isNaN(date.getTime())) {
    throw new Error("invalid date for sun direction");
  }
  const startOfYearMs = Date.UTC(date.getUTCFullYear(), 0, 0);
  const dayOfYear = (date.getTime() - startOfYearMs) / 86400000;
  const declRad = ((-23.44 * Math.PI) / 180) * Math.cos(((2 * Math.PI) / 365) * (dayOfYear + 10));
  const hourUTC = date.getUTCHours() + date.getUTCMinutes() / 60 + date.getUTCSeconds() / 3600;
  const hourAngle = ((hourUTC - 12) / 24) * 2 * Math.PI;
  return target
    .set(
      Math.cos(declRad) * Math.cos(hourAngle),
      Math.sin(declRad),
      Math.cos(declRad) * Math.sin(hourAngle),
    )
    .normalize();
}

function Atmosphere({ radiusRender }: { radiusRender: number }) {
  return (
    <mesh>
      <sphereGeometry args={[radiusRender * 1.015, 32, 32]} />
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

/** Sun directional light (in the Earth body frame) + the day/night shader's sun direction.
 *  Position from the approximate UTC sun model; world direction synced each frame. */
function Sun({
  simTimeIso,
  intensity,
  dayNightMaterial,
  radiusRender,
}: {
  simTimeIso: string | null;
  intensity: number;
  dayNightMaterial: THREE.ShaderMaterial | null;
  radiusRender: number;
}) {
  const lightRef = useRef<THREE.DirectionalLight>(null);
  const worldPos = useMemo(() => new THREE.Vector3(), []);
  const localSunDir = useMemo(() => new THREE.Vector3(), []);

  // Reposition the sun on sim_time change (globe/earth.ts updateSunPosition).
  useEffect(() => {
    const light = lightRef.current;
    if (!light || !simTimeIso) return;
    const date = new Date(simTimeIso);
    if (Number.isNaN(date.getTime())) return;
    const dist = radiusRender * 50;
    sunDirectionForDate(date, localSunDir);
    light.position.copy(localSunDir).multiplyScalar(dist);
  }, [localSunDir, simTimeIso]);

  // After the frame rotation (FrameDriver -2), feed the post-rotation sun world direction
  // into the day/night shader. Default priority runs after the rotation is set this frame.
  useFrame(() => {
    const light = lightRef.current;
    if (!light || !dayNightMaterial) return;
    light.getWorldPosition(worldPos).normalize();
    dayNightMaterial.uniforms.u_sunDirection!.value.copy(worldPos);
  });

  return <directionalLight ref={lightRef} color={0xffffff} intensity={intensity} />;
}

function SunReference({ simTimeIso }: { simTimeIso: string | null }) {
  const pointRef = useRef<THREE.Points>(null);
  const camera = useThree((s) => s.camera);
  const cameraWorld = useMemo(() => new THREE.Vector3(), []);
  const cameraLocal = useMemo(() => new THREE.Vector3(), []);
  const direction = useMemo(() => {
    if (!simTimeIso) return null;
    const date = new Date(simTimeIso);
    if (Number.isNaN(date.getTime())) return null;
    return sunDirectionForDate(date);
  }, [simTimeIso]);
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0]), 3));
    return g;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  useFrame(() => {
    const point = pointRef.current;
    if (!point || !direction) {
      if (point) point.visible = false;
      return;
    }
    point.visible = true;
    const parent = point.parent;
    camera.getWorldPosition(cameraWorld);
    if (parent) {
      parent.worldToLocal(cameraLocal.copy(cameraWorld));
      point.position.copy(cameraLocal);
    } else {
      point.position.copy(cameraWorld);
    }
    point.position.addScaledVector(direction, starShellRadiusForCameraFar(camera.far) * 0.98);
  });
  return (
    <points ref={pointRef} geometry={geometry} frustumCulled={false}>
      <pointsMaterial
        color={0xfff1a8}
        size={8}
        sizeAttenuation={false}
        transparent
        opacity={0.95}
        depthWrite={false}
      />
    </points>
  );
}

/** Country boundaries: Natural Earth 110m as one LineSegments at
 *  R*1.001, shown in political + day-night modes. Loaded once, async. */
function Boundaries({ visible, radiusRender }: { visible: boolean; radiusRender: number }) {
  const [geometry, setGeometry] = useState<THREE.BufferGeometry | null>(null);

  useEffect(() => {
    let alive = true;
    let ownedGeometry: THREE.BufferGeometry | null = null;
    const r = radiusRender * 1.001;
    const toXYZ = (lon: number, lat: number): [number, number, number] => {
      const latR = (lat * Math.PI) / 180;
      const lonR = (lon * Math.PI) / 180;
      return [
        r * Math.cos(latR) * Math.cos(lonR),
        r * Math.sin(latR),
        -r * Math.cos(latR) * Math.sin(lonR),
      ];
    };
    void (async () => {
      try {
        const resp = await fetch("/ne_110m_countries.geojson");
        if (!resp.ok) return;
        const geojson = await resp.json();
        if (!alive) return;
        const verts: number[] = [];
        for (const feature of geojson.features ?? []) {
          const geom = feature.geometry;
          if (!geom) continue;
          const polys: number[][][] =
            geom.type === "Polygon"
              ? geom.coordinates
              : geom.type === "MultiPolygon"
                ? geom.coordinates.flat()
                : [];
          for (const ring of polys) {
            let prev: [number, number, number] | null = null;
            for (const coord of ring) {
              if (coord[0] === undefined || coord[1] === undefined) continue;
              const pt = toXYZ(coord[0], coord[1]);
              if (prev) verts.push(prev[0], prev[1], prev[2], pt[0], pt[1], pt[2]);
              prev = pt;
            }
          }
        }
        if (!verts.length) return;
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(verts), 3));
        ownedGeometry = g;
        setGeometry(g);
      } catch {
        /* boundaries are non-essential */
      }
    })();
    return () => {
      alive = false;
      ownedGeometry?.dispose();
    };
  }, []);

  return (
    <lineSegments geometry={geometry ?? undefined} visible={visible && geometry !== null} renderOrder={2}>
      <lineBasicMaterial color={0x88aacc} transparent opacity={0.55} depthWrite={false} />
    </lineSegments>
  );
}

/** The Earth body appearance — blue-marble + day/night meshes + atmosphere + sun + borders. */
export function Earth({
  globeMode,
  simTimeIso,
}: {
  globeMode: GlobeMode;
  simTimeIso: string | null;
}) {
  const { radiusRender } = useBodyFrame();
  const textures = useLoader(THREE.TextureLoader, ["/earth-blue-marble.jpg", "/earth-night.jpg"]);
  const dayTexture = textures[0]!;
  const nightTexture = textures[1]!;
  useMemo(() => {
    dayTexture.colorSpace = THREE.SRGBColorSpace;
    nightTexture.colorSpace = THREE.SRGBColorSpace;
  }, [dayTexture, nightTexture]);

  const dayNightMaterial = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: {
          u_dayMap: { value: dayTexture },
          u_nightMap: { value: nightTexture },
          u_sunDirection: { value: new THREE.Vector3(1, 0, 0) },
        },
        vertexShader: DAYNIGHT_VERT,
        fragmentShader: DAYNIGHT_FRAG,
      }),
    [dayTexture, nightTexture],
  );
  useEffect(() => () => dayNightMaterial.dispose(), [dayNightMaterial]);

  const showBlueMarble = globeMode === "blue-marble";
  const showDayNight = globeMode === "day-night";
  const showBoundaries = globeMode === "blue-marble" || globeMode === "political" || globeMode === "day-night";
  // Earth day-night mode does its own shader lighting; the directional remains for other bodies.
  const sunIntensity = 1.0;

  return (
    <>
      <mesh visible={showBlueMarble}>
        <sphereGeometry args={[radiusRender, 64, 64]} />
        <meshPhongMaterial map={dayTexture} shininess={5} />
      </mesh>
      <mesh visible={showDayNight} material={dayNightMaterial}>
        <sphereGeometry args={[radiusRender, 64, 64]} />
      </mesh>
      <Atmosphere radiusRender={radiusRender} />
      <Boundaries visible={showBoundaries} radiusRender={radiusRender} />
      <Sun
        simTimeIso={simTimeIso}
        intensity={sunIntensity}
        dayNightMaterial={dayNightMaterial}
        radiusRender={radiusRender}
      />
      <SunReference simTimeIso={simTimeIso} />
    </>
  );
}

export function Moon() {
  const { radiusRender } = useBodyFrame();
  const moonTexture = useLoader(THREE.TextureLoader, "/moon-lroc-color.jpg");
  useMemo(() => {
    moonTexture.colorSpace = THREE.SRGBColorSpace;
    moonTexture.anisotropy = 4;
  }, [moonTexture]);
  return (
    <mesh>
      <sphereGeometry args={[radiusRender, 96, 96]} />
      <meshStandardMaterial map={moonTexture} roughness={1.0} metalness={0.0} />
    </mesh>
  );
}

export function starShellRadiusForCameraFar(cameraFar: number): number {
  if (!Number.isFinite(cameraFar) || cameraFar <= 0) {
    throw new Error(`invalid camera far plane for starfield: ${cameraFar}`);
  }
  const desired = Math.max(EARTH_RADIUS_RENDER * 80, cameraFar * 0.45);
  return Math.min(desired, cameraFar * 0.9);
}

function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (1664525 * state + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

/**
 * Camera-centered inertial star background.
 *
 * Stars are not scene-local geometry: at cislunar scale a finite shell around Earth becomes
 * visible and can sit between Earth and Luna. The points stay centered on the active camera in
 * the star frame, so the sky behaves like a distant background while the frame driver still owns
 * Earth-fixed vs inertial sky rotation.
 */
export function Starfield() {
  const pointsRef = useRef<THREE.Points>(null);
  const camera = useThree((s) => s.camera);
  const cameraWorld = useMemo(() => new THREE.Vector3(), []);
  const cameraLocal = useMemo(() => new THREE.Vector3(), []);
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const count = 3000;
    const random = seededRandom(0x5eed1234);
    const pos = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const theta = random() * Math.PI * 2;
      const phi = Math.acos(2 * random() - 1);
      pos[i * 3] = Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = Math.cos(phi);
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return g;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  useFrame(() => {
    const points = pointsRef.current;
    if (!points) return;
    const parent = points.parent;
    camera.getWorldPosition(cameraWorld);
    if (parent) {
      parent.worldToLocal(cameraLocal.copy(cameraWorld));
      points.position.copy(cameraLocal);
    } else {
      points.position.copy(cameraWorld);
    }
    points.scale.setScalar(starShellRadiusForCameraFar(camera.far));
  });
  return (
    <points ref={pointsRef} geometry={geometry} frustumCulled={false}>
      <pointsMaterial
        color={0xffffff}
        size={1.0}
        sizeAttenuation={false}
        transparent
        opacity={0.6}
        depthWrite={false}
      />
    </points>
  );
}
